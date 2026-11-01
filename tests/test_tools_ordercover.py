"""tools/ordercover.py: the orders that keep each case's envelope, and their smallest covers."""

from types import SimpleNamespace

import ordercover as C
from vagrant_moses import sidorder as S


def capture(path, **kw):
    """The timeline and arguments of a tune."""
    args = SimpleNamespace(backend="auto", settle=39566, horizon=5, **kw)
    return S.Timeline(*C.W.capture({"path": str(path)}, None, args)), args


def test_measure_finds_orders_keeping_each_case(metal_warrior):
    tl, args = capture(metal_warrior, frames=150)
    rec = C.measure(tl, args)
    assert rec["masks"] and all(n > 0 for _, n, _ in rec["masks"])
    assert any(int(m, 16) for m, _, _ in rec["masks"])


def test_hard_restarts_count_note_ons(metal_warrior):
    tl, args = capture(metal_warrior, frames=300)
    notes = C.hard_restarts(tl, args)["notes"]
    assert notes and all(n > 0 and frames >= 0 for _, frames, _, n in notes)
    assert any(kept is not None for *_, kept, _ in notes)
    lines = C.hard_restart_lines([{"player": "p", "hr": {"notes": notes}}], 39566)
    assert "3 PAL frames" in lines[0] and lines[1].startswith("    p: ")


def test_summary_reports_covers():
    recs = [{"player": "p", "masks": [[hex(S.mask_of(2, 3)), 4, "g+t-"], ["0x0", 1, "a00 g+t-"]]}]
    text = C.summary(recs, 2)
    assert "kept by no order: 1" in text and "a00 g+t-" in text
    assert "smallest covers: 2 of 1 orders" in text
