"""Lift many tunes in parallel and tally the results by message, numbers masked.

    python tools/lift_tally.py TUNES.csv --root $HVSC [--player P] [--songs 1] [--expressions]
        [--check TICKS [--registers 2,3]] [--only REGEX] [--out results.csv]

``TUNES.csv`` is a survey tune list or an earlier ``--out`` (``--only`` keeps matching results).
"""

import argparse
import csv
import os
import re
import signal
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import get_context
from pathlib import Path

for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "UNITRACKER_WORKERS"):
    os.environ.setdefault(_var, "1")

# pylint: disable=wrong-import-position
import numpy as np
from tqdm import tqdm

from unitracker.lift import backends
from unitracker.player.vm import SongPlayer, first_mismatch


def native_grid(raw: bytes, ticks: int) -> np.ndarray:
    """The native player's register grid over ``ticks`` ticks."""
    player = backends.native_player(raw)
    grid = np.empty((ticks, 25), dtype=np.uint8)
    for i in range(ticks):
        player.play_frame()
        grid[i] = player.regs
    return grid


def _timeout(*_):
    raise TimeoutError("timed out")


def outcome(path: str, args) -> tuple[str, str]:
    """``(path, result)``: ``lifted``, ``match``, ``mismatch tick T reg R`` or the error; the
    check compares only ``args.registers`` when given."""
    signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(args.timeout)
    try:
        raw = Path(path).read_bytes()
        song = backends.lift(raw, expressions=args.expressions)
        if not args.check:
            return path, "lifted"
        player, theirs = SongPlayer(song), native_grid(raw, args.check)
        ours = player.render_grid(args.check)
        if args.registers:
            other = np.ones(25, dtype=bool)
            other[[int(r, 0) for r in args.registers.split(",")]] = False
            ours[:, other] = theirs[:, other]
        bad = first_mismatch(ours, theirs, player.starts)
        return path, "match" if bad is None else f"mismatch tick {bad[0]} reg ${bad[1]:02X}"
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return path, f"{type(exc).__name__}: {exc}"
    finally:
        signal.alarm(0)


def cause(result: str) -> str:
    """A result with its numbers masked."""
    return re.sub(r"\$[0-9A-Fa-f]+|\b\d+\b", "#", result)


def tune_paths(tunes: Path, root: Path, args) -> list[str]:
    """The tune paths of a survey list or results file, filtered by player, songs and result."""
    with tunes.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    return [
        str(root / row["path"])
        for row in rows
        if args.player in row.get("player", args.player)
        and (args.songs is None or int(row.get("songs") or args.songs) == args.songs)
        and re.search(args.only, row.get("result", ""))
    ]


def main(argv=None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tunes", type=Path)
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("HVSC", ".")))
    parser.add_argument("--player", default="")
    parser.add_argument("--songs", type=int)
    parser.add_argument("--only", default="")
    parser.add_argument("--expressions", action="store_true")
    parser.add_argument("--check", type=int, default=0, metavar="TICKS")
    parser.add_argument("--registers", default="")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--workers", type=int, default=os.cpu_count())
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    paths = tune_paths(args.tunes, args.root, args)
    results = {}
    with ProcessPoolExecutor(args.workers, mp_context=get_context("spawn")) as pool:
        futures = [pool.submit(outcome, p, args) for p in paths]
        for future in tqdm(as_completed(futures), total=len(futures), unit="tune"):
            path, result = future.result()
            results[path] = result
    if args.out:
        with args.out.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["path", "result"])
            writer.writerows(sorted(results.items()))
    for result, count in Counter(map(cause, results.values())).most_common():
        print(f"{count:5d}  {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
