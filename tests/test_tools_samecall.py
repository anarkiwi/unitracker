"""tools/samecall.py: control, AD and SR registers written more than once in one play call."""

import samecall as S


def test_kind_names_what_a_repeated_write_changes():
    assert S.kind(4, [0x41, 0x41]) == "same"
    assert S.kind(4, [0x40, 0x41]) == "gate"
    assert S.kind(4, [0x41, 0x49]) == "test"
    assert S.kind(4, [0x41, 0x21]) == "value"
    assert S.kind(5, [0x00, 0xC0]) == "value"


def test_measure_counts_a_tunes_repeated_writes(metal_warrior):
    rec = S.measure({"path": str(metal_warrior)}, None, 200)
    assert rec["frames"] == 200
    assert set(rec["calls"]) <= {
        f"{n}:{k}" for n in S.NAMES.values() for k in ("same", "gate", "test", "value")
    }
    for seq in rec["seqs"]:
        values = seq.split(" ")[1].split(">")
        assert len(values) > 1 and len(set(values)) > 1


def test_summary_reports_each_player():
    recs = [
        {"player": "p", "calls": {"ctrl:gate": 3}, "seqs": {"ctrl 40>41": 3}, "frames": 10},
        {"player": "p", "error": "x"},
    ]
    text = S.summary(recs)
    assert "p: 1 tunes measured, 1 failed/skipped" in text
    assert "ctrl:gate" in text and "seq ctrl 40>41 in 1 tunes" in text
