"""Playroutine record types: their declarations, and their row templates as the VM runs them."""

import pytest
from conftest import mini_song

from unitracker.player.vm import SongPlayer
from unitracker.playroutine.expr import ExprError
from unitracker.playroutine.library import library
from unitracker.playroutine.records import RecordType, RecordTypeError, record_types


def test_record_type_defaults_refs_and_names():
    rtype = RecordType(
        "wavetab", {"fields": {"w": 0, "cut": -1, "next": 0}, "chain": "next", "refs": {"w": "x"}}
    )
    assert rtype.prefix == "w" and rtype.first == 0 and rtype.form == "list"
    assert rtype.default("cut") == -1 and rtype.default("other") == 0
    assert rtype.refs == {"w": [("x", None)]}
    assert RecordType("s", {"none": 0}).first == 1
    rtype = RecordType("wavetab", {"writes_as": "ctrl"})
    assert (rtype.name, rtype.written, rtype.prefix) == ("wavetab", "ctrl", "c")
    assert RecordType("wavetab", {"writes_as": "ctrl", "prefix": "w"}).prefix == "w"
    rtypes = record_types({"record_types": {"vinit": {"fixed": True}, "vstate": {"fixed": True}}})
    assert rtypes["vinit"].prefix == rtypes["vstate"].prefix == "v"


@pytest.mark.parametrize(
    "specs,match",
    [
        ({"r": {"form": "grid"}}, "form"),
        ({"r": {"do": {"set": {}}}}, "action list"),
        ({"r": {"fields": ["a"], "chain": "next"}}, "chain field"),
        ({"r": {"fields": ["a"], "unwritten": ["b"]}}, r"unwritten fields \['b'\] are not fields"),
        ({"r": {"fields": ["a"], "uniform": ["b"]}}, r"uniform fields \['b'\] are not fields"),
        ({"a": {"writes_as": "ctrl"}, "b": {"writes_as": "ctrl"}}, "share one written name"),
        ({"a": {"writes_as": "x"}}, "share one written name"),
        ({"pw": {}, "pulse": {"writes_as": "pw", "prefix": "q"}}, "share one written name"),
        ({"a": {"prefix": "c"}, "b": {"writes_as": "ctrl"}}, "share one prefix name"),
        ({"pulse": {"writes_as": "pw"}}, r"\['phrase', 'pulse'\] share one prefix name"),
    ],
)
def test_bad_record_type_declarations(specs, match):
    with pytest.raises(RecordTypeError, match=match):
        record_types({"record_types": specs})


def test_templates_run_only_for_defined_rows():
    with pytest.raises(ExprError, match="template"):
        SongPlayer(mini_song({"global": {"g": {}}, "tick": [{"set": {"g": "row.x"}}]}))
    playroutine = {
        "global": {"g": {}},
        "record_types": {"r": {"fields": ["v"], "do": [{"set": {"g": "row.v + 1"}}]}},
        "init": [{"run": {"record": "r", "index": 3, "template": "do"}}],
        "tick": [{"run": {"record": "r", "index": 2, "template": "do"}}],
        "ghost": {"global": {0x15: "g"}},
    }
    player = SongPlayer(mini_song(playroutine, records={"r": {2: {"v": 7}}}))
    player.play_frame()
    assert player.cell("g") == 0
    player.play_frame()
    assert player.cell("g") == 8


GT_NOTE = {"note": 24, "wave": 0x21, "freq": 0x1234}
DM_STATE = {"tbase": 0x10, "res": 0x23, "rbit": 2, "rmask": 0xFD}
DM_ROW = {
    "wgh": 0x41, "wgl": 0x01, "ad": 0x0A, "sr": 0xF8, "tr": 0x05, "af": 0x83, "pw": 0x4C,
    "ps": 0x91, "re": 0x9F, "fv": 0x2F, "cp": 0x07, "acid": 0x8110,
}  # fmt: skip
DM_VOICE = {
    "ctrl@2": 0x41, "eor@2": 0x01, "ad@2": 0x0A, "sr@2": 0xF8, "note@2": 0x15, "mode@2": 0x83,
    "pw@2": 0x4C40, "ps@2": 0x91,
}  # fmt: skip
GT_WAVE = ("goattracker2", "ctrltab", GT_NOTE, "do", True)
GT_PULSE = ("goattracker2", "pulsetab", None, "do", True)
GT_PULSE_EACH = ("goattracker2", "pulsetab", {"pulse": 0x100}, "each", True)
GT_FILTER = ("goattracker2", "filttab", {"filtercutoff": 0x33}, "do", False)
GT_FILTER_EACH = GT_FILTER[:3] + ("each", False)
DM_SID = ("defmon", "sidtab", DM_STATE, "do", True)


def template_player(where, row):
    """``(player, tables)``: library ``name``'s player after one tick that sets ``cells`` then
    runs template ``field`` of ``rtype`` row 1, once per voice when ``voice``."""
    name, rtype, cells, field, voice = where
    pr = library(name)
    tables = pr.pop("tables")
    run = [{"set": cells}] if cells else []
    run.append({"run": {"record": rtype, "index": 1, "template": field}})
    pr["init"], pr["tick"] = ([{"for_voices": run}] if voice else run), []
    tracks = [{"plays": [{"phrase": "a"}], "loop": 0}] * 3
    song3 = mini_song(pr, tracks, voices=3, tables=tables, records={rtype: {1: row}})
    return SongPlayer(song3), tables


@pytest.mark.parametrize(
    "where,row,expect",
    [
        (
            GT_WAVE,
            {"w": 0x41, "n": 0},
            {"wave@0": 0x41, "lastnote@0": 24, "skip@0": 1, "freq@0": 24},
        ),
        (GT_WAVE, {"w": 0x11, "n": 5}, {"wave@0": 0x11, "lastnote@0": 29, "freq@0": 29}),
        (GT_WAVE, {"w": 0x02, "n": 0x81}, {"wave@0": 0x21, "lastnote@0": 1, "freq@0": 1}),
        (GT_WAVE, {"w": 0xE1, "n": 0x80}, {"wave@0": 1, "skip@0": 0, "freq@0": None}),
        (
            GT_WAVE,
            {"w": 0xF5, "n": 0x40},
            {"wave@0": 0x21, "ad@0": 0x40, "skip@0": 1, "freq@0": None},
        ),
        (GT_PULSE, {"l": 0x84, "r": 0x20}, {"pulse@1": 0x420}),
        (GT_PULSE_EACH, {"l": 0x20, "r": 0xF0}, {"pulse@2": 0xF0}),
        (GT_FILTER, {"l": 0x90, "r": 0xF1, "cut": 0x40}, (0x10, 0xF1, 0x40)),
        (GT_FILTER, {"l": 0x90, "r": 0xF1}, (0x10, 0xF1, 0x33)),
        (GT_FILTER, {"l": 0x00, "r": 0x22}, (0, 0, 0x22)),
        (GT_FILTER_EACH, {"l": 0x10, "r": 0x02}, (0, 0, 0x35)),
        (
            DM_SID,
            DM_ROW,
            DM_VOICE | {"res": 0x03 | 0x9F | 2, "fv": 0x2F, "cp": 0x07, "astep": 0x110, "sub": 0},
        ),
        (DM_SID, {"tr": 0x85}, {"note@0": 5}),
        (DM_SID, {"re": 0}, {"res": 0x23 & 0xFD}),
        (DM_SID, {"re": 0x51}, {"res": 0x51}),
        (DM_SID, {"acid": 0x1234}, {"acc": 0x1234, "astep": 0}),
        (DM_SID, {"acid": 0xC102}, {"astep": 0x0102, "sub": 1}),
        (DM_SID, {}, {"note@0": 0, "res": 0x23}),
    ],
)
def test_library_templates(where, row, expect):
    """Each library row template run once: ``expect`` maps ``cell`` or ``cell@voice`` to its
    value, ``freq`` to the ``ftab`` note (None: the note's freq unchanged); a tuple is the
    filter's (type, ctrl, cutoff)."""
    player, tables = template_player(where, row)
    player.play_frame()
    if isinstance(expect, tuple):
        expect = dict(zip(("filtertype", "filterctrl", "filtercutoff"), expect))
    for key, value in expect.items():
        cell, _, at = key.partition("@")
        if cell == "freq":
            value = GT_NOTE["freq"] if value is None else tables["ftab"][value]
        assert player.cell(cell, int(at) if at else None) == value, key
