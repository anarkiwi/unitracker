"""Count the native commands each tune's lift plays, by code and argument.

    python tools/command_census.py TUNE.sid ... [--codes 14,15]

Lifts each tune without expressions and prints one line per tune: its backend and each
``code:arg`` with its count, restricted to ``--codes`` when given.
"""

import argparse
from collections import Counter
from pathlib import Path

from tqdm import tqdm

from unitracker.lift import backends
from unitracker.player.vm import event_commands


def census(ir: dict, codes=None) -> Counter:
    """``{(code, arg): count}`` over every event of the lifter's song."""
    out = Counter()
    for events in (ir.get("phrases") or {}).values():
        for event in events:
            for code, arg in event_commands(event):
                if codes is None or code in codes:
                    out[(int(code), int(arg))] += 1
    return out


def main(argv=None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sids", type=Path, nargs="+")
    parser.add_argument("--codes", default="")
    args = parser.parse_args(argv)
    codes = {int(c, 0) for c in args.codes.split(",") if c} or None
    for sid in tqdm(args.sids, unit="tune"):
        raw = sid.read_bytes()
        try:
            backend = backends.detect(raw)
            found = census(backends.lift(raw, expressions=False), codes)
        except ValueError as exc:
            tqdm.write(f"{sid.stem}: {exc}")
            continue
        shown = " ".join(f"{c}:{a:#x}x{n}" for (c, a), n in sorted(found.items()))
        tqdm.write(f"{sid.stem} [{backend}]: {shown or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
