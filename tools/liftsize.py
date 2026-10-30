"""How each lifted song's document compares with its native tune: its bytes, tokens and xz bytes
per native byte with each top-level section's share, ranked to find representations that blow up.

``python tools/liftsize.py CORPUS LIST.csv ... [--hvsc DIR] [--top N] [--jobs J] [--out F.json]``
"""

import argparse
import json
import lzma
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from pysidtracker import SidImage
from pysidtracker.jobs import imap
from tqdm import tqdm

import shared
from unitracker.optimiser.measure import size

SECTION = re.compile(rb"^([A-Za-z_]\w*):", re.M)
QUANTILES = (0.1, 0.5, 0.9, 0.99, 1.0)
MEASURES = ("lifted", "tokens", "xz")


def sections(text: bytes) -> dict:
    """Each top-level section's (``ctrl:``, ``phrase:``, ``song:``...) share of ``text``'s bytes."""
    marks = [(m.start(), m.group(1).decode()) for m in SECTION.finditer(text)]
    ends = [start for start, _ in marks[1:]] + [len(text)]
    out = defaultdict(int)
    for (start, key), end in zip(marks, ends):
        out[key] += end - start
    return {k: v / max(1, len(text)) for k, v in out.items()}


def sized(text: bytes, native: int) -> dict:
    """A document's bytes, tokens and xz bytes, their ratios to the tune's ``native`` bytes and its
    sections' shares."""
    sizes = {"lifted": len(text), "tokens": size(text.decode()), "xz": len(lzma.compress(text))}
    shares = sections(text)
    return sizes | {
        "sections": shares,
        "dominant": max(shares, key=shares.get, default=""),
        "ratio": {k: v / native for k, v in sizes.items()},
    }


def measure(path: Path, docs: list) -> list:
    """The records of one tune's lifted songs ``docs``, ``(name, document path, meta, subtune)``
    each: its :func:`sized` bytes."""
    raw = path.read_bytes()
    native = len(SidImage.from_sid(raw).image)
    out = []
    for doc, lifted, meta, _ in docs:
        text = lifted.read_bytes()
        rec = {"doc": doc, "path": str(path), "player": meta.get("player")}
        rec |= {"ticks": meta.get("ticks"), "native": native}
        out.append(rec | sized(text, native))
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
    """Lines of each size ratio's quantiles."""
    return [f"  {key}/native bytes: {quantiles([r['ratio'][key] for r in rs])}" for key in MEASURES]


def worst(r: dict) -> str:
    """A song's line: its xz size ratio, dominant section and name."""
    section = f"{r['dominant']} {r['sections'].get(r['dominant'], 0):.0%}"
    return f"    {r['ratio']['xz']:.3g}  {section}  {r['doc']}"


def summary(recs: list, top: int) -> str:
    """Per player, each size ratio's quantiles and the ``top`` songs by their xz size ratio."""
    ok = [r for r in recs if "ratio" in r]
    lines = [f"{len(ok)} songs ({len(recs) - len(ok)} failed); quantiles {QUANTILES}"]
    for player in sorted({r["player"] for r in ok}):
        rs = [r for r in ok if r["player"] == player]
        lines.append(f"{player}: {len(rs)} songs")
        lines += distributions(rs)
        lines.append(f"  top {top} by xz bytes/native: ratio, section, doc")
        rs.sort(key=lambda r: -r["ratio"]["xz"])
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
