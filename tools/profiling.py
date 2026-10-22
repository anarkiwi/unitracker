"""Time and profile a tune's lift per stage, its conversion per start, or the optimiser.

``lift`` prints each stage's seconds; ``convert`` derives each distinct start uncached, with its
seconds, control states and z3 checks; ``optimise`` runs passes, traces, or refuted-candidate
divergences. ``--profile`` keeps cProfile stats and prints the top functions.
"""

import argparse
import cProfile
import faulthandler
import hashlib
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import sympy
import z3
from tqdm import tqdm

import shared
from unitracker import cache, optimise
from unitracker.lift import backends
from unitracker.modfit import convert as C
from unitracker.modfit import grammar as G
from unitracker.modfit import phase as P
from unitracker.optimise import driver, proof
from unitracker.player.vm import SongPlayer

KEYS = shared.ROOT / "out" / "modfit" / "profile"

# ---- lift --------------------------------------------------------------------------------


def lift(args) -> int:
    """Each lift stage's seconds, profiled."""
    lifter = backends.lifter(
        shared.tune(args.source).read_bytes(), args.subtune, not args.no_expressions
    )
    prof = cProfile.Profile()
    prof.enable()
    stage_times, t0 = [], time.perf_counter()
    for name, song in lifter.stages():
        stage_times.append((name, time.perf_counter() - t0, song))
        t0 = time.perf_counter()
    prof.disable()
    for name, dt, _ in stage_times:
        print(f"{dt:9.2f}s  {name}")
    song = stage_times[-1][2]
    rows = sum(len(r) for r in song["records"].values())
    print(
        f"{sum(d for _, d, _ in stage_times):9.2f}s  total: "
        f"over {rows} rows in {len(song['tracks'])} tracks"
    )
    shared.profile_report(prof, args.profile, args.top)
    return 0


# ---- convert -----------------------------------------------------------------------------


def starts(source: str, subtune) -> tuple:
    """``(analysis, [start key], {start key: occurrence count})`` of a tune, the song and its
    keys kept under ``out/modfit/profile`` by source, subtune and code identity."""
    key = f"{source}:{subtune}:{cache.code_identity()}"
    where = KEYS / f"{hashlib.sha256(key.encode()).hexdigest()[:16]}.pkl"
    if where.exists():
        ir, counts = pickle.loads(where.read_bytes())
    else:
        ir = C.expand_repeats(shared.lift(source, subtune, expressions=False))
        obs = C.observe(ir, None)
        an = C.Analysis(ir)
        counts = {}
        for occ in tqdm(obs.occurrences, desc="start keys", unit="occ"):
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
    if o is None:
        return
    print(f"  order {o.kind}: prefix {o.prefix}, period {o.period}, runs "
          + " ".join(f"{c}x{n}" for c, n in o.runs), flush=True)  # fmt: skip
    for e in P.value_forms(values, o, m.mask):
        print(f"  value  ops={sympy.count_ops(e)}: {G.text(e)}")
    for e in P.closed_forms(values, o, m.mask, None):
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


def convert(args) -> int:
    """Each distinct start's derivation timed, slowest last."""
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
        shared.profile_report(prof, args.profile, args.top)
    return 0


# ---- optimise ----------------------------------------------------------------------------


def candidates(opt, name: str):
    """``(edit, written)`` of each written candidate of pass ``name`` that changes the song's
    text."""
    # pylint: disable=protected-access
    for edit in tqdm(optimise.PASSES[name](opt.base), desc="candidates", unit="edit"):
        written = driver._written(driver._apply(opt.base, edit), opt.state["native"])
        if written is not None and written[0] != opt.text:
            yield edit, written


def divergence(ir: dict, name: str) -> int:
    """Print, for each refuted candidate of a pass, the tick its playback state first differs
    from the song's and the tick its grid first differs."""
    opt = driver.Optimiser(ir, workers=1)
    base, states = SongPlayer(opt.base), []
    print("edit first_state_change failure")
    for edit, written in candidates(opt, name):
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
    return 0


def check_baseline(ir: dict, name: str) -> int:
    """Prove each written candidate of a pass from the start and resumed from the song's
    baseline; print the seconds each took and return 1 when any result differs."""
    opt = driver.Optimiser(ir, workers=1)
    played, failed = [0.0, 0.0], 0
    for edit, written in candidates(opt, name):
        results = []
        for k, ref in enumerate((optimise.Trace(ir), opt.ref)):
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
    # pylint: disable=protected-access
    log = []
    screen, trace, screen_one = driver.Optimiser.screen, proof.Trace.__init__, driver._screen

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


def optimise_(args) -> int:
    """The optimiser on a tune's lift: timed, profiled, or a pass's candidates examined."""
    ir = shared.lift(args.source)
    if args.cut:
        for track in ir["tracks"]:
            track["plays"] = track["plays"][: args.cut]
            track["loop"] = 0
    if args.check_baseline:
        return check_baseline(ir, args.check_baseline)
    if args.divergence:
        return divergence(ir, args.divergence)
    log = instrument() if args.stats else None
    prof = cProfile.Profile() if args.profile else None
    start = time.perf_counter()
    if prof:
        prof.enable()
    passes = args.passes.split(",") if args.passes else None
    out = optimise.Trace(ir) if args.trace else optimise.optimise(ir, passes, args.workers)
    if prof:
        prof.disable()
    elapsed = time.perf_counter() - start
    if args.trace:
        np.save(args.trace, np.asarray(out.rows, dtype=np.uint8))
        print(f"{elapsed:.2f}s {len(out.rows)} ticks, {len(out.rows) / elapsed:.0f} ticks/s")
    else:
        if args.output:
            args.output.write_text(out.text, encoding="utf-8")
        kept = sum(f.kept for f in out.findings)
        print(f"{elapsed:.2f}s tokens {out.before} -> {out.after}, "
              f"{len(out.findings)} proved, {kept} kept")  # fmt: skip
    if log is not None:
        summarise(log)
    if prof:
        shared.profile_report(prof, args.profile, args.top)
    return 0


def main(argv=None) -> int:
    """Parse the command line and run the command."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("source", metavar="TUNE.sid")
    common.add_argument("--profile", help="write cProfile stats here and print the top functions")
    common.add_argument("--top", type=int, default=30)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("lift", help="each lift stage's seconds", parents=[common])
    p.add_argument("--subtune", type=int)
    p.add_argument("--no-expressions", action="store_true")
    p = sub.add_parser(
        "convert", help="each distinct start's derivation, uncached", parents=[common]
    )
    p.add_argument("--subtune", type=int)
    p.add_argument("--start", type=int, help="only this start index")
    p.add_argument("--terms", action="store_true", help="each start's states and candidates")
    p.add_argument("--stall", type=float, help="dump the stacks every this many seconds")
    p = sub.add_parser("optimise", help="the optimiser's passes on a tune's lift", parents=[common])
    p.add_argument("--passes")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--output", type=Path)
    p.add_argument("--cut", type=int, help="keep each track's first CUT plays, looping")
    p.add_argument("--trace", help="only trace the song to recurrence; save its grid here")
    p.add_argument("--divergence", metavar="PASS", help="refuted candidates' first divergence")
    p.add_argument("--check-baseline", metavar="PASS", help="prove from the start and resumed")
    p.add_argument("--stats", action="store_true", help="count screens, traces and ticks")
    args = ap.parse_args(argv)
    return {"lift": lift, "convert": convert, "optimise": optimise_}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
