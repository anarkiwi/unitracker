"""Time or profile the optimiser on an HVSC tune's lift.

    python tools/profile_optimise.py TUNE.sid --passes instruments,programs
        [--workers N] [--cut N] [--stats] [--output OUT.yaml] [--profile OUT.prof] [--top N]

A ``.sid`` argument that is not a local file is resolved through the test tune cache. The
optimised text goes to ``--output``; ``--profile`` writes cProfile stats and prints the top
functions by cumulative and by own time.
"""

import argparse
import cProfile
import pstats
import sys
import time
from pathlib import Path

import numpy as np
from tqdm import tqdm

from unitracker import optimise

CACHE = Path(__file__).resolve().parent.parent / ".tunecache"


def tune(source: str) -> Path:
    """The ``.sid`` file ``source``, or the HVSC-relative tune it names through the tune cache."""
    path = Path(source)
    if path.suffix != ".sid":
        raise ValueError(f"{source}: not a .sid tune")
    if path.exists():
        return path
    from pysidtracker.testing import resolve_tune  # pylint: disable=import-outside-toplevel

    return resolve_tune(source, cache_dir=CACHE)


def load(source: str) -> dict:
    """The lift of tune ``source``."""
    from unitracker.lift import backends  # pylint: disable=import-outside-toplevel

    return backends.lift(tune(source).read_bytes())


def divergence(ir: dict, name: str) -> None:
    """Print, for each written candidate of a pass that fails, the tick its playback state first
    differs from the song's and the tick its grid first differs."""
    # pylint: disable=import-outside-toplevel,protected-access
    from unitracker.optimise import driver
    from unitracker.player.vm import SongPlayer

    opt = driver.Optimiser(ir, workers=1)
    base = SongPlayer(opt.base)
    states = []
    print("edit first_state_change failure")
    for edit in tqdm(optimise.PASSES[name](opt.base), desc="candidates", unit="edit"):
        written = driver._written(driver._apply(opt.base, edit), opt.state["native"])
        if written is None or written[0] == opt.text:
            continue
        bad = optimise.mismatch(opt.ref, written[2])
        if bad is None or not isinstance(bad[0], int):
            continue
        cand = SongPlayer(written[2])
        for tick in range(bad[0] + 1):
            while len(states) <= tick:
                base.play_frame()
                states.append(base.state())
            cand.play_frame()
            if cand.state() != states[tick]:
                break
        print(f"{edit} {tick} {bad[0]}")


def check_baseline(ir: dict, name: str) -> int:
    """Prove each written candidate of a pass from the start and resumed from the song's
    baseline; print the seconds each took and return 1 when any result differs."""
    # pylint: disable=import-outside-toplevel,protected-access
    from unitracker.optimise import driver

    opt = driver.Optimiser(ir, workers=1)
    refs = (optimise.Trace(ir), opt.ref)
    played, failed = [0.0, 0.0], 0
    for edit in tqdm(optimise.PASSES[name](opt.base), desc="candidates", unit="edit"):
        written = driver._written(driver._apply(opt.base, edit), opt.state["native"])
        if written is None or written[0] == opt.text:
            continue
        results = []
        for k, ref in enumerate(refs):
            start = time.perf_counter()
            results.append(optimise.mismatch(ref, written[2]))
            played[k] += time.perf_counter() - start
        if results[0] != results[1]:
            failed += 1
            print(f"{edit}: {results[0]} from the start, {results[1]} resumed")
    print(f"{failed} differ; {played[0]:.1f}s from the start, {played[1]:.1f}s resumed")
    return int(failed > 0)


def instrument() -> list:
    """Record every screen and every proof trace of an in-process run, in order."""
    from unitracker.optimise import driver, proof  # pylint: disable=import-outside-toplevel

    log = []
    screen, trace = driver.Optimiser.screen, proof.Trace.__init__

    def screened(self, name):
        log.append(("screen", name, self.tokens))
        return screen(self, name)

    def traced(self, ir, *args, **kwargs):
        start = time.perf_counter()
        try:
            trace(self, ir, *args, **kwargs)
        finally:
            rows = len(getattr(self, "rows", ()))
            bad = getattr(self, "bad", None)
            log.append(("trace", rows, bad is None, time.perf_counter() - start))

    def each(job):
        start = time.perf_counter()
        out = screen_one(job)
        log.append(("candidate", out[1], out[2], time.perf_counter() - start))
        return out

    screen_one = driver._screen  # pylint: disable=protected-access
    driver.Optimiser.screen, proof.Trace.__init__, driver._screen = screened, traced, each
    return log


def summarise(log: list) -> None:
    """Per screen: candidate traces, those run to recurrence, ticks and seconds."""
    rows, kinds = [], {}
    for entry in log:
        if entry[0] == "screen":
            rows.append([f"{entry[1]}@{entry[2]}", 0, 0, 0, 0.0])
        elif entry[0] == "candidate":
            kind = "unwritten" if entry[1] is None else ("proved" if entry[2] else "refuted")
            kind += "" if entry[1] is None else (" shrinking" if entry[1] < 0 else " not shrinking")
            count, seconds = kinds.get(kind, (0, 0.0))
            kinds[kind] = (count + 1, seconds + entry[3])
        elif rows:
            rows[-1][1] += 1
            rows[-1][2] += entry[2]
            rows[-1][3] += entry[1]
            rows[-1][4] += entry[3]
    print("screen traces full ticks seconds")
    for row in rows:
        print(f"{row[0]} {row[1]} {row[2]} {row[3]} {row[4]:.1f}")
    print("candidates count seconds")
    for kind, (count, seconds) in sorted(kinds.items()):
        print(f"{kind}: {count} {seconds:.1f}")


def main(argv=None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("song", metavar="TUNE.sid")
    parser.add_argument("--passes", default=None)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--cut", type=int, help="keep each track's first CUT plays, looping")
    parser.add_argument(
        "--trace", type=Path, help="only trace the song to recurrence; save its grid to TRACE"
    )
    parser.add_argument(
        "--divergence",
        metavar="PASS",
        help="for each refuted candidate of PASS on the song, its first state change and failure",
    )
    parser.add_argument(
        "--check-baseline",
        metavar="PASS",
        help="prove each candidate of PASS from the start and from the baseline; compare",
    )
    parser.add_argument("--stats", action="store_true", help="count screens, traces and ticks")
    args = parser.parse_args(argv)
    ir = load(args.song)
    if args.cut:
        for track in ir["tracks"]:
            track["plays"] = track["plays"][: args.cut]
            track["loop"] = 0
    passes = args.passes.split(",") if args.passes else None
    profiler = cProfile.Profile() if args.profile else None
    log = instrument() if args.stats else None
    if args.check_baseline:
        return check_baseline(ir, args.check_baseline)
    if args.divergence:
        divergence(ir, args.divergence)
        return 0
    start = time.perf_counter()
    if profiler:
        profiler.enable()
    out = optimise.Trace(ir) if args.trace else optimise.optimise(ir, passes, args.workers)
    if profiler:
        profiler.disable()
        profiler.dump_stats(args.profile)
    elapsed = time.perf_counter() - start
    if args.trace:
        np.save(args.trace, np.asarray(out.rows, dtype=np.uint8))
        print(f"{elapsed:.2f}s {len(out.rows)} ticks, {len(out.rows) / elapsed:.0f} ticks/s")
    else:
        if args.output:
            args.output.write_text(out.text, encoding="utf-8")
        kept = sum(f.kept for f in out.findings)
        found = len(out.findings)
        print(f"{elapsed:.2f}s tokens {out.before} -> {out.after}, {found} proved, {kept} kept")
    if log is not None:
        summarise(log)
    if profiler:
        stats = pstats.Stats(str(args.profile), stream=sys.stdout)
        stats.sort_stats("cumulative").print_stats(args.top)
        stats.sort_stats("tottime").print_stats(args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
