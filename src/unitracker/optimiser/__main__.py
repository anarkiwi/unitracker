"""``python -m unitracker.optimiser IN.yaml [-o OUT.yaml] [--ticks N] [--inline MODE]``."""

import argparse
import sys
from pathlib import Path

from unitracker.optimiser.driver import optimise
from unitracker.optimiser.inline import MODES


def main(argv=None) -> int:
    """Optimise a document, print its size before and after, and write it."""
    parser = argparse.ArgumentParser(prog="python -m unitracker.optimiser", description=__doc__)
    parser.add_argument("document", type=Path)
    parser.add_argument("-o", "--out", type=Path, help="the optimised document (default stdout)")
    parser.add_argument("--ticks", type=int, help="the guarantee's horizon in ticks")
    parser.add_argument("--rounds", type=int, default=8)
    parser.add_argument(
        "--inline", choices=MODES, default="named", help="the definitions written inline"
    )
    args = parser.parse_args(argv)
    result = optimise(
        args.document.read_text(encoding="utf-8"),
        args.ticks,
        rounds=args.rounds,
        progress=True,
        inline=args.inline,
    )
    if args.out:
        args.out.write_text(result.text, encoding="utf-8")
    else:
        sys.stdout.write(result.text)
    for name, label in result.kept:
        print(f"{name}: {label}", file=sys.stderr)
    print(f"refused: {len(result.refused)} edits", file=sys.stderr)
    print(f"before: {result.before} tokens\nafter: {result.after} tokens", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
