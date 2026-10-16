"""Record type declarations."""

import pytest
from conftest import mini_song

from unitracker.expr import ExprError
from unitracker.records import RecordType, RecordTypeError
from unitracker.vm import SongPlayer


def test_defaults_and_refs():
    rtype = RecordType(
        "wavetab", {"fields": {"w": 0, "cut": -1, "next": 0}, "chain": "next", "refs": {"w": "x"}}
    )
    assert rtype.prefix == "w" and rtype.first == 0 and rtype.form == "list"
    assert rtype.default("cut") == -1 and rtype.default("other") == 0
    assert rtype.refs == {"w": [("x", None)]}
    assert RecordType("s", {"none": 0}).first == 1


@pytest.mark.parametrize(
    "spec,match",
    [
        ({"form": "grid"}, "form"),
        ({"do": {"set": {}}}, "action list"),
        ({"fields": ["a"], "chain": "next"}, "chain field"),
    ],
)
def test_bad_declarations(spec, match):
    with pytest.raises(RecordTypeError, match=match):
        RecordType("r", spec)


def test_row_outside_a_template():
    with pytest.raises(ExprError, match="template"):
        SongPlayer(mini_song({"global": {"g": {}}, "tick": [{"set": {"g": "row.x"}}]}))


def test_template_runs_only_for_defined_rows():
    playroutine = {
        "global": {"g": {}},
        "record_types": {"r": {"fields": ["v"], "do": [{"set": {"g": "row.v + 1"}}]}},
        "init": [{"run": {"record": "r", "index": 3, "field": "do"}}],
        "tick": [{"run": {"record": "r", "index": 2, "field": "do"}}],
        "ghost": {"global": {0x15: "g"}},
    }
    player = SongPlayer(mini_song(playroutine, records={"r": {2: {"v": 7}}}))
    player.play_frame()
    assert player.cell("g") == 0
    player.play_frame()
    assert player.cell("g") == 8
