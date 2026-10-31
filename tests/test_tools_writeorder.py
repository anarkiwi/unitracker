"""tools/writeorder.py: candidate write orders, their frames and the envelope writes they keep."""

import numpy as np
import pytest
from pylibresidfp import SID

import writeorder as W

FRAME = np.array(
    [
        (76, 22, 0x08),
        (82, 23, 0xF1),
        (90, 24, 0x1F),
        (271, 5, 0x00),
        (280, 6, 0xE5),
        (289, 2, 0x00),
        (298, 3, 0x00),
        (307, 0, 0x7C),
        (316, 1, 0x05),
        (320, 4, 0x08),
        (329, 4, 0x09),
    ],
    np.int64,
)


def test_candidates_are_fixed_distinct_and_complete():
    cands = W.candidates()
    assert len(cands) == 144 and cands == W.candidates()
    assert len({text for text, _ in cands}) == len({order for _, order in cands}) == 144
    lasts = {(r, 1) for r in range(25) if r % 7 < 7}
    for _, order in cands:
        assert lasts <= set(order) and len(set(order)) == len(order)
        assert all(order.index((r, 2)) < order.index((r, 1)) for r, k in order if k == 2)


def reference(writes: np.ndarray, order: tuple) -> np.ndarray:
    """One candidate's frame written out directly: its kept writes in its order, in the slots."""
    at = {}
    for i, reg in enumerate(writes[:, 1].tolist()):
        at.setdefault(reg, []).append(i)
    keep = [at[r][-k] for r, k in order if len(at.get(r, [])) >= k]
    new = writes[keep].copy()
    new[:, 0] = np.sort(new[:, 0])
    return new


def frames(seed: int, count: int) -> list:
    """Random frames: every register written, some twice or three times, in random order."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(count):
        regs = list(range(25)) + list(rng.choice(25, rng.integers(0, 6)))
        regs = rng.permutation(regs)
        cycles = np.sort(rng.choice(np.arange(60, 900), len(regs), replace=False))
        values = rng.integers(0, 256, len(regs))
        values[np.isin(regs, (4, 11, 18))] &= (
            rng.integers(0, 2, (np.isin(regs, (4, 11, 18))).sum()) | 0xFE
        )
        out.append(np.column_stack([cycles, regs, values]).astype(np.int64))
    return out


def test_vectorised_candidates_match_writing_each_out():
    cands = W.candidates()
    pos = W.positions(cands)
    at = {item: i for i, item in enumerate(W.UNIVERSE)}
    for w in [FRAME, *frames(1, 40)]:
        cycles, idx = W.slot_cycles(w, pos)
        for c, (_, order) in enumerate(cands):
            new = reference(w, order)
            assert sorted(cycles[c][cycles[c] >= 0].tolist()) == new[:, 0].tolist()
            assert all(
                cycles[c, at[item]] == cyc
                for item, (cyc, _, _) in zip(
                    [i for i in order if cycles[c, at[i]] >= 0], new.tolist()
                )
            )
        for v in range(3):
            regs = (1, 0x0A, 0x88)
            rows = W.envelope_rows(cycles, idx, w, v, regs, 4)
            for c, (_, order) in enumerate(cands):
                mine, _ = W.effective(W.envelope_writes(reference(w, order), v), v, regs)
                assert rows[c][rows[c, :, 0] >= 0].tolist() == mine.tolist()


def test_effective_drops_envelope_no_ops():
    writes = np.array([(0, 4, 0x41), (5, 4, 0x40), (9, 4, 0x20), (12, 5, 0x0A), (20, 6, 0x88)])
    kept, regs = W.effective(writes, 0, (1, 0x0A, 0x80))
    assert kept.tolist() == [[5, 4, 0x40], [20, 6, 0x88]] and regs == (0, 0x0A, 0x88)
    other, _ = W.effective(writes, 1, (1, 0x0A, 0x80))
    assert len(other) == 0


def test_envelope_writes_and_displacement():
    env = W.envelope_writes(FRAME, 0)
    assert env[:, 1].tolist() == [5, 6, 4, 4]
    moved = env.copy()
    moved[:, 0] += [0, 0, 30, -9]
    assert W.displacement(env, moved) == 30
    assert W.displacement(env, env[1:]) == 0


def phases_verdict(release: int, ad: int, sr: int, order: list) -> str:
    """The verdict on voice 1's AD, SR and gate-on writes at cycles 100, 109 and 118 put in
    ``order`` (registers), after two frames of release at the SR ``release``."""
    sid = SID()
    sid.run([(0, 5, 0), (1, 6, release), (2, 4, 0x40)], 2 * 19656)
    value = {5: ad, 6: sr, 4: 0x41}
    native = np.array([(100, 5, ad), (109, 6, sr), (118, 4, 0x41)], np.int64)
    new = np.array([(c, r, value[r]) for c, r in zip((100, 109, 118), order)], np.int64)
    at, until = np.array([sid.cycle, 0, 0]), sid.cycle + 3 * 19656
    table = {}
    a, b = (sid.envelope_phases(0, w + at, until).expand(table) for w in (native, new))
    return W.verdict(a, b, W.displacement(native, new))


@pytest.mark.parametrize(
    "release,ad,sr,order,expect",
    [
        (0x00, 0x5F, 0xAD, [6, 4, 5], "noise"),
        (0x0F, 0x54, 0xCC, [6, 5, 4], "drift"),
        (0xF8, 0xCE, 0x05, [4, 5, 6], "shape"),
        (0xF8, 0x32, 0xAC, [6, 5, 4], "timing"),
    ],
)
def test_verdicts_over_every_rate_counter_phase(release, ad, sr, order, expect):
    assert phases_verdict(release, ad, sr, order) == expect


def test_moving_a_hard_restart_gate_on_only_drifts():
    sid = SID()
    sid.run([(0, 5, 0), (1, 6, 0), (2, 4, 0x40)], 2 * 19656)
    at, until = np.array([sid.cycle, 0, 0]), sid.cycle + 3 * 19656
    native = np.array([(100, 5, 0x0A), (110, 6, 0x88), (120, 4, 0x41)], np.int64)
    moved = native + [2, 0, 0]
    table = {}
    a, b = (sid.envelope_phases(0, w + at, until).expand(table) for w in (native, moved))
    assert W.verdict(a, a, 0) == "noise"
    assert W.verdict(a, b, W.displacement(native, moved)) == "drift"


def test_no_op_envelope_writes_leave_every_candidate_alike():
    cands = W.candidates()
    for w in frames(2, 20):
        cycles, idx = W.slot_cycles(w, W.positions(cands))
        for v in range(3):
            env = W.envelope_writes(w, v)
            last = {int(r): int(x) for _, r, x in env.tolist()}
            regs = (last.get(7 * v + 4, 0) & 1, last.get(7 * v + 5, 0), last.get(7 * v + 6, 0))
            held = env.copy()
            held[:, 2] = [last[int(r)] for r in held[:, 1]]
            native, _ = W.effective(held, v, regs)
            assert len(native) == 0
            rows = W.envelope_rows(cycles, idx, held_frame(w, held), v, regs, 4)
            assert (rows[:, :, 0] < 0).all()


def held_frame(w: np.ndarray, env: np.ndarray) -> np.ndarray:
    """``w`` with its envelope writes replaced by ``env`` (same cycles and registers)."""
    out = w.copy()
    out[np.isin(out[:, 0], env[:, 0])] = env
    return out
