"""Trace a SID-Wizard tune's spec-player state cell by cell: ``swtrace.py TUNE FRAMES CELL...``
prints each frame's value of the layout cells named (per-voice cells with ``@v``), then the
voice registers of ``--regs V``."""

import argparse
import sys

from pysidtracker import find_format
from tqdm import tqdm

from shared import tune

STRIDE = 7


def trace(raw: bytes, frames: int, cells: list, subtune: int = 0, voice: int | None = None):
    """The rows ``(frame, cell values..., voice registers...)`` after each frame; ``name@v`` is
    voice ``v``'s cell."""
    fmt = find_format(raw)
    player = fmt.spec_player(fmt.load(raw, subtune), notes=True)
    layout = player._bind(player.view(), player.entry)  # pylint: disable=protected-access
    where = []
    for cell in cells:
        name, _, at = cell.partition("@")
        where.append(layout[name] + (int(at) * STRIDE if at else 0))
    for frame in tqdm(range(frames), unit="frame", disable=frames < 500):
        player.play_frame()
        regs = player.regs[STRIDE * voice : STRIDE * voice + STRIDE] if voice is not None else ()
        yield (frame, *(player.memory[a] for a in where), *regs)


def main(argv=None) -> int:
    """Print the trace."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("tune")
    parser.add_argument("frames", type=int)
    parser.add_argument("cells", nargs="*")
    parser.add_argument("--subtune", type=int, default=0)
    parser.add_argument("--regs", type=int, help="also this 0-based voice's registers")
    args = parser.parse_args(argv)
    raw = tune(args.tune).read_bytes()
    print(
        "frame",
        *args.cells,
        *(["fl", "fh", "pl", "ph", "ctl", "ad", "sr"] if args.regs is not None else []),
    )
    for row in trace(raw, args.frames, args.cells, args.subtune, args.regs):
        print(*row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
