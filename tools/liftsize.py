"""How each lifted song's document compares with its native tune: its objects of each kind
against the native model's (the lift's invariants), and its bytes, tokens and xz bytes per native
byte with each top-level section's share, ranked to find representations that blow up.

``python tools/liftsize.py CORPUS LIST.csv ... [--hvsc DIR] [--top N] [--jobs J] [--out F.json]``
"""

import argparse
import json
import lzma
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pymusicassembler
from pydefmon.defmon import DefmonSong
from pysidtracker import SidImage
from pysidtracker.jobs import imap
from pysidwizard import parse_sid
from pysidwizard.writer import build_swm
from tqdm import tqdm

import shared
from unitracker.cli import lifter
from unitracker.direct import invariants
from unitracker.optimiser.measure import size

SECTION = re.compile(rb"^([A-Za-z_]\w*):", re.M)
QUANTILES = (0.1, 0.5, 0.9, 0.99, 1.0)
MEASURES = ("lifted", "tokens", "xz")


def _musicassembler(raw: bytes) -> int:
    song = pymusicassembler.read(raw)
    parts = [*song.orderlists, *song.patterns, *song.instruments, *song.inner_programs]
    return sum(len(p.to_bytes()) for p in parts if p is not None)


DATA = {
    "sidwizard": lambda raw: len(build_swm(parse_sid(raw))),
    "defmon": lambda raw: len(DefmonSong.from_sid_bytes(raw).to_bytes()) - 2,
    "musicassembler": _musicassembler,
}


def native_data(player: str, raw: bytes) -> int | None:
    """The bytes of the tune's song data (every subtune's patterns, orderlists, instruments and
    tables) as its format's package encodes it; None where it has no encoder or fails."""
    try:
        return DATA[player](raw) if player in DATA else None
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def sections(text: bytes) -> dict:
    """Each top-level section's (``ctrl:``, ``phrase:``, ``song:``...) share of ``text``'s bytes."""
    marks = [(m.start(), m.group(1).decode()) for m in SECTION.finditer(text)]
    ends = [start for start, _ in marks[1:]] + [len(text)]
    out = defaultdict(int)
    for (start, key), end in zip(marks, ends):
        out[key] += end - start
    return {k: v / max(1, len(text)) for k, v in out.items()}


def ratios(have: dict, native: dict) -> dict:
    """Each kind's lifted count over its native count (``song``, ``ctrl_layers``: its largest
    voice's or layer's)."""
    out = {}
    for kind, bound in native.items():
        pairs = zip(have[kind], bound) if isinstance(bound, tuple) else [(have[kind], bound)]
        out[kind] = max(c / b if b else float("inf") if c else 0.0 for c, b in pairs)
    return out


def counted(raw: bytes, subtune: int, text: str) -> dict:
    """A song's lifted and native object counts, their :func:`ratios` and the first kind that
    breaks the lift's invariants; a failed native count's error, its kinds unchecked."""
    counts = {"lifted": invariants.lifted(text)}
    try:
        counts["native"] = lifter(raw)[1].native_counts(raw, subtune)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        counts["native"], counts["error"] = {}, repr(exc)
    have, native = counts["lifted"], counts["native"]
    return {"counts": counts, "kinds": ratios(have, native)} | {
        "exceeded": invariants.exceeded(have, native)
    }


def sized(text: bytes, native: int, data: int | None) -> dict:
    """A document's bytes, tokens and xz bytes, their ratios to the tune's ``native`` and song
    ``data`` bytes, and its sections' shares."""
    sizes = {"lifted": len(text), "tokens": size(text.decode()), "xz": len(lzma.compress(text))}
    shares = sections(text)
    return sizes | {
        "sections": shares,
        "dominant": max(shares, key=shares.get, default=""),
        "ratio": {k: v / native for k, v in sizes.items()},
        "data_ratio": {k: v / data for k, v in sizes.items()} if data else None,
    }


def measure(path: Path, docs: list) -> list:
    """The records of one tune's lifted songs ``docs``, ``(name, document path, meta, subtune)``
    each: its :func:`counted` objects and :func:`sized` bytes."""
    raw = path.read_bytes()
    native = len(SidImage.from_sid(raw).image)
    data = native_data(docs[0][2].get("player", ""), raw)
    out = []
    for doc, lifted, meta, subtune in docs:
        text = lifted.read_bytes()
        rec = {"doc": doc, "path": str(path), "player": meta.get("player")}
        rec |= {"ticks": meta.get("ticks"), "native": native, "native_data": data}
        out.append(rec | sized(text, native, data) | counted(raw, subtune, text.decode()))
    return out


def tasks(corpus: Path, sources, hvsc=None) -> tuple:
    """The ``(tune path, docs)`` tasks of the corpus's lifted songs found in the tune lists
    ``sources``, grouped by tune, and the names of the songs not found."""
    rows = {shared.name(r): r for r in shared.songs(shared.rows(sources), "all")}
    groups, missing = defaultdict(list), []
    for meta in sorted((corpus / "meta").glob("*.json")):
        lifted = corpus / "lifted" / f"{meta.stem}.yaml"
        if not lifted.exists():
            continue
        if meta.stem not in rows:
            missing.append(meta.stem)
            continue
        row = rows[meta.stem]
        doc = (meta.stem, lifted, json.loads(meta.read_text()), int(row.get("subtune") or 0))
        groups[row["path"]].append(doc)
    return [(shared.tune(p, hvsc), docs) for p, docs in groups.items()], missing


def run(args) -> list:
    """Every lifted song's :func:`measure` record, in parallel, a failed tune's error in its
    songs' records."""
    todo, missing = tasks(args.corpus, args.tunes, args.hvsc)
    if missing:
        print(f"{len(missing)} lifted songs not in the tune lists, e.g. {missing[0]}")
    out = []
    for i, recs, err in tqdm(imap(measure, todo, args.jobs), total=len(todo), unit="sid"):
        out += recs or [{"doc": doc, "error": repr(err)} for doc, *_ in todo[i][1]]
    return sorted(out, key=lambda r: r["doc"])


def quantiles(values) -> str:
    """The :data:`QUANTILES` of ``values``."""
    return " ".join(f"{v:.3g}" for v in np.quantile(values, QUANTILES, method="nearest"))


def distributions(rs: list) -> list:
    """Lines of each kind's lifted/native ratio quantiles, then each size ratio's."""
    lines = []
    for kind in invariants.KINDS:
        values = [r["kinds"][kind] for r in rs if kind in r["kinds"]]
        if values:
            lines.append(f"  {kind} lifted/native: {quantiles(values)}")
    for base, over in (("ratio", "native"), ("data_ratio", "data")):
        for key in MEASURES:
            values = [r[base][key] for r in rs if r[base]]
            if values:
                lines.append(f"  {key}/{over} bytes: {quantiles(values)}")
    return lines


def worst(r: dict) -> str:
    """A song's line: its largest kind ratio's kind and counts, size ratio and dominant section."""
    kind = max(r["kinds"], key=r["kinds"].get, default="")
    have, most = r["counts"]["lifted"].get(kind), r["counts"]["native"].get(kind)
    section = f"{r['dominant']} {r['sections'].get(r['dominant'], 0):.0%}"
    return f"    {kind} {have}/{most}  {r['ratio']['lifted']:.3g}  {section}  {r['doc']}"


def summary(recs: list, top: int) -> str:
    """Per player, the songs breaking the lift's invariants by kind, each kind's and size
    ratio's quantiles, and the ``top`` songs by their largest kind ratio."""
    ok = [r for r in recs if "ratio" in r]
    lines = [f"{len(ok)} songs ({len(recs) - len(ok)} failed); quantiles {QUANTILES}"]
    for player in sorted({r["player"] for r in ok}):
        rs = [r for r in ok if r["player"] == player]
        broken = Counter(r["exceeded"].split()[2] for r in rs if r["exceeded"])
        unread = sum("error" in r["counts"] for r in rs)
        lines.append(f"{player}: {len(rs)} songs, {broken.total()} break an invariant")
        lines.append(f"  first kind exceeded: {dict(broken.most_common())}; {unread} unread")
        lines += distributions(rs)
        lines.append(f"  top {top} by kind ratio: kind lifted/native, bytes/native, section, doc")
        rs.sort(key=lambda r: -max(r["kinds"].values(), default=0))
        lines += [worst(r) for r in rs[:top]]
    return "\n".join(lines)


def main(argv=None) -> int:
    """Parse the command line, measure the corpus's songs and print the summary."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("corpus", type=Path, help="a tools/corpus.py forms directory")
    ap.add_argument("tunes", nargs="+", help="tune lists (.csv) naming the corpus's tunes")
    ap.add_argument("--hvsc", help="the HVSC root the lists' paths are under")
    ap.add_argument("--top", type=int, default=10, help="songs listed per player")
    ap.add_argument("--jobs", type=int, default=max(1, len(os.sched_getaffinity(0)) - 2))
    ap.add_argument("--out", type=Path, help="write each song's record as JSON")
    args = ap.parse_args(argv)
    recs = run(args)
    if args.out:
        args.out.write_text(json.dumps(recs, indent=1), encoding="utf-8")
    print(summary(recs, args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
