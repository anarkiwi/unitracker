"""tools/lift_corpus.py: verdicts, per-kind objects against native and sizes."""

import lift_corpus
import pytest

WARRIOR = "MUSICIANS/C/Cadaver/Metal_Warrior_Title_Remix.sid"


@pytest.mark.usefixtures("metal_warrior")
def test_an_exact_tune_reports_its_objects_per_kind_and_its_sizes():
    got = lift_corpus.classify(WARRIOR, 300, 600)
    assert got["verdict"] == "exact"
    assert set(got["objects"]) == {"instruments", "restart", "ctrl", "pw", "filter", "phrases"}
    assert all(a >= b for a, b in got["objects"].values())
    assert got["song_bytes"] > 0 and got["fixed_bytes"] > 0
    assert got["native_bytes"] > 0


def test_the_copies_of_each_kind_are_its_lifted_less_its_native_objects(capsys):
    results = [
        {
            "verdict": "exact",
            "name": "a",
            "objects": {"phrases": [5, 3], "ctrl": [2, 2]},
            "song_bytes": 10,
            "fixed_bytes": 20,
            "native_bytes": 30,
        },
        {"verdict": "refused", "name": "b"},
    ]
    lift_corpus.print_sizes(results)
    out = capsys.readouterr().out
    assert "a: phrases:5/3 ctrl:2/2 song 10 fixed 20 native 30" in out
    assert "copies per kind: {'phrases': 2, 'ctrl': 0}" in out


def test_a_missing_tune_is_reported():
    assert lift_corpus.classify("NOWHERE/none.sid", 10, 60)["verdict"] == "missing"
