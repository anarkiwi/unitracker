"""Command-line entry points: ``unitracker-lift``, ``-vm`` and ``-optimise``."""

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
    """Lift a GoatTracker 2, defMON or SID-Wizard .sid to its generic unitracker document,
    verified on unitracker-player against the native player."""
    parser = argparse.ArgumentParser(prog="unitracker-lift", description=lift_main.__doc__)
    parser.add_argument("sid", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--subtune", type=int)
    parser.add_argument(
        "--internal", action="store_true", help="write the lifter's internal, player-naming form"
    )
    parser.add_argument(
        "--embed", action="store_true", help="with --internal, write the playroutine in full"
    )
    parser.add_argument(
        "--optimise", action="store_true", help="rewrite the song smaller with the same output"
    )
    args = parser.parse_args(argv)
    lift = backends.lifter(args.sid.read_bytes(), args.subtune)
    song = lift.run()
    if args.optimise:
        song = optimise.optimise(song).ir
        lift.verify(song)
    if args.internal:
        text = songio.dumps(song, embed=args.embed)
    elif lift.document is None:
        print(f"{args.sid}: no generic document: {lift.gap}", file=sys.stderr)
        return 1
    else:
        text = lift.document
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    if lift.verdict is None:
        print(f"unverified: {lift.gap}", file=sys.stderr)
        return 1
    print(lift.verdict.line(), file=sys.stderr)
    return 0 if lift.verdict.ok else 1


def optimise_main(argv=None) -> int:
    """Rewrite a unitracker YAML song as a smaller document with the same register grid."""
    parser = argparse.ArgumentParser(prog="unitracker-optimise", description=optimise_main.__doc__)
    parser.add_argument("song", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument(
        "--report",
        type=Path,
        metavar="MD",
        help="write the report of proved redundancies, dead state and unreached vocabulary",
    )
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
    """Play an internal unitracker YAML song on the lifter's VM and an emulated SID."""
    parser = argparse.ArgumentParser(prog="unitracker-vm", description=play_main.__doc__)
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
