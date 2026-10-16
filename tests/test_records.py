"""Record type declarations: keys and their kinds, defaults, rules, templates."""

import pytest
from conftest import mini_song

from unitracker.expr import ExprError
from unitracker.records import Kind, RecordType, RecordTypeError
from unitracker.vm import SongPlayer


def test_kinds():
    assert Kind("int", "k").scalar == "int"
    assert Kind("ref prog", "k").ref == "prog"
    assert Kind("value set,spec", "k").forms == ("set", "spec")


@pytest.mark.parametrize("text", ["words", "value nope", "ref", "value"])
def test_bad_kinds(text):
    with pytest.raises(RecordTypeError):
        Kind(text, "k")


def test_defaults_and_refs():
    rtype = RecordType(
        "prog",
        {
            "keys": {"x": "ref other", "hold": "int"},
            "defaults": {"hold": 1},
            "chain": "next",
            "none": -1,
            "first": 0,
        },
    )
    assert rtype.prefix == "p" and rtype.first == 0
    assert rtype.default("hold") == 1 and rtype.default("other") == 0
    assert rtype.default("next") == -1
    assert rtype.refs() == {"x": "other"}
    assert RecordType("s", {"none": 0}).first == 1
    assert RecordType("s", {"chain": "next"}).default("next") == 0


@pytest.mark.parametrize(
    "spec,match",
    [
        ({"do": {"set": {}}}, "action list"),
        ({"rules": [{"check": "1"}]}, "rule"),
        ({"keys": {"a": "nope"}}, "bad kind"),
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
        "record_types": {"r": {"keys": {"v": "int"}, "do": [{"set": {"g": "row.v + 1"}}]}},
        "init": [{"run": {"record": "r", "index": 3, "field": "do"}}],
        "tick": [{"run": {"record": "r", "index": 2, "field": "do"}}],
        "ghost": {"global": {0x15: "g"}},
    }
    player = SongPlayer(mini_song(playroutine, records={"r": {2: {"v": 7}}}))
    player.play_frame()
    assert player.cell("g") == 0
    player.play_frame()
    assert player.cell("g") == 8
