"""The v1.2 document: canonical writing, reading back, and the errors a reader reports."""

import copy

import numpy as np
import pytest
import yaml
from pygoattracker import decompile_sid

from unitracker import songio
from unitracker.goattracker import lift
from unitracker.songio import SongFormatError
from unitracker.vm import SongPlayer

PLAYROUTINE = {
    "name": "mini",
    "defaults": {
        "clock": {"cycles_per_tick": 19656, "clock_hz": 985248},
        "voices": 2,
        "registers": [0] * 25,
    },
    "instrument": {"type": "ins", "base": 0},
    "commands": {8: {"name": "run", "ref": "prog"}},
    "record_types": {
        "ins": {
            "prefix": "i",
            "none": 0,
            "pin": [1],
            "form": "map",
            "fields": {"a": 0, "p": 0},
            "refs": {"p": "prog"},
        },
        "prog": {
            "prefix": "w",
            "none": 0,
            "chain": "next",
            "fields": {"v": 0, "x": 0, "next": 0},
            "refs": {"x": {"type": "prog", "when": "v == 99"}},
            "do": [{"set": {"out": "row.v"}}],
        },
        "stopped": {
            "prefix": "t",
            "chain": "next",
            "stop": "v >= 128",
            "fields": ["v", "next"],
        },
        "init": {"fixed": True, "form": "map", "fields": ["o"]},
    },
    "voice": {"ptr": {"width": 8}, "out": {"width": 8}},
    "procs": {
        "step": [
            {"if": "ev_instr", "then": [{"set": {"ptr": "ins[ev_instr].p"}}]},
            {"if": "ev_cmd == 8", "then": [{"set": {"ptr": "ev_arg"}}]},
            {
                "if": "ptr",
                "then": [
                    {"run": {"record": "prog", "index": "ptr", "field": "do"}},
                    {"set": {"ptr": "prog[ptr].x if prog[ptr].v == 99 else prog[ptr].next"}},
                ],
            },
        ]
    },
    "tick": [{"for_voices": [{"fetch": True}, {"call": "step"}]}],
    "ghost": {"voice": {0: "out", 1: "ev_note", 2: "ins[1].a + init[0].o"}},
}


def song() -> dict:
    """A two-voice song exercising every part of the writer."""
    phrase_a = [{"note": 24, "instrument": 1}, {}, {"note": 26, "instrument": 2, "steps": 2}]
    phrase_b = [{"note": 27, "instrument": 1}, {}, {"note": 29, "instrument": 2, "steps": 2}]
    return {
        "unitracker": "ir",
        "meta": {"title": "Mini", "author": "Test", "released": "2026", "source": "x"},
        "hints": {"ticks_per_step": 1},
        "clock": {"cycles_per_tick": 19656, "clock_hz": 985248},
        "voices": 2,
        "registers": [0] * 24 + [15],
        "tables": {"t": [1, 2, 3]},
        "playroutine": copy.deepcopy(PLAYROUTINE),
        "records": {
            "ins": {1: {"a": 1, "p": 1}, 2: {"a": 2, "p": 2}, 3: {"p": 4}, 9: {"a": 7}},
            "prog": {
                1: {"v": 10, "next": 2},
                2: {"v": 20, "next": 3},
                3: {"v": 30, "next": 1},
                4: {"v": 99, "x": 6, "next": 5},
                5: {"v": 50, "next": 0},
                6: {"v": 60, "next": 0},
                7: {"v": 1, "next": 0},
            },
            "init": {0: {"o": 5}},
        },
        "tracks": [
            {"plays": [{"phrase": "a"}, {"phrase": "b"}], "loop": 0},
            {"plays": [{"phrase": "c", "repeat": 2}], "loop": 0},
        ],
        "phrases": {
            "a": phrase_a,
            "b": phrase_b,
            "c": [{"command": [8, 1]}, {"note": 30, "instrument": 3, "steps": 3}],
            "unused": [{"note": 1}],
        },
    }


def grid(ir, n=64):
    return SongPlayer(ir).render_grid(n)


def test_roundtrip_plays_identically_and_is_canonical():
    ir = song()
    text = songio.dumps(ir, embed=True)
    back = songio.loads(text)
    np.testing.assert_array_equal(grid(back), grid(ir))
    assert songio.dumps(back, embed=True) == text
    doc = yaml.safe_load(text)
    assert list(doc) == ["unitracker", "playroutine", "meta", "hints", "registers", "song", "loop"]
    assert doc["meta"] == {"title": "Mini", "author": "Test", "released": "2026"}
    assert doc["registers"] == {24: 15}
    assert doc["hints"] == {"ticks_per_step": 1, "phrase_steps": 4}


def test_programs_split_named_inline_and_by_first_use():
    doc = yaml.safe_load(songio.dumps(song(), embed=True))
    first = doc["song"][0]["def"]
    assert first["init"] == {0: {"o": 5}}
    assert first["prog"] == {"w1": [[10], [20], [30], {"loop": 0}], "w2": [[60], "end"]}
    assert first["ins"]["i1"] == {"a": 1, "p": "w1"}
    assert first["ins"]["i2"] == {"a": 2, "p": [[20], [30], [10], {"loop": 0}]}
    assert first["ins"]["i3"] == {"p": [[99, "w2"], [50], "end"]}
    assert "i4" not in first["ins"]
    assert first["phrase"]["p1"] == ["C2.i1*2", "D2.i2*2"]
    assert first["phrase"]["p2"] == ["-!run:@w1", "F#2.i3*3"]
    assert doc["song"][0]["play"] == ["p1", "p2"]
    assert doc["song"][1]["play"] == ["p1+3", "p2"]
    assert doc["loop"] == 0


def test_reference_form_patches_library_tables(metal_warrior):
    ir = lift(decompile_sid(metal_warrior.read_bytes(), subtune=0))
    doc = yaml.safe_load(songio.dumps(ir))
    assert doc["playroutine"]["ref"] == "goattracker2"
    assert doc["playroutine"]["constants"] == {"deftempo": 11}
    assert "tables" not in doc["playroutine"]
    assert "clock" not in doc and "registers" not in doc
    ir["tables"]["ftab"] = ir["tables"]["ftab"][:4]
    back = songio.loads(songio.dumps(ir))
    assert back["tables"]["ftab"] == ir["tables"]["ftab"]
    ir["playroutine"]["procs"]["wnote"] = []
    with pytest.raises(SongFormatError, match="differs from the library"):
        songio.dumps(ir)


def test_dump_writes_atomically(tmp_path):
    path = tmp_path / "s.yaml"
    songio.dump(song(), path, embed=True)
    assert songio.load(path)["meta"]["title"] == "Mini"
    assert [p.name for p in tmp_path.iterdir()] == ["s.yaml"]


@pytest.mark.parametrize(
    "change,match",
    [
        (lambda ir: ir["records"]["prog"].pop(2), "row 2 is referenced but not defined"),
        (lambda ir: ir["records"]["ins"][1].update(z=1), "not declared"),
    ],
)
def test_writer_errors(change, match):
    ir = song()
    change(ir)
    with pytest.raises(SongFormatError, match=match):
        songio.dumps(ir, embed=True)


def edit(path, value):
    def apply(doc):
        node = doc
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    return apply


DEF = ("song", 0, "def")


@pytest.mark.parametrize(
    "change,match",
    [
        (edit(("unitracker",), 0), "not a unitracker v1"),
        (edit(("song",), []), "list of rows"),
        (edit(("song", 1), {"def": {}}), "play list"),
        (edit(("playroutine",), "mini"), "mapping"),
        (edit(("playroutine",), {"ref": "nope"}), "no playroutine"),
        (edit(DEF + ("prog", "w1"), [[10]]), "rows then end"),
        (edit(DEF + ("prog", "w1"), [[10], {"loop": 7}]), "bad program end"),
        (edit(DEF + ("prog", "w1"), [[1, 2, 3], "end"]), "bad row"),
        (edit(DEF + ("prog", "w1"), [{"v": 1}, "end"]), "bad row"),
        (edit(DEF + ("ins", "i1"), [1]), "must be a mapping"),
        (edit(DEF + ("ins", "i1"), {"q": 1}), "unknown field"),
        (edit(DEF + ("ins", "i1"), {"a": 1.5}), "bad value"),
        (edit(DEF + ("ins", "i1"), {"a": [[1], "end"]}), "cannot hold a program"),
        (edit(DEF + ("ins", "i1"), {"p": "i2"}), "not a prog"),
        (edit(DEF + ("ins", "i1"), {"p": "nothing"}), "unknown name"),
        (edit(DEF + ("ins", "1x"), {}), "bad name"),
        (edit(DEF + ("ins", "w1"), {}), "defined twice"),
        (edit(DEF + ("phrase", "w1"), ["-"]), "defined twice"),
        (edit(DEF + ("bogus",), {"x": 1}), "unknown record type"),
        (edit(DEF + ("init", 0), {"o": 1}), None),
        (edit(DEF + ("phrase", "p2"), ["-!ad:@w1"]), "can't express"),
        (edit(DEF + ("phrase", "p2"), ["-!run:@zz"]), "unknown name"),
        (edit(DEF + ("phrase", "p2"), ["C2.w1"]), "not a ins"),
        (edit(("song", 0, "play"), ["p1", "p2+x"]), "bad play entry"),
        (edit(("song", 0, "play"), ["p1"]), "entries"),
    ],
)
def test_reader_errors(change, match):
    doc = yaml.safe_load(songio.dumps(song(), embed=True))
    change(doc)
    text = yaml.safe_dump(doc, sort_keys=False)
    if match is None:
        songio.loads(text)
        return
    with pytest.raises(ValueError, match=match):
        songio.loads(text)


def test_reader_rejects_rows_beyond_max_and_bad_stop():
    doc = yaml.safe_load(songio.dumps(song(), embed=True))
    doc["playroutine"]["record_types"]["prog"]["max"] = 3
    with pytest.raises(SongFormatError, match="more than 3"):
        songio.loads(yaml.safe_dump(doc))
    doc = yaml.safe_load(songio.dumps(song(), embed=True))
    doc["song"][0]["def"]["stopped"] = {"t1": [[5], "end"]}
    with pytest.raises(SongFormatError, match="stop row"):
        songio.loads(yaml.safe_dump(doc))
    doc["song"][0]["def"]["stopped"] = {"t1": [[5], [200], "end"]}
    assert songio.loads(yaml.safe_dump(doc))["records"]["stopped"] == {
        0: {"v": 5, "next": 1},
        1: {"v": 200, "next": 0},
    }


def test_reader_table_patch_needs_a_default():
    doc = yaml.safe_load(songio.dumps(song(), embed=True))
    doc["playroutine"] = {"ref": "goattracker2", "tables": {"zz": {0: 1}}}
    with pytest.raises(SongFormatError, match="no library default"):
        songio.loads(yaml.safe_dump(doc))


def test_when_expression_errors():
    ir = song()
    ir["playroutine"]["record_types"]["prog"]["refs"]["x"]["when"] = "nope[1]"
    with pytest.raises(SongFormatError, match="prog"):
        songio.dumps(ir, embed=True)
