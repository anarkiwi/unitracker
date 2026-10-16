"""Command-line entry points: ``unitracker-lift`` and ``unitracker-play``."""

import argparse
import sys
from pathlib import Path

import numpy as np
from tqdm import tqdm

from unitracker import backends, songio
from unitracker.vm import SongPlayer


def lift_main(argv=None) -> int:
    """Lift a GoatTracker 2 or defMON .sid to unitracker YAML."""
    parser = argparse.ArgumentParser(prog="unitracker-lift", description=lift_main.__doc__)
    parser.add_argument("sid", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--subtune", type=int)
    args = parser.parse_args(argv)
    text = songio.dumps(backends.lift(args.sid.read_bytes(), args.subtune))
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def _ticks(song, seconds: float) -> int:
    clock = song["clock"]
    return int(seconds * clock["clock_hz"] / clock["cycles_per_tick"])


def _render(player, n: int, desc: str) -> np.ndarray:
    grid = np.empty((n, 25), dtype=np.uint8)
    for i in tqdm(range(n), desc=desc, unit="tick"):
        player.play_frame()
        grid[i] = player.regs
    return grid


def check(song: dict, sid: Path, n: int, subtune=None) -> tuple[int, int | None, int | None]:
    """Compare ``n`` ticks with the tune's native player: (ticks, first bad tick, reg)."""
    ours = _render(SongPlayer(song), n, "unitracker")
    theirs = _render(backends.native_player(sid.read_bytes(), subtune), n, "native")
    bad = np.argwhere(ours != theirs)
    if len(bad):
        return n, int(bad[0][0]), int(bad[0][1])
    return n, None, None


def play_main(argv=None) -> int:
    """Play a unitracker YAML song on an emulated SID."""
    parser = argparse.ArgumentParser(prog="unitracker-play", description=play_main.__doc__)
    parser.add_argument("song", type=Path)
    parser.add_argument("--wav", type=Path)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--model", choices=("8580", "6581"), default="8580")
    parser.add_argument("--check", type=Path, metavar="SID")
    parser.add_argument("--subtune", type=int)
    args = parser.parse_args(argv)
    song = songio.load(args.song)
    n = _ticks(song, args.seconds)
    status = 0
    if args.check:
        ticks, tick, reg = check(song, args.check, n, args.subtune)
        if tick is None:
            print(f"match: {ticks} ticks identical to the native player")
        else:
            print(f"mismatch: tick {tick}, register ${reg:02X}")
            status = 1
    if args.wav:
        SongPlayer(song).render_wav(args.wav, args.seconds, args.model, progress=True)
        print(f"wrote {args.wav}")
    return status
