"""tools/writeorder.py: candidate write orders, their frames and the envelope writes they keep."""

import numpy as np

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


def test_reorders_follow_each_candidate_in_the_frame_slots():
    cands = W.candidates()
    count = {r: int((FRAME[:, 1] == r).sum()) for r in range(25)}
    for (text, order), new in zip(cands, W.reorders(FRAME, W.positions(cands))):
        expect = [r for r, k in order if count[r] >= k]
        assert new[:, 1].tolist() == expect
        dropped = set() if "ctrl:2" in text else {320}
        assert new[:, 0].tolist() == sorted(set(FRAME[:, 0].tolist()) - dropped)
        values = {(r, v) for _, r, v in new.tolist()}
        assert ((4, 0x08) in values) == ("ctrl:2" in text)
        assert {(r, v) for _, r, v in FRAME.tolist() if r != 4} <= values


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


def test_lfsr_cycle_of_rate_zero():
    assert W.lfsr_cycle(0x7F).tolist() == [0x7FFF >> i for i in range(9)]
