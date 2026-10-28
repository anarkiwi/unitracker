"""Attribute a defMON transliteration's differing registers to the sidTAB rows behind them.

A run of a voice's differing control register is caused by the rows the replay fetches on that
voice at its first tick ($12DF): a ``gap`` row (``Lift.gaps``), a ``shadow`` row whose ``-``
nibble another layer or program set otherwise, or ``none``. See ``main`` for the corpus mode.
"""

import argparse
import csv
import json
import os
from collections import Counter
from pathlib import Path

import numpy as np
from pydefmon import DefmonPlayer
from pysidtracker import SidImage, gridcmp
from pysidtracker.jobs import deadline, imap
from tqdm import tqdm
from unitracker_player.player import play

import shared
from unitracker.direct import dmlift, invariants
from unitracker.direct.dmimage import locate
from unitracker.optimiser.measure import size

ROWS = (0x12EF, 0x1382)  # layer A's and layer B's row cell of voice 1 ($1000 build)
CTRL = (4, 11, 18)


def fetches(raw: bytes, subtune: int, calls: int) -> tuple:
    """``(grid, log)``: ``calls`` of the replay's register grid and per call the ``(voice,
    layer, row)`` of each sidTAB row it fetches, in the order it fetches them."""
    ref = DefmonPlayer(raw, subtune=subtune)
    obs, at = ref._obs, locate(ref._mem)[0]  # pylint: disable=protected-access
    cells = {
        at[base] + off: (v, layer)
        for layer, base in enumerate(ROWS)
        for v, off in enumerate(dmlift.VOICES)
    }
    log, last = [], [None]

    def stored(addr, value):
        last[0] = (*cells[addr], (value - 1) & 0xFF)

    obs.subscribe_to_write(list(cells), stored)
    obs.subscribe_to_write([at.get(0xFB, 0xFB)], lambda *_: log[-1].append(last[0]))
    grid = np.zeros((calls, 25), np.uint8)
    for c in range(calls):
        log.append([])
        ref.play_frame()
        grid[c] = ref.regs
    return grid, log


def attribute(raw: bytes, subtune: int = 0) -> tuple:
    """``(lift, text, cells, runs)``: the transliteration, its differing cells over the song and
    its :func:`control_runs`."""
    lift = dmlift.Lift(dmlift.Tune(raw, subtune))
    text = lift.document()
    ours = play(text, ticks=min(lift.ticks(), dmlift.MAX_TICKS))
    theirs, log = fetches(raw, subtune, len(ours))
    return lift, text, gridcmp.cell_diff(ours, theirs), control_runs(lift, ours, theirs, log)


def control_runs(lift, ours, theirs, log) -> list:
    """Each run of a voice's differing control register as ``(voice, first tick, ticks,
    cause, (layer, row) or None)``, by the rows the replay's :func:`fetches` ``log`` holds."""
    gaps, runs = {row for _, row, _ in lift.gaps}, []
    for v, reg in enumerate(CTRL):
        bad = np.r_[False, ours[:, reg] != theirs[:, reg], False]
        for start, end in np.flatnonzero(bad[1:] != bad[:-1]).reshape(-1, 2).tolist():
            wg = [
                (layer, row)
                for voice, layer, row in (log[start - 1] if start else ())
                if voice == v and {"wgh", "wgl"} & lift.tune.columns(row).keys()
            ]
            cause = "none" if not wg else "gap" if wg[-1][1] in gaps else "shadow"
            runs.append((v, start, end - start, cause, wg[-1] if wg else None))
    return runs


def data_bytes(raw: bytes, lift) -> int:
    """The replay image's bytes from its data base ($1800 in the $1000 build) to its end."""
    image = SidImage.from_sid(raw)
    return image.header.real_load_address + len(image.image) - lift.tune.at[dmlift.SIDTAB_LO]


def verdict(cells, runs) -> dict:
    """``exact``, ``gaps`` (every differing cell a control register a gap row's run holds) or
    ``differing``; the cells by cause, and the other registers' differing cells."""
    causes = Counter()
    for _, _, ticks, cause, _ in runs:
        causes[cause] += ticks
    other = cells[~np.isin(cells[:, 1], CTRL)]
    gaps = cells.size and not other.size and len(cells) == causes["gap"]
    return {
        "verdict": "gaps" if gaps else "differing" if cells.size else "exact",
        "cells": len(cells),
        "first": cells[0].tolist() if cells.size else None,
        "causes": dict(causes),
        "other": {int(r): int(n) for r, n in zip(*np.unique(other[:, 1], return_counts=True))},
        "first_other": other[0].tolist() if other.size else None,
    }


def record(path: str, subtune: int = 0) -> dict:
    """One tune's :func:`verdict`, its gap rows and cut patterns, sizes and the first count
    its document exceeds."""
    raw = Path(path).read_bytes()
    with deadline(LIMIT):
        lift, text, cells, runs = attribute(raw, subtune)
    native = dmlift.native_counts(raw, subtune)
    return (
        {"path": path}
        | verdict(cells, runs)
        | {
            "gap_rows": len({row for _, row, _ in lift.gaps}),
            "cuts": len(lift.cuts),
            "ticks": lift.ticks(),
            "bytes": len(text),
            "tokens": size(text),
            "data": data_bytes(raw, lift),
            "exceeded": invariants.exceeded(invariants.lifted(text), native),
        }
    )


LIMIT = 1800


def corpus(tunes: Path, hvsc: str, jobs: int, out: Path) -> list:
    """Each DefMon tune's :func:`record` of ``tunes`` (subtune 0), written to ``out`` as JSON
    lines, its verdicts' tally printed."""
    with open(tunes, newline="", encoding="utf-8") as f:
        paths = [str(Path(hvsc) / r["path"]) for r in csv.DictReader(f) if r["player"] == "DefMon"]
    tally, recs = Counter(), []
    with open(out, "w", encoding="utf-8") as f:
        progress = tqdm(imap(record, [(p,) for p in paths], jobs), total=len(paths), unit="sid")
        for i, rec, error in progress:
            rec = rec or {"path": paths[i], "verdict": "failed", "error": repr(error)}
            tally[rec["verdict"]] += 1
            recs.append(rec)
            f.write(json.dumps(rec) + "\n")
            progress.set_postfix(tally)
    print(dict(tally))
    return recs


def sizes(recs: list) -> None:
    """Print the quantiles of the records' document bytes and tokens and their median per
    native data byte."""
    sized = [r for r in recs if "bytes" in r]
    data = np.array([r["data"] for r in sized])
    for key in ("bytes", "tokens"):
        values = np.array([r[key] for r in sized])
        quantiles = np.quantile(values, (0, 0.5, 0.9, 1)).round().astype(int).tolist()
        median = np.median(values / data)
        print(f"{key}: min/median/p90/max {quantiles}, per data byte median {median:.2f}")


def report(path: str, subtune: int, top: int) -> None:
    """Print one tune's differing control runs by cause and the rows behind the most cells."""
    with open(path, "rb") as f:
        lift, _, cells, runs = attribute(f.read(), subtune)
    causes, rows = Counter(), Counter()
    for _, _, ticks, cause, at in runs:
        causes[cause] += ticks
        rows[(cause, "AB"[at[0]] if at else "-", at[1] if at else None)] += ticks
    print(f"{path}: {len(cells)} cells, {len(runs)} control runs, by cause {dict(causes)}")
    print(f"  gap rows (entry, row, nibble pairs): {lift.gaps}")
    if runs:
        print(f"  first run: voice {runs[0][0] + 1} tick {runs[0][1]} {runs[0][3:]}")
    for (cause, layer, row), count in rows.most_common(top):
        print(f"  {cause:6} layer {layer} row {row}: {count} cells")


def main(argv=None) -> int:
    """Report each tune's differing control runs, or with ``--corpus LIST.csv`` write each
    DefMon tune's :func:`record` to ``--out`` and print the tally and sizes."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("sids", nargs="*")
    parser.add_argument("--subtune", type=int, default=0)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--hvsc", default=os.environ.get("HVSC", "C64Music"))
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--out", type=Path, default=shared.ROOT / "out" / "dmlift-corpus.jsonl")
    args = parser.parse_args(argv)
    if args.corpus:
        sizes(corpus(args.corpus, args.hvsc, args.jobs, args.out))
    for path in args.sids:
        report(path, args.subtune, args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
