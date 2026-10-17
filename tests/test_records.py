"""Record type declarations."""

import pytest
from conftest import mini_song

from unitracker.format.expr import ExprError
from unitracker.format.records import RecordType, RecordTypeError, record_types
from unitracker.player.vm import SongPlayer


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


def test_a_type_is_written_under_its_shared_name_and_prefix():
    rtype = RecordType("wavetab", {"writes_as": "ctrl"})
    assert (rtype.name, rtype.written, rtype.prefix) == ("wavetab", "ctrl", "c")
    assert RecordType("wavetab", {"writes_as": "ctrl", "prefix": "w"}).prefix == "w"


@pytest.mark.parametrize(
    "specs,match",
    [
        ({"a": {"writes_as": "ctrl"}, "b": {"writes_as": "ctrl"}}, "share one written name"),
        ({"a": {"writes_as": "x"}}, "share one written name"),
        ({"pw": {}, "pulse": {"writes_as": "pw", "prefix": "q"}}, "share one written name"),
        ({"a": {"prefix": "c"}, "b": {"writes_as": "ctrl"}}, "share one prefix name"),
        ({"pulse": {"writes_as": "pw"}}, r"\['phrase', 'pulse'\] share one prefix name"),
    ],
)
def test_written_names_and_prefixes_are_each_one_types(specs, match):
    with pytest.raises(RecordTypeError, match=match):
        record_types({"record_types": specs})


def test_fixed_types_may_share_a_prefix():
    rtypes = record_types({"record_types": {"vinit": {"fixed": True}, "vstate": {"fixed": True}}})
    assert rtypes["vinit"].prefix == rtypes["vstate"].prefix == "v"


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
