"""Time each distinct start of a song's expression conversion, in process.

    python tools/profile_convert.py SONG.sid|HVSC/PATH.sid [--subtune N] [--start I]
        [--terms] [--profile OUT.prof] [--top N] [--stall SECONDS]

The tune is lifted without expressions, its occurrences observed and every distinct start
derived one at a time without caches; each start's seconds, unit and key print as it
completes, slowest last. ``--start`` derives only that start index; ``--terms`` first prints
its control states, phase order, value recurrence and closed-form candidates with their sizes.
``--stall`` dumps every
thread's stack each time a start runs that many seconds; ``--profile`` writes cProfile stats
over the derivations and prints the top functions.
"""

import argparse
import cProfile
import faulthandler
import hashlib
import pickle
import pstats
import sys
import time
from pathlib import Path

from tqdm import tqdm

import sympy

from unitracker.modfit import convert as C
from unitracker.modfit import grammar as G
from unitracker.modfit import phase as P

CACHE = Path(__file__).resolve().parent.parent / ".tunecache"
KEYS = Path(__file__).resolve().parent.parent / "out" / "modfit" / "profile"


def lifted(source: str, subtune) -> dict:
    """The internal song of a tune lifted with its native modulations."""
    path = Path(source)
    if not path.exists():
        from pysidtracker.testing import resolve_tune  # pylint: disable=import-outside-toplevel

        path = resolve_tune(source, cache_dir=CACHE)
    from unitracker.lift import backends  # pylint: disable=import-outside-toplevel

    return backends.lift(path.read_bytes(), subtune, expressions=False)


def starts(source: str, subtune) -> tuple:
    """``(analysis, [start key], {start key: occurrence count})`` of a tune, the song and its
    keys kept under ``out/modfit/profile`` by source and subtune."""
    where = KEYS / f"{hashlib.sha256(f'{source}:{subtune}'.encode()).hexdigest()[:16]}.pkl"
    if where.exists():
        ir, counts = pickle.loads(where.read_bytes())
    else:
        ir = C.expand_repeats(lifted(source, subtune))
        obs = C.observe(ir, C.window(ir), None)
        an = C.Analysis(ir)
        counts = {}
        for occ in tqdm(obs["occurrences"], desc="start keys", unit="occ"):
            k = an.key(an.unit(occ.unit), occ)
            if k is not None:
                counts[k] = counts.get(k, 0) + 1
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_bytes(pickle.dumps((ir, counts)))
    return C.Analysis(ir), list(counts), counts


def terms(an, key: tuple) -> None:
    """Print a start's graph size, phase order, value recurrence and closed-form candidates."""
    name, start, _ = key
    m = an.machines[name]
    graph = m.explore(start)
    _, values, edges = P.minimal(graph, P.Lift(m.form, m.cell, an.freq))
    lo, hi, r, k = (dict(start)[c] for c in P.DOMAIN) if m.chip else m.domain(start)
    values = [G.shortest([x, G.on_domain(x, r, k, lo, hi)]) for x in values]
    o = P.order(edges)
    print(f"  {len(graph.nodes)} states, {len(values)} classes", flush=True)
    if o is None:
        return
    print(f"  order {o.kind}: prefix {o.prefix}, period {o.period}")
    for e in P.value_forms(values, o, m.mask):
        print(f"  value  ops={sympy.count_ops(e)}: {G.text(e)}")
    for e in P.closed_forms(values, o, m.mask, None):
        print(f"  closed ops={sympy.count_ops(e)}: {G.text(e)}")


def main(argv=None) -> int:
    """Run the tool."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("source")
    ap.add_argument("--subtune", type=int)
    ap.add_argument("--start", type=int)
    ap.add_argument("--terms", action="store_true")
    ap.add_argument("--profile")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--stall", type=float)
    args = ap.parse_args(argv)
    t0 = time.perf_counter()
    an, keys, counts = starts(args.source, args.subtune)
    print(f"observed {len(keys)} starts in {time.perf_counter() - t0:.1f}s", flush=True)
    chosen = [args.start] if args.start is not None else range(len(keys))
    prof = cProfile.Profile() if args.profile else None
    times = []
    for i in tqdm(chosen, desc="derivations", unit="start"):
        print(f"[{i}] {keys[i][0]} x{counts[keys[i]]}: {keys[i][1]!r} v0={keys[i][2]!r}")
        if args.terms:
            terms(an, keys[i])
        if args.stall:
            faulthandler.dump_traceback_later(args.stall, repeat=True, file=sys.stderr)
        t = time.perf_counter()
        if prof:
            prof.enable()
        an.derived(keys[i])
        if prof:
            prof.disable()
        faulthandler.cancel_dump_traceback_later()
        times.append((time.perf_counter() - t, i))
        print(f"[{i}] {times[-1][0]:.2f}s", flush=True)
    for dt, i in sorted(times)[-10:]:
        print(f"{dt:8.2f}s  [{i}] {keys[i][0]}")
    print(f"total {sum(dt for dt, _ in times):.1f}s over {len(times)} starts")
    if prof:
        prof.dump_stats(args.profile)
        for order in ("cumulative", "tottime"):
            pstats.Stats(prof).sort_stats(order).print_stats(args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
