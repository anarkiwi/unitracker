"""The SID register write order that best keeps what tunes' players audibly command: every candidate
order scored on how many (frame, voice) envelopes it changes audibly against the native writes.

``python tools/writeorder.py TUNES.csv|TUNE.sid ... [--frames N] [--out F.json]``; see
docs/writeorder.md.
"""

import json
import os
import sys
from collections import Counter, defaultdict
from itertools import permutations, product
from pathlib import Path

import numpy as np
from pylibresidfp import SID
from pysidtracker import SidImage
from pysidtracker.emu import call_play, observe_data_access
from pysidtracker.jobs import deadline, imap
from pysidtracker.oracle import boot_flat, cached_sidtrace, play_source, raise_cycles
from pysidtracker.registers import cycles_per_frame_for_flags, sid_model_for_flags
from tqdm import tqdm
from vagrant_moses import memmap as mm

import shared

REGS = 25
OTHER = ("freq_lo", "freq_hi", "pw_lo", "pw_hi")
VOICE_REG = {"freq_lo": 0, "freq_hi": 1, "pw_lo": 2, "pw_hi": 3, "ctrl": 4, "ad": 5, "sr": 6}
GLOBAL = (("fc_lo", 21), ("fc_hi", 22), ("res_filt", 23), ("mode_vol", 24))
ENV = ("ctrl:2", "ad", "sr", "ctrl")
CONTROLLED = (
    "envelope", "state", "next_state", "gate", "rate", "exponential_counter",
    "exponential_counter_period", "counter_enabled", "state_pipeline", "envelope_pipeline",
    "exponential_pipeline", "attack", "decay", "sustain", "release",
)  # fmt: skip
VERDICTS = ("same", "noise", "drift", "shape", "timing")
AUDIBLE = ("shape", "timing")
TRACED = 3


def item(token: str, voice: int | None) -> tuple:
    """``(register, k)`` of a spec token, ``name`` or ``name:k``, for ``voice`` (None: global)."""
    name, _, k = token.partition(":")
    reg = dict(GLOBAL)[name] if voice is None else 7 * voice + VOICE_REG[name]
    return reg, int(k or 1)


def candidates() -> list:
    """Every candidate order as ``(spec text, ((register, k), ...))``, in a fixed order: each
    voice's gate and envelope writes in any order (the last control write after the one before
    it, which may be dropped), before or after its frequency and pulse writes, the voices one after
    another or interleaved register by register, and the song registers first or last."""
    envs = [p for p in permutations(ENV) if p.index("ctrl:2") < p.index("ctrl")]
    envs += list(permutations(ENV[1:]))
    out = []
    for env, env_first, by_voice, songs_first in product(envs, (True, False), *[(True, False)] * 2):
        per_voice = list(env) + list(OTHER) if env_first else list(OTHER) + list(env)
        if by_voice:
            body = [item(t, v) for v in range(3) for t in per_voice]
            text = f"voice({','.join(per_voice)})"
        else:
            body = [item(t, v) for t in per_voice for v in range(3)]
            text = f"interleave({','.join(per_voice)})"
        songs = [item(name, None) for name, _ in GLOBAL]
        order = songs + body if songs_first else body + songs
        names = ",".join(name for name, _ in GLOBAL)
        out.append((f"{names},{text}" if songs_first else f"{text},{names}", tuple(order)))
    return out


UNIVERSE = sorted({i for _, order in candidates() for i in order})
ITEM_REG = np.array([r for r, _ in UNIVERSE], np.int64)
ENV_ITEMS = [np.flatnonzero((ITEM_REG >= 7 * v + 4) & (ITEM_REG <= 7 * v + 6)) for v in range(3)]
BIG = np.iinfo(np.int64).max


def positions(cands: list) -> np.ndarray:
    """``(candidates, items)``: each :data:`UNIVERSE` item's place in each order (a large number
    where an order drops it)."""
    at = {it: j for j, it in enumerate(UNIVERSE)}
    out = np.full((len(cands), len(UNIVERSE)), len(UNIVERSE), np.int64)
    for c, (_, order) in enumerate(cands):
        out[c, [at[i] for i in order]] = np.arange(len(order))
    return out


def native_index(writes: np.ndarray) -> np.ndarray:
    """Per :data:`UNIVERSE` item, the index of the write it names in ``writes``, -1 if none."""
    at = defaultdict(list)
    for i, reg in enumerate(writes[:, 1].tolist()):
        at[reg].append(i)
    return np.array([at[r][-k] if len(at[r]) >= k else -1 for r, k in UNIVERSE], np.int64)


def slot_cycles(writes: np.ndarray, pos: np.ndarray) -> tuple:
    """``(cycles, index)``: per candidate and :data:`UNIVERSE` item, the cycle the candidate
    writes the item's write at (the frame's slots, in the candidate's order; -1 where it writes
    none), and :func:`native_index` of ``writes``."""
    idx = native_index(writes)
    kept = (idx >= 0) & (pos < len(UNIVERSE))
    order = np.argsort(np.where(kept, pos, BIG), axis=1, kind="stable")
    rank = np.argsort(order, axis=1, kind="stable")
    own = np.where(idx >= 0, writes[np.maximum(idx, 0), 0], BIG)
    slots = np.sort(np.where(kept, own, BIG), axis=1)
    return np.where(kept, np.take_along_axis(slots, rank, axis=1), -1), idx


def envelope_rows(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    cycles: np.ndarray, idx: np.ndarray, writes: np.ndarray, voice: int, regs: tuple, width: int
) -> np.ndarray:
    """``(candidates, width, 3)``: each candidate's :func:`effective` envelope writes of
    ``voice`` from ``regs``, in cycle order, padded with -1."""
    items = ENV_ITEMS[voice]
    cyc = cycles[:, items]
    order = np.argsort(np.where(cyc >= 0, cyc, BIG), axis=1, kind="stable")
    cyc, it = np.take_along_axis(cyc, order, axis=1), items[order]
    reg, val = ITEM_REG[it], writes[np.maximum(idx[it], 0), 2]
    held = [np.full(len(cyc), r, np.int64) for r in regs]
    out = np.full((len(cyc), width, 3), -1, np.int64)
    fill = np.zeros(len(cyc), np.int64)
    for p in range(cyc.shape[1]):
        r, v = reg[:, p] - 7 * voice - 4, val[:, p]
        now = np.choose(r, [held[0], held[1], held[2]])
        eff = (cyc[:, p] >= 0) & np.where(r == 0, (v & 1) != now, v != now)
        rows = np.flatnonzero(eff)
        out[rows, fill[rows]] = np.column_stack([cyc[rows, p], reg[rows, p], v[rows]])
        fill[rows] += 1
        for k in range(3):
            held[k] = np.where(eff & (r == k), v & 1 if k == 0 else v, held[k])
    return out


def envelope_writes(writes: np.ndarray, voice: int) -> np.ndarray:
    """The writes that reach ``voice``'s envelope generator: its control, AD and SR."""
    return writes[(writes[:, 1] >= 7 * voice + 4) & (writes[:, 1] <= 7 * voice + 6)]


def effective(writes: np.ndarray, voice: int, regs: tuple) -> tuple:
    """``(writes, regs)``: the writes among ``writes`` that change ``voice``'s envelope generator
    from ``regs`` (gate bit, AD, SR) -- a control write that keeps the gate or an AD or SR write of
    the value it holds is a no-op for it -- and the registers they leave."""
    gate, ad, sr = regs
    keep = []
    for i, (_, reg, value) in enumerate(writes.tolist()):
        if reg == 7 * voice + 4 and value & 1 != gate:
            gate = value & 1
        elif reg == 7 * voice + 5 and value != ad:
            ad = value
        elif reg == 7 * voice + 6 and value != sr:
            sr = value
        else:
            continue
        keep.append(i)
    return writes[keep], (gate, ad, sr)


def displacement(native: np.ndarray, new: np.ndarray) -> int:
    """The most cycles any write kept from ``native`` moved in ``new`` (pairing each register's
    writes from the last)."""
    most = 0
    for reg in np.unique(new[:, 1]):
        a, b = native[native[:, 1] == reg, 0], new[new[:, 1] == reg, 0]
        most = max(most, int(np.abs(a[len(a) - len(b) :] - b).max()))
    return most


def expand(phases, table: dict) -> tuple:
    """``(ids, times)`` of :class:`pylibresidfp.Phases`: per phase, the id in ``table`` of its
    envelope levels and states, and the cycles of its events (-1 past its last)."""
    pieces, events = phases.pieces, phases.events
    width = int(pieces[:, 3].max())
    ids = np.empty(phases.count, np.int64)
    times = np.full((phases.count, width), -1, np.int64)
    for k0, k1, first, count in pieces.tolist():
        ev = events[first : first + count]
        ids[k0:k1] = table.setdefault(ev[:, 2:].tobytes(), len(table))
        times[k0:k1, :count] = ev[:, 0] + ev[:, 1] * np.arange(k1 - k0)[:, None]
    return ids, times


def verdict(native, new, moved: int) -> str:
    """The verdict on a voice's envelope from every phase of its rate counter, ``native`` and
    ``new`` its :class:`pylibresidfp.Phases`: ``noise`` where each phase takes the same levels
    and states with every step within ``moved`` cycles; ``drift`` where the two only pair up
    across phases that way (a reorder no write can command: the rate counter's phase is not the
    player's); else ``shape`` where the levels and states differ, ``timing`` where a step moves
    further (the ADSR delay)."""
    table = {}
    (ia, ta), (ib, tb) = expand(native, table), expand(new, table)
    width = max(ta.shape[1], tb.shape[1])
    ta, tb = (np.pad(t, ((0, 0), (0, width - t.shape[1])), constant_values=-1) for t in (ta, tb))

    def close(a, b):
        return (ia[a] == ib[b]) & (np.abs(ta[a] - tb[b]) <= moved).all(axis=1)

    every = np.arange(len(ia))
    if close(every, every).all():
        return "noise"
    sa, sb = (np.lexsort([*t.T[::-1], i]) for i, t in ((ia, ta), (ib, tb)))
    if close(sa, sb).all():
        return "drift"
    return "shape" if (ia[sa] != ib[sb]).any() else "timing"


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
    """``(model, clock_hz, init writes, frames)`` of a tune row, cycles from the first write."""
    path = shared.tune(row["path"], hvsc)
    image = SidImage.from_bytes(path.read_bytes())
    head, subtune = image.header, int(row.get("subtune") or 0)
    cpf = cycles_per_frame_for_flags(head.flags)
    ntsc = cpf == mm.NTSC.lines * mm.NTSC.cycles_per_line
    backend = args.backend
    if backend == "auto":
        backend = "jennings" if head.play_address else "sidtrace"
    if backend == "jennings":
        init, frames = jennings_writes(image, subtune, args.frames, cpf)
    else:
        init, frames = sidtrace_writes(path, subtune, args.frames, args.cache)
    base = np.array([min([frames[0][0], *init[:1, 0]]) if frames else 0, 0, 0], np.int64)
    frames = [(int(s - base[0]), w - base) for s, w in frames]
    clock = (mm.NTSC if ntsc else mm.PAL).clock_hz
    return sid_model_for_flags(head.flags), clock, init - base, frames


class Judge:  # pylint: disable=too-few-public-methods
    """Verdicts on one voice's envelope through a frame and the :data:`TRACED` - 1 after it, from
    every phase of its rate counter, cached on the rest of its envelope state and the writes that
    reach it."""

    def __init__(self):
        self.cache, self.native, self.traced = {}, {}, 0

    def __call__(self, sid: SID, state: tuple, native: np.ndarray, new: np.ndarray, until: int):
        """The verdict on ``new`` against ``native`` (writes relative to the chip's cycle) for the
        voice ``state[0]`` with the :data:`CONTROLLED` state ``state[1:]``, to ``until``."""
        key = (state, native.tobytes(), new.tobytes(), until)
        if key not in self.cache:
            at = np.array([sid.cycle, 0, 0], np.int64)
            mine = key[:2] + (sid.cycle,)
            if mine not in self.native:
                self.native = {mine: sid.envelope_phases(state[0], native + at, sid.cycle + until)}
            a, b = self.native[mine], sid.envelope_phases(state[0], new + at, sid.cycle + until)
            self.cache[key] = verdict(a, b, displacement(native, new))
            self.traced += a.count
        return self.cache[key]


def measure(row: dict, hvsc, args) -> dict:  # pylint: disable=too-many-locals
    """One tune's verdict counts per candidate: each frame's (frame, voice) envelopes under the
    candidate's order against the native writes."""
    model, clock, init, frames = capture(row, hvsc, args)
    if len(frames) <= TRACED:
        return {"skipped": "too few frames"}
    cands = candidates()
    pos, judge = positions(cands), Judge()
    counts = np.zeros((len(cands), len(VERDICTS)), np.int64)
    sid = SID(model, clock_hz=clock, sampling_hz=8000.0)
    sid.run(init, frames[0][0])
    for f in range(len(frames) - TRACED):
        start, w = frames[f]
        if len(w):
            w = w - [start, 0, 0]
            fields, (cycles, idx) = sid.voices(), slot_cycles(w, pos)
            ahead = np.concatenate([frames[f + j][1] for j in range(1, TRACED)]) - [start, 0, 0]
            until = frames[f + TRACED][0] - start
            for v in range(3):
                regs = (
                    int(fields["gate"][v]),
                    int(fields["attack"][v]) << 4 | int(fields["decay"][v]),
                    (int(fields["sustain"][v]) & 0xF) << 4 | int(fields["release"][v]),
                )
                native, after = effective(envelope_writes(w, v), v, regs)
                if not native.size:
                    counts[:, 0] += 1
                    continue
                width = max(len(ENV_ITEMS[v]), len(native))
                rows = envelope_rows(cycles, idx, w, v, regs, width)
                padded = np.full((width, 3), -1, np.int64)
                padded[: len(native)] = native
                same = (rows == padded).all(axis=(1, 2))
                counts[same, 0] += 1
                if same.all():
                    continue
                tail, _ = effective(envelope_writes(ahead, v), v, after)
                state = (v, *(int(fields[k][v]) for k in CONTROLLED))
                full = np.concatenate([native, tail])
                distinct, inverse = np.unique(
                    rows[~same].reshape(-1, 3 * width), axis=0, return_inverse=True
                )
                found = [
                    VERDICTS.index(
                        judge(sid, state, full, np.concatenate([m[m[:, 0] >= 0], tail]), until)
                    )
                    for m in distinct.reshape(-1, width, 3)
                ]
                np.add.at(counts, (np.flatnonzero(~same), np.array(found)[inverse.ravel()]), 1)
            w = w + [start, 0, 0]
        sid.run(w, max([frames[f + 1][0], *w[-1:, 0]]))
    return {"frames": len(frames) - TRACED, "traced": judge.traced, "counts": counts.tolist()}


def timed(row: dict, hvsc, args) -> dict:
    """:func:`measure` of a tune row within ``args.timeout`` seconds."""
    with deadline(args.timeout):
        return measure(row, hvsc, args)


def summary(recs: list, top: int) -> str:
    """The candidates with the fewest audible (``shape`` and ``timing``) envelopes over all tunes,
    each with its per-player audible counts and verdict totals."""
    cands = candidates()
    ok = [r for r in recs if "counts" in r]
    players = sorted({r.get("player", "") for r in ok})
    by = {p: sum(np.array(r["counts"]) for r in ok if r.get("player", "") == p) for p in players}
    total = sum(by.values())
    loud = [VERDICTS.index(v) for v in AUDIBLE]
    audible = total[:, loud].sum(1)
    lines = [
        f"{len(ok)} tunes ({len(recs) - len(ok)} failed or skipped),"
        f" {sum(r['frames'] for r in ok)} frames, {sum(r['traced'] for r in ok)} traces",
        "  rank audible "
        + " ".join(f"{p[:14]:>14}" for p in players)
        + "  "
        + " ".join(VERDICTS)
        + "  order",
    ]
    for rank, c in enumerate(np.argsort(audible, kind="stable")[:top], 1):
        per = " ".join(f"{int(by[p][c, loud].sum()):14}" for p in players)
        lines.append(
            f"  {rank:4} {int(audible[c]):7} {per}  "
            + " ".join(str(int(x)) for x in total[c])
            + f"  {cands[c][0]}"
        )
    return "\n".join(lines)


def main(argv=None) -> int:
    """Parse the command line, score the candidates on the tunes in parallel, print the ranking."""
    ap = shared.corpus_parser(__doc__)
    ap.add_argument("--backend", choices=("auto", "jennings", "sidtrace"), default="auto")
    ap.add_argument("--frames", type=int, default=3000)
    ap.add_argument("--top", type=int, default=20, help="candidates printed")
    ap.add_argument("--cache", type=Path, default=Path("out/writeorder/sidtrace"))
    ap.add_argument("--timeout", type=float, default=1800, help="seconds per tune")
    ap.add_argument("--jobs", type=int, default=max(1, len(os.sched_getaffinity(0)) - 2))
    ap.add_argument("--out", type=Path, help="write each tune's record as JSON")
    args = ap.parse_args(argv)
    rows = shared.rows(args.sources, args.player)
    recs = []
    tasks = [(r, args.hvsc, args) for r in rows]
    for i, rec, err in tqdm(imap(timed, tasks, args.jobs), total=len(tasks), unit="tune"):
        head = {"path": rows[i]["path"], "player": rows[i].get("player", "")}
        recs.append(head | (rec if rec is not None else {"error": repr(err)}))
    print(summary(recs, args.top))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(recs), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
