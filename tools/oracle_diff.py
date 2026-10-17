"""Compare a tune's native player, and optionally a song, with the sidtrace 6502 oracle.

    python tools/oracle_diff.py TUNE.sid [--song song.yaml] [--patch OFFSET=HEX ...]

``--patch`` overwrites file bytes so the native player, lifter and oracle play one patched tune
(written to ``out/oracle/``); the oracle's lead over the native grid is its driver init frames.
"""

import argparse
from pathlib import Path

import numpy as np
from pysidtracker.testing import oracle_grid

from unitracker import backends, songio
from unitracker.vm import SongPlayer, first_mismatch

OUT = Path("out/oracle")


def patched(sid: Path, patches: list) -> Path:
    """``sid`` with each ``OFFSET=HEX`` patch applied, written under ``OUT``."""
    if not patches:
        return sid
    data = bytearray(sid.read_bytes())
    for patch in patches:
        offset, hexbytes = patch.split("=")
        new = bytes.fromhex(hexbytes)
        start = int(offset, 0)
        data[start : start + len(new)] = new
    tag = "_".join(p.replace("=", "-") for p in patches)
    path = OUT / f"{sid.stem}_{tag}.sid"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(data))
    return path


def lead_of(oracle: np.ndarray, native: np.ndarray, most: int) -> tuple:
    """``(lead, first differing (tick, register))`` of the lead that matches longest, the
    first frame excused (the driver's init residue)."""
    best = (0, (0, 0))
    for lead in range(most + 1):
        seg = oracle[lead : lead + len(native)]
        diff = np.argwhere(seg[1:] != native[1 : len(seg)])
        if not diff.size:
            return lead, None
        first = (int(diff[0][0]) + 1, int(diff[0][1]))
        if first[0] > best[1][0]:
            best = (lead, first)
    return best


def main(argv=None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sid", type=Path)
    parser.add_argument("--song", type=Path)
    parser.add_argument("--patch", action="append", default=[])
    parser.add_argument("--ticks", type=int, default=3007)
    parser.add_argument("--subtune", type=int)
    parser.add_argument("--lead-max", type=int, default=8)
    args = parser.parse_args(argv)
    sid = patched(args.sid, args.patch)
    print("tune:", sid)
    native = np.asarray(
        backends.native_player(sid.read_bytes(), args.subtune).render_grid(args.ticks),
        dtype=np.uint8,
    )
    frames = args.ticks + args.lead_max
    oracle = np.asarray(
        oracle_grid(
            sid.resolve(), oracle_cache=OUT / "csv", seconds=frames // 50 + 2, frames=frames
        ),
        dtype=np.uint8,
    )[:, :25]
    lead, first = lead_of(oracle, native, args.lead_max)
    print("oracle lead:", lead)
    print("native vs oracle, first difference:", first)
    if args.song:
        player = SongPlayer(songio.load(args.song))
        ours = player.render_grid(args.ticks)
        theirs = oracle[lead : lead + args.ticks]
        print("song vs oracle, first mismatch:", first_mismatch(ours, theirs, player.starts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
