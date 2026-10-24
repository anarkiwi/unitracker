"""Tokens each optimiser pass saves: per document, one optimisation's tokens saved by each pass
(the driver's ``saved``), the rename's, and with ``--lossy`` the lossy mode's first (its prelude
forms off with ``--no-prelude``); then the size with every pass and ``--inline all``.

    python tools/pass_savings.py DOC.yaml ... [--ticks N] [--jobs J] [--lossy [--no-prelude]]
"""

import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path

from tqdm import tqdm

from unitracker.optimiser import lossy as _lossy
from unitracker.optimiser.driver import PASSES, optimise
from unitracker.optimiser.measure import size

COLUMNS = ["lossy"] + [name for name, _ in PASSES] + ["names"]


def savings(path: Path, ticks: int, lossy: bool = False, prelude: bool = True) -> tuple:
    """``(name, tokens before, {column: tokens saved}, tokens after, tokens inline all)``."""
    text = path.read_text(encoding="utf-8")
    before, saved = size(text), {}
    if lossy:
        simplified = _lossy.simplify(text, ticks, None, prelude=prelude)
        saved["lossy"], text = simplified.before - simplified.after, simplified.text
    result = optimise(text, ticks)
    saved |= dict(result.saved)
    return path.stem, before, saved, result.after, optimise(text, ticks, inline="all").after


def table(rows: list) -> str:
    """The markdown table of ``rows``: tokens, each column's saving, after, inline all."""
    columns = [c for c in COLUMNS if any(c in saved for _, _, saved, _, _ in rows)]
    head = ["document", "tokens"] + columns + ["after", "inline all"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for name, before, saved, after, inline in rows:
        cells = [str(before)] + [f"-{saved.get(c, 0)}" for c in columns]
        cells += [f"{after} (-{100 * (before - after) / before:.1f}%)", str(inline)]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv=None) -> int:
    """Measure each document's savings in parallel and print the table."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("documents", nargs="+", type=Path)
    parser.add_argument("--ticks", type=int, default=3000)
    parser.add_argument("--jobs", type=int, default=max(1, len(os.sched_getaffinity(0)) - 2))
    parser.add_argument("--lossy", action="store_true", help="run the lossy mode first")
    parser.add_argument("--no-prelude", action="store_true", help="--lossy: no prelude forms")
    args = parser.parse_args(argv)
    run = partial(savings, ticks=args.ticks, lossy=args.lossy, prelude=not args.no_prelude)
    with ProcessPoolExecutor(args.jobs) as pool:
        jobs = pool.map(run, args.documents)
        rows = list(tqdm(jobs, total=len(args.documents), unit="doc"))
    print(table(rows))
    return 0


if __name__ == "__main__":
    os.environ.update(dict.fromkeys(("OMP_NUM_THREADS", "MKL_NUM_THREADS"), "2"))
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")
    sys.exit(main())
