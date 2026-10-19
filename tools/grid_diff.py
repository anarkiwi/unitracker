"""Compare a generic document's register grid on unitracker-player with its tune's native player.

    python tools/grid_diff.py DOC.yaml TUNE.sid [--ticks N] [--from T --to T] [--subtune N]

Prints each voice's first-note tick, the first mismatch under the project criterion (each voice
from its first note), and both grids (document / native) for the tick range, marking differing
registers.
"""

import argparse
from pathlib import Path

import numpy as np

from unitracker.lift import backends
from unitracker.player import oracle
from unitracker.player.vm import first_mismatch


def row(regs) -> str:
    """One grid row as voice groups and the global registers."""
    parts = [" ".join(f"{x:02x}" for x in regs[7 * v : 7 * v + 7]) for v in range(3)]
    return " | ".join([*parts, " ".join(f"{x:02x}" for x in regs[21:])])


def main(argv=None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("doc", type=Path)
    parser.add_argument("sid", type=Path)
    parser.add_argument("--ticks", type=int, default=64)
    parser.add_argument("--subtune", type=int)
    parser.add_argument("--from", dest="start", type=int, default=0)
    parser.add_argument("--to", type=int, default=8)
    args = parser.parse_args(argv)
    ours, starts = oracle.play(args.doc.read_text(encoding="utf-8"), args.ticks, progress=True)
    native = backends.native_player(args.sid.read_bytes(), args.subtune)
    theirs = np.asarray(native.render_grid(args.ticks), dtype=np.uint8)[: len(ours)]
    print("starts:", starts)
    print("first mismatch:", first_mismatch(ours, theirs, starts))
    for t in range(args.start, min(args.to, len(ours))):
        diff = [f"${r:02x}" for r in np.nonzero(ours[t] != theirs[t])[0]]
        print(f"{t:4d} doc    {row(ours[t])}   {' '.join(diff)}")
        print(f"     native {row(theirs[t])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
