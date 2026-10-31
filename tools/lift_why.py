"""Why a lifted GoatTracker 2 document differs from the spec player: the first differing cell,
then the document player's events, binds, stops and program starts for each call in a window next
to the spec player's register writes and chosen player variables.

``python tools/lift_why.py TUNE [--frame N] [--before K] [--after K] [--vars NAME ...]``
"""

import argparse
import sys
from pathlib import Path

from pygoattracker import decompile_sid, gt2spec
from pysidtracker import find_format
from pysidtracker.testing import resolve_tune
from unitracker_player.compiler import load
from unitracker_player.player import Player
from unitracker.lift import goattracker2 as lift

KINDS = ("event", "start", "bind", "stop")


def first_cell(raw: bytes, subtune: int, frames: int) -> tuple:
    """``(text, frame, register)`` of a tune's first differing cell, ``frame`` -1 for none."""
    cells, text = lift.verify(raw, subtune, frames)
    return (text, -1, -1) if not cells.size else (text, int(cells[0][0]), int(cells[0][1]))


def document_trace(text: str, frames: int) -> dict:
    """``{call: [(kind, args)]}``: what the document player observes on each call."""
    log, call = {}, [0]

    def observe(kind, *args):
        if kind in KINDS:
            log.setdefault(call[0], []).append((kind, args))

    player = Player(load(text), observe)
    for call[0] in range(frames):
        player.step()
    return log


def spec_trace(raw: bytes, subtune: int, frames: int, names: list) -> list:
    """``[(frame, writes, {name: [per channel byte]})]`` of the spec player."""
    model = decompile_sid(raw, subtune)
    lay = gt2spec.binder(model)(model.memory, 0)
    player = find_format(raw).spec_player(model, notes=model.header.is_ntsc and 1022727 or 985248)
    out = []
    for frame in range(frames):
        writes = player.play_frame()
        view = player.view()
        out.append((frame, writes, {n: [view[lay[n] + 7 * c] for c in range(3)] for n in names}))
    return out


def window(ours: dict, theirs: list, lo: int, hi: int) -> None:
    """Print both players' steps for the calls ``lo`` to ``hi``."""
    for call in range(lo, hi):
        print(f"-- call {call}")
        for kind, items in ours.get(call, []):
            print(f"   doc  {kind} {items[:4]}")
        _, writes, variables = theirs[call]
        print(f"   spec writes {writes} {variables or ''}")


def main(argv=None) -> int:
    """Print the window around the first differing cell (or ``--frame``)."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("tune")
    parser.add_argument("--subtune", type=int, default=0)
    parser.add_argument("--frame", type=int)
    parser.add_argument("--frames", type=int, default=lift.CHECK)
    parser.add_argument("--before", type=int, default=3)
    parser.add_argument("--after", type=int, default=2)
    parser.add_argument("--vars", nargs="*", default=[])
    args = parser.parse_args(argv)
    raw = Path(resolve_tune(args.tune) or args.tune).read_bytes()
    text, frame, reg = first_cell(raw, args.subtune, args.frames)
    frame = args.frame if args.frame is not None else frame
    print(f"first differing cell: frame {frame} register {reg}")
    if frame < 0:
        return 0
    lo, hi = max(frame - args.before, 0), frame + args.after + 1
    window(document_trace(text, hi), spec_trace(raw, args.subtune, hi, args.vars), lo, hi)
    return 0


if __name__ == "__main__":
    sys.exit(main())
