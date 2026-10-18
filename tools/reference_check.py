"""Lift the reference tunes, check each round-trips canonically and plays its native grid.

    python tools/reference_check.py OUTDIR [--ticks N] [--forbid WORD ...] [--tunes SID ...]
        [--plain]

Writes OUTDIR/<tune>.yaml per tune and prints its round trip, first mismatch and forbidden words;
``--tunes`` checks other tunes; ``--plain`` lifts them without the expression conversion and
prints the commands they write and the first mismatch of the lift's replay.
"""

import argparse
import contextlib
import re
import time
from pathlib import Path

import numpy as np

from unitracker.cli import lift_main
from unitracker.format import songio
from unitracker.lift import backends
from unitracker.player.vm import SongPlayer, event_commands, first_mismatch

CACHE = Path(__file__).resolve().parent.parent / ".tunecache"
TUNES = (
    "MUSICIANS/C/Cadaver/Metal_Warrior_Title_Remix.sid",
    "MUSICIANS/H/Hermit/Csillag_szazketto_kettoskereszt.sid",
    "MUSICIANS/I/Ilkke/Crosseyed.sid",
)


def native_grid(sid: Path, ticks: int) -> np.ndarray:
    """The native player's register grid of ``ticks`` ticks."""
    native = backends.native_player(sid.read_bytes(), None)
    return np.asarray(native.render_grid(ticks), dtype=np.uint8)


def plain_check(sid: Path, ticks: int) -> str:
    """One tune's report line for its lift without expressions: its tempo commands as written,
    and the first mismatch of its replay."""
    ir = backends.lift(sid.read_bytes(), expressions=False)
    writer = songio._Writer(ir)  # pylint: disable=protected-access
    written = set()
    for events in ir["phrases"].values():
        for event in events:
            for cmd in event_commands(event):
                with contextlib.suppress(songio.SongFormatError, KeyError):
                    written.add(writer.command(*cmd))
    tempos = sorted(c for c in written if c and c.split(":")[0] in ("tempo", "vtempo"))
    player = SongPlayer(ir)
    ours = player.render_grid(ticks)
    return (
        f"tempo commands {tempos}; "
        f"first mismatch: {first_mismatch(ours, native_grid(sid, ticks), player.starts)}"
    )


def check(sid: Path, out: Path, ticks: int, forbid) -> str:
    """One tune's report line."""
    lift_main([str(sid), "-o", str(out)])
    text = out.read_text()
    canonical = songio.dumps(songio.loads(text)) == text
    player = SongPlayer(songio.loads(text))
    ours = player.render_grid(ticks)
    theirs = native_grid(sid, ticks)
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
    parser.add_argument("--tunes", nargs="*", type=Path)
    parser.add_argument("--plain", action="store_true")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    tunes = args.tunes or [CACHE / t for t in TUNES]
    for k, sid in enumerate(tunes, start=1):
        start = time.monotonic()
        if args.plain:
            line = plain_check(sid, args.ticks)
        else:
            line = check(sid, args.out / f"{sid.stem}.yaml", args.ticks, args.forbid)
        print(
            f"[{k}/{len(tunes)}] {sid.stem} ({time.monotonic() - start:.0f}s): {line}", flush=True
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
