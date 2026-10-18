"""The v1.19 document: canonical writing, reading back, and the errors a reader reports."""

import copy
import re

import numpy as np
import pytest
import yaml
from pygoattracker import decompile_sid

from unitracker.format import songio
from unitracker.format.codec import COMMAND_NAMES, ROW_KEYS, CommandCodec
from unitracker.format.records import EXPR
from unitracker.format.songio import SongFormatError
from unitracker.format.yamlstyle import parse
from unitracker.lift.goattracker import lift
from unitracker.player.vm import SongPlayer

PLAYROUTINE = {
    "name": "mini",
    "defaults": {
        "clock": {"cycles_per_tick": 19656, "clock_hz": 985248},
        "voices": 2,
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
    doc = parse(text)
    assert list(doc) == ["unitracker", "playroutine", "meta", "hints", "song", "loop"]
    assert doc["meta"] == {"title": "Mini", "author": "Test", "released": "2026"}
    assert doc["hints"] == {"ticks_per_step": 1, "phrase_steps": 4}


def test_programs_split_named_inline_and_by_first_use():
    doc = parse(songio.dumps(song(), embed=True))
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
    doc = parse(songio.dumps(ir))
    assert doc["playroutine"]["ref"] == "goattracker2"
    assert doc["playroutine"]["constants"] == {"deftempo": 11}
    assert "tables" not in doc["playroutine"]
    assert "clock" not in doc
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


@pytest.mark.parametrize(
    "value,match",
    [
        ({"tempo": 6}, "hints: unknown key 'tempo'"),
        ({"ticks_per_step": "6"}, "hints: ticks_per_step must be a positive integer"),
        ({"ticks_per_step": 0}, "hints: ticks_per_step must be a positive integer"),
        ({"phrase_steps": True}, "hints: phrase_steps must be a positive integer"),
        ([6], "hints must be a mapping"),
    ],
)
def test_reader_rejects_bad_hints(value, match):
    doc = yaml.safe_load(songio.dumps(song(), embed=True))
    doc["hints"] = value
    with pytest.raises(SongFormatError, match=match):
        songio.loads(yaml.dump(doc))


def test_writer_refuses_disagreeing_phrase_steps():
    doc = yaml.safe_load(songio.dumps(song(), embed=True))
    doc["hints"]["phrase_steps"] = 5
    ir = songio.loads(yaml.dump(doc))
    assert ir["hints"] == {"ticks_per_step": 1, "phrase_steps": 5}
    with pytest.raises(SongFormatError, match=r"phrase_steps 5 disagrees .* lengths \[4\]"):
        songio.dumps(ir, embed=True)
    ir = song()
    ir["phrases"]["c"].append({})
    ir["hints"]["phrase_steps"] = 4
    with pytest.raises(SongFormatError, match=r"phrase_steps 4 disagrees .* lengths \[4, 5\]"):
        songio.dumps(ir, embed=True)


def test_document_without_hints_reads_and_writes():
    ir = song()
    del ir["hints"]
    ir["phrases"]["c"].append({})
    text = songio.dumps(ir, embed=True)
    assert "hints" not in yaml.safe_load(text)
    back = songio.loads(text)
    assert not back["hints"]
    assert songio.dumps(back, embed=True) == text


def edit(path, value):
    def apply(doc):
        node = doc
        for key in path[:-1]:
            node = node.setdefault(key, {}) if isinstance(node, dict) else node[key]
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
        (edit(DEF + ("ins", "i1"), {"q": 1}), "unknown key q in ins"),
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
        (edit(DEF + ("phrase", "p2"), ["-!run:@w1!run:@w1!run:@w1"]), "more than 2 commands"),
        (edit(DEF + ("phrase", "p2"), ["C2.w1"]), "not a ins"),
        (edit(("song", 0, "play"), ["p1", "p2+x"]), "bad play entry"),
        (edit(("song", 0, "play"), ["p1"]), "entries"),
    ],
)
def test_reader_errors(change, match):
    doc = parse(songio.dumps(song(), embed=True))
    change(doc)
    text = yaml.safe_dump(doc, sort_keys=False)
    if match is None:
        songio.loads(text)
        return
    with pytest.raises(ValueError, match=match):
        songio.loads(text)


LIST_SONG = """
unitracker: 1.19
playroutine: {{ref: {pr}}}
song:
  - def:
      {tname}:
        {name}1: [{row}, end]
      phrase:
        p1: [off]
    play: [p1, p1, p1]
loop: ~
"""
LIBRARY = ("goattracker2", "sidwizard", "defmon")


def _unknown(pr: str):
    """``(path, value, message)``: one key outside the format at each place a document can hold
    one, generated from the playroutine's record types, codecs and commands."""
    lib = songio.library(pr)
    rtypes, codecs = songio.record_types(lib), songio.codecs(lib, {})
    inline = {CommandCodec(lib["commands"], rtypes).inline(n) for n in COMMAND_NAMES}
    out = [(("bogus",), 1, "unknown key bogus in document")]
    out.append((DEF + ("phrase", "p1"), ["off!bogus:1"], "unknown command !bogus in 'off!bogus:1'"))
    for rtype in rtypes.values():
        if rtype.name == EXPR:
            continue
        if rtype.name != rtype.written:
            out.append((DEF + (rtype.name, "z1"), {}, f"unknown record type {rtype.name!r}"))
        if rtype.name in inline:
            out.append((DEF + (rtype.written, "z1"), [], f"unknown record type {rtype.written!r}"))
            continue
        codec = codecs.get(rtype.name)
        known = codec.keys if codec else set(rtype.fields) - rtype.unwritten
        name = 0 if rtype.fixed else f"{rtype.prefix}9"

        def entry(row, rtype=rtype):
            return row if rtype.chain is None else [row, "end"]

        for key in sorted((set(rtype.fields) | set(ROW_KEYS)) - known - {rtype.chain}):
            path = DEF + (rtype.written, name)
            out.append((path, entry({key: 1}), f"unknown key {key} in {rtype.written}"))
        if codec and rtype.form == "list":
            out.append((DEF + (rtype.written, name), entry([1]), "with shared keys"))
    return out


@pytest.mark.parametrize("pr", LIBRARY)
def test_the_reader_refuses_any_key_outside_the_format(pr):
    base = parse(LIST_SONG.format(pr=pr, tname="ctrl", name="c", row="{ctrl: 65}"))
    songio.loads(yaml.safe_dump(base, sort_keys=False))
    cases = _unknown(pr)
    assert len(cases) > 10
    for path, value, message in cases:
        doc = copy.deepcopy(base)
        edit(path, value)(doc)
        with pytest.raises(SongFormatError, match=re.escape(message)):
            songio.loads(yaml.safe_dump(doc, sort_keys=False))


def _unwritable_program(lib: dict) -> dict:
    """Records holding the instrument's control program as one looping row no codec shape
    writes."""
    rtypes, native = songio.record_types(lib), lib["instrument"]["type"]
    target = rtypes[native] if rtypes[native].chain else rtypes[rtypes["ins"].refs["ctrl"][0][0]]
    codec, blank = songio.codecs(lib, {})[target.name], {f: target.default(f) for f in target.own}
    row = next(
        r for f in target.own for v in range(1, 256) if codec.encode(r := {**blank, f: v}) is None
    )
    records = {target.name: {1: {**row, target.chain: 1 if target.none is None else target.none}}}
    return records | ({"ins": {1: {"ctrl": 1}}} if native == "ins" else {})


@pytest.mark.parametrize("pr", LIBRARY)
def test_the_writer_refuses_what_has_no_written_form(pr):
    """An unwritten instrument field off its default, and a program row no codec shape writes."""
    lib = songio.library(pr)
    ins = songio.record_types(lib)["ins"]
    cases = [
        ({"ins": {1: {f: ins.default(f) + 1}}}, f"ins.{f} has no written form")
        for f in sorted(ins.unwritten)
    ] + [(_unwritable_program(lib), "ctrl: row")]
    loaded = songio.loads(LIST_SONG.format(pr=pr, tname="ctrl", name="c", row="{ctrl: 65}"))
    for records, message in cases:
        ir = copy.deepcopy(loaded)
        ir["records"].update(records)
        ir["phrases"]["p1"] = [{"note": 30, "instrument": 1 + lib["instrument"].get("base", 0)}]
        with pytest.raises(SongFormatError, match=re.escape(message)):
            songio.dumps(ir)


def test_a_pulse_program_name_is_not_a_phrase_name():
    text = LIST_SONG.format(pr="goattracker2", tname="pw", name="pw", row="{pw: 2048}")
    text = text.replace("p1: [off]", "p1: [off!run:@pw1]")
    out = songio.dumps(songio.loads(text))
    assert "pw1: [{pw: 2048}, end]" in out and "p1: [off!run:@pw1]" in out
    assert songio.dumps(songio.loads(out)) == out
    with pytest.raises(SongFormatError, match="name 'p1' defined twice"):
        songio.loads(text.replace("pw1", "p1"))


def test_the_version_is_its_text():
    text = LIST_SONG.format(pr="goattracker2", tname="ctrl", name="c", row="{ctrl: 65}")
    assert songio.dumps(songio.loads(text)).startswith("unitracker: 1.19\n")
    with pytest.raises(SongFormatError, match=r"not a unitracker v1\.19 document"):
        songio.loads(text.replace("1.19", "1.1", 1))


def test_reader_rejects_rows_beyond_max_and_bad_stop():
    doc = parse(songio.dumps(song(), embed=True))
    doc["playroutine"]["record_types"]["prog"]["max"] = 3
    with pytest.raises(SongFormatError, match="more than 3"):
        songio.loads(yaml.safe_dump(doc))
    doc = parse(songio.dumps(song(), embed=True))
    doc["song"][0]["def"]["stopped"] = {"t1": [[5], "end"]}
    with pytest.raises(SongFormatError, match="stop row"):
        songio.loads(yaml.safe_dump(doc))
    doc["song"][0]["def"]["stopped"] = {"t1": [[5], [200], "end"]}
    assert songio.loads(yaml.safe_dump(doc))["records"]["stopped"] == {
        0: {"v": 5, "next": 1},
        1: {"v": 200, "next": 0},
    }


def test_reader_table_patch_needs_a_default():
    doc = parse(songio.dumps(song(), embed=True))
    doc["playroutine"] = {"ref": "goattracker2", "tables": {"zz": {0: 1}}}
    with pytest.raises(SongFormatError, match="no library default"):
        songio.loads(yaml.safe_dump(doc))


def test_when_expression_errors():
    ir = song()
    ir["playroutine"]["record_types"]["prog"]["refs"]["x"]["when"] = "nope[1]"
    with pytest.raises(SongFormatError, match="prog"):
        songio.dumps(ir, embed=True)
