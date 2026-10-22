"""Compare a generic document's register grid on unitracker-player with its tune's native player.

Exits 0 when the grids agree, 1 when they differ; ``--json`` writes the machine-readable diff.

    python tools/grid_diff.py DOC.yaml TUNE.sid [--ticks N] [--subtune N] [--from T --to T]
        [--json PATH]
"""

import argparse
import json
from pathlib import Path

import numpy as np
from pysidtracker import gridcmp

from unitracker.lift import backends
from unitracker.lift import verify
from unitracker.player import oracle
from unitracker.player.vm import REGISTERS


def row(regs) -> str:
    """One grid row as voice groups and the global registers."""
    parts = [" ".join(f"{x:02x}" for x in regs[7 * v : 7 * v + 7]) for v in range(3)]
    return " | ".join([*parts, " ".join(f"{x:02x}" for x in regs[21:])])


def report(verdict: verify.Verdict) -> str:
    """The human-readable diff: the gridcmp onset report of the verdict, doc against native."""
    grid = gridcmp.Verdict(
        n=verdict.ticks,
        unit="tick",
        ok=verdict.ok,
        played=verdict.played,
        starts=verdict.starts,
        onset=verdict.onset,
        whole=verdict.whole,
        cells=tuple(verdict.cells),
    )
    return gridcmp.runs_report(grid, names=REGISTERS, subject="document", a="doc", b="native")


def main(argv=None) -> int:
    """The entry point: the diff of the two grids, 0 when they agree and 1 when they differ."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("doc", type=Path)
    parser.add_argument("sid", type=Path)
    parser.add_argument("--ticks", type=int, default=3007)
    parser.add_argument("--subtune", type=int)
    parser.add_argument("--from", dest="start", type=int, default=0)
    parser.add_argument("--to", type=int, default=8)
    parser.add_argument(
        "--json",
        dest="json_path",
        nargs="?",
        const="-",
        default=None,
        metavar="PATH",
        help="write the machine-readable diff to PATH (stdout when a dash)",
    )
    args = parser.parse_args(argv)
    ours, starts = oracle.play(args.doc.read_text(encoding="utf-8"), args.ticks, progress=True)
    theirs = backends.native_player(args.sid.read_bytes(), args.subtune).render_grid(args.ticks)
    started = [tick for tick in starts if tick is not None]
    verdict = verify.compare_grids(ours, starts if started else None, theirs)
    if args.json_path is not None:
        text = json.dumps(verdict.summary(), indent=2) + "\n"
        if args.json_path == "-":
            print(text, end="")
        else:
            path = Path(args.json_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    else:
        print("starts:", starts)
        print(report(verdict))
        for tick in range(args.start, min(args.to, len(ours))):
            differing = [f"${r:02x}" for r in np.nonzero(ours[tick] != theirs[tick])[0]]
            print(f"{tick:4d} doc    {row(ours[tick])}   {' '.join(differing)}")
            print(f"     native {row(theirs[tick])}")
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
