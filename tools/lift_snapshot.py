"""Write each tune's lift as ``OUTDIR/<tune>.yaml`` (or its error as ``.err``), to ``diff -r``.

    python tools/lift_snapshot.py OUTDIR SID ... [@LIST] [--hvsc DIR] [--plain]

A ``SID`` that is not a file is read under ``--hvsc`` (default ``$HVSC``); ``@LIST`` reads
arguments one per line; ``--plain`` lifts without expressions and writes the internal song's
``pprint`` text, as its native rows may have no written form.
"""

import argparse
import os
import pprint
from pathlib import Path

from tqdm import tqdm

from unitracker.format import songio
from unitracker.lift import backends


def snapshot(sid: Path, out: Path, expressions: bool) -> bool:
    """Write ``sid``'s lift under ``out``; whether it lifted."""
    try:
        song = backends.lift(sid.read_bytes(), expressions=expressions)
        text = songio.dumps(song) if expressions else pprint.pformat(song, width=100)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        (out / f"{sid.stem}.err").write_text(f"{type(exc).__name__}: {exc}\n")
        return False
    (out / f"{sid.stem}.yaml").write_text(text)
    return True


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
