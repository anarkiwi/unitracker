"""The SID register write order that keeps what tunes' players command, over a corpus: the tunes'
writes are captured here and measured by :mod:`vagrant_moses.sidorder` (each frame's event
patterns, the pattern every per-voice order of the kept writes gives, and how often each differs
from the native one).

``python tools/writeorder.py TUNES.csv|TUNE.sid ... [--frames N] [--top K] [--out F.json]``; see
docs/writeorder.md.
"""

import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from pysidtracker import SidImage
from pysidtracker.emu import call_play, observe_data_access
from pysidtracker.jobs import deadline
from pysidtracker.oracle import boot_flat, cached_sidtrace, play_source, raise_cycles
from pysidtracker.registers import cycles_per_frame_for_flags
from vagrant_moses import sidorder as S

import shared

REGS, ORDERS, PAIRS, GATE, TEST = S.REGS, S.ORDERS, S.PAIRS, S.GATE, S.TEST
events, kept, text = S.events, S.kept, S.text


def jennings_writes(image: SidImage, subtune: int, frames: int, cpf: int) -> tuple:
    """``(init writes, [(start, frame writes)])`` of the in-process emulator in absolute cycles: a
    call starts ``cpf`` after the last, or after the last write of a call that overran that."""
    log, box = [], {}

    def setup(mpu, _):
        box["mpu"] = mpu

        def write(addr, value):
            reg = (addr - 0xD400) & 0x1F
            if 0xD400 <= addr < 0xD800 and reg < REGS:
                log.append((box["mpu"].processorCycles, reg, value))

        observe_data_access(mpu, on_write=write)

    _, mpu, mem, play = boot_flat(image, subtune, setup=setup)
    init, start, out = np.array(log, np.int64).reshape(-1, 3), mpu.processorCycles, []
    for _ in range(frames):
        log.clear()
        begin = mpu.processorCycles
        call_play(mpu, mem, play)
        w = np.array(log, np.int64).reshape(-1, 3)
        w[:, 0] += start - begin
        out.append((start, w))
        start = max(start + cpf, int(w[-1, 0]) + 1 if len(w) else 0)
    return init, out


def sidtrace_writes(path: Path, subtune: int, frames: int, cache: Path) -> tuple:
    """``(init writes, [(start, frame writes)])`` of the sidtrace oracle, a frame from one raise
    of the play source to the next."""
    rows = cached_sidtrace(
        path,
        cache / f"{path.stem}-{subtune}.csv.zst",
        calls=frames,
        song=subtune + 1,
        seconds=frames // 50 + 2,
    )
    raised = Counter(r.value for r in rows if r.chip is None and isinstance(r.value, str))
    raises = raise_cycles(rows, play_source(rows, 0) or raised.most_common(1)[0][0])
    w = np.array(
        [(r.cycle, r.reg, r.value) for r in rows if r.chip == 0 and r.reg is not None],
        np.int64,
    ).reshape(-1, 3)
    w = w[w[:, 1] < REGS]
    cut = np.searchsorted(w[:, 0], raises[: frames + 1])
    return w[: cut[0]], [(int(raises[f]), w[cut[f] : cut[f + 1]]) for f in range(len(cut) - 1)]


def capture(row: dict, hvsc, args) -> tuple:
    """``(init writes, [(start, frame writes)])`` of a tune row, cycles from 1 at its first."""
    path = shared.tune(row["path"], hvsc)
    image = SidImage.from_bytes(path.read_bytes())
    head, subtune = image.header, int(row.get("subtune") or 0)
    cpf = cycles_per_frame_for_flags(head.flags)
    backend = args.backend
    if backend == "auto":
        backend = "jennings" if head.play_address else "sidtrace"
    if backend == "jennings":
        init, frames = jennings_writes(image, subtune, args.frames, cpf)
    else:
        init, frames = sidtrace_writes(path, subtune, args.frames, args.cache)
    base = np.array([min([*init[:1, 0], *(s for s, _ in frames[:1])]) - 1, 0, 0], np.int64)
    return init - base, [(int(s - base[0]), w - base) for s, w in frames]


def measure(row: dict, hvsc, args) -> dict:
    """One tune's frames and, per order (:data:`ORDERS`), the (frame, voice) cases whose event
    pattern it changes, with each distinct ``(native, ordered)`` pattern pair and its count."""
    init, frames = capture(row, hvsc, args)
    changed, pairs = S.pattern_changes(init, frames)
    return {
        "frames": len(frames),
        "changed": changed.tolist(),
        "pairs": [[a, b, i, n] for (a, b, i), n in pairs.items()],
    }


def capture_arguments(ap):
    """``ap`` with the options :func:`capture` and a corpus run take."""
    ap.add_argument("--backend", choices=("auto", "jennings", "sidtrace"), default="auto")
    ap.add_argument("--frames", type=int, default=3000)
    ap.add_argument("--cache", type=Path, default=Path("out/writeorder/sidtrace"))
    ap.add_argument("--timeout", type=float, default=600, help="seconds per tune")
    ap.add_argument("--jobs", type=int, default=max(1, len(os.sched_getaffinity(0)) - 2))
    ap.add_argument("--out", type=Path, help="write each tune's record as JSON")
    return ap


def timed(row: dict, hvsc, args) -> dict:
    """:func:`measure` of a tune row within ``args.timeout`` seconds."""
    with deadline(args.timeout):
        return measure(row, hvsc, args)


def summary(recs: list, top: int) -> str:  # pylint: disable=too-many-locals
    """The orders by how many (frame, voice) cases they change, per player, then the commonest
    distinct ``native -> ordered`` pattern pairs and the orders that give each."""
    ok = [r for r in recs if "changed" in r]
    players = sorted({r.get("player", "") for r in ok})
    by = {p: sum(np.array(r["changed"]) for r in ok if r.get("player", "") == p) for p in players}
    total = sum(by.values())
    lines = [
        f"{len(ok)} tunes ({len(recs) - len(ok)} failed or skipped), "
        f"{sum(r['frames'] for r in ok)} frames",
        "  changed " + " ".join(f"{p[:14]:>14}" for p in players) + "  order",
    ]
    for i in np.argsort(total, kind="stable"):
        per = " ".join(f"{int(by[p][i]):14}" for p in players)
        lines.append(f"  {int(total[i]):7} {per}  {','.join(ORDERS[i])}")
    pairs, orders = Counter(), defaultdict(set)
    for r in ok:
        for a, b, i, n in r["pairs"]:
            pairs[(r.get("player", ""), a, b)] += n
            orders[(r.get("player", ""), a, b)].add(i)
    lines.append(f"  {len(pairs)} distinct (player, native, ordered) pattern pairs; commonest:")
    for key, n in pairs.most_common(top):
        lines.append(f"  {n:8}  {key[0]:14} {key[1]:32} -> {key[2]:32} {len(orders[key])} orders")
    return "\n".join(lines)


def main(argv=None) -> int:
    """Parse the command line, measure the tunes in parallel and print the summary."""
    ap = capture_arguments(shared.corpus_parser(__doc__))
    ap.add_argument("--top", type=int, default=30, help="pattern pairs printed")
    args = ap.parse_args(argv)
    rows = shared.rows(args.sources, args.player)
    tasks = [(r, args.hvsc, args) for r in rows]
    print(summary(shared.run_corpus(timed, rows, tasks, args.jobs, args.out), args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
