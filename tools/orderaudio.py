"""How a write order's changed (frame, voice) event patterns sound: each voice played alone at a
fixed pitch with its own gate, test, AD and SR writes at their native cycles, the native frame
against the order's, and how long and by how much their envelopes differ.

``python tools/orderaudio.py TUNES.csv|TUNE.sid ... [--order sr,ad,ctrl:2,ctrl]``
"""

import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from pylibresidfp import SID
from pysidtracker.audio import write_wav
from pysidtracker.jobs import deadline
from vagrant_moses import memmap as mm
from vagrant_moses import sidorder as S

import shared
import writeorder as W

RATE = 44100.0


class Replay:
    """A chip playing a timeline's stream with audio, for the state at each frame start in turn."""

    def __init__(self, tl):
        self.tl, self.frame = tl, 0
        self.sid = SID(clock_hz=tl.clock, sampling_hz=RATE, method="resample")
        self.sid.run(tl.init, tl.starts[0])

    def state(self, frame: int) -> bytes:
        """The chip's saved state at the start of ``frame`` (frames in ascending order)."""
        while self.frame < frame:
            self.sid.run(self.tl.frames[self.frame][1], self.tl.starts[self.frame + 1])
            self.frame += 1
        return self.sid.save_state()


def render(snapshot: bytes, writes: np.ndarray, until: int) -> np.ndarray:
    """The audio of ``writes`` played from a chip restored to ``snapshot`` to ``until``."""
    sid = SID(clock_hz=mm.PAL.clock_hz, sampling_hz=RATE, method="resample")
    sid.restore_state(snapshot)
    return sid.run(writes, until)


def measure(row: dict, hvsc, args) -> dict:
    """Each (frame, voice) the order changes: its pattern pair and how the envelopes differ over
    that frame and the ``args.horizon`` - 1 after it (the same native writes in both)."""
    init, frames = W.capture(row, hvsc, args)
    order, out, heard = tuple(args.order.split(",")), [], set()
    tl = S.Timeline(init, frames, mm.PAL.clock_hz)
    replay = Replay(tl) if args.wav else None
    if args.wav:
        args.wav.mkdir(parents=True, exist_ok=True)
    for case, (_, natives, reordered, until) in S.order_cases(tl, order, args.horizon):
        key = (case["native"], case["ordered"])
        if args.wav and case["ms"] and key not in heard:
            heard.add(key)
            name = f"{Path(row['path']).stem}-f{case['frame']}v{case['voice']}.wav"
            gap = np.zeros(int(RATE / 10), np.int16)
            snapshot = replay.state(case["frame"])
            pair = [render(snapshot, natives, until), gap, render(snapshot, reordered, until)]
            write_wav(args.wav / name, np.concatenate(pair).astype(np.int16), RATE)
            case["wav"] = name
        out.append(case)
    return {"cases": out}


def timed(row: dict, hvsc, args) -> dict:
    """:func:`measure` of a tune row within ``args.timeout`` seconds."""
    with deadline(args.timeout):
        return measure(row, hvsc, args)


def summary(recs: list) -> str:
    """Per pattern pair: cases, and the median and largest time the envelopes differ and the
    largest difference against full scale."""
    groups = defaultdict(list)
    for r in recs:
        for c in r.get("cases", []):
            key = (r.get("player", ""), c["native"], c["ordered"])
            groups[key].append((c["ms"], c["dbfs"]))
    cases = [x for g in groups.values() for x in g]
    lines = [f"{len(cases)} changed (frame, voice) cases in {len(groups)} pattern pairs"]
    if cases:
        ms, dbfs = np.array(cases).T
        lines.append(
            f"  identical envelopes in {(ms == 0).sum()} cases; where they differ, for median "
            f"{np.median(ms[ms > 0]):.1f} ms (max {ms.max():.1f}) by up to median "
            f"{np.median(dbfs[ms > 0]):.1f} dBFS (max {dbfs.max():.1f})"
        )
    lines.append("  cases  same  ms med   ms max  dBFS med dBFS max  native -> ordered")
    for key, g in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:40]:
        ms, dbfs = np.array(g).T
        loud = ms > 0
        med = np.median(dbfs[loud]) if loud.any() else -math.inf
        lines.append(
            f"  {len(g):5} {(~loud).sum():5} {np.median(ms):7.1f} {ms.max():8.1f} {med:8.1f}"
            f" {dbfs.max():8.1f}  {key[1]} -> {key[2]}"
        )
    return "\n".join(lines)


def main(argv=None) -> int:
    """Parse the command line, measure the tunes in parallel and print the summary."""
    ap = W.capture_arguments(shared.corpus_parser(__doc__))
    ap.add_argument("--order", default="sr,ad,ctrl:2,ctrl")
    ap.add_argument("--horizon", type=int, default=10, help="frames compared from each case")
    ap.add_argument("--wav", type=Path, help="write the first differing case of each pair here")
    args = ap.parse_args(argv)
    rows = shared.rows(args.sources, args.player)
    tasks = [(r, args.hvsc, args) for r in rows]
    print(summary(shared.run_corpus(timed, rows, tasks, args.jobs, args.out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
