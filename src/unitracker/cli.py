"""Command-line entry point: ``unitracker-lift``."""

import argparse
import sys
from pathlib import Path

from unitracker import optimise
from unitracker.lift import backends


def lift_main(argv=None) -> int:
    """Lift a GoatTracker 2, defMON or SID-Wizard .sid to its generic unitracker document,
    verified on unitracker-player against the native player."""
    parser = argparse.ArgumentParser(prog="unitracker-lift", description=lift_main.__doc__)
    parser.add_argument("sid", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--subtune", type=int)
    parser.add_argument(
        "--optimise", action="store_true", help="rewrite the song smaller with the same output"
    )
    args = parser.parse_args(argv)
    lift = backends.lifter(args.sid.read_bytes(), args.subtune)
    song = lift.run()
    if args.optimise:
        lift.verify(optimise.optimise(song).ir)
    if lift.document is None:
        print(f"{args.sid}: no generic document: {lift.gap}", file=sys.stderr)
        return 1
    if args.output:
        args.output.write_text(lift.document, encoding="utf-8")
    else:
        sys.stdout.write(lift.document)
    if lift.verdict is None:
        print(f"unverified: {lift.gap}", file=sys.stderr)
        return 1
    print(lift.verdict.line(), file=sys.stderr)
    return 0 if lift.verdict.ok else 1
