"""The optimiser's lossy mode over documents: tokens exact-only against lossy, what each code
modulator became, the cells' errors, and optionally WAVs of each document before and after.

    python tools/lossy_eval.py DOC.yaml ... [--ticks N] [--wav DIR [--seconds S]]
        [--freq-tol P95,MAX] [--pw-tol P95,MAX] [--cut-tol P95,MAX] [--jobs J]
"""

import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path

from tqdm import tqdm
from unitracker_grammar import PAL_HZ
from unitracker_grammar.document import read

from unitracker.optimiser import lossy, optimise
from unitracker.optimiser.guarantee import grid

CYCLES = 19656


def wav(text: str, ticks: int, clock: tuple, dst: Path) -> Path:
    """``text``'s register grid over ``ticks`` rendered on a 6581 at ``clock`` (cycles per
    tick, clock Hz) to ``dst``."""
    from pysidtracker.audio import render_wav  # pylint: disable=import-outside-toplevel

    frames, _ = grid(text, ticks)
    writes = ([(r, int(x)) for r, x in enumerate(row)] for row in frames)
    return render_wav(
        writes, dst, model="6581", cycles_per_frame=clock[0], clock_frequency=clock[1] or PAL_HZ
    )


def evaluate(path: Path, ticks: int, tolerances: dict, wavs: Path | None, seconds: float):
    """``(name, row, lines)``: a document's sizes and report, its WAVs written to ``wavs``."""
    text = path.read_text(encoding="utf-8")
    exact = optimise(text, ticks)
    both = optimise(text, ticks, lossy=tolerances)
    simplified = both.lossy
    replaced = "; ".join(
        f"{r.name}->{'/'.join(sorted({c.name for c in r.calls.values()}))}" for r in simplified.kept
    )
    errors = " ".join(
        f"{c} {s[0]:.1f}/{s[1]:.1f}/{s[2]:.1f}" for c, s in sorted(simplified.stats.items())
    )
    row = (
        f"| {path.stem} | {exact.before} | {exact.after} | {simplified.after} | {both.after} "
        f"| {len(simplified.kept)} ({replaced or '-'}) | {len(simplified.refused)} "
        f"| {errors or '-'} |"
    )
    if wavs is not None:
        clock = read(text).header.clock or (CYCLES, PAL_HZ)
        frames = int(seconds * (clock[1] or PAL_HZ) / clock[0])
        wav(text, frames, clock, wavs / f"{path.stem}.before.wav")
        wav(both.text, frames, clock, wavs / f"{path.stem}.lossy.wav")
    return path.stem, row, simplified.report()


def main(argv=None) -> int:
    """Print the table of the documents of ``argv``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("documents", type=Path, nargs="+")
    parser.add_argument("--ticks", type=int, default=15000)
    parser.add_argument("--wav", type=Path, help="write each document's WAVs here")
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--jobs", type=int, default=os.cpu_count() // 2 or 1)
    lossy.add_arguments(parser)
    args = parser.parse_args(argv)
    args.lossy = True
    if args.wav:
        args.wav.mkdir(parents=True, exist_ok=True)
    run = partial(evaluate, ticks=args.ticks, tolerances=lossy.tolerances_of(args))
    run = partial(run, wavs=args.wav, seconds=args.seconds)
    print(
        "| tune | tokens | exact | lossy | lossy+exact | replaced | refused | error p50/p95/max |"
    )
    print("|---|---|---|---|---|---|---|---|")
    with ProcessPoolExecutor(args.jobs) as pool:
        results = list(tqdm(pool.map(run, args.documents), total=len(args.documents), unit="doc"))
    for _, row, _ in results:
        print(row)
    for name, _, lines in results:
        print(f"\n{name}:", *lines, sep="\n  ", file=sys.stderr)
    return 0


if __name__ == "__main__":
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ.setdefault(var, "2")
    sys.exit(main())
