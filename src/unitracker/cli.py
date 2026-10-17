"""Command-line entry points: ``unitracker-lift``, ``-play`` and ``-optimise``."""

import argparse
import sys
from pathlib import Path

import numpy as np
from tqdm import tqdm

from unitracker import optimise
from unitracker.analysis import orthogonality
from unitracker.format import songio
from unitracker.lift import backends
from unitracker.player.vm import SongPlayer, first_mismatch


def lift_main(argv=None) -> int:
    """Lift a GoatTracker 2 or defMON .sid to unitracker YAML."""
    parser = argparse.ArgumentParser(prog="unitracker-lift", description=lift_main.__doc__)
    parser.add_argument("sid", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--subtune", type=int)
    parser.add_argument(
        "--embed", action="store_true", help="write the playroutine in full, not by reference"
    )
    parser.add_argument(
        "--optimise", action="store_true", help="rewrite the song smaller with the same output"
    )
    args = parser.parse_args(argv)
    song = backends.lift(args.sid.read_bytes(), args.subtune)
    if args.optimise:
        song = optimise.optimise(song).ir
    if args.output:
        songio.dump(song, args.output, embed=args.embed)
    else:
        text = songio.dumps(song, embed=args.embed)
        sys.stdout.write(text)
    return 0


def optimise_main(argv=None) -> int:
    """Rewrite a unitracker YAML song as a smaller document with the same register grid."""
    parser = argparse.ArgumentParser(prog="unitracker-optimise", description=optimise_main.__doc__)
    parser.add_argument("song", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--report", type=Path, metavar="MD", help="write the orthogonality report")
    parser.add_argument("--passes", help="comma-separated passes, default all in order")
    parser.add_argument("--workers", type=int, help="processes proving candidates (default: cores)")
    args = parser.parse_args(argv)
    passes = args.passes.split(",") if args.passes else None
    unknown = sorted(set(passes or ()) - set(optimise.PASSES))
    if unknown:
        parser.error(f"unknown passes {unknown}; known: {', '.join(optimise.PASSES)}")
    result = optimise.optimise(songio.load(args.song), passes, args.workers)
    if args.output:
        args.output.write_text(result.text, encoding="utf-8")
    else:
        sys.stdout.write(result.text)
    if args.report:
        lines = orthogonality.report([(args.song.stem, result)])
        args.report.write_text(lines, encoding="utf-8")
    print(f"{args.song}: {result.before} -> {result.after} tokens", file=sys.stderr)
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
    player = SongPlayer(song)
    ours = _render(player, n, "unitracker")
    theirs = _render(backends.native_player(sid.read_bytes(), subtune), n, "native")
    bad = first_mismatch(ours, theirs, player.starts)
    return (n, *bad) if bad else (n, None, None)


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
