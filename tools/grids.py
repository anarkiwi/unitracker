"""Register grids of documents, saved and compared: proves two builds (e.g. a format change's
before and after, each in its own environment) play the same.

    python tools/grids.py play DIR --out GRIDS.npz [--ticks N] [--jobs J]
    python tools/grids.py compare A.npz B.npz
"""

import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path

import numpy as np
from tqdm import tqdm
from unitracker_player.player import play


def grid(path: Path, ticks: int) -> np.ndarray | str:
    """``path``'s registers over ``ticks`` frames, or the error that refused it."""
    try:
        return play(path.read_text(encoding="utf-8"), ticks)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return f"{type(exc).__name__}: {exc}"


def played(root: Path, out: Path, ticks: int, jobs: int) -> int:
    """Saves the grid of every ``.yaml`` below ``root``, keyed by its path relative to it."""
    paths = sorted(root.rglob("*.yaml"))
    with ProcessPoolExecutor(jobs) as pool:
        grids = list(tqdm(pool.map(partial(grid, ticks=ticks), paths), total=len(paths)))
    keys = [str(p.relative_to(root)) for p in paths]
    np.savez_compressed(out, **{k: np.asarray(g) for k, g in zip(keys, grids)})
    print(f"{sum(not isinstance(g, str) for g in grids)}/{len(paths)} played")
    return 0


def compared(a: Path, b: Path) -> int:
    """Prints each key whose grids differ or that one side lacks; 1 when any does."""
    left, right = np.load(a), np.load(b)
    bad = 0
    for key in sorted(set(left.files) | set(right.files)):
        if key not in left.files or key not in right.files:
            print(f"{key}: only in {'A' if key in left.files else 'B'}")
        elif not np.array_equal(left[key], right[key]):
            x, y = left[key], right[key]
            tick = next(
                (t for t in range(min(len(x), len(y))) if not np.array_equal(x[t], y[t])), None
            )
            print(f"{key}: differs ({x.shape} vs {y.shape}, first tick {tick})")
        else:
            continue
        bad += 1
    print(f"{len(set(left.files) | set(right.files)) - bad} same, {bad} differ")
    return int(bad > 0)


def main(argv=None) -> int:
    """Run the command."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("play", help="save every document's grid")
    p.add_argument("root", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--ticks", type=int, default=3000)
    p.add_argument("--jobs", type=int, default=max(1, len(os.sched_getaffinity(0)) - 2))
    p = sub.add_parser("compare", help="report the documents whose grids differ")
    p.add_argument("a", type=Path)
    p.add_argument("b", type=Path)
    args = parser.parse_args(argv)
    if args.command == "play":
        return played(args.root, args.out, args.ticks, args.jobs)
    return compared(args.a, args.b)


if __name__ == "__main__":
    sys.exit(main())
