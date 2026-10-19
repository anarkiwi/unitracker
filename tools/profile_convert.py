"""Time each distinct start of a song's expression conversion, in process.

    python tools/profile_convert.py SONG.sid|HVSC/PATH.sid [--subtune N] [--start I]
        [--terms] [--profile OUT.prof] [--top N] [--stall SECONDS]

The tune is lifted without expressions, its occurrences observed and every distinct start
derived one at a time without caches; each start's seconds, control states and z3 solver
checks print as it completes, slowest last. ``--start`` derives only that start index;
``--terms`` first prints its control states with the firings they hold, phase order, value
recurrence and closed-form candidates with their sizes. ``--stall`` dumps every thread's stack
each time a start runs that many seconds; ``--profile`` writes cProfile stats over the
derivations and prints the top functions.
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
import z3

from unitracker.modfit import convert as C
from unitracker.modfit import grammar as G
from unitracker.modfit import phase as P

CACHE = Path(__file__).resolve().parent.parent / ".tunecache"
KEYS = Path(__file__).resolve().parent.parent / "out" / "modfit" / "profile"


def lifted(source: str, subtune) -> dict:
    """The lifter's song of a tune lifted with its native modulations."""
    path = Path(source)
    if not path.exists():
        from pysidtracker.testing import resolve_tune  # pylint: disable=import-outside-toplevel

        path = resolve_tune(source, cache_dir=CACHE)
    from unitracker.lift import backends  # pylint: disable=import-outside-toplevel

    return backends.lift(path.read_bytes(), subtune, expressions=False)


def starts(source: str, subtune) -> tuple:
    """``(analysis, [start key], {start key: occurrence count})`` of a tune, the song and its
    keys kept under ``out/modfit/profile`` by source, subtune and code identity."""
    name = f"{source}:{subtune}:{C.code_identity()}"
    where = KEYS / f"{hashlib.sha256(name.encode()).hexdigest()[:16]}.pkl"
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
    m, start = an.machines[key[0]], key[1]
    graph = m.explore(start)
    _, raw, edges, lengths = P.minimal(graph, P.Lift(m.form, m.cell, an.freq))
    lo, hi, r, k = (dict(start)[c] for c in P.DOMAIN) if m.chip else m.domain(start)
    values = [G.shortest([x, G.on_domain(x, r, k, lo, hi)]) for x in raw]
    o = P.order(edges, lengths, raw)
    print(f"  {len(graph.nodes)} states of {sum(graph.lengths)} firings, {len(values)} classes")
    if o is not None:
        forms(values, o, m.mask)


def forms(values: list, o, mask: int) -> None:
    """Print a phase order's runs and its value and closed-form candidates."""
    runs = " ".join(f"{c}x{n}" for c, n in o.runs)
    print(f"  order {o.kind}: prefix {o.prefix}, period {o.period}, runs {runs}", flush=True)
    for e in P.value_forms(values, o, mask):
        print(f"  value  ops={sympy.count_ops(e)}: {G.text(e)}")
    for e in P.closed_forms(values, o, mask, None):
        print(f"  closed ops={sympy.count_ops(e)}: {G.text(e) if G.valid(e) else e}")


class Checks:
    """A count of z3 solver checks while installed."""

    def __init__(self):
        self.count, self.check = 0, z3.Solver.check

    def __enter__(self):
        def counted(solver, *args):
            self.count += 1
            return self.check(solver, *args)

        z3.Solver.check = counted
        return self

    def __exit__(self, *exc):
        z3.Solver.check = self.check


def timed(an, key: tuple, prof, stall) -> tuple:
    """``(seconds, control states, z3 checks)`` of one start's derivation."""
    if stall:
        faulthandler.dump_traceback_later(stall, repeat=True, file=sys.stderr)
    t = time.perf_counter()
    if prof:
        prof.enable()
    with Checks() as checks:
        d, _ = an.derived(key)
    if prof:
        prof.disable()
    faulthandler.cancel_dump_traceback_later()
    return time.perf_counter() - t, d.states, checks.count


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
        times.append((*timed(an, keys[i], prof, args.stall), i))
        print(
            f"[{i}] {times[-1][0]:.2f}s, {times[-1][1]} states, {times[-1][2]} checks", flush=True
        )
    for dt, states, n, i in sorted(times)[-10:]:
        print(f"{dt:8.2f}s  [{i}] {keys[i][0]}: {states} states, {n} checks")
    print(
        f"total {sum(t[0] for t in times):.1f}s, {sum(t[2] for t in times)} checks over "
        f"{len(times)} starts; most states {max((t[1] for t in times), default=0)}, "
        f"most checks {max((t[2] for t in times), default=0)}"
    )
    if prof:
        prof.dump_stats(args.profile)
        for order in ("cumulative", "tottime"):
            pstats.Stats(prof).sort_stats(order).print_stats(args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
