"""Tokens each optimiser pass saves: per document, the optimised size with the driver's passes
up to and including each one (in the driver's order), and with every pass and ``--inline all``.

    python tools/pass_savings.py DOC.yaml ... [--ticks N] [--jobs J]
"""

import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path

from tqdm import tqdm

from unitracker.optimiser.driver import PASSES, optimise
from unitracker.optimiser.measure import size


def savings(path: Path, ticks: int) -> tuple:
    """``(name, [tokens lifted, after each pass prefix..., all inline])`` of one document."""
    text = path.read_text(encoding="utf-8")
    row = [size(text)]
    for k in range(1, len(PASSES) + 1):
        row.append(optimise(text, ticks, order=PASSES[:k]).after)
    row.append(optimise(text, ticks, inline="all").after)
    return path.stem, row


def table(rows: list) -> str:
    """The markdown table of ``rows``: tokens, then each pass's saving in tokens and percent."""
    head = ["document", "lifted"] + [name for name, _ in PASSES] + ["inline all"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for name, values in rows:
        cells = [str(values[0])]
        for before, after in zip(values, values[1:-1]):
            cells.append(f"-{before - after} ({100 * (before - after) / values[0]:.1f}%)")
        cells.append(f"{values[-1]} ({100 * (values[0] - values[-1]) / values[0]:.1f}% total)")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv=None) -> int:
    """Measure each document's savings in parallel and print the table."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("documents", nargs="+", type=Path)
    parser.add_argument("--ticks", type=int, default=3000)
    parser.add_argument("--jobs", type=int, default=max(1, len(os.sched_getaffinity(0)) - 2))
    args = parser.parse_args(argv)
    with ProcessPoolExecutor(args.jobs) as pool:
        jobs = pool.map(partial(savings, ticks=args.ticks), args.documents)
        rows = list(tqdm(jobs, total=len(args.documents), unit="doc"))
    print(table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
