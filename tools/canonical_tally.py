"""Run the canonical passes over documents or lifted tunes in parallel and tally them per pass.

    python tools/canonical_tally.py SONG.yaml|TUNE.sid [...] [--jobs J] [--workers N] [--out DIR]

Per pass: candidates found, refused (no written form, or the codec's reading refuses it) and
proved; tokens before and after (``tools/token_report.py``'s count). ``--out`` writes each result.
"""

import argparse
import os
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import get_context
from pathlib import Path

for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_var, "2")

# pylint: disable=wrong-import-position
from tqdm import tqdm

from unitracker import optimise
from unitracker.format import songio
from unitracker.lift import backends
from unitracker.lift.goattracker import GoatTrackerLift
from unitracker.lift.sidwizard import SidWizardLift


def converted(raw: bytes) -> dict:
    """A tune's lift up to its ``convert`` pass, before ``canonical``."""
    lifts = {"goattracker2": GoatTrackerLift, "sidwizard": SidWizardLift}
    lift = lifts.get(backends.detect(raw))
    if lift is None:
        return backends.lift(raw)
    return next(song for name, song in lift.from_sid(raw).stages() if name == "convert")


def tally(path: str, out: Path | None, workers: int) -> tuple:
    """``(path, per-pass tally, tokens before, after, error)`` of one document or tune."""
    try:
        song = converted(Path(path).read_bytes()) if path.endswith(".sid") else songio.load(path)
        result = optimise.optimise(song, list(optimise.CANONICAL), workers, progress=False)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return path, {}, 0, 0, f"{type(exc).__name__}: {exc}"
    if out is not None:
        (out / f"{Path(path).stem}.yaml").write_text(result.text, encoding="utf-8")
    return path, result.tally, result.before, result.after, ""


def main(argv=None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+")
    parser.add_argument(
        "--workers", type=int, default=int(os.environ.get("UNITRACKER_WORKERS", 0)) or None
    )
    parser.add_argument("--jobs", type=int, default=1, help="songs at once, each with --workers")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
    totals, tokens = {name: Counter() for name in optimise.CANONICAL}, Counter()
    with ProcessPoolExecutor(args.jobs, mp_context=get_context("spawn")) as pool:
        futures = [pool.submit(tally, p, args.out, args.workers) for p in args.paths]
        for future in tqdm(as_completed(futures), total=len(futures), unit="song"):
            path, counts, before, after, error = future.result()
            for name, count in counts.items():
                totals[name].update(count)
            tokens.update(before=before, after=after, changed=before != after, failed=bool(error))
            if error or before != after:
                print(f"{path}: {error or f'{before} -> {after} tokens'}", flush=True)
    print("| pass | found | refused | proved |\n|---|---|---|---|")
    for name, count in totals.items():
        print(f"| `{name}` | {count['found']} | {count['refused']} | {count['proved']} |")
    print(f"tokens {tokens['before']} -> {tokens['after']} over {len(args.paths)} songs")
    print(f"({tokens['changed']} changed, {tokens['failed']} not run)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
