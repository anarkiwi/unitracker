"""Transliterate a seeded sample of SID-Wizard tunes and verify each over its song length: the
verdict, the first differing cell, the gaps the lift recorded, its invariants and its sizes.

``python tools/swsample.py LIST.csv [--hvsc DIR] [--count N] [--seed S] [--jobs J] [--out F.csv]``
"""

import argparse
import csv
import math
import os
import random
import signal
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from pysidwizard import parse_sid
from pysidwizard.writer import build_swm
from tqdm import tqdm

from unitracker.direct import invariants, swlift
from unitracker.direct.swtrace import CLOCK
from unitracker.optimiser.measure import size

FIELDS = (
    *("path", "verdict", "frames", "cells", "first", "bytes", "tokens", "native", "gaps"),
    *("exceeded", "error"),
)
PLAYER = "SidWizard"


def _expired(*_):
    raise TimeoutError("over the time limit")


def frames_of(seconds: str, cap: int) -> int:
    """The ticks of a song length in seconds at the PAL frame rate, at most ``cap``."""
    first = float((seconds or "0").split()[0])
    return min(cap, math.ceil(first * CLOCK[1] / CLOCK[0])) or cap


def native(raw: bytes) -> int | str:
    """The module's song data bytes as pysidwizard writes them, empty where it refuses."""
    try:
        return len(build_swm(parse_sid(raw)))
    except ValueError:
        return ""


def check(job: tuple) -> dict:
    """One tune's row."""
    path, frames, limit = job
    out = dict.fromkeys(FIELDS, "") | {"path": str(path), "frames": frames}
    signal.signal(signal.SIGALRM, _expired)
    signal.alarm(limit)
    try:
        raw = path.read_bytes()
        lift = swlift.lifter(raw)
        text = lift.document()
        cells, _, _ = swlift.verify(raw, 0, frames)
        out |= {
            "verdict": "differing" if cells.size else "exact",
            "cells": len(cells),
            "first": f"{int(cells[0][0])} {int(cells[0][1])}" if cells.size else "",
            "bytes": len(text),
            "tokens": size(text),
            "native": native(raw),
            "gaps": ";".join(f"{k}:{len(v)}" for k, v in sorted(lift.gaps.seen.items())),
            "exceeded": invariants.exceeded(invariants.lifted(text), swlift.native_counts(raw))
            or "",
        }
    except Exception as exc:  # pylint: disable=broad-exception-caught
        out |= {"verdict": "failed", "error": f"{type(exc).__name__}: {exc}".splitlines()[0]}
    finally:
        signal.alarm(0)
    return out


def summary(rows: list) -> str:
    """Verdict counts, the gaps' tally and the size distribution of the lifted tunes."""
    counts = {k: sum(r["verdict"] == k for r in rows) for k in ("exact", "differing", "failed")}
    lines = [" ".join(f"{k} {v}" for k, v in counts.items())]
    sized = [r for r in rows if r["bytes"] != ""]
    if sized:
        for key in ("bytes", "tokens"):
            ratio = np.array([r[key] / r["native"] for r in sized if r["native"] != ""])
            q = np.quantile(ratio, (0.1, 0.5, 0.9, 1.0))
            marks = " ".join(f"{k} {v:.2f}" for k, v in zip(("p10", "p50", "p90", "max"), q))
            lines.append(f"{key} per native byte: {marks}")
    tally = {}
    for row in rows:
        for gap in filter(None, row["gaps"].split(";")):
            kind = gap.rsplit(":", 1)[0]
            tally[kind] = tally.get(kind, 0) + 1
    lines += [f"  {n:4d} tunes: {k}" for k, n in sorted(tally.items(), key=lambda kv: -kv[1])]
    errors = {}
    for row in rows:
        if row["error"]:
            errors[row["error"][:80]] = errors.get(row["error"][:80], 0) + 1
    lines += [f"  {n:4d} failed: {k}" for k, n in sorted(errors.items(), key=lambda kv: -kv[1])]
    return "\n".join(lines)


def main(argv=None) -> int:
    """Sample, check and summarise."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("list", type=Path)
    parser.add_argument("--hvsc", type=Path, default=Path(os.environ.get("HVSC", ".")))
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--cap", type=int, default=60000, help="most ticks verified")
    parser.add_argument("--timeout", type=int, default=1800, help="seconds per tune")
    parser.add_argument("--out", type=Path, default=Path("out/sw-lift/sample.csv"))
    args = parser.parse_args(argv)
    with args.list.open() as f:
        tunes = [r for r in csv.DictReader(f) if PLAYER in r["player"]]
    tunes = random.Random(args.seed).sample(tunes, min(args.count, len(tunes)))
    jobs = [(args.hvsc / r["path"], frames_of(r["lengths"], args.cap), args.timeout) for r in tunes]
    rows = []
    with ProcessPoolExecutor(args.jobs) as pool:
        futures = [pool.submit(check, job) for job in jobs]
        progress = tqdm(as_completed(futures), total=len(jobs), unit="sid")
        for done in progress:
            rows.append(done.result())
            progress.set_postfix(exact=sum(r["verdict"] == "exact" for r in rows))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as f:
        writer = csv.DictWriter(f, FIELDS)
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: r["path"]))
    print(summary(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
