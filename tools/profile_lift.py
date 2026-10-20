"""Time a tune's lift per stage, with cProfile over the whole run.

    python tools/profile_lift.py TUNE|HVSC/PATH.sid [--subtune N] [--no-expressions] [--top N]

Each stage's seconds print in order, then the top functions by cumulative and self time.
"""

import argparse
import cProfile
import pstats
import sys
import time
from pathlib import Path

from unitracker.lift import backends

CACHE = Path(__file__).resolve().parent.parent / ".tunecache"


def tune_path(source: str) -> Path:
    """The tune's local path, resolving an HVSC name through ``pysidtracker.testing``."""
    path = Path(source)
    if not path.exists():
        from pysidtracker.testing import resolve_tune  # pylint: disable=import-outside-toplevel

        path = resolve_tune(source, cache_dir=CACHE)
    return path


def main(argv=None) -> int:
    """Run the tool."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("source")
    ap.add_argument("--subtune", type=int)
    ap.add_argument("--no-expressions", action="store_true")
    ap.add_argument("--profile")
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args(argv)
    lifter = backends.lifter(
        tune_path(args.source).read_bytes(), args.subtune, not args.no_expressions
    )
    prof = cProfile.Profile()
    prof.enable()
    stage_times = []
    t0 = time.perf_counter()
    for name, song in lifter.stages():
        stage_times.append((name, time.perf_counter() - t0, song))
        t0 = time.perf_counter()
    for name, dt, _ in stage_times:
        print(f"{dt:9.2f}s  {name}")
    _, _, song = stage_times[-1]
    rows = sum(len(r) for r in song["records"].values())
    print(
        f"{sum(d for _, d, _ in stage_times):9.2f}s  total: "
        f"over {rows} rows in {len(song['tracks'])} tracks"
    )
    for order in ("cumulative", "tottime"):
        pstats.Stats(prof).sort_stats(order).print_stats(args.top)
    if args.profile:
        prof.dump_stats(args.profile)
        print(f"wrote {args.profile}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
