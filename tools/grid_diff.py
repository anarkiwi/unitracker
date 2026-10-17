"""Compare a song's register grid with its tune's native player around the voices' starts.

    python tools/grid_diff.py song.yaml TUNE.sid [--ticks N] [--from T --to T]

Prints each voice's start tick, the first mismatch under the song's-own-state criterion,
and both grids (ours / native) for the tick range, marking differing registers.
"""

import argparse
from pathlib import Path

import numpy as np

from unitracker.format import songio
from unitracker.lift import backends
from unitracker.player.vm import SongPlayer, first_mismatch


def row(regs) -> str:
    """One grid row as voice groups and the global registers."""
    parts = [" ".join(f"{x:02x}" for x in regs[7 * v : 7 * v + 7]) for v in range(3)]
    return " | ".join([*parts, " ".join(f"{x:02x}" for x in regs[21:])])


def main(argv=None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("song", type=Path)
    parser.add_argument("sid", type=Path)
    parser.add_argument("--ticks", type=int, default=64)
    parser.add_argument("--subtune", type=int)
    parser.add_argument("--from", dest="start", type=int, default=0)
    parser.add_argument("--to", type=int, default=8)
    args = parser.parse_args(argv)
    player = SongPlayer(songio.load(args.song))
    ours = player.render_grid(args.ticks)
    native = backends.native_player(args.sid.read_bytes(), args.subtune)
    theirs = np.asarray(native.render_grid(args.ticks), dtype=np.uint8)
    print("starts:", player.starts)
    print("first mismatch:", first_mismatch(ours, theirs, player.starts))
    for t in range(args.start, min(args.to, args.ticks)):
        diff = [f"${r:02x}" for r in np.nonzero(ours[t] != theirs[t])[0]]
        print(f"{t:4d} ours   {row(ours[t])}   {' '.join(diff)}")
        print(f"     native {row(theirs[t])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
