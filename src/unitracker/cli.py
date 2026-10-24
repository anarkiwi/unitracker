"""``unitracker-lift``: a tune's generic document by its format's direct lifter, optionally
optimised."""

import argparse
import importlib
import sys
from pathlib import Path

from pysidtracker import find_format
from pysidtracker.errors import SidParseError

from unitracker.optimiser import optimise

# format name: (direct lifter module, its verify function)
LIFTERS = {
    "goattracker2": ("unitracker.direct.goattracker2", "verify"),
    "sidwizard": ("unitracker.direct.sidwizard", "verify"),
    "defmon": ("unitracker.direct.defmon", "compare"),
    "musicassembler": ("unitracker.direct.musicassembler", "verify"),
}


class FormatError(ValueError):
    """A tune no direct lifter reads."""


def lifter(raw: bytes):
    """``(format name, lifter module, its verify function)`` of the tune ``raw``."""
    try:
        name = find_format(raw).name
    except SidParseError as exc:
        raise FormatError(f"no tracker format recognised: {exc}") from exc
    if name not in LIFTERS:
        raise FormatError(f"no direct lifter for {name}")
    module, check = LIFTERS[name]
    module = importlib.import_module(module)
    return name, module, getattr(module, check)


def lift(raw: bytes, subtune: int = 0) -> str:
    """The generic document of 0-based ``subtune`` of the tune ``raw``."""
    text = lifter(raw)[1].transcribe(raw, subtune)
    return text[0] if isinstance(text, tuple) else text


def verify(raw: bytes, subtune: int = 0, frames: int = 20000):
    """The ``(tick, register, ours, theirs)`` cells where the lifted document's grid differs from
    the native player's over ``frames`` ticks."""
    return lifter(raw)[2](raw, subtune, frames)[0]


def lift_main(argv=None) -> int:
    """Lift a GoatTracker 2, defMON, SID-Wizard or Music Assembler .sid to its generic unitracker document."""
    parser = argparse.ArgumentParser(prog="unitracker-lift", description=lift_main.__doc__)
    parser.add_argument("sid", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--subtune", type=int, default=0, help="0-based")
    parser.add_argument(
        "--optimise", action="store_true", help="rewrite the document smaller with the same grid"
    )
    parser.add_argument("--ticks", type=int, help="--optimise: the guarantee's horizon")
    args = parser.parse_args(argv)
    try:
        text = lift(args.sid.read_bytes(), args.subtune)
    except FormatError as exc:
        print(f"{args.sid}: {exc}", file=sys.stderr)
        return 1
    if args.optimise:
        result = optimise(text, args.ticks, progress=True)
        print(f"{result.before} -> {result.after} tokens", file=sys.stderr)
        text = result.text
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0
