"""Normalizing native write streams to canonical flushes."""

from unitracker.player.canonical import CANONICAL_ORDER, canonical_frames


def test_order_covers_every_register_once():
    assert sorted(CANONICAL_ORDER) == list(range(25))
    assert CANONICAL_ORDER[:4] == (0x15, 0x16, 0x17, 0x18)
    assert CANONICAL_ORDER[4:11] == (2, 3, 6, 5, 0, 1, 4)


def test_reorders_and_drops_unchanged():
    frames = [[(4, 0x41), (0, 1), (5, 9)], [(4, 0x41), (5, 9), (6, 3)]]
    out = list(canonical_frames(frames, regs=[0] * 25))
    assert out == [[[(5, 9), (0, 1), (4, 0x41)]], [[(6, 3)]]]


def test_repeated_register_splits_the_frame():
    out = list(canonical_frames([[(4, 0x40), (1, 2), (4, 0x41)]], regs=[0] * 25))
    assert out == [[[(1, 2), (4, 0x40)], [(4, 0x41)]]]


def test_empty_segments_dropped_and_values_masked():
    out = list(canonical_frames([[(4, 0), (4, 0x100)]], regs=[0] * 25))
    assert out == [[]]
    assert list(canonical_frames([[(0, 7)]])) == [[[(0, 7)]]]
