"""How large each lifted song's document is against its native tune: bytes, tokens and xz bytes
per native byte, and each top-level section's share, ranked to find representations that blow up.

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
import pymusicassembler
from pydefmon.defmon import DefmonSong
from pysidtracker import SidImage
from pysidtracker.jobs import imap
from pysidwizard import parse_sid
from pysidwizard.writer import build_swm
from tqdm import tqdm

import shared
from unitracker.optimiser.measure import size

SECTION = re.compile(rb"^([A-Za-z_]\w*):", re.M)
QUANTILES = (0.1, 0.5, 0.9, 0.99, 1.0)
MEASURES = ("lifted", "tokens", "xz")
MAD_SIGMA = 1.4826


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


def measure(path: Path, docs: list) -> list:
    """The records of one tune's lifted songs ``docs``, ``(name, document path, meta)`` each:
    sizes, their ratios to the tune's native and song data bytes, and section shares."""
    raw = path.read_bytes()
    native = len(SidImage.from_sid(raw).image)
    data = native_data(docs[0][2].get("player", ""), raw)
    out = []
    for doc, lifted, meta in docs:
        text = lifted.read_bytes()
        sizes = {"lifted": len(text), "tokens": size(text.decode()), "xz": len(lzma.compress(text))}
        shares = sections(text)
        rec = {
            "doc": doc,
            "path": str(path),
            "player": meta.get("player"),
            "ticks": meta.get("ticks"),
        }
        rec |= {"native": native, "native_data": data, **sizes, "sections": shares}
        rec["ratio"] = {k: v / native for k, v in sizes.items()}
        rec["data_ratio"] = {k: v / data for k, v in sizes.items()} if data else None
        rec["dominant"] = max(shares, key=shares.get, default="")
        out.append(rec)
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
        groups[rows[meta.stem]["path"]].append((meta.stem, lifted, json.loads(meta.read_text())))
    return [(shared.tune(p, hvsc), docs) for p, docs in groups.items()], missing


def run(args) -> list:
    """Every lifted song's :func:`measure` record, in parallel, a failed tune's error in its
    songs' records."""
    todo, missing = tasks(args.corpus, args.tunes, args.hvsc)
    if missing:
        print(f"{len(missing)} lifted songs not in the tune lists, e.g. {missing[0]}")
    out = []
    for i, recs, err in tqdm(imap(measure, todo, args.jobs), total=len(todo), unit="sid"):
        out += recs or [{"doc": doc, "error": repr(err)} for doc, _, _ in todo[i][1]]
    return sorted(out, key=lambda r: r["doc"])


def robust_z(values) -> np.ndarray:
    """Each value's log's distance from the logs' median in MAD-estimated standard deviations."""
    logs = np.log(values)
    dev = logs - np.median(logs)
    return dev / (MAD_SIGMA * (np.median(np.abs(dev)) or 1.0))


def summary(recs: list, top: int) -> str:
    """Per player, each ratio's quantiles and the ``top`` songs by lifted and by xz ratio, each
    with its robust z-score (:func:`robust_z`) and dominant section."""
    ok = [r for r in recs if "ratio" in r]
    lines = [f"{len(ok)} songs ({len(recs) - len(ok)} failed); quantiles {QUANTILES}"]
    for player in sorted({r["player"] for r in ok}):
        rs = [r for r in ok if r["player"] == player]
        lines.append(f"{player}: {len(rs)} songs")
        for base in ("ratio", "data_ratio"):
            for key in MEASURES:
                values = [r[base][key] for r in rs if r[base]]
                if values:
                    q = " ".join(f"{v:.3g}" for v in np.quantile(values, QUANTILES))
                    lines.append(f"  {key}/{'native' if base == 'ratio' else 'data'}: {q}")
        for key in ("lifted", "xz"):
            z = robust_z([r["ratio"][key] for r in rs])
            lines.append(f"  top by {key}/native: lifted/native xz/native z bytes section doc")
            lines += [
                f"  {rs[k]['ratio']['lifted']:9.3g} {rs[k]['ratio']['xz']:6.3g} {z[k]:5.1f} "
                f"{rs[k]['lifted']:>9} {rs[k]['dominant']} "
                f"{rs[k]['sections'].get(rs[k]['dominant'], 0):.0%} {rs[k]['doc']}"
                for k in np.argsort(-z)[:top]
            ]
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
