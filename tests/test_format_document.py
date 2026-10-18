"""The v1.20 document: canonical writing, reading back and its errors, record type declarations
and templates, and the track timeline."""

import copy
import re

import numpy as np
import pytest
import yaml
from conftest import mini_song

from unitracker.format import songio
from unitracker.format import timeline as tl
from unitracker.format.codec import COMMAND_NAMES, ROW_KEYS, CommandCodec
from unitracker.format.expr import ExprError
from unitracker.format.records import EXPR, RecordType, RecordTypeError, record_types
from unitracker.format.songio import SongFormatError
from unitracker.format.timeline import END, TimelineError
from unitracker.format.yamlstyle import parse
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
            "none": None,
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
                    {"run": {"record": "prog", "index": "ptr", "template": "do"}},
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


def edit(path, value):
    def apply(doc):
        node = doc
        for key in path[:-1]:
            node = node.setdefault(key, {}) if isinstance(node, dict) else node[key]
        node[path[-1]] = value

    return apply


DEF = ("song", 0, "def")
LIST_SONG = """
unitracker: 1.20
playroutine: {{ref: {pr}}}
song:
  - def:
      {tname}:
        {name}1: [{row}, end]
      phrase:
        p1: [off]
    play: [p1, p1, p1]
  - end
"""
LIBRARY = ("goattracker2", "sidwizard", "defmon")


def list_song(pr="goattracker2", tname="ctrl", name="c", row="{ctrl: 65}"):
    return LIST_SONG.format(pr=pr, tname=tname, name=name, row=row)


def test_roundtrip_plays_identically_and_is_canonical():
    ir = song()
    text = songio.dumps(ir, embed=True)
    back = songio.loads(text)
    np.testing.assert_array_equal(SongPlayer(back).render_grid(64), SongPlayer(ir).render_grid(64))
    assert songio.dumps(back, embed=True) == text
    doc = parse(text)
    assert list(doc) == ["unitracker", "playroutine", "meta", "song"]
    assert doc["meta"] == {"title": "Mini", "author": "Test", "released": "2026"}
    first = doc["song"][0]["def"]
    assert first["init"] == {0: {"o": 5}}
    assert first["prog"] == {"w1": [[10], [20], [30], {"loop": 0}], "w2": [[60], "end"]}
    assert first["ins"]["i1"] == {"a": 1, "p": "w1"}
    assert first["ins"]["i2"] == {"a": 2, "p": [[20], [30], [10], {"loop": 0}]}
    assert first["ins"]["i3"] == {"p": [[99, "w2"], [50], "end"]}
    assert "i4" not in first["ins"]
    assert first["phrase"]["p1"] == ["C2.i1*2", "D2.i2*2"]
    assert first["phrase"]["p2"] == ["-!run:w1", "F#2.i3*3"]
    assert doc["song"][0]["play"] == ["p1", "p2"]
    assert doc["song"][1]["play"] == ["p1+3", "p2"]
    assert doc["song"][-1] == {"loop": 0}


def test_a_document_is_dumped_whole_and_loads_back(tmp_path):
    ir = song()
    ir["phrases"]["c"].append({})
    path = tmp_path / "s.yaml"
    songio.dump(ir, path, embed=True)
    assert [p.name for p in tmp_path.iterdir()] == ["s.yaml"]
    text = path.read_text()
    assert songio.dumps(songio.load(path), embed=True) == text


def test_reference_form_patches_library_tables(references):
    ref = references["metal_warrior"]
    doc = parse(ref.text)
    assert doc["playroutine"]["ref"] == "goattracker2"
    assert "constants" not in doc["playroutine"] and doc["tempo"] == 12
    assert "tables" not in doc["playroutine"]
    assert "clock" not in doc
    ir = ref.ir()
    ir["tables"]["ftab"] = ir["tables"]["ftab"][:4]
    assert songio.loads(songio.dumps(ir))["tables"]["ftab"] == ir["tables"]["ftab"]
    ir["playroutine"]["procs"]["wnote"] = []
    with pytest.raises(SongFormatError, match="differs from the library"):
        songio.dumps(ir)


@pytest.mark.parametrize(
    "change,match",
    [
        (lambda ir: ir["records"]["prog"].pop(2), "row 2 is referenced but not defined"),
        (lambda ir: ir["records"]["ins"][1].update(z=1), "not declared"),
        (
            edit(("playroutine", "record_types", "prog", "refs", "x", "when"), "nope[1]"),
            "prog: unknown table 'nope'",
        ),
    ],
)
def test_writer_errors(change, match):
    ir = song()
    change(ir)
    with pytest.raises(SongFormatError, match=match):
        songio.dumps(ir, embed=True)


@pytest.mark.parametrize(
    "change,match",
    [
        (edit(("hints",), {"phrase_steps": 4}), "unknown key hints in document"),
        (edit(("tempo",), 6), "mini: the player has no tempo"),
        (edit(("tempo",), [6, 6]), "two different tempos"),
        (edit(("song",), []), "list of rows"),
        (edit(("song", 1), {"def": {}}), "play list"),
        (edit(("song", 2), {"play": ["p1", "p2"]}), r"ends with end or \{loop: k\}"),
        (edit(("song", 2), {"loop": 9}), "loop row 9 is outside"),
        (edit(("loop",), 0), "unknown key loop in document"),
        (edit(("playroutine",), "mini"), "mapping"),
        (edit(("playroutine",), {"ref": "nope"}), "no playroutine"),
        (edit(("playroutine",), {"ref": "goattracker2", "tables": {"zz": {0: 1}}}), "no library"),
        (edit(("playroutine", "record_types", "prog", "max"), 3), "more than 3"),
        (edit(DEF + ("prog", "w1"), [[10]]), "rows then end"),
        (edit(DEF + ("prog", "w1"), [[10], {"loop": 7}]), "bad program end"),
        (edit(DEF + ("prog", "w1"), [[1, 2, 3], "end"]), "bad row"),
        (edit(DEF + ("stopped",), {"t1": [[5], "end"]}), "stop row"),
        (edit(DEF + ("ins", "i1"), [1]), "must be a mapping"),
        (edit(DEF + ("ins", "i1"), {"a": 1.5}), "bad value"),
        (edit(DEF + ("ins", "i1"), {"a": [[1], "end"]}), "cannot hold a program"),
        (edit(DEF + ("ins", "i1"), {"p": "i2"}), "not a prog"),
        (edit(DEF + ("ins", "i1"), {"p": "nothing"}), "unknown name 'nothing'"),
        (edit(DEF + ("ins", "1x"), {}), "bad name"),
        (edit(DEF + ("ins", "w1"), {}), "defined twice"),
        (edit(DEF + ("phrase", "p2"), ["-!ad:w1"]), "can't express"),
        (edit(DEF + ("phrase", "p2"), ["-!run:zz"]), "unknown name 'zz' in"),
        (edit(DEF + ("phrase", "p2"), ["-!run:w1!run:w1!run:w1"]), "more than 2 commands"),
        (edit(("song", 0, "play"), ["p1", "p2+x"]), "bad play entry"),
    ],
)
def test_reader_errors(change, match):
    doc = parse(songio.dumps(song(), embed=True))
    change(doc)
    with pytest.raises(SongFormatError, match=match):
        songio.loads(yaml.safe_dump(doc, sort_keys=False))


def test_reader_reads_a_program_ending_on_its_stop_row():
    doc = parse(songio.dumps(song(), embed=True))
    doc["song"][0]["def"]["stopped"] = {"t1": [[5], [200], "end"]}
    assert songio.loads(yaml.safe_dump(doc))["records"]["stopped"] == {
        0: {"v": 5, "next": 1},
        1: {"v": 200, "next": 0},
    }


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
    base = parse(list_song(pr))
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
        r
        for f in target.own
        if f not in target.refs
        for v in range(1, 256)
        if codec.encode(r := {**blank, f: v}) is None
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
    loaded = songio.loads(list_song(pr))
    for records, message in cases:
        ir = copy.deepcopy(loaded)
        ir["records"].update(records)
        ir["phrases"]["p1"] = [{"note": 30, "instrument": 1 + lib["instrument"].get("base", 0)}]
        with pytest.raises(SongFormatError, match=re.escape(message)):
            songio.dumps(ir)


def test_a_pulse_program_name_is_not_a_phrase_name():
    text = list_song(tname="pw", name="pw", row="{pw: 2048}").replace("[off]", "[off!run:pw1]")
    out = songio.dumps(songio.loads(text))
    assert "pw1: [{pw: 2048}, end]" in out and "p1: [off!run:pw1]" in out
    assert songio.dumps(songio.loads(out)) == out
    with pytest.raises(SongFormatError, match="name 'p1' defined twice"):
        songio.loads(text.replace("pw1", "p1"))


def test_the_version_is_its_text():
    text = list_song()
    assert songio.dumps(songio.loads(text)).startswith("unitracker: 1.20\n")
    for version in ("1.19", "1.2", "0"):
        with pytest.raises(SongFormatError, match=r"not a unitracker v1\.20 document"):
            songio.loads(text.replace("1.20", version, 1))


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
    pr = songio.library(name)
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


def play(name, transpose=0, repeat=1):
    return {"phrase": name, "transpose": transpose, "repeat": repeat}


def expand(track, ticks, lengths):
    """The ``(time, phrase, transpose)`` starts of a track over ``ticks`` steps."""
    intro, cycle = tl._items(track)  # pylint: disable=protected-access
    out, t, k = [], 0, 0
    while t < ticks and (k < len(intro) or cycle):
        item = intro[k] if k < len(intro) else cycle[(k - len(intro)) % len(cycle)]
        out.append((t, *item))
        t += lengths[item[0]]
        k += 1
    return out


def column(rows, voice):
    return [row[voice] for row in rows]


@pytest.mark.parametrize(
    "lengths,tracks,check",
    [
        (
            {"a": 4, "b": 4},
            [{"plays": [play("a"), play("b", 2)], "loop": 0}, {"plays": [play("b")], "loop": 0}],
            lambda rows, loop: (rows, loop) == ([[("a", 0), ("b", 0)], [("b", 2), ("b", 0)]], 0),
        ),
        (
            {"a": 2, "b": 3},
            [{"plays": [play("a")], "loop": 0}, {"plays": [play("b")], "loop": 0}],
            lambda rows, loop: loop == 0
            and column(rows, 0).count(("a", 0)) == 3
            and column(rows, 1).count(("b", 0)) == 2,
        ),
        (
            {"i": 3, "a": 2, "b": 4},
            [
                {"plays": [play("i"), play("a", repeat=2)], "loop": 1},
                {"plays": [play("b", 1)], "loop": 0},
            ],
            lambda rows, loop: True,
        ),
        (
            {"a": 2, "b": 5},
            [{"plays": [play("a")], "loop": None}, {"plays": [play("b")], "loop": 0}],
            lambda rows, loop: rows[1][0] == END,
        ),
        (
            {"a": 2, "b": 5},
            [{"plays": [play("a")], "loop": None}, {"plays": [play("b")], "loop": None}],
            lambda rows, loop: loop is None and rows[-1] == [None, END],
        ),
    ],
)
def test_timeline_round_trip(lengths, tracks, check):
    rows, loop = tl.to_rows(tracks, lengths)
    assert check(rows, loop)
    back = tl.from_rows(rows, loop, lengths, len(tracks))
    for orig, new in zip(tracks, back):
        assert expand(orig, 400, lengths) == expand(new, 400, lengths)


@pytest.mark.parametrize(
    "rows,loop,match",
    [
        ([], 0, "no rows"),
        ([[("a", 0)]], 0, "entries"),
        ([[("a", 0), None]], 0, "must start"),
        ([[("a", 0), ("b", 0)], [("a", 0), ("a", 0)]], 0, "still playing"),
        ([[("z", 0), ("a", 0)]], 0, "unknown phrase"),
        ([[("a", 0), ("a", 0)]], 5, "outside"),
        ([[END, ("a", 0)]], 0, "never starts"),
    ],
)
def test_timeline_errors(rows, loop, match):
    with pytest.raises(TimelineError, match=match):
        tl.from_rows(rows, loop, {"a": 2, "b": 4}, 2)


def test_unaligned_loops_beyond_the_row_limit(monkeypatch):
    monkeypatch.setattr(tl, "MAX_ROWS", 3)
    with pytest.raises(TimelineError, match="align"):
        tl.to_rows(
            [{"plays": [play("a")], "loop": 0}, {"plays": [play("b")], "loop": 0}],
            {"a": 2, "b": 3},
        )
