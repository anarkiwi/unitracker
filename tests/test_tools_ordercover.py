"""tools/ordercover.py: the orders that keep each case's envelope, and their smallest covers."""

from types import SimpleNamespace

import numpy as np

import ordercover as C
import writeorder as W


def mask(*orders) -> int:
    """The mask of ``orders`` (indices into :data:`writeorder.ORDERS`)."""
    out = np.zeros(len(W.ORDERS), bool)
    out[list(orders)] = True
    return int("".join("1" if x else "0" for x in out), 2)


def test_bits_inverts_mask():
    assert np.flatnonzero(C.bits(mask(0, 7, 59))).tolist() == [0, 7, 59]


def test_covers_finds_every_smallest_cover():
    masks = {mask(1, 2): 5, mask(2, 3): 1, mask(4): 2, 0: 9}
    size, found = C.covers(masks, 3)
    assert size == 2 and found == [(2, 4)]
    assert C.covers({mask(1): 1, mask(2): 1, mask(3): 1}, 2) == (None, [])


def test_same_compares_traces():
    rec = np.rec.fromarrays([[0, 9], [1, 2], [0, 0]], names=("cycle", "envelope", "state"))
    other = rec.copy()
    assert C.same(rec, other)
    other["cycle"][1] = 10
    assert not C.same(rec, other) and not C.same(rec, rec[:1])


def test_measure_finds_orders_keeping_each_case(metal_warrior):
    args = SimpleNamespace(backend="auto", frames=150, horizon=5)
    rec = C.measure({"path": str(metal_warrior)}, None, args)
    assert rec["masks"] and all(n > 0 for _, n, _ in rec["masks"])
    assert any(int(m, 16) for m, _, _ in rec["masks"])


def test_settle_is_a_wrap_and_a_release():
    settle = C.settle_cycles(985248)
    assert 0x7FFF < settle < 0x7FFF + 19656


def test_hard_restarts_count_note_ons(metal_warrior):
    args = SimpleNamespace(backend="auto", frames=300, settle=39566, horizon=5)
    notes = C.hard_restarts({"path": str(metal_warrior)}, None, args)["notes"]
    assert notes and all(n > 0 and frames >= 0 for _, frames, _, n in notes)
    assert any(kept is not None for *_, kept, _ in notes)
    lines = C.hard_restart_lines([{"player": "p", "hr": {"notes": notes}}], 39566)
    assert "3 PAL frames" in lines[0] and lines[1].startswith("    p: ")


def test_neutral_clears_values_from_the_hard_restart_on():
    rows = np.array([(0, 5, 0x2A), (10, 5, 0x0F), (10, 6, 0xF3), (20, 4, 0x20)], np.int64)
    out = C.neutral(rows, 10)
    assert out[:, 2].tolist() == [0x2A, 0x00, 0x03, 0x20]


def test_summary_reports_covers():
    recs = [{"player": "p", "masks": [[hex(mask(2, 3)), 4, "g+t-"], ["0x0", 1, "a00 g+t-"]]}]
    text = C.summary(recs, 2)
    assert "kept by no order: 1" in text and "a00 g+t-" in text
    assert "smallest covers: 2 of 1 orders" in text
