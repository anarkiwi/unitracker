"""Lift the reference tunes, check each round-trips canonically and plays its native grid.

    python tools/reference_check.py OUTDIR [--ticks N] [--forbid WORD ...]

Writes OUTDIR/<tune>.yaml per tune and prints its round trip, first mismatch and forbidden words.
"""

import argparse
import re
import time
from pathlib import Path

import numpy as np

from unitracker.cli import lift_main
from unitracker.format import songio
from unitracker.lift import backends
from unitracker.player.vm import SongPlayer, first_mismatch

CACHE = Path(__file__).resolve().parent.parent / ".tunecache"
TUNES = (
    "MUSICIANS/C/Cadaver/Metal_Warrior_Title_Remix.sid",
    "MUSICIANS/H/Hermit/Csillag_szazketto_kettoskereszt.sid",
    "MUSICIANS/I/Ilkke/Crosseyed.sid",
)


def check(sid: Path, out: Path, ticks: int, forbid) -> str:
    """One tune's report line."""
    lift_main([str(sid), "-o", str(out)])
    text = out.read_text()
    canonical = songio.dumps(songio.loads(text)) == text
    player = SongPlayer(songio.loads(text))
    ours = player.render_grid(ticks)
    native = backends.native_player(sid.read_bytes(), None)
    theirs = np.asarray(native.render_grid(ticks), dtype=np.uint8)
    found = sorted({w for w in forbid if re.search(rf"(?<![\w]){re.escape(w)}\b", text)})
    return (
        f"round trip {'ok' if canonical else 'FAILS'}; "
        f"first mismatch: {first_mismatch(ours, theirs, player.starts)}; "
        f"forbidden: {found or 'none'}"
    )


def main(argv=None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--ticks", type=int, default=3007)
    parser.add_argument("--forbid", nargs="*", default=[])
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for k, tune in enumerate(TUNES, start=1):
        sid = CACHE / tune
        start = time.monotonic()
        line = check(sid, args.out / f"{sid.stem}.yaml", args.ticks, args.forbid)
        print(
            f"[{k}/{len(TUNES)}] {sid.stem} ({time.monotonic() - start:.0f}s): {line}", flush=True
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
