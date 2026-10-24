"""The optimiser's passes over documents: the tokens each pass saved, each over the song's
length (its first loop at most ``--limit`` ticks), the edits each kept and the renames.

    python tools/passes_eval.py DOC.yaml ... [--limit N] [--jobs J] [--out DIR]
"""

import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path

from corpus import song_ticks
from tqdm import tqdm

from unitracker.optimiser import PASSES, optimise


def evaluate(path: Path, limit: int, out: Path | None) -> tuple:
    """``(row, lines)``: a document's tokens before, saved per pass and after, and its kept
    edits, the optimised text written to ``out``."""
    text = path.read_text(encoding="utf-8")
    ticks = song_ticks(text, limit)
    result = optimise(text, ticks)
    if out is not None:
        (out / path.name).write_text(result.text, encoding="utf-8")
    counts = {name: sum(n == name for n, _ in result.kept) for name, _ in PASSES + (("names", 0),)}
    cells = [f"{-result.saved[n]:+d} ({counts[n]})" for n, _ in PASSES + (("names", 0),)]
    row = f"| {path.stem} | {ticks} | {result.before} | {' | '.join(cells)} | {result.after} |"
    return row, [f"{n}: {label}" for n, label in result.kept] + [f"refused: {len(result.refused)}"]


def main(argv=None) -> int:
    """Print the table of the documents of ``argv``, their kept edits to stderr."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("documents", type=Path, nargs="+")
    parser.add_argument("--limit", type=int, default=100000)
    parser.add_argument("--jobs", type=int, default=os.cpu_count() // 2 or 1)
    parser.add_argument("--out", type=Path, help="write each optimised document here")
    args = parser.parse_args(argv)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
    names = [n for n, _ in PASSES] + ["names"]
    print(f"| tune | ticks | tokens | {' | '.join(names)} | after |")
    print("|---" * (len(names) + 4) + "|")
    run = partial(evaluate, limit=args.limit, out=args.out)
    with ProcessPoolExecutor(args.jobs) as pool:
        results = list(tqdm(pool.map(run, args.documents), total=len(args.documents), unit="doc"))
    for row, _ in results:
        print(row)
    for path, (_, lines) in zip(args.documents, results):
        print(f"\n{path.stem}:", *lines, sep="\n  ", file=sys.stderr)
    return 0


if __name__ == "__main__":
    THREADS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")
    os.environ.update({var: os.environ.get(var, "2") for var in THREADS})
    sys.exit(main())
