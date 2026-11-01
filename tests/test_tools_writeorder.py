"""tools/writeorder.py: capture and corpus summary over the moved pattern logic."""

from types import SimpleNamespace

import writeorder as W


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
        {"player": "p", "frames": 10, "changed": [0] * 17 + [4] * 43, "pairs": pairs},
        {"player": "p", "error": "x"},
    ]
    text = W.summary(recs, 5)
    assert "1 tunes (1 failed or skipped), 10 frames" in text
    assert "+g(00,00)" in text and "1 orders" in text
