"""Where a SID-Wizard transliteration's grid first leaves the reference player's, register by
register, with each voice's row, instrument and table cursors at that tick; with ``--why``,
pysidtracker's account of the reference's first differing cell (pysidwizard's and the 6502's).

``python tools/swdiff.py TUNE.sid [--subtune N] [--frames F] [--window W] [--regs R ...] [--why]``
"""

import argparse
import sys
from pathlib import Path

import numpy as np
from pysidtracker import gridcmp
from pysidtracker.whydiff import explain_divergence
from pysidwizard import parse_sid_song
from unitracker_player.player import play

from unitracker.direct import swlift
from unitracker.direct.swtrace import standard


def states(swm, ticks: int) -> list:
    """Per tick, each voice's ``(pattern, row, instrument, wf, pw, chord)`` as the reference
    player leaves it, from tick ``LEAD`` on."""
    ref, out = standard(swm), []
    patterns = {id(p): k + 1 for k, p in enumerate(swm.patterns)}
    for _ in range(ticks + swlift.LEAD):
        ref.play_frame()
        out.append(
            [
                (
                    patterns.get(id(v.pattern)),
                    v.pattern_row,
                    v.instrument_idx,
                    v.wf_pos,
                    v.pw_pos,
                    v.chord_pos,
                    v.speed_counter,
                )
                for v in ref.voices
            ]
        )
    return out[swlift.LEAD :]


def grids(raw: bytes, subtune: int, frames: int) -> tuple:
    """``(lift, ours, theirs)``: the document's grid and the reference's from its first row's
    TICK_2."""
    lift = swlift.lifter(raw, subtune)
    ours = np.asarray(play(lift.document(), ticks=frames), np.uint8)
    theirs = standard(lift.swm).render_grid(len(ours) + swlift.LEAD)
    return lift, ours, np.asarray(theirs, np.uint8)[swlift.LEAD :]


def report(raw: bytes, subtune: int, frames: int, window: int, regs: list) -> str:
    """The first differing tick of each register and a window of both grids around it."""
    lift, ours, theirs = grids(raw, subtune, frames)
    cells = gridcmp.cell_diff(ours, theirs)
    if not cells.size:
        return "exact"
    seen, lines = states(lift.swm, int(cells[:, 0].max()) + 1), []
    for reg in sorted(set(int(r) for r in cells[:, 1])):
        if regs and gridcmp.REG_NAMES[reg] not in regs:
            continue
        first = int(cells[cells[:, 1] == reg][0, 0])
        lines.append(f"{gridcmp.REG_NAMES[reg]}: first {first}, {np.sum(cells[:, 1] == reg)} cells")
        for t in range(max(0, first - window), min(len(ours), first + window + 1)):
            mark = "*" if ours[t, reg] != theirs[t, reg] else " "
            lines.append(f" {mark}{t:6d} ours {ours[t, reg]:3d} ref {theirs[t, reg]:3d}  {seen[t]}")
    return "\n".join(lines)


def why(raw: bytes, subtune: int, frames: int, width: int = 100) -> str:
    """The first cell where the reference player leaves the document's grid (the reference's
    first ``LEAD`` ticks before it), explained by both players' steps to it."""
    text = swlift.lifter(raw, subtune).document()
    ours = np.asarray(play(text, ticks=frames), np.int16)

    def player(data: bytes):
        return standard(parse_sid_song(data, subtune + 1))

    head = np.asarray(player(raw).render_grid(swlift.LEAD), np.int16)
    found = explain_divergence(
        raw, player, np.concatenate([head, ours]), song=subtune + 1, label="pysidwizard"
    )
    return "exact" if found is None else found.text(width)


def main(argv=None) -> int:
    """Print the report for one tune."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("sid", type=Path)
    parser.add_argument("--subtune", type=int, default=0)
    parser.add_argument("--frames", type=int, default=6000)
    parser.add_argument("--window", type=int, default=3)
    parser.add_argument("--regs", nargs="*", default=[])
    parser.add_argument("--why", action="store_true", help="explain the first differing cell")
    args = parser.parse_args(argv)
    raw = args.sid.read_bytes()
    if args.why:
        print(why(raw, args.subtune, args.frames))
        return 0
    print(report(raw, args.subtune, args.frames, args.window, args.regs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
