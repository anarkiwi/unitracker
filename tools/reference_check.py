"""Lift the reference tunes and print each one's verify verdict on unitracker-player.

    python tools/reference_check.py OUTDIR [--forbid WORD ...] [--tunes SID ...]

Writes OUTDIR/<tune>.yaml, each tune's generic document, and prints its verdict and the forbidden
words it holds; ``--tunes`` checks other tunes.
"""

import argparse
import re
import time
from pathlib import Path

from unitracker.lift import backends

CACHE = Path(__file__).resolve().parent.parent / ".tunecache"
TUNES = (
    "MUSICIANS/C/Cadaver/Metal_Warrior_Title_Remix.sid",
    "MUSICIANS/H/Hermit/Csillag_szazketto_kettoskereszt.sid",
    "MUSICIANS/I/Ilkke/Crosseyed.sid",
)


def check(sid: Path, out: Path, forbid) -> str:
    """One tune's report line."""
    lift = backends.lifter(sid.read_bytes())
    lift.run()
    if lift.document is None:
        return f"no generic document: {lift.gap}"
    out.write_text(lift.document, encoding="utf-8")
    found = sorted({w for w in forbid if re.search(rf"(?<![\w]){re.escape(w)}\b", lift.document)})
    verdict = lift.verdict.line() if lift.verdict else f"unverified: {lift.gap}"
    return f"{verdict}; forbidden: {found or 'none'}"


def main(argv=None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--forbid", nargs="*", default=[])
    parser.add_argument("--tunes", nargs="*", type=Path)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    tunes = args.tunes or [CACHE / t for t in TUNES]
    for k, sid in enumerate(tunes, start=1):
        start = time.monotonic()
        line = check(sid, args.out / f"{sid.stem}.yaml", args.forbid)
        print(
            f"[{k}/{len(tunes)}] {sid.stem} ({time.monotonic() - start:.0f}s): {line}", flush=True
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
