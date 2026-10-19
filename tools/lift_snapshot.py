"""Write each tune's generic document as ``OUTDIR/<tune>.yaml`` (or its error as ``.err``).

    python tools/lift_snapshot.py OUTDIR SID ... [@LIST] [--hvsc DIR] [--plain]

A ``SID`` that is not a file is read under ``--hvsc`` (default ``$HVSC``); ``@LIST`` reads
arguments one per line; ``--plain`` lifts without expressions and writes the lifter's song's
``pprint`` text, as its native rows have no generic document. The outputs are for ``diff -r``.
"""

import argparse
import os
import pprint
from pathlib import Path

from tqdm import tqdm

from unitracker.lift import backends


def snapshot(sid: Path, out: Path, expressions: bool) -> bool:
    """Write ``sid``'s lift under ``out``; whether it lifted to a written text."""
    try:
        lift = backends.lifter(sid.read_bytes(), expressions=expressions)
        song = lift.run()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        error = f"{type(exc).__name__}: {exc}"
    else:
        text = lift.document if expressions else pprint.pformat(song, width=100)
        if text is not None:
            (out / f"{sid.stem}.yaml").write_text(text)
            return True
        error = f"gap: {lift.gap}"
    (out / f"{sid.stem}.err").write_text(f"{error}\n")
    return False


def main() -> None:
    """Command-line entry."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], fromfile_prefix_chars="@")
    parser.add_argument("out", type=Path)
    parser.add_argument("sids", nargs="+")
    parser.add_argument("--hvsc", type=Path, default=Path(os.environ.get("HVSC", ".")))
    parser.add_argument("--plain", action="store_true", help="lift without expressions")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    sids = [Path(s) if Path(s).is_file() else args.hvsc / s for s in args.sids]
    lifted = sum(snapshot(s, args.out, not args.plain) for s in tqdm(sids, unit="tune"))
    print(f"{lifted}/{len(sids)} lifted into {args.out}")


if __name__ == "__main__":
    main()
