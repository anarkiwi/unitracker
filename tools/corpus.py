"""Lift tunes and corpora: verdicts by cause, samples, canonical-pass tallies, censuses, one
document's grid diff, and pysidtracker survey renderers (``--render corpus:render``).

A tune is a path, a name under ``--hvsc`` or an HVSC name; a ``.csv`` is a survey ``find``
list or an earlier ``check`` table. Usage: README.md, "Debugging tools".
"""

import argparse
import csv
import json
import os
import pprint
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from lark import Token, Tree
from pysidtracker import gridcmp
from pysidtracker.survey import deadline
from tqdm import tqdm
from unitracker_grammar import parse

import shared
from unitracker import optimise
from unitracker.cache import Pool
from unitracker.lift import backends, verify
from unitracker.lift.base import LiftError
from unitracker.player import oracle
from unitracker.player.vm import SongPlayer, event_commands, runs

FIELDS = (
    "doc",
    "path",
    "md5",
    "player",
    "verified",
    "ticks",
    "onset",
    "onset_reg",
    "motion",
    "whole",
    "whole_reg",
    "cells",
    "registers",
    "forbidden",
    "gap",
)
GROUPS = ("freq", "freq", "pw", "pw", "ctrl", "ad", "sr")
GLOBALS = ("cut", "cut", "res/route", "mode/vol")
PLAYERS = {
    "goattracker2": re.compile(r"^GoatTracker_V2\.x$"),
    "sidwizard": re.compile(r"SidWizard", re.I),
    "defmon": re.compile(r"^DefMon$"),
}

# ---- check -------------------------------------------------------------------------------


def span(a: int, b: int) -> str:
    """A tick or a run of ticks."""
    return str(a) if a == b else f"{a}-{b}"


def brief(verdict, shown: int = 6) -> str:
    """A mismatch's differing registers on one line: each, its span, and its first values."""
    entries = runs(list(verdict.cells))
    parts = [
        f"${e['reg']:02X} {e['name']}: {e['cells']} {'tick' if e['cells'] == 1 else 'ticks'} "
        f"{span(e['from'], e['to'])} (doc {e['runs'][0]['values'][0][2]:02x}, "
        f"native {e['runs'][0]['values'][0][3]:02x})"
        for e in entries[:shown]
    ]
    if len(entries) > shown:
        parts.append(f"+{len(entries) - shown} more")
    return "; ".join(parts)


def motion(theirs, ours) -> str:
    """Which grid holds a register across a mismatch's tick, from the two ticks' values:
    ``native holds`` where the native player keeps the value the document changes."""
    if theirs[0] == theirs[1]:
        return "native holds"
    return "document holds" if ours[0] == ours[1] else "both move"


def vm_verdict(lift, song: dict, ticks: int, registers: str) -> verify.Verdict:
    """The lifter's song replayed on its VM against ``ticks`` of the native grid, only
    ``registers`` (comma-separated offsets) compared when given."""
    player, theirs = SongPlayer(song), np.asarray(lift.native().render_grid(ticks), np.uint8)
    ours = player.render_grid(ticks)
    if registers:
        other = np.ones(theirs.shape[1], dtype=bool)
        other[[int(r, 0) for r in registers.split(",")]] = False
        ours[:, other] = theirs[: len(ours), other]
    return verify.compare_grids(ours, player.starts, theirs)


def lifted(path: Path, opts: dict) -> tuple:
    """``(lift, written text, verdict)`` of one tune under the ``check`` options."""
    lift = backends.lifter(path.read_bytes(), expressions=not opts["plain"])
    song = lift.run()
    if not opts["plain"]:
        return lift, lift.document, lift.verdict
    verdict = vm_verdict(lift, song, opts["ticks"], opts["registers"]) if opts["ticks"] else None
    return lift, pprint.pformat(song, width=100), verdict


def check_one(row: dict, opts: dict) -> dict:
    """One tune's result row: its player, whether its lift plays the native grid, the first
    mismatch and which grid holds it, the first from tick 0, its differing registers and
    forbidden words, or the lifting gap; an exception is its gap."""
    shared.single_worker()
    out = Path(opts["out"])
    res = dict.fromkeys(FIELDS, "")
    res.update(doc=shared.name(row), path=row["path"], md5=row.get("md5", ""), verified="no")
    try:
        with deadline(opts["timeout"]):
            lift, text, verdict = lifted(shared.tune(row["path"], opts["hvsc"]), opts)
            res["player"] = lift.playroutine
    except Exception as exc:  # pylint: disable=broad-exception-caught
        res["gap"] = f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
    else:
        if text is None:
            res["gap"] = f"gap: {lift.gap}"
    if res["gap"]:
        (out / f"{res['doc']}.err").write_text(res["gap"] + "\n", encoding="utf-8")
        return res
    (out / f"{res['doc']}.yaml").write_text(text, encoding="utf-8")
    words = (w for w in opts["forbid"] if re.search(rf"(?<![\w]){re.escape(w)}\b", text))
    res["forbidden"] = " ".join(sorted(set(words)))
    if verdict is None:
        res["gap"] = "" if opts["plain"] else f"unverified: {lift.gap}"
        return res
    if opts["diff"]:
        (out / f"{res['doc']}.diff.json").write_text(
            json.dumps(verdict.summary(), indent=2) + "\n", encoding="utf-8"
        )
    res.update(
        verified="yes" if verdict.ok else "no",
        ticks=verdict.ticks,
        cells=len(verdict.cells),
        registers=brief(verdict),
    )
    for key, found in (("onset", verdict.onset), ("whole", verdict.whole)):
        if found is not None:
            res[key], res[f"{key}_reg"] = found[0], f"${found[1]:02X}"
    if verdict.onset is not None and verdict.onset[0] and not opts["plain"]:
        tick, reg = verdict.onset
        theirs = np.asarray(lift.native().render_grid(tick + 1))
        ours = oracle.play(text, tick + 1)[0]
        res["motion"] = motion(theirs[tick - 1 : tick + 1, reg], ours[tick - 1 : tick + 1, reg])
    elif verdict.onset is None and not verdict.ok:
        res["gap"] = f"the document ends at tick {verdict.played}"
    return res


def category(row: dict) -> str:
    """A row's category: verified, lifted (unchecked), the register group of its first
    mismatch and which grid holds it, or its gap with its numbers and records elided."""
    if row.get("verified") == "yes":
        return "verified"
    if row.get("gap"):
        return "gap: " + re.sub(r"\d+", "N", re.sub(r"\{.*?\}|\[.*?\]", "...", row["gap"]))[:80]
    if not row.get("onset_reg"):
        return "lifted"
    reg = int(row["onset_reg"][1:], 16)
    group = GROUPS[reg % 7] if reg < 21 else GLOBALS[reg - 21]
    return f"mismatch: {group}, {row['motion'] or 'first tick'}"


def report(rows: list) -> None:
    """Print the rows, each player's verified count and the categories."""
    print(f"{'doc':40} {'player':12} {'ok':3} {'onset':>10} {'':14} {'from tick 0':>11}  detail")
    for r in rows:
        onset = f"{r['onset']} {r['onset_reg']}" if r["onset"] != "" else ""
        whole = f"{r['whole']} {r['whole_reg']}" if r["whole"] != "" else ""
        detail = r["gap"] or r["registers"]
        if r["forbidden"]:
            detail += f" [forbidden: {r['forbidden']}]"
        print(
            f"{r['doc'][:40]:40} {r['player']:12} {r['verified']:3} {onset:>10}"
            f" {r['motion']:14} {whole:>11}  {detail}"
        )
    print()
    for player in sorted({r["player"] for r in rows}):
        mine = [r for r in rows if r["player"] == player]
        print(f"{player or '?'}: {sum(r['verified'] == 'yes' for r in mine)}/{len(mine)} verified")
    for cause, count in Counter(category(r) for r in rows).most_common():
        print(f"  {count:4d}  {cause}")


def against(rows: list, path: Path) -> None:
    """Print each row's first mismatch beside the one of the same document in ``path``."""
    with path.open(newline="") as fh:
        before = {r["doc"]: r for r in csv.DictReader(fh)}

    def where(r: dict | None) -> str:
        if r is None:
            return "-"
        if r["verified"] == "yes":
            return "verified"
        return f"{r['onset']} {r['onset_reg']}".strip() or r["gap"][:40]

    print(f"\n{'doc':40} {'before':>12} {'after':>12}")
    for r in rows:
        print(f"{r['doc'][:40]:40} {where(before.get(r['doc'])):>12} {where(r):>12}")


def pools(rows: list) -> dict:
    """``{backend: [tune rows]}`` by the rows' SIDId player."""
    out = {name: [] for name in PLAYERS}
    for row in rows:
        for name, pattern in PLAYERS.items():
            if pattern.search(row.get("player", "")):
                out[name].append(row)
    return out


def quotas(count: int, open_pools: list, done: dict) -> dict:
    """Each open pool's next draw: an even share of what the closed pools left, less its lifts."""
    wanted = count - sum(n for name, n in done.items() if name not in open_pools)
    share, extra = divmod(max(wanted, 0), max(len(open_pools), 1))
    return {name: max(share + (i < extra) - done[name], 0) for i, name in enumerate(open_pools)}


def sample(by_player: dict, count: int, rng, check_rows) -> list:
    """Draw from each player's shuffled pool in rounds until ``count`` tunes have documents;
    ``check_rows`` maps a round's tune rows to their result rows."""
    pending = {name: rng.sample(rows, len(rows)) for name, rows in by_player.items()}
    done, out = dict.fromkeys(by_player, 0), []
    progress = tqdm(total=count, desc="documents", unit="tune")
    while sum(done.values()) < count and any(pending.values()):
        draws = quotas(count, [n for n, q in pending.items() if q], done)
        if not any(draws.values()):
            break
        batch = []
        for name, quota in draws.items():
            batch += [(name, row) for row in pending[name][:quota]]
            del pending[name][:quota]
        for (name, _), res in zip(batch, check_rows([row for _, row in batch])):
            written = documented(res)
            done[name] += written
            progress.update(written)
            out.append(res)
    progress.close()
    return out


def documented(res: dict) -> bool:
    """Whether a result row's tune was written (a document, or a plain lift's song)."""
    return bool(res["player"]) and not res["gap"].startswith(("gap: ", "unverified: "))


def check(args) -> int:
    """The ``check`` command; 1 when a sample falls short of its count."""
    args.out.mkdir(parents=True, exist_ok=True)
    opts = {k: getattr(args, k) for k in ("plain", "ticks", "registers", "forbid", "diff")}
    opts.update(out=str(args.out), hvsc=args.hvsc, timeout=args.timeout)
    tunes = shared.rows(args.tunes or shared.REFERENCES, args.player, args.subtunes)
    tunes = [t for t in tunes if "verified" not in t or re.search(args.only, category(t))]

    def run(batch: list) -> list:
        pool = Pool(args.jobs, len(batch))
        try:
            return pool.map(check_one, [(row, opts) for row in batch], "check")
        finally:
            pool.close()

    if args.sample:
        rows = sample(pools(tunes), args.sample, random.Random(args.seed), run)
    else:
        rows = run(tunes)
    rows.sort(key=lambda r: (r["player"], r["doc"]))
    report(rows)
    if args.against:
        against(rows, args.against)
    with (args.out / "results.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return int(bool(args.sample) and sum(map(documented, rows)) < args.sample)


# ---- canonical ---------------------------------------------------------------------------


def tally(path: str, out: str | None, workers: int) -> tuple:
    """``(path, per-pass tally, tokens before, after, error)`` of one tune's canonical passes,
    run on its lift's ``convert`` stage."""
    try:
        lift = backends.lifter(shared.tune(path).read_bytes())
        song = next(s for stage, s in lift.stages() if stage == "convert")
        result = optimise.optimise(song, list(optimise.CANONICAL), workers, progress=False)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return path, {}, 0, 0, f"{type(exc).__name__}: {exc}"
    if out is not None:
        Path(out, f"{Path(path).stem}.yaml").write_text(result.text, encoding="utf-8")
    return path, result.tally, result.before, result.after, ""


def canonical(args) -> int:
    """The ``canonical`` command: per pass, the candidates found, refused and proved."""
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
    out = str(args.out) if args.out else None
    totals, tokens = {name: Counter() for name in optimise.CANONICAL}, Counter()
    pool = Pool(args.jobs, len(args.tunes))
    try:
        results = pool.map(tally, [(p, out, args.workers) for p in args.tunes], "canonical")
    finally:
        pool.close()
    for path, counts, before, after, error in results:
        for name, count in counts.items():
            totals[name].update(count)
        tokens.update(
            before=before, after=after, changed=int(before != after), failed=int(bool(error))
        )
        if error or before != after:
            print(f"{path}: {error or f'{before} -> {after} tokens'}")
    print("| pass | found | refused | proved |\n|---|---|---|---|")
    for name, count in totals.items():
        print(f"| `{name}` | {count['found']} | {count['refused']} | {count['proved']} |")
    print(f"tokens {tokens['before']} -> {tokens['after']} over {len(args.tunes)} songs")
    print(f"({tokens['changed']} changed, {tokens['failed']} not run)")
    return 0


# ---- census ------------------------------------------------------------------------------

CHAINS = ("cmd_", "row_", "ins_")
KINDS = {
    "RESTART": "restart",
    "INS": "ins",
    "CTRL": "ctrl",
    "PW": "pw",
    "FILTER": "filter",
    "X": "x",
    "PHRASE": "phrase",
}
OPS = {
    "MUL": "*",
    "FLOORDIV": "//",
    "MOD": "%",
    "ABS": "abs(",
    "MIN": "min(",
    "MAX": "max(",
    "F": "F(",
    "NEG": "neg",
    "V": "v",
    "PHI": "φ",
    "N": "N",
}
EXPRESSIONS = ("fexpr", "expr")


def _link(node) -> bool:
    return isinstance(node, Tree) and node.data.startswith(CHAINS)


def _split(node: Tree) -> tuple:
    """A chain link's own children and the next link, or None."""
    own = [c for c in node.children if not _link(c)]
    return own, next((c for c in node.children if _link(c)), None)


def _tokens(node) -> list:
    if isinstance(node, Token):
        return [node]
    return list(node.scan_values(lambda v: isinstance(v, Token)))


class Census:
    """Counts keyed by ``(section, item)``, and the documents or tunes that use each."""

    def __init__(self):
        self.count = Counter()
        self.docs = defaultdict(set)

    def add(self, doc: str, section: str, item: str, n: int = 1) -> None:
        """Record ``n`` occurrences of ``item`` in ``section`` for ``doc``."""
        self.count[(section, item)] += n
        self.docs[(section, item)].add(doc)

    def song(self, doc: str, backend: str, ir: dict, codes=None) -> None:
        """Tally a lifter's song's backend and every native command its events play, by
        ``code:arg`` (only ``codes`` when given)."""
        self.add(doc, "backend", backend)
        for events in (ir.get("phrases") or {}).values():
            for event in events:
                for code, arg in event_commands(event):
                    if codes is None or code in codes:
                        self.add(doc, f"{backend} command", f"{int(code)}:{int(arg):#x}")

    def document(self, doc: str, text: str) -> None:
        """Tally document ``text``, named ``doc``."""
        for node in parse(text).children:
            if isinstance(node, Tree):
                getattr(self, f"_{node.data}")(doc, node)

    def _meta(self, doc, node):
        for item in node.children:
            self.add(doc, "meta key", item.children[0].value)

    def _clock(self, doc, _node):
        self.add(doc, "header", "clock")

    def _voices(self, doc, _node):
        self.add(doc, "header", "voices")

    def _tempo(self, doc, node):
        self.add(doc, "header", "tempo swing" if len(node.children) == 2 else "tempo")

    def _tables(self, doc, node):
        for tok in node.children:
            if isinstance(tok, Token):
                self.add(doc, "table", tok.value)

    def _init(self, doc, node):
        self.add(doc, "header", "init")
        for event in node.children:
            self.event(doc, "init", event)

    def _row(self, doc, node):
        sections = [c for c in node.children if c.data == "section"]
        self.add(doc, "timeline", "def row" if sections else "play row")
        for section in sections:
            self.section(doc, section)
        for play in node.children[-1].children:
            types = [t.type for t in play.children]
            form = {"CONT": "~", "END": "end"}.get(types[0], "phrase")
            self.add(doc, "play entry", "transposed" if "SIGNED" in types else form)

    def _ending(self, doc, node):
        self.add(doc, "timeline", "loop" if node.children else "end")

    def section(self, doc: str, node: Tree) -> None:
        """Tally one record type's definitions in a row."""
        kind = KINDS[node.children[0].type]
        values = node.children[1::2]
        self.add(doc, "record type", kind, len(values))
        for value in values:
            if kind == "phrase":
                for event in value.children:
                    self.event(doc, "phrase", event)
            elif kind == "ins":
                self.chain(doc, "ins", _split(value)[1])
            else:
                self.value(doc, kind, [value])

    def chain(self, doc: str, where: str, node) -> list:
        """Tally ``(where key, key)`` and ``(where key, shape)`` of each key the chain of links
        from ``node`` writes; the keys in order."""
        keys = []
        while node is not None:
            own, nxt = _split(node)
            if own:
                key = node.data.split("_", 1)[1]
                key = f"!{key}" if node.data.startswith("cmd_") else key
                keys.append(key)
                self.add(doc, f"{where} key", key)
                self.add(doc, f"{where} {key}", self.value(doc, f"{where} {key}", own))
            node = nxt
        return keys

    def value(self, doc: str, where: str, nodes: list) -> str:
        """How ``nodes`` are written (token types, ``program``, ``int`` or ``expr``), tallying
        the programs and expressions among them."""
        shapes = []
        for node in nodes:
            if isinstance(node, Token):
                shapes.append(node.type.lower())
            elif node.data == "program":
                self.program(doc, where.split()[-1], node)
                shapes.append("program")
            elif node.data in EXPRESSIONS:
                shapes.append(self.expression(doc, where, node))
            else:
                shapes.append(self.value(doc, where, node.children))
        return "/".join(shapes)

    def program(self, doc: str, kind: str, node: Tree) -> None:
        """Tally a program's rows, their keys and its ending."""
        rows = [c for c in node.children if isinstance(c, Tree)]
        self.add(doc, f"{kind} program", "loop" if node.children[-1].type == "INT" else "end")
        self.add(doc, f"{kind} rows", "row", len(rows))
        for row in rows:
            if not row.children:
                self.add(doc, f"{kind} rows", "empty row")
            self.chain(doc, f"{kind} row", next(iter(row.children), None))

    def expression(self, doc: str, where: str, node: Tree) -> str:
        """Tally an expression's operators under ``where ops``; ``int`` for a constant."""
        ops = Counter()
        for tok in _tokens(node):
            if tok.type in ("ADD", "CMP"):
                ops[tok.value.strip()] += 1
            elif tok.type == "XTABLE":
                ops[f"{tok.value}[]"] += 1
            elif tok.type in OPS:
                ops[OPS[tok.type]] += 1
        for op, n in ops.items():
            self.add(doc, f"{where} ops", op, n)
        return "expr" if ops else "int"

    def event(self, doc: str, where: str, node: Tree) -> None:
        """Tally an event's note form, instrument, commands, keep flags and repeat."""
        tokens = [c for c in node.children if isinstance(c, Token)]
        note = tokens[0].value
        named = any(t.type == "INS" for t in tokens)
        self.add(doc, f"{where} note", note if note in ("-", "off", "on") else "pitch")
        self.add(doc, f"{where} event", "names instrument" if named else "no instrument")
        if tokens[-1].type == "INT":
            self.add(doc, f"{where} event", "*steps")
        trees = [c for c in node.children if isinstance(c, Tree)]
        keys = self.chain(doc, f"{where} command", trees[0])
        for keep in trees[1:]:
            if keep.children:
                keys.append(keep.children[0].value)
                self.add(doc, f"{where} command key", keys[-1])
        if len(keys) > 1:
            self.add(doc, f"{where} event", f"{len(keys)} commands")
            self.add(doc, f"{where} command set", "+".join(keys))

    def report(self, total: int) -> str:
        """One table per section: item, documents using it, occurrences."""
        lines = [f"## {total} documents"]
        by_section = defaultdict(list)
        for (section, item), n in self.count.items():
            by_section[section].append((item, len(self.docs[(section, item)]), n))
        for section in sorted(by_section):
            lines.append(f"\n{section}:")
            for item, docs, n in sorted(by_section[section], key=lambda t: (-t[1], -t[2], t[0])):
                lines.append(f"  {item:28s} {docs:4d} docs {n:8d}")
        return "\n".join(lines)


def census(args) -> int:
    """The ``census`` command: generic documents' vocabulary and tunes' native commands."""
    found = Census()
    codes = {int(c, 0) for c in args.codes.split(",") if c} or None
    for path in tqdm(args.inputs, unit="input"):
        if path.suffix != ".sid":
            found.document(str(path), path.read_text(encoding="utf-8"))
            continue
        raw = path.read_bytes()
        try:
            found.song(
                str(path), backends.detect(raw), backends.lift(raw, expressions=False), codes
            )
        except ValueError as exc:
            tqdm.write(f"{path.stem}: {exc}")
    print(found.report(len(args.inputs)))
    if args.json:
        out = {f"{s}|{i}": [n, sorted(found.docs[(s, i)])] for (s, i), n in found.count.items()}
        args.json.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


# ---- grid and the survey's renderers -----------------------------------------------------


def grid(args) -> int:
    """The ``grid`` command: a document on unitracker-player against its tune's native player,
    the onset diff and the grids around it, or the JSON diff; ``--grids`` keeps both grids as
    gridcmp files. 1 when they differ."""
    ours, starts = oracle.play(args.doc.read_text(encoding="utf-8"), args.ticks, progress=True)
    raw = shared.tune(args.sid).read_bytes()
    theirs = np.asarray(backends.native_player(raw, args.subtune).render_grid(args.ticks))
    verdict = verify.compare_grids(ours, starts, theirs)
    if args.grids:
        gridcmp.write_grid(ours, args.grids / "doc.csv.zst")
        gridcmp.write_grid(theirs, args.grids / "native.csv.zst")
    if args.json is not None:
        text = json.dumps(verdict.summary(), indent=2) + "\n"
        if args.json == "-":
            sys.stdout.write(text)
        else:
            Path(args.json).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json).write_text(text, encoding="utf-8")
        return int(not verdict.ok)
    print(f"starts: {starts}\n{verdict.report()}")
    if verdict.onset is not None:
        print(gridcmp.window_report(theirs, ours, verdict.onset[0], args.context))
    return int(not verdict.ok)


def render(data, song, nframes):
    """The survey renderer: 1-based ``song`` of a ``.sid`` lifted in-process and its generic
    document played on unitracker-player."""
    shared.single_worker()
    lift = backends.lifter(data, song - 1)
    lift.run()
    if lift.document is None:
        raise LiftError(f"no generic document: {lift.gap}")
    played = oracle.play(lift.document, nframes)[0]
    info = {"backend": backends.detect(data), "chars": len(lift.document)}
    return np.asarray(played, np.int16), info


def native(data, song, nframes):
    """The survey renderer of 1-based ``song`` of a ``.sid`` on its backend's native player."""
    played = backends.native_player(data, song - 1).render_grid(nframes)
    return np.asarray(played, np.int16), {"backend": backends.detect(data)}


def main(argv=None) -> int:
    """Parse the command line and run the command."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0], fromfile_prefix_chars="@")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check", help="lift tunes in parallel; verdicts and causes")
    p.add_argument("tunes", nargs="*", metavar="TUNE.sid|LIST.csv")
    p.add_argument("--hvsc", type=Path, default=os.environ.get("HVSC"))
    p.add_argument("--out", type=Path, default=Path("out/check"))
    p.add_argument("--plain", action="store_true", help="lift without expressions")
    p.add_argument("--ticks", type=int, default=0, help="--plain: replay on the VM this long")
    p.add_argument("--registers", default="", help="--plain --ticks: compare only these")
    p.add_argument("--player", default="", help="a list's SIDId players (regex)")
    p.add_argument("--subtunes", type=int, help="a list's tunes with at most this many")
    p.add_argument("--only", default="", help="an earlier table's rows of these causes (regex)")
    p.add_argument("--sample", type=int, help="this many documents, balanced over players")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--forbid", nargs="*", default=[], help="words a document must not hold")
    p.add_argument("--diff", action="store_true", help="keep each verdict's JSON diff")
    p.add_argument("--against", type=Path, help="an earlier results.csv to compare with")
    p.add_argument("--timeout", type=float, default=0, help="seconds per tune (0: none)")
    p.add_argument("--jobs", type=int, default=max(1, len(os.sched_getaffinity(0)) // 4))
    p = sub.add_parser("canonical", help="tally the canonical passes over tunes")
    p.add_argument("tunes", nargs="+", metavar="TUNE.sid")
    p.add_argument(
        "--workers", type=int, default=int(os.environ.get("UNITRACKER_WORKERS", 0)) or None
    )
    p.add_argument("--jobs", type=int, default=1, help="songs at once, each with --workers")
    p.add_argument("--out", type=Path)
    p = sub.add_parser("census", help="tally documents' vocabulary and tunes' native commands")
    p.add_argument("inputs", type=Path, nargs="+", metavar="DOC.yaml|TUNE.sid")
    p.add_argument("--codes", default="", help="only these native command codes")
    p.add_argument("--json", type=Path)
    p = sub.add_parser("grid", help="where a document's grid differs from its native player's")
    p.add_argument("doc", type=Path)
    p.add_argument("sid")
    p.add_argument("--ticks", type=int, default=3007)
    p.add_argument("--subtune", type=int)
    p.add_argument("--context", type=int, default=3, help="ticks either side of the onset")
    p.add_argument("--json", nargs="?", const="-", metavar="PATH", help="the JSON diff")
    p.add_argument("--grids", type=Path, help="write both grids as gridcmp files here")
    args = ap.parse_args(argv)
    commands = {"check": check, "canonical": canonical, "census": census, "grid": grid}
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
