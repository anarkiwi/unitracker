"""Modulator diagnostics: a tune's occurrences and derived texts, its expressions' firing
schedules, the writers of a unit's cell, and expression forms compared before and after.

``exprs`` pairs the expressions two sets of generic documents write differently; ``display``
records each display form a conversion chooses and compares them with the current code.
"""

import argparse
import functools
import os
import pickle
import re
import sys
from collections import Counter
from pathlib import Path

import yaml
from tqdm import tqdm

import shared
from unitracker import document
from unitracker.document import retime, writer
from unitracker.modfit import convert
from unitracker.modfit import grammar as G
from unitracker.modfit.flow import action_lists, walk
from unitracker.player import oracle
from unitracker.playroutine.library import library

MAX_TICKS, TICKS = 100_000, 3007
FIELDS = {"pw", "cut", "freq"}
NAME = re.compile(r"[a-z]+\d+|none")

# ---- occurrences -------------------------------------------------------------------------


def occurrences(ir: dict, unit: str | None = None, cells: tuple = ()) -> list:
    """One line per occurrence of a lifter's song with its native modulations: unit, voice,
    tick, label, the asked start cells, event instance and derived text (None: no
    modulation)."""
    ir = convert.expand_repeats(ir)
    obs = convert.observe(ir, None)
    _, texts = convert.derive_all(ir, obs, None, workers=1)
    out = []
    for occ, text in zip(obs.occurrences, texts):
        if unit is None or occ.unit == unit:
            shown = " ".join(f"{c}={occ.start.get(c)}" for c in cells)
            out.append(
                f"{occ.unit} v{occ.voice} t{occ.tick} {occ.label} {shown} {occ.instance}: {text}"
            )
    return out


# ---- firings -----------------------------------------------------------------------------


def schedules(native, text: str, want) -> dict:
    """``{(cell key, expression): [(skips, length, first tick, origin)]}`` of the modulators of
    cell ``want`` (every cell for None) in document ``text``."""
    found: dict = {}
    for cell, ticks, origin in oracle.firings(text, native.ticks, progress=True):
        fired = native.fired.get(cell) or {}
        occ = next((fired[t] for t in ticks if t in fired), None)
        if occ is not None and (want is None or cell == want):
            skips = tuple(phi for phi, t in enumerate(ticks) if fired.get(t) != occ)
            mods = found.setdefault((cell[0], native.texts[occ]), [])
            mods.append((skips, len(ticks), ticks[0], origin))
    return found


def schedule_lines(found: dict, shown: int) -> list:
    """Each expression's distinct schedules, the most frequent ``shown`` of them."""
    lines = []
    for (key, expr), mods in sorted(found.items()):
        merged = retime.merged(retime.Schedule(frozenset(m[0]), m[1]) for m in mods)
        state = "agree" if merged is not None else "DIFFER"
        lines.append(f"{key} {expr!r}: {len(mods)} modulators, {state}")
        for (skips, n, origin), count in Counter((m[0], m[1], m[3]) for m in mods).most_common(
            shown
        ):
            first = next(m[2] for m in mods if (m[0], m[1], m[3]) == (skips, n, origin))
            lines.append(
                f"    {count:5d} x length {n:5d} from tick {first:6d} by {origin}:"
                f" skips {list(skips[:16])}"
            )
    return lines


def firings(ir: dict, cell=None, ticks=None, timed: bool = False, shown: int = 6) -> list:
    """The schedule lines of song ``ir``'s modulators of ``cell`` (``(key, voice)``, None:
    every cell), before re-timing unless ``timed``; with ``ticks`` ``(A, B)``, each tick's
    native occurrence and generic ``(φ, start)``."""
    native = document.native_state(ir, MAX_TICKS, TICKS)
    text = document.write(ir, native) if timed else writer.untimed(ir, native)[0]
    lines = schedule_lines(schedules(native, text, cell), shown)
    if ticks and cell:
        fired, phis = native.fired.get(cell) or {}, {}
        for c, at, _ in oracle.firings(text, ticks[1]):
            if c == cell:
                phis.update((t, (phi, at[0])) for phi, t in enumerate(at))
        for t in range(*ticks):
            occ = fired.get(t)
            lines.append(
                f"{t} native {occ} {native.texts.get(occ)} generic (φ, start) {phis.get(t)}"
            )
    return lines


# ---- writers -----------------------------------------------------------------------------


def writes(pr: dict) -> list:
    """``(where, kind, cell, expression)`` of every ``set``, ``accumulate`` and ``with`` in the
    playroutine's action lists, ``where`` the list's dotted path."""
    out = []
    for where, actions in action_lists(pr).items():
        for d in walk(actions):
            out += [(where, "set", c, e) for c, e in (d.get("set") or {}).items()]
            if "accumulate" in d:
                out.append((where, "accumulate", d["accumulate"], d.get("step")))
            out += [(where, "with", c, e) for c, e in (d.get("with") or {}).items()]
    return out


def hook_path(at: dict) -> str:
    """The dotted path prefix of the action lists a start hook's function names."""
    if "proc" in at:
        return f"procs.{at['proc']}"
    if "type" in at:
        return f"record_types.{at['type']}.{at.get('template')}"
    return f"records.{at.get('record')}."


def writer_lines(name: str) -> list:
    """For each unit of a library playroutine: its cell's init values, every write of it, and
    whether each start's function writes it or inherits the value the last writer left."""
    pr = library(name)
    found = writes(pr)
    cells = {**(pr.get("global") or {}), **(pr.get("voice") or {})}
    lines = [f"# {name}"]
    for unit in pr["modulators"]["units"]:
        own = set(unit["cell"].split(":")[:2])
        lines.append(f"\n## unit {unit['name']}: key {unit['key']}, cell {unit['cell']}")
        lines += [f"  init {c} = {(cells.get(c) or {}).get('init', 0)}" for c in sorted(own)]
        lines += [
            f"  {kind:10s} {cell:8s} {where:36s} {expr}"
            for where, kind, cell, expr in found
            if cell in own
        ]
        for hook in unit.get("hooks") or [{"at": unit.get("site", {})}]:
            path = hook_path(hook.get("at") or {})
            hit = any(c in own and w.startswith(path) for w, _, c, _ in found)
            lines.append(f"  start {path}: {'writes the cell' if hit else 'inherits the cell'}")
    return lines


# ---- exprs and display -------------------------------------------------------------------


def pair_lines(label: str, a: str, b: str, verdict: str) -> tuple:
    """``(lines, not fewer tokens)`` of an expression written ``a`` before and ``b`` after."""
    sizes = (G.tokens(a), len(a)), (G.tokens(b), len(b))
    lines = [
        f"{label}: {verdict}",
        f"  before ({sizes[0][0]} tokens, {sizes[0][1]} chars): {a}",
        f"  after  ({sizes[1][0]} tokens, {sizes[1][1]} chars): {b}",
    ]
    return lines, sizes[1][0] >= sizes[0][0]


def leaves(x, path=()):
    """``(path, value)`` of every scalar of a loaded YAML document."""
    if isinstance(x, dict):
        for k, v in x.items():
            yield from leaves(v, (*path, k))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from leaves(v, (*path, i))
    else:
        yield path, x


def expressions(doc: dict) -> dict:
    """``{path: text}`` of every expression a generic document's records define: an ``x``
    record, or a ``pw``, ``cut`` or ``freq`` field."""
    out = {}
    for path, value in leaves(doc.get("song") or [], ("song",)):
        if len(path) < 5 or path[2] != "def" or path[3] == "phrase" or not isinstance(value, str):
            continue
        if (path[3] == "x" or path[-1] in FIELDS) and not NAME.fullmatch(value):
            out[path] = value
    return out


def changed(old: Path, new: Path) -> list:
    """``(tune, path, before, after)`` of every differing expression of the namesake documents."""
    out = []
    for f in sorted(old.glob("*.yaml")):
        if (new / f.name).exists():
            a = expressions(yaml.safe_load(f.read_text(encoding="utf-8")))
            b = expressions(yaml.safe_load((new / f.name).read_text(encoding="utf-8")))
            out += [(f.stem, k, x, b[k]) for k, x in a.items() if b.get(k, x) != x]
    return out


def proof(a, b, bits: int) -> str:
    """Where ``a`` equals ``b``: for every ``v``, else ``0 <= v < 2**bits``, else modulo the
    largest ``2**k`` (``k <= bits``) there, as a unit's wrap to ``k`` bits writes them."""
    if G.equivalent(a, b):
        return "every v"
    bounds = (("v", 0, (1 << bits) - 1),)
    if G.equivalent(a, b, bounds):
        return f"v < 2**{bits}"
    k = next((k for k in range(bits, 0, -1) if G.equivalent(a, b, bounds, k)), None)
    return f"v < 2**{bits} modulo 2**{k}" if k else "NOT EQUAL"


def exprs(old: Path, new: Path, bits: int) -> int:
    """Print each expression pair the two document sets write differently, with where it is
    proved equal; the number of pairs not in fewer tokens."""
    longer = 0
    for tune, path, a, b in tqdm(changed(old, new), desc="pairs", unit="pair"):
        verdict = f"equal for {proof(G.parse(a), G.parse(b), bits)}"
        lines, worse = pair_lines(f"{tune} {'/'.join(map(str, path))}", a, b, verdict)
        print("\n".join(lines))
        longer += worse
    print(f"{longer} pairs not in fewer tokens")
    return longer


class Calls:
    """Counts of ``display`` and ``polar`` calls while installed, ``display`` uncached."""

    def __init__(self):
        self.display = self.polar = 0
        self.saved = G.display, G.polar

    def __enter__(self):
        inner = G.display.__wrapped__

        @functools.lru_cache(maxsize=None)
        def display(*args):
            self.display += 1
            return inner(*args)

        def polar(*args):
            self.polar += 1
            return self.saved[1](*args)

        G.display, G.polar = display, polar
        return self

    def __exit__(self, *exc):
        G.display, G.polar = self.saved


def record(out: Path, tunes: list, subtune) -> None:
    """Convert each tune and keep ``(tune, e, domain, text)`` per ``polar`` call; the text, as an
    unpickled term is rebuilt evaluated."""
    rows, polar = [], G.polar

    def kept(e, domain=()):
        shown = polar(e, domain)
        rows.append((tune, e, tuple(domain), G.text(shown)))
        return shown

    G.polar = kept
    try:
        for tune in tqdm(tunes, desc="tunes", unit="tune"):
            convert.analyse(shared.lift(tune, subtune, expressions=False), cache=None, workers=1)
    finally:
        G.polar = polar
    out.write_bytes(pickle.dumps(rows))
    print(f"{len(rows)} display forms of {len(tunes)} tunes")


def compare(paths: list) -> int:
    """Print the recorded forms the current code writes differently; the number of them."""
    rows = [r for p in paths for r in pickle.loads(Path(p).read_bytes())]
    unique = list(dict.fromkeys((e, d) for _, e, d, _ in rows))
    before = {(e, d): s for _, e, d, s in rows}
    changes, longer, most = 0, 0, (0, 0)
    for e, d in tqdm(unique, desc="forms", unit="form"):
        with Calls() as calls:
            shown = G.polar(e, d)
        most = max(most, (calls.display, calls.polar))
        if G.text(shown) != before[e, d]:
            changes += 1
            equal = G.equivalent(G.parse(before[e, d], e.free_symbols), shown, d)
            lines, worse = pair_lines(str(d), before[e, d], G.text(shown), f"equal {equal}")
            print("\n".join(lines))
            longer += worse
    print(
        f"{changes} of {len(unique)} forms differ, {longer} of them not in fewer tokens; "
        f"most display calls {most[0]}"
    )
    return changes


def main(argv=None) -> int:
    """Parse the command line and run the command."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("occurrences", help="a tune's occurrences and their derived texts")
    p.add_argument("sid")
    p.add_argument("--unit")
    p.add_argument("--cells", default="", help="start cells to show, comma-separated")
    p = sub.add_parser("firings", help="each expression's native firing schedule")
    p.add_argument("sid")
    p.add_argument("--subtune", type=int, help="the subtune from 1 (default: the header's)")
    p.add_argument("--cell", help="KEY:VOICE (voice from 0) or cut")
    p.add_argument("--ticks", help="A:B, the ticks to list")
    p.add_argument("--shown", type=int, default=6, help="schedules shown per expression")
    p.add_argument("--timed", action="store_true", help="the re-timed document's modulators")
    p = sub.add_parser("writers", help="what writes each unit's cell, per playroutine")
    p.add_argument("playroutines", nargs="+")
    p = sub.add_parser("exprs", help="expressions two document sets write differently")
    p.add_argument("old", type=Path)
    p.add_argument("new", type=Path)
    p.add_argument("--bits", type=int, default=16)
    p = sub.add_parser("display", help="record display forms, or compare recorded ones")
    p.add_argument("mode", choices=("record", "compare"))
    p.add_argument("out", type=Path)
    p.add_argument("tunes", nargs="*")
    p.add_argument("--subtune", type=int)
    args = ap.parse_args(argv)
    os.environ.setdefault("UNITRACKER_WORKERS", "1")
    if args.command == "occurrences":
        ir = shared.lift(args.sid, expressions=False)
        print("\n".join(occurrences(ir, args.unit, tuple(c for c in args.cells.split(",") if c))))
    elif args.command == "firings":
        ir = shared.lift(args.sid, None if args.subtune is None else args.subtune - 1)
        key, _, voice = (args.cell or "").partition(":")
        cell = (key, int(voice) if voice else None) if args.cell else None
        ticks = tuple(int(x) for x in args.ticks.split(":")) if args.ticks else None
        print("\n".join(firings(ir, cell, ticks, args.timed, args.shown)))
    elif args.command == "writers":
        print("\n".join(x for name in args.playroutines for x in writer_lines(name)))
    elif args.command == "exprs":
        return int(exprs(args.old, args.new, args.bits) > 0)
    elif args.mode == "record":
        record(args.out, args.tunes, args.subtune)
    else:
        return int(compare([args.out, *args.tunes]) > 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
