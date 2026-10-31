"""``unitracker-lift``: a tracker tune's generic document, optionally optimised."""

import argparse
import importlib
import sys
from pathlib import Path

from pysidtracker import find_format
from pysidtracker.errors import SidParseError

from unitracker.optimiser import lossy, optimise
from unitracker.optimiser.inline import MODES

# format name: its lifter module
LIFTERS = {
    "defmon": "unitracker.lift.defmon",
    "goattracker2": "unitracker.lift.goattracker2",
    "musicassembler": "unitracker.lift.musicassembler",
}


class FormatError(ValueError):
    """A tune no lifter reads."""


def lifter(raw: bytes):
    """``(format name, lifter module)`` of the tune ``raw``."""
    try:
        name = find_format(raw).name
    except SidParseError as exc:
        raise FormatError(f"no tracker format recognised: {exc}") from exc
    if name not in LIFTERS:
        raise FormatError(f"no lifter for {name}")
    return name, importlib.import_module(LIFTERS[name])


def lift(raw: bytes, subtune: int = 0, frames: int | None = None) -> str:
    """The generic document of 0-based ``subtune`` of the tune ``raw``, over ``frames`` ticks
    where given."""
    return lifter(raw)[1].lift(raw, subtune, frames)


def verify(raw: bytes, subtune: int = 0, frames: int = 20000):
    """The ``(tick, register, ours, theirs)`` cells where the lifted document's grid differs from
    the reference player's over ``frames`` ticks."""
    return lifter(raw)[1].verify(raw, subtune, frames)[0]


def lift_main(argv=None) -> int:
    """Lift a GoatTracker 2 .sid to its generic document."""
    parser = argparse.ArgumentParser(prog="unitracker-lift", description=lift_main.__doc__)
    parser.add_argument("sid", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--subtune", type=int, default=0, help="0-based")
    parser.add_argument(
        "--optimise", action="store_true", help="rewrite the document smaller with the same grid"
    )
    parser.add_argument("--ticks", type=int, help="--optimise: the guarantee's horizon")
    parser.add_argument(
        "--inline", choices=MODES, default="named", help="--optimise: the definitions inline"
    )
    lossy.add_arguments(parser)
    args = parser.parse_args(argv)
    try:
        text = lift(args.sid.read_bytes(), args.subtune)
    except ValueError as exc:
        print(f"{args.sid}: {exc}", file=sys.stderr)
        return 1
    if args.optimise:
        result = optimise(
            text,
            args.ticks,
            progress=True,
            lossy=lossy.tolerances_of(args),
            inline=args.inline,
        )
        for line in result.lossy.report() if result.lossy else ():
            print(line, file=sys.stderr)
        print(f"{result.before} -> {result.after} tokens", file=sys.stderr)
        text = result.text
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0
