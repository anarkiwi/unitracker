"""List a tune's modulator occurrences with the expression text the conversion derives for each.

    python tools/occurrences.py TUNE.sid [--unit NAME] [--cells c1,c2]

Prints one line per occurrence of the native lift: unit, voice, tick, label, the start
cells asked for, its event instance and its text (None: no modulation).
"""

import argparse
from pathlib import Path

from unitracker.lift import backends
from unitracker.modfit import convert


def report(ir: dict, unit: str | None = None, cells: tuple = ()) -> list[str]:
    """The occurrence lines of the lifter's song with its native modulations."""
    ir = convert.expand_repeats(ir)
    obs = convert.observe(ir, convert.window(ir), None)
    _, texts = convert.derive_all(ir, obs, None, workers=1)
    out = []
    for occ, text in zip(obs["occurrences"], texts):
        if unit is not None and occ.unit != unit:
            continue
        shown = " ".join(f"{c}={occ.start.get(c)}" for c in cells)
        out.append(
            f"{occ.unit} v{occ.voice} t{occ.tick} {occ.label} {shown} {occ.instance}: {text}"
        )
    return out


def main(argv=None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sid", type=Path)
    parser.add_argument("--unit")
    parser.add_argument("--cells", default="")
    args = parser.parse_args(argv)
    ir = backends.lift(args.sid.read_bytes(), expressions=False)
    cells = tuple(c for c in args.cells.split(",") if c)
    print("\n".join(report(ir, args.unit, cells)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
