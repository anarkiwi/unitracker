"""Shared vocabulary: row codecs, command names, note offsets, and the vocabulary itself."""

import math
import re

import pytest
import yaml
from pygoattracker import decompile_sid

from unitracker.format import songio
from unitracker.format.codec import (
    COMMAND_NAMES,
    ROW_KEYS,
    CodecError,
    CommandCodec,
    Evaluator,
    RowCodec,
    ctrl_text,
    ctrl_value,
    evaluate,
    note_value,
    route_text,
    route_value,
    written_value,
)
from unitracker.format.records import record_types
from unitracker.format.yamlstyle import parse
from unitracker.lift.defmon import lift as lift_defmon
from unitracker.lift.goattracker import lift as lift_gt
from unitracker.lift.sidwizard import lift as lift_sw
from unitracker.player.vm import SongPlayer

GT = songio.library("goattracker2")
DM = songio.library("defmon")
SW = songio.library("sidwizard")
GT_TYPES = record_types(GT)
DM_TYPES = record_types(DM)
SW_TYPES = record_types(SW)
LIBS = {"goattracker2": (GT, GT_TYPES), "sidwizard": (SW, SW_TYPES), "defmon": (DM, DM_TYPES)}


def codec(pr_name, name):
    pr, types = LIBS[pr_name]
    ev = Evaluator(pr.get("functions"), pr.get("tables"), pr.get("constants"))
    return RowCodec(types[name], types[name].codec, int(pr.get("note_offset", 0)), ev)


def written(raw_row, c):
    return {k: written_value(k, v) for k, v in c.encode(raw_row).items()}


@pytest.mark.parametrize(
    "raw,shared",
    [
        ({"w": 0xE9, "n": 0}, {"ctrl": 9, "note": "+0"}),
        ({"w": 129, "n": 196}, {"ctrl": 129, "note": "=68"}),
        ({"w": 1, "n": 5}, {"wait": 1, "note": "+5"}),
        ({"w": 65, "n": 0x80}, {"ctrl": 65}),
        ({"w": 0, "n": 12}, {"note": "+12"}),
        ({"w": 0, "n": 0x7F}, {"note": "-1"}),
    ],
)
def test_goattracker_wave_rows(raw, shared):
    c = codec("goattracker2", "ctrltab")
    assert written(raw, c) == shared
    assert c.decode(shared) == raw


@pytest.mark.parametrize(
    "name,raw,shared",
    [
        ("pulsetab", {"l": 0x84, "r": 0}, {"pw": 1024}),
        (
            "filttab",
            {"l": 0x90, "r": 193, "cut": 128},
            {"mode": 16, "res": 12, "route": 1, "cut": 1024},
        ),
        ("filttab", {"l": 0x90, "r": 193, "cut": -1}, {"mode": 16, "res": 12, "route": 1}),
        ("filttab", {"l": 0, "r": 240}, {"cut": 1920}),
    ],
)
def test_goattracker_pulse_and_filter_rows(name, raw, shared):
    c = codec("goattracker2", name)
    assert written(raw, c) == shared
    assert c.decode(shared) == raw


@pytest.mark.parametrize(
    "name,raw",
    [
        ("ctrltab", {"w": 0xF1, "n": 1}),
        ("pulsetab", {"l": 0, "r": 16}),
        ("filttab", {"l": 5, "r": 1, "cut": 3}),
    ],
)
def test_goattracker_leftover_rows_have_no_shape(name, raw):
    assert codec("goattracker2", name).encode(raw) is None


@pytest.mark.parametrize(
    "name,shared,match",
    [
        ("filttab", {"cut": 1025}, "values of"),
        ("filttab", {"mode": 16, "res": 0, "route": 1, "cut": 4}, "values of"),
        ("ctrltab", {"ctrl": 0xE5}, "values of"),
        ("ctrltab", {"ctrl": 9, "wait": 1}, "values of"),
        ("ctrltab", {"note": "=0"}, "values of"),
        ("ctrltab", {"hold": 2}, "unknown key hold in ctrl"),
        ("pulsetab", {"pw": 4097}, "values of"),
    ],
)
def test_goattracker_rejects_what_it_cannot_express(name, shared, match):
    with pytest.raises(CodecError, match=match):
        codec("goattracker2", name).decode(shared)


FIRST, MIDDLE, LAST, ONLY = (
    {"first": 1, "last": 0},
    {"first": 0, "last": 0},
    {"first": 0, "last": 1},
    {"first": 1, "last": 1},
)
HR = {"gate": 0, "ad": 0x0F, "sr": 0}


@pytest.mark.parametrize(
    "pr_name,place,shared",
    [
        ("goattracker2", FIRST, {"hold": 4, **HR}),
        ("goattracker2", FIRST, {"hold": 63, "gate": 0}),
        ("goattracker2", FIRST, {}),
        ("goattracker2", LAST, {"ctrl": 9, "gate": 1}),
        ("goattracker2", LAST, {"gate": 0}),
        ("goattracker2", LAST, {}),
        ("sidwizard", FIRST, {"hold": 2, "ctrl": 0x18, **HR}),
        ("sidwizard", FIRST, {**HR, "ad": 0x2F}),
        ("sidwizard", LAST, {"ctrl": 9}),
        ("sidwizard", ONLY, {}),
    ],
)
def test_restart_rows_read_back_at_their_place(pr_name, place, shared):
    c = codec(pr_name, "restart")
    assert c.encode(c.decode(shared, place), place) == shared


@pytest.mark.parametrize(
    "pr_name,place,shared",
    [
        ("goattracker2", FIRST, {"hold": 64, "gate": 0}),
        ("goattracker2", FIRST, {**HR, "ad": 0x1F}),
        ("goattracker2", FIRST, {"ad": 0x0F, "sr": 0}),
        ("goattracker2", FIRST, {"ctrl": 9}),
        ("goattracker2", MIDDLE, {"hold": 2}),
        ("goattracker2", LAST, {"hold": 2}),
        ("goattracker2", LAST, {"ctrl": 9}),
        ("goattracker2", LAST, {"ctrl": 0xFE, "gate": 1}),
        ("goattracker2", ONLY, {"ctrl": 9, "gate": 1}),
        ("goattracker2", LAST, {"ctrl": "^8", "gate": 1}),
        ("sidwizard", FIRST, {"hold": 3, **HR}),
        ("sidwizard", FIRST, {"hold": 2, "ctrl": 0x20, **HR}),
        ("sidwizard", FIRST, {"hold": 2, "ad": 0x0F, "sr": 0}),
        ("sidwizard", MIDDLE, {"hold": 1, **HR}),
        ("sidwizard", LAST, {"gate": 0}),
    ],
)
def test_restart_programs_hold_only_what_the_player_plays(pr_name, place, shared):
    with pytest.raises(
        CodecError, match=re.escape(f"restart: the player can't express row {shared!r}")
    ):
        codec(pr_name, "restart").decode(shared, place)


@pytest.mark.parametrize(
    "pr_name,name,shared,match",
    [
        ("goattracker2", "filttab", {"mode": 16, "res": 12}, r"values of \['route'\]"),
        ("goattracker2", "filttab", {"mode": 16, "res": 16, "route": 1}, r"values of \['res'\]"),
        ("goattracker2", "filttab", {"mode": 16, "res": 1, "route": "+"}, r"values of \['route'\]"),
        (
            "sidwizard",
            "flttab",
            {"mode": 16, "res": 3, "cut": 64, "route": 7},
            "unknown key route in filter",
        ),
        ("sidwizard", "flttab", {"mode": 16, "res": 16, "cut": 64}, r"values of \['res'\]"),
        ("defmon", "sidtab", {"route": "+"}, r"values of \['route'\]"),
        ("defmon", "sidtab", {"res": 3}, r"values of \['res'\]"),
        ("defmon", "sidtab", {"res": 3, "route": 8}, r"values of \['route'\]"),
        ("defmon", "sidtab", {"res": 1, "route": "-"}, r"values of \['res'\]"),
        ("defmon", "sidtab", {"route": "- 1"}, r"values of \['route'\]"),
    ],
)
def test_res_and_route_are_refused_where_the_player_cannot_set_them(pr_name, name, shared, match):
    with pytest.raises(CodecError, match=match):
        codec(pr_name, name).decode(shared)


@pytest.mark.parametrize(
    "byte,shared",
    [
        (0, {"route": "-"}),
        (0xF8, {"res": 15, "route": "+ 8"}),
        (0x9F, {"res": 9, "route": "+ 15"}),
        (0xF7, {"res": 15, "route": 7}),
        (0x10, {"res": 1, "route": 0}),
    ],
)
def test_defmon_resonance_edits_are_res_and_route(byte, shared):
    c = codec("defmon", "sidtab")
    assert written({"re": byte}, c) == shared
    assert c.decode(shared)["re"] == byte


def test_defmon_resonance_edits_round_trip_every_byte():
    c = codec("defmon", "sidtab")
    for byte in range(-1, 256):
        assert c.decode(written({"re": byte}, c))["re"] == byte


def test_route_forms():
    assert [route_value(v) for v in (7, "+", "-", "+ 8", "- 3")] == [
        (0, 7),
        (1, 0),
        (-1, 0),
        (1, 8),
        (-1, 3),
    ]
    assert [route_text(*v) for v in ((0, 7), (1, 0), (-1, 0), (1, 8))] == [7, "+", "-", "+ 8"]
    for bad in (True, "+8", "x", "+ x"):
        with pytest.raises(CodecError, match="bad route"):
            route_value(bad)


@pytest.mark.parametrize(
    "pr_name,name,raw,shared,back",
    [
        (
            "goattracker2",
            "filttab",
            {"l": 0x90, "r": 193, "x": 3},
            {"mode": 16, "res": 12, "route": 1, "cut": ("x", 3)},
            {"l": 0x90, "r": 193, "cut": -1, "x": 3},
        ),
        (
            "goattracker2",
            "filttab",
            {"l": 0x90, "r": 1, "cut": 128},
            {"mode": 16, "res": 0, "route": 1, "cut": 1024},
            {"l": 0x90, "r": 1, "cut": 128},
        ),
        (
            "sidwizard",
            "flttab",
            {"a": 0x90, "res": 3, "x": 4},
            {"mode": 16, "cut": ("x", 4), "res": 3},
            {"a": 0x90, "b": 0, "res": 3, "x": 4},
        ),
        (
            "sidwizard",
            "flttab",
            {"a": 0x90, "b": 8, "res": 3},
            {"mode": 16, "cut": 64, "res": 3},
            {"a": 0x90, "b": 8, "res": 3},
        ),
        ("sidwizard", "pwtab", {"a": 0x81, "b": 2}, {"pw": 258}, {"a": 0x81, "b": 2}),
    ],
)
def test_filter_cut_holds_an_expression_or_a_number(pr_name, name, raw, shared, back):
    c = codec(pr_name, name)
    assert written(raw, c) == shared
    assert c.decode(shared) == back


@pytest.mark.parametrize(
    "pr_name,name,raw",
    [
        ("goattracker2", "filttab", {"l": 0, "r": 5, "x": 3}),
        ("goattracker2", "pulsetab", {"l": 32, "r": 16}),
        ("sidwizard", "flttab", {"a": 0x90, "b": 8, "x": 4}),
        ("sidwizard", "flttab", {"a": 0x90, "b": 8, "k": 1}),
        ("sidwizard", "pwtab", {"a": 64, "b": 248}),
        ("sidwizard", "pwtab", {"a": 0x81, "b": 2, "k": 1}),
    ],
)
def test_sweeps_and_tracked_rows_have_no_shape(pr_name, name, raw):
    assert codec(pr_name, name).encode(raw) is None


@pytest.mark.parametrize(
    "pr_name,name,shared",
    [
        ("goattracker2", "filttab", {"cut": ("x", 3)}),
        ("goattracker2", "pulsetab", {"pw": ("x", 2)}),
        ("goattracker2", "ctrltab", {"ctrl": ("x", 1)}),
        ("sidwizard", "pwtab", {"pw": ("x", 1)}),
        ("sidwizard", "wftab", {"ctrl": ("x", 1)}),
    ],
)
def test_keys_without_an_expression_field_refuse_one(pr_name, name, shared):
    with pytest.raises(CodecError, match="holds no expression here"):
        codec(pr_name, name).decode(shared)


@pytest.mark.parametrize(
    "pr_name,name,keys",
    [
        ("goattracker2", "filttab", {"cut": True}),
        ("goattracker2", "pulsetab", {}),
        ("goattracker2", "ctrltab", {"freq": False}),
        ("sidwizard", "flttab", {"cut": True}),
        ("sidwizard", "pwtab", {}),
        ("sidwizard", "wftab", {}),
        ("defmon", "sidtab", {"pw": False, "cut": False, "freq": False}),
        ("defmon", "vinit", {}),
    ],
)
def test_expression_keys(pr_name, name, keys):
    assert codec(pr_name, name).expr_keys() == keys


DM_UNSET = {"re": -1, "fv": -1}


def test_defmon_row_and_freeze():
    c = codec("defmon", "sidtab")
    raw = {"dl": 1, "wgh": 80, "wgl": 5, "ad": 0, "sr": 68, "tr": 12, "xp": 2}
    shared = {
        "hold": 2,
        "ctrl": "80^5",
        "note": "+12",
        "ad": 0,
        "sr": 68,
        "pw": ("x", 2),
    }
    assert written(raw, c) == shared
    assert c.decode(shared) == {**DM_UNSET, **raw}
    assert written({"dl": 242, "tr": 180}, c) == {"note": "=28"}
    c.check_end({"dl": 0x80, "tr": 180}, {"note": "=28"})
    with pytest.raises(CodecError, match="can't end"):
        c.check_end({"dl": 0x80}, {"hold": 3})


@pytest.mark.parametrize(
    "text,value",
    [(65, 65), ("^8", (None, 8)), ("64^1", (64, 1)), ("16^0", (16, 0))],
)
def test_ctrl_values_and_their_masks(text, value):
    assert ctrl_value(text) == value
    if isinstance(value, tuple):
        assert ctrl_text(value) == text


def test_a_ctrl_mask_is_the_defmon_eor_column():
    c = codec("defmon", "sidtab")
    assert written({"wgl": 8}, c) == {"ctrl": "^8"}
    assert c.decode({"ctrl": "^8"})["wgl"] == 8 and c.decode({"ctrl": "^8"})["wgh"] == -1
    assert c.decode({"ctrl": 16})["wgl"] == -1
    for bad in (True, "8^", "^-1", "x"):
        with pytest.raises(CodecError, match="bad ctrl"):
            ctrl_value(bad)


@pytest.mark.parametrize(
    "pr_name,name,shared",
    [
        ("goattracker2", "ctrltab", {"ctrl": "^8"}),
        ("goattracker2", "ctrltab", {"ctrl": "65^1"}),
        ("sidwizard", "wftab", {"ctrl": "^1"}),
    ],
)
def test_instrument_players_cannot_express_a_ctrl_mask(pr_name, name, shared):
    with pytest.raises(CodecError, match="the player can't express row"):
        codec(pr_name, name).decode(shared)


def test_defmon_pitch_expression_runs_the_slide_mode():
    c = codec("defmon", "sidtab")
    raw = {"dl": 1, "wgh": 64, "wgl": 1, "af": 0x80, "xf": 2, "xc": 5}
    shared = {"hold": 2, "ctrl": "64^1", "freq": ("x", 2), "cut": ("x", 5)}
    assert written(raw, c) == shared
    assert c.decode(shared) == {**DM_UNSET, "tr": -1, "ad": -1, "sr": -1, **raw}
    assert c.decode({"freq": ("x", 1)})["af"] == 0x80


def test_defmon_note_column_round_trips_every_byte_and_the_offset_has_no_form():
    c = codec("defmon", "sidtab")
    for byte in range(256):
        assert c.decode(written({"tr": byte}, c))["tr"] == byte
    for byte in range(0x80):
        assert c.encode({"af": byte}) is None


def test_defmon_filter_mode_is_the_shared_mode():
    c = codec("defmon", "sidtab")
    for byte in range(0, 256, 16):
        assert written({"fv": byte}, c) == {"mode": byte}
        assert c.decode({"mode": byte})["fv"] == byte
    assert written({"fv": 0x2F}, c) == {"mode": 0x20}
    with pytest.raises(CodecError, match=r"values of \['mode'\]"):
        c.decode({"mode": 0x21})
    with pytest.raises(CodecError, match="can't express !vol"):
        CommandCodec(DM["commands"], DM_TYPES).decode("vol", 15)


@pytest.mark.parametrize(
    "cols",
    [
        {"pw": 1},
        {"ps": 0x85},
        {"acid": 5},
        {"af": 0x90},
        {"af": 0x80},
        {"af": 0x10, "xf": 1},
        {"af": 0x80, "xf": 1, "pw": 1},
        {"af": 0x80, "xf": 1, "acid": 0x8001},
    ],
)
def test_defmon_native_modulation_columns_have_no_shape(cols):
    assert codec("defmon", "sidtab").encode(cols) is None


@pytest.mark.parametrize(
    "shared,match",
    [
        ({"freq": 5}, "keys"),
        ({"pw": 256}, "values of"),
        ({"cut": 1024}, "values of"),
        ({"ps": 0x85}, "unknown key ps in ctrl"),
    ],
)
def test_defmon_refuses_plain_values_in_expression_keys(shared, match):
    with pytest.raises(CodecError, match=match):
        codec("defmon", "sidtab").decode(shared)


def test_check_end_compares_expression_keys_by_presence():
    c = codec("defmon", "sidtab")
    end = {"dl": 0x80, "tr": 180, "af": 0x80, "xf": 1}
    c.check_end(end, {"note": "=28", "freq": "v + 1"})
    c.check_end(end, {"note": "=28", "freq": "v - 7"})
    for row in ({"note": "=28"}, {"note": "=29", "freq": "v"}):
        with pytest.raises(CodecError, match="can't end"):
            c.check_end(end, row)
    with pytest.raises(CodecError, match="can't end"):
        c.check_end({"dl": 0x80, "tr": 180}, {"note": "=28", "freq": "v"})


def test_defmon_document_ends_on_freeze():
    doc = {
        "unitracker": "1.19",
        "playroutine": {"ref": "defmon"},
        "song": [{"def": {"ctrl": {"c1": [{"ctrl": 65}, {"hold": 3}, "end"]}}, "play": ["~"] * 3}],
    }
    with pytest.raises(songio.SongFormatError, match="can't end a program"):
        songio.loads(yaml.safe_dump(doc))


def gt_commands():
    return CommandCodec(GT["commands"], GT_TYPES)


@pytest.mark.parametrize(
    "code,arg,shared",
    [
        (15, 16, ("tempo", 16)),
        (15, 3, ("tempo", 3)),
        (15, 0x90, ("vtempo", 16)),
        (12, 128, ("cut", 1024)),
        (13, 15, ("vol", 15)),
        (13, 0x20, ("mark", 0x20)),
        (8, 3, ("run", ("ctrltab", 3))),
        (10, 2, ("run", ("filttab", 2))),
    ],
)
def test_goattracker_commands(code, arg, shared):
    cmds = gt_commands()
    assert cmds.encode(code, arg) == shared
    assert cmds.decode(*shared) == (code, arg)


def test_command_rejections():
    cmds = gt_commands()
    assert cmds.encode(15, 2) == ("tempo", 3)
    with pytest.raises(CodecError, match="no shared name"):
        cmds.encode(15, 0)
    with pytest.raises(CodecError, match="can't say which"):
        cmds.encode(8, 0)
    for name, value in [("cut", 1025), ("tempo", 2), ("run", 5), ("funk", ("ctrltab", 1))]:
        with pytest.raises(CodecError, match="can't express"):
            cmds.decode(name, value)
    with pytest.raises(CodecError, match="no shared name"):
        cmds.encode(4, 1)
    dm = CommandCodec(DM["commands"], DM_TYPES)
    assert dm.ref(1) == "sidtab" and dm.ref(2) is None
    with pytest.raises(CodecError, match="has no shared name"):
        dm.encode(1, 4)


@pytest.mark.parametrize(
    "pr_name,code,arg,shared",
    [
        ("goattracker2", 16, 3, ("freq", ("x", 3))),
        ("goattracker2", 16, 0, ("freq", "none")),
        ("goattracker2", 17, 0, ("legato", 0)),
        ("sidwizard", 16, 3, ("freq", ("x", 3))),
        ("sidwizard", 16, 0, ("freq", "none")),
        ("sidwizard", 63, 0, ("legato", 0)),
        ("sidwizard", 112, 5, ("run", ("wftab", 5))),
        ("sidwizard", 112, 0, ("run", "none")),
    ],
)
def test_freq_and_legato_commands(pr_name, code, arg, shared):
    pr, types = LIBS[pr_name]
    cmds = CommandCodec(pr["commands"], types)
    assert cmds.ref(16) == "x"
    assert cmds.encode(code, arg) == shared
    assert cmds.decode(*shared) == (code, arg)


@pytest.mark.parametrize("pr_name", ["goattracker2", "sidwizard"])
def test_unplayable_commands_are_refused(pr_name):
    pr, types = LIBS[pr_name]
    cmds = CommandCodec(pr["commands"], types)
    for name, value in [("freq", 5), ("freq", ("pwtab", 1)), ("legato", 1)]:
        with pytest.raises(CodecError, match="can't express"):
            cmds.decode(name, value)
    with pytest.raises(CodecError, match="unknown command !noteglide"):
        cmds.decode("noteglide", 1)
    with pytest.raises(CodecError, match="a byte"):
        cmds.decode("freq", ("x", 256))
    for code in (1, 2, 3, 632):
        with pytest.raises(CodecError, match="no shared name"):
            cmds.encode(code, 1)
    with pytest.raises(CodecError, match="shared command"):
        CommandCodec({1: {"name": "mod"}}, types)


def test_goattracker_legato_takes_no_argument():
    with pytest.raises(CodecError, match="no shared name"):
        gt_commands().encode(17, 1)
    with pytest.raises(CodecError, match="can't express"):
        gt_commands().decode("legato", 1)


@pytest.mark.parametrize(
    "bad",
    [
        {"keys": {"wave": 1}},
        {"keep": ["ctrl"]},
    ],
)
def test_codec_declarations_are_checked(bad):
    with pytest.raises(CodecError):
        RowCodec(GT_TYPES["ctrltab"], [bad], 0)
    with pytest.raises(CodecError, match="shared command"):
        CommandCodec({1: {"name": "wave"}}, GT_TYPES)
    with pytest.raises(CodecError, match="unknown type"):
        CommandCodec({1: {"name": "run", "ref": "nope"}}, GT_TYPES)


def test_note_values():
    assert note_value(5) == (False, 5)
    assert note_value("=68") == (True, 68)
    assert note_value("-3") == (False, -3)
    assert note_value("=-24") == (True, -24)
    for bad in [True, "=+3", "x", 1.5]:
        with pytest.raises(CodecError):
            note_value(bad)
    with pytest.raises(CodecError):
        evaluate("nope(", {})


def midi(freq_word):
    hz = freq_word * 985248 / 16777216
    return round(69 + 12 * math.log2(hz / 440))


def test_note_offsets_follow_the_frequency_tables():
    gt, dm = GT["tables"], DM["tables"]
    for index in range(24, 96):
        name = index - GT["note_offset"]
        assert midi(gt["ftab"][index]) == name + 12
    for index in range(24, 110):
        word = dm["note_lo"][index] | dm["note_hi"][index] << 8
        assert midi(word) == index - DM["note_offset"] + 12
    sw = SW["tables"]
    for index in range(1, 96):
        word = sw["freqlo"][index] | sw["freqhi"][index] << 8
        assert midi(word) == index - SW["note_offset"] + 12


INS_KEYS = {
    "defmon": {"ctrl", "filter"},
    "goattracker2": {"ad", "sr", "ctrl", "pw", "filter", "freq", "restart"},
    "sidwizard": {
        "ad",
        "sr",
        "restart",
        "ctrl",
        "pw",
        "filter",
        "freq",
    },
}
# Player-specific keys a program row may carry besides the shared ones.
ROW_KEEP = {"defmon": {"cp"}}
# Outside program rows, a shared key may appear only where it keeps its shared meaning:
# envelope and control-register values in init records, the filter registers as res, route and
# mode, and in an instrument ctrl names the program its voice runs on the control register, pw,
# filter and freq the program or expression driving the pulse width, the filter and the
# frequency.
SHARED_OUTSIDE_ROWS = {
    ("goattracker2", "ins"): {"ad", "sr", "ctrl", "pw", "filter", "freq"},
    ("sidwizard", "ins"): {"ad", "sr", "ctrl", "pw", "filter", "freq"},
    ("defmon", "ins"): {"ctrl", "filter"},
    ("defmon", "vinit"): {"note", "pw", "freq"},
    ("defmon", "ginit"): set(),
}
PROGRAMS = ("ctrl", "pw", "filter", "restart")
INS_FORMS = {
    "ctrl": (list, str),
    "pw": (list, str, int),
    "filter": (list, str, int),
    "freq": (str, int),
}
INIT_KEYS = {
    "vinit": {"sacc"},
    "ginit": {"sub"},
}


def _check_rows(name, rows, keep, exprs=frozenset()):
    for row in rows:
        if isinstance(row, dict) and set(row) != {"loop"}:
            assert set(row) <= set(ROW_KEYS) | keep, (name, row)
            text = {
                k
                for k, v in row.items()
                if isinstance(v, str) and v != "none" and k not in ("note", "route", "ctrl")
            }
            assert text <= exprs, (name, row)
        assert not isinstance(row, list), (name, row)


def _check_program(pr_name, shared, value):
    tname = next(n for n, t in LIBS[pr_name][1].items() if t.written == shared)
    exprs = set(codec(pr_name, tname).expr_keys())
    _check_rows(tname, value, ROW_KEEP.get(pr_name, set()), exprs)


def _commands(atoms):
    return {part.split(":")[0] for a in atoms if isinstance(a, str) for part in a.split("!")[1:]}


def _check_shared_outside_rows(pr_name, tname, value):
    used = set(value) & set(ROW_KEYS)
    assert used <= SHARED_OUTSIDE_ROWS.get((pr_name, tname), set()), (pr_name, tname, value)
    for key in used:
        if tname == "ins" and key in INS_FORMS:
            assert isinstance(value[key], INS_FORMS[key]), value
        else:
            assert isinstance(value[key], int) or key in ("note", "ctrl"), (tname, key, value)


def _check_def(pr_name, tname, value):
    if tname == "ins":
        assert set(value) <= INS_KEYS[pr_name]
        _check_shared_outside_rows(pr_name, tname, value)
        rtypes = LIBS[pr_name][1]
        for key, refs in rtypes["ins"].refs.items():
            if isinstance(value.get(key), list):
                _check_program(pr_name, rtypes[refs[0][0]].written, value[key])
    elif tname in INIT_KEYS:
        assert set(value) <= INIT_KEYS[tname] | SHARED_OUTSIDE_ROWS[(pr_name, tname)], value
        _check_shared_outside_rows(pr_name, tname, value)
    elif tname in PROGRAMS:
        _check_program(pr_name, tname, value)
    elif tname == "x":
        assert isinstance(value, (str, int)), value
    else:
        assert not set(value if isinstance(value, dict) else {}) & set(ROW_KEYS), (tname, value)


def vocabulary(doc):
    """Assert a song uses only the fixed v1.19 vocabulary, each shared key in its one meaning."""
    pr_name = doc["playroutine"].get("ref") or doc["playroutine"]["name"]
    commands = set()
    for item in doc["song"]:
        for tname, defs in (item.get("def") or {}).items():
            for value in defs.values():
                if tname == "phrase":
                    commands |= _commands(value)
                else:
                    _check_def(pr_name, tname, value)
    assert commands <= set(COMMAND_NAMES)
    return commands


def _keys(node, out):
    """Every mapping key under ``node``."""
    if isinstance(node, dict):
        for key, value in node.items():
            out.add(key)
            _keys(value, out)
    elif isinstance(node, list):
        for value in node:
            _keys(value, out)
    return out


def _song_keys(doc):
    keys = set()
    for item in doc["song"]:
        for tname, defs in (item.get("def") or {}).items():
            if tname != "phrase":
                for value in defs.values():
                    _keys(value, keys)
    return {k for k in keys if isinstance(k, str)}


def test_every_song_uses_only_the_shared_vocabulary(metal_warrior, crosseyed, csillag):
    ir = lift_gt(decompile_sid(metal_warrior.read_bytes(), subtune=0))
    gt_doc = parse(songio.dumps(ir))
    gt_cmds = vocabulary(gt_doc)
    assert gt_cmds == {"run", "freq", "legato", "tempo"}
    dm_doc = parse(songio.dumps(lift_defmon(crosseyed.read_bytes())))
    dm_cmds = vocabulary(dm_doc)
    assert dm_cmds == {"restart"}
    sw_doc = parse(songio.dumps(lift_sw(csillag.read_bytes())))
    sw_cmds = vocabulary(sw_doc)
    assert sw_cmds == {"freq", "ad", "sr", "run", "legato"}
    # A key two players both use is a shared key; every other key belongs to one player.
    keys = [_song_keys(d) - {"loop"} for d in (gt_doc, dm_doc, sw_doc)]
    for i, mine in enumerate(keys):
        for j, theirs in enumerate(keys):
            if i != j:
                assert mine & theirs <= set(ROW_KEYS) | {"restart"}
    assert not keys[2] & (set(COMMAND_NAMES) - {"restart"} - set(ROW_KEYS))


def defmon_doc(vinit, ginit):
    return songio.loads(
        yaml.safe_dump(
            {
                "unitracker": "1.19",
                "playroutine": {"ref": "defmon"},
                "song": [
                    {
                        "def": {
                            "vinit": {0: vinit},
                            "ginit": {0: ginit},
                            "phrase": {"p1": ["-*4"]},
                        },
                        "play": ["p1"] * 3,
                    }
                ],
                "loop": 0,
            }
        )
    )


def nfreq(note):
    return DM["tables"]["note_lo"][note] | DM["tables"]["note_hi"][note] << 8


@pytest.mark.parametrize("freq", [3, -5, 0])
def test_defmon_init_keys_mean_what_they_mean_in_rows(freq):
    ir = defmon_doc({"pw": 0x0567, "note": "=36", **({"freq": freq} if freq else {})}, {"sub": 1})
    voice, word = ir["records"]["vinit"][0], nfreq(60) + freq
    assert (voice["pw"], voice["tbase"], voice["flo"] | voice["fhi"] << 8) == (0x0567, 60, word)
    assert ir["records"]["ginit"][0] == {"sub": 1}
    grid = SongPlayer(ir).render_grid(2)
    assert list(grid[0][0:4]) == [word & 0xFF, word >> 8, 0x67, 0x05]
    text = songio.dumps(ir)
    assert f"0: {{note: =36, pw: 1383{f', freq: {freq}' if freq else ''}}}" in text
    assert songio.dumps(songio.loads(text)) == text


def test_defmon_init_frequency_is_an_offset_that_wraps():
    ir = defmon_doc({}, {})
    voice = ir["records"]["vinit"][0]
    assert voice["flo"] | voice["fhi"] << 8 == nfreq(0)
    voice.update(flo=0xFF, fhi=0xFF, tbase=0)
    assert f"freq: {0xFFFF - nfreq(0) - 0x10000}" in songio.dumps(ir)
    voice.update(flo=0, fhi=0, tbase=100)
    text = songio.dumps(ir)
    assert f"freq: -{nfreq(100)}" in text and songio.dumps(songio.loads(text)) == text


@pytest.mark.parametrize(
    "vinit,match",
    [({"pw": 0x1100}, r"values of \['pw'\]"), ({"note": "+3"}, r"values of \['note'\]")],
)
def test_defmon_init_rejects_what_has_no_written_form(vinit, match):
    with pytest.raises(songio.SongFormatError, match=match):
        defmon_doc(vinit, {})


DM_ROUTES = """
unitracker: 1.19
playroutine: {ref: defmon}
song:
  - def:
      ins:
        i1: {ctrl: [{res: 2, route: 3}, end]}
        i2: {ctrl: [{res: 15, route: + 8}, end]}
        i3: {ctrl: [{route: '-'}, end]}
      phrase:
        p1: [-*8]
        p2: [-.i1*2, -.i2*4, -.i3*2]
    play: [p1, p2, p1]
loop: 0
"""


def test_defmon_routes_relative_to_the_programs_voice():
    ir = songio.loads(DM_ROUTES)
    text = songio.dumps(ir)
    assert "route: + 8" in text and "route: '-'" in text
    assert songio.dumps(songio.loads(text)) == text
    d417 = [int(r[0x17]) for r in SongPlayer(ir).render_grid(10)]
    assert list(dict.fromkeys(d417)) == [0, 0x23, 0xF8 | 0x03 | 2, 0xF8 | 0x01]


def test_writer_refuses_an_init_cell_with_no_written_form(crosseyed):
    ir = lift_defmon(crosseyed.read_bytes())
    ir["records"]["vinit"][0]["pw"] = 0x1100
    with pytest.raises(songio.SongFormatError, match=r"vinit.*values of \['pw'\]"):
        songio.dumps(ir)


def test_writer_refuses_a_map_row_no_shape_fits(crosseyed):
    ir = lift_defmon(crosseyed.read_bytes())
    ir["playroutine"]["record_types"]["vinit"]["codec"][0]["when"] = "0"
    with pytest.raises(songio.SongFormatError, match="no shared-vocabulary form"):
        songio.dumps(ir, embed=True)


@pytest.mark.parametrize(
    "bad,match",
    [
        ({"raw": {"w": {"search": [0, 3]}, "n": {"search": [0, 3]}}}, "one field per shape"),
        ({"raw": {"w": {"range": [0, 3]}}}, "one field per shape"),
    ],
)
def test_search_declarations_are_checked(bad, match):
    with pytest.raises(CodecError, match=match):
        RowCodec(GT_TYPES["ctrltab"], [bad], 0)


def test_codec_expressions_read_only_declared_tables():
    with pytest.raises(CodecError, match="unknown table"):
        Evaluator()("t[0]", {})
    assert Evaluator(None, {"t": [7]})("t[0] + t[5]", {}) == 7


@pytest.mark.parametrize(
    "raw,shared",
    [
        ({"w": 17, "a": 0, "h": 1}, {"hold": 2, "ctrl": 17, "note": "+0"}),
        ({"w": 80, "a": 0xE0 | 0x1C, "h": 0x3F}, {"hold": 64, "ctrl": 80, "note": "-4"}),
        ({"w": 65, "a": 0x80}, {"ctrl": 65}),
    ],
)
def test_sidwizard_wf_rows_hold_for_the_arp_speed(raw, shared):
    c = codec("sidwizard", "wftab")
    assert written(raw, c) == shared
    assert c.decode(shared) == {"h": 0, "d": 0, **raw}


@pytest.mark.parametrize(
    "raw",
    [{"w": 0x50, "a": 0x7F}, {"w": 0x50, "a": 0, "h": 0x40}, {"w": 5, "a": 3, "h": 2}],
)
def test_sidwizard_wf_rows_without_a_shape(raw):
    assert codec("sidwizard", "wftab").encode(raw) is None


@pytest.mark.parametrize("hold", [1, 0, 65])
def test_sidwizard_wf_rows_refuse_holds_the_player_cannot_set(hold):
    with pytest.raises(CodecError, match="can't express"):
        codec("sidwizard", "wftab").decode({"hold": hold, "ctrl": 17})


SW_CHORD = """
unitracker: 1.19
playroutine: {ref: sidwizard}
song:
  - def:
      ins:
        i1: {ad: 1, sr: 240, ctrl: [{hold: 2, ctrl: 17, note: +0}, {hold: 2, ctrl: 17, note: +3}, {
            loop: 0}]}
      ctrl:
        c1: [{hold: 3, ctrl: 17, note: +4}, {hold: 3, ctrl: 17, note: +7}, {loop: 0}]
      phrase:
        p1: [C4.i1*4, C4!run:@c1*4]
    play: [p1, p1, p1]
loop: 0
"""


def _runs(values):
    out = [1]
    for prev, cur in zip(values, values[1:]):
        out[-1:] = [out[-1] + 1] if cur == prev else [out[-1], 1]
    return out


def test_sidwizard_chords_are_ctrl_programs_started_by_run():
    ir = songio.loads(SW_CHORD)
    assert songio.dumps(songio.loads(songio.dumps(ir))) == songio.dumps(ir)
    events = ir["phrases"][next(iter(ir["phrases"]))]
    assert events[1]["command"][0] == 112
    freq = [int(r[0]) | int(r[1]) << 8 for r in SongPlayer(ir).render_grid(48)]
    first, second = freq[6:24], freq[30:48]
    assert set(_runs(first)[1:-1]) == {2} and len(set(first)) == 2
    assert set(_runs(second)[1:-1]) == {3} and len(set(second)) == 2
    assert not set(first) & set(second)
