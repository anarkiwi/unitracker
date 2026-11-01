"""How fast the write-order solver runs on captured tunes: its time (timeline, order masks and hard
restarts) against the music's length, per tune.

``python tools/solvertime.py TUNES.csv|TUNE.sid ... [--per-player N] [--horizon N]``
"""

import sys
import time

from tqdm import tqdm
from vagrant_moses import memmap as mm
from vagrant_moses import sidorder as S

import shared
import writeorder as W


def solve(init, frames, horizon: int) -> dict:
    """The solver's seconds on a captured stream and the music's seconds."""
    begin = time.process_time()
    tl = S.Timeline(init, frames, mm.PAL.clock_hz)
    masks = S.order_masks(tl, horizon)
    notes = S.hard_restarts(tl, 39566, horizon)
    seconds = time.process_time() - begin
    music = (frames[-1][0] - frames[0][0]) / mm.PAL.clock_hz + 19656 / mm.PAL.clock_hz
    return {
        "frames": len(frames),
        "cases": sum(n for n, _ in masks.values()),
        "notes": sum(notes.values()),
        "solver_s": seconds,
        "music_s": music,
        "x_real": music / seconds,
    }


def main(argv=None) -> int:
    """Capture each tune and print the solver's time and multiple of real time."""
    ap = W.capture_arguments(shared.corpus_parser(__doc__))
    ap.add_argument("--horizon", type=int, default=10)
    ap.add_argument("--per-player", type=int, default=0, help="tunes kept per player (0: all)")
    args = ap.parse_args(argv)
    rows, seen = [], {}
    for row in shared.rows(args.sources, args.player):
        player = row.get("player", "")
        if not args.per_player or seen.get(player, 0) < args.per_player:
            seen[player] = seen.get(player, 0) + 1
            rows.append(row)
    print(f"{'tune':40} {'player':24} {'frames':>6} {'cases':>6} {'solver s':>9} {'x real':>7}")
    for row in tqdm(rows, unit="tune"):
        rec = solve(*W.capture(row, args.hvsc, args), args.horizon)
        name = row["path"].rsplit("/", 1)[-1]
        print(
            f"{name:40.40} {row.get('player', ''):24.24} {rec['frames']:6} {rec['cases']:6} "
            f"{rec['solver_s']:9.3f} {rec['x_real']:7.1f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
