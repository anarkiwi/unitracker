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

import shared
import writeorder as W

PITCH = 0x1D45
SAW, KEEP = 0x20, 0x09
RATE = 44100.0


def voice_writes(w: np.ndarray, voice: int) -> np.ndarray:
    """``voice``'s control, AD and SR writes of ``w`` moved to voice 1, the control's waveform a
    sawtooth and sync and ring off."""
    out = w[(w[:, 1] >= 7 * voice + 4) & (w[:, 1] <= 7 * voice + 6)].copy()
    out[:, 1] -= 7 * voice
    ctrl = out[:, 1] == 4
    out[ctrl, 2] = out[ctrl, 2] & KEEP | SAW
    return out


def ordered(w: np.ndarray, order: tuple) -> np.ndarray:
    """Voice-1 writes ``w`` as ``order`` keeps and arranges them, in the native cycle slots."""
    mine = [(int(r), int(v)) for _, r, v in w.tolist()]
    kept = W.kept(mine, order)
    slots = sorted(int(c) for c, r, v in w.tolist() if (r, v) in set(kept))[: len(kept)]
    return np.array([(c, r, v) for c, (r, v) in zip(slots, kept)], np.int64).reshape(-1, 3)


def levels(trace, start: int, until: int) -> np.ndarray:
    """The envelope level each cycle from ``start`` to ``until`` of an envelope trace."""
    out = np.empty(until - start, np.int64)
    edges = np.append(np.asarray(trace.cycle) - start, until - start)
    for (a, b), level in zip(zip(edges[:-1], edges[1:]), trace.envelope):
        out[max(a, 0) : b] = level
    return out


def compare(a: np.ndarray, b: np.ndarray, clock: float) -> dict:
    """How two envelope level series differ: for how long (ms), the largest difference against
    full scale (dBFS), and the largest level ratio (dB, a level of 0 counted as 1)."""
    gap = np.abs(a - b)
    ratio = np.abs(20 * np.log10(np.maximum(a, 1) / np.maximum(b, 1)))
    return {
        "ms": 1000.0 * int((gap > 0).sum()) / clock,
        "dbfs": 20 * math.log10(gap.max() / 255) if gap.any() else -math.inf,
        "db": float(ratio.max()) if len(ratio) else 0.0,
    }


def render(sid: SID, writes: np.ndarray, until: int) -> np.ndarray:
    """The audio of ``writes`` played from a copy of ``sid`` to ``until``."""
    return sid.copy().run(writes, until)


def chips(clock: float) -> list:
    """One chip per voice playing voice 1 at :data:`PITCH` and full volume."""
    out = []
    for _ in range(3):
        sid = SID(clock_hz=clock, sampling_hz=RATE, method="resample")
        sid.run([(0, 24, 15), (0, 0, PITCH & 0xFF), (0, 1, PITCH >> 8)], 1)
        out.append(sid)
    return out


def measure(row: dict, hvsc, args) -> dict:  # pylint: disable=too-many-locals
    """Each (frame, voice) the order changes: its pattern pair and how the envelopes differ over
    that frame and the ``args.horizon`` - 1 after it (the same native writes in both)."""
    init, frames = W.capture(row, hvsc, args)
    order, horizon = tuple(args.order.split(",")), args.horizon
    sids, regs, out, heard = chips(mm.PAL.clock_hz), np.zeros(W.REGS, np.int64), [], set()
    if args.wav:
        args.wav.mkdir(parents=True, exist_ok=True)
    regs[init[:, 1]] = init[:, 2]
    for v, sid in enumerate(sids):
        sid.run(voice_writes(init[init[:, 0] >= sid.cycle], v), frames[0][0])
    for f, (_, w) in enumerate(frames[:-horizon]):
        for v, sid in enumerate(sids):
            mine = voice_writes(w, v)
            start = tuple(int(x) for x in regs[7 * v + 4 : 7 * v + 7])
            pairs = [(r, x) for _, r, x in mine.tolist()]
            native = W.events(start, pairs)
            new = W.events(start, W.kept(pairs, order))
            if new != native:
                tail = [voice_writes(frames[g][1], v) for g in range(f + 1, f + horizon)]
                until = frames[f + horizon][0]
                a = sid.envelope_trace(0, np.concatenate([mine, *tail]), until)
                b = sid.envelope_trace(0, np.concatenate([ordered(mine, order), *tail]), until)
                case = {"frame": f, "voice": v, "native": W.text(native), "ordered": W.text(new)}
                case |= compare(
                    levels(a, sid.cycle, until), levels(b, sid.cycle, until), mm.PAL.clock_hz
                )
                key = (case["native"], case["ordered"])
                if args.wav and case["ms"] and key not in heard:
                    heard.add(key)
                    name = f"{Path(row['path']).stem}-f{f}v{v}.wav"
                    natives = np.concatenate([mine, *tail])
                    reordered = np.concatenate([ordered(mine, order), *tail])
                    gap = np.zeros(int(RATE / 10), np.int16)
                    pair = [render(sid, natives, until), gap, render(sid, reordered, until)]
                    write_wav(args.wav / name, np.concatenate(pair).astype(np.int16), RATE)
                    case["wav"] = name
                out.append(case)
            sid.run(mine, frames[f + 1][0])
        regs[w[:, 1]] = w[:, 2]
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
