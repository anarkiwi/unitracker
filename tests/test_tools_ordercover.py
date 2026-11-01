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


def test_summary_reports_covers():
    recs = [{"player": "p", "masks": [[hex(mask(2, 3)), 4, "g+t-"], ["0x0", 1, "a00 g+t-"]]}]
    text = C.summary(recs, 2)
    assert "kept by no order: 1" in text and "a00 g+t-" in text
    assert "smallest covers: 2 of 1 orders" in text
