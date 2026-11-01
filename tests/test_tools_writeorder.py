"""tools/writeorder.py: voices' event patterns and the per-voice orders that keep them."""

from types import SimpleNamespace

import writeorder as W

GT2_NOTE = [(5, 0x00), (6, 0xE5), (4, 0x09)]
MA_PULSE = [(6, 0x48), (5, 0x08), (4, 0x40), (4, 0x41)]


def test_orders_are_every_per_voice_order_of_the_kept_writes():
    assert len(W.ORDERS) == len(set(W.ORDERS)) == 18
    assert all(o.index("ctrl:2") < o.index("ctrl") for o in W.ORDERS if "ctrl:2" in o)


def test_events_are_edges_with_the_envelope_in_effect():
    assert W.events((0x40, 0x0F, 0x00), GT2_NOTE) == ((0x09, 0x09, 0x00, 0xE5),)
    pulse = ((0x01, 0x00, 0x08, 0x48), (0x01, 0x01, 0x08, 0x48))
    assert W.events((0x41, 0x08, 0x48), MA_PULSE) == pulse
    assert not W.events((0x41, 0, 0), [(4, 0x21), (4, 0x41), (5, 0x0A)])


def test_kept_takes_the_last_writes_in_the_orders_sequence():
    assert W.kept(GT2_NOTE, ("ad", "sr", "ctrl")) == [(5, 0x00), (6, 0xE5), (4, 0x09)]
    assert W.kept(GT2_NOTE, ("ctrl:2", "ctrl", "sr", "ad")) == [(4, 0x09), (6, 0xE5), (5, 0x00)]
    assert W.kept(GT2_NOTE + [(4, 0x08)], ("ctrl:2", "ad", "sr", "ctrl"))[0] == (4, 0x09)
    expect = [(5, 0x08), (6, 0x48), (4, 0x40), (4, 0x41)]
    assert W.kept(MA_PULSE, ("ad", "sr", "ctrl:2", "ctrl")) == expect


def test_orders_that_keep_or_change_a_pattern():
    start = (0x41, 0x08, 0x48)
    native = W.events(start, MA_PULSE)
    keeps = {o for o in W.ORDERS if W.events(start, W.kept(MA_PULSE, o)) == native}
    assert ("ad", "sr", "ctrl:2", "ctrl") in keeps
    assert ("ad", "sr", "ctrl") not in keeps
    gt2 = W.events((0x40, 0x0F, 0x00), GT2_NOTE)
    assert W.events((0x40, 0x0F, 0x00), W.kept(GT2_NOTE, ("ad", "sr", "ctrl"))) == gt2
    assert W.events((0x40, 0x0F, 0x00), W.kept(GT2_NOTE, ("ctrl", "ad", "sr"))) != gt2


def test_text():
    assert W.text(()) == "-"
    pattern = ((0x09, 0x09, 0x0F, 0x00), (0x01, 0x00, 0x0F, 0x00))
    assert W.text(pattern) == "+g+t(0F,00) -g(0F,00)"


def test_measure_counts_pattern_changes(metal_warrior):
    args = SimpleNamespace(backend="auto", frames=300)
    rec = W.measure({"path": str(metal_warrior)}, None, args)
    assert rec["frames"] == 300 and len(rec["changed"]) == len(W.ORDERS)
    for native, new, i, n in rec["pairs"]:
        assert native != new and 0 <= i < len(W.ORDERS) and n > 0
    assert sum(n for *_, i, n in rec["pairs"] if i == 0) == rec["changed"][0]


def test_summary_ranks_orders_and_lists_pairs():
    pairs = [["+g(00,00)", "-", 17, 4]]
    recs = [
        {"player": "p", "frames": 10, "changed": [0] * 17 + [4], "pairs": pairs},
        {"player": "p", "error": "x"},
    ]
    text = W.summary(recs, 5)
    assert "1 tunes (1 failed or skipped), 10 frames" in text
    assert "+g(00,00)" in text and "1 orders" in text
