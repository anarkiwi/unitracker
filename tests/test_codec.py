"""Shared vocabulary: row codecs, command names, note offsets, and the vocabulary itself."""

import math

import pytest
import yaml
from pygoattracker import decompile_sid

from unitracker import songio
from unitracker.codec import (
    COMMAND_NAMES,
    ROW_KEYS,
    CodecError,
    CommandCodec,
    Evaluator,
    RowCodec,
    evaluate,
    note_text,
    note_value,
)
from unitracker.defmon import lift as lift_defmon
from unitracker.goattracker import lift as lift_gt
from unitracker.records import record_types
from unitracker.sidwizard import lift as lift_sw
from unitracker.vm import SongPlayer

GT = songio.library("goattracker2")
DM = songio.library("defmon")
SW = songio.library("sidwizard")
GT_TYPES = record_types(GT)
DM_TYPES = record_types(DM)
SW_TYPES = record_types(SW)
LIBS = {"goattracker2": (GT, GT_TYPES), "sidwizard": (SW, SW_TYPES), "defmon": (DM, DM_TYPES)}


def codec(pr_name, name):
    pr, types = LIBS[pr_name]
    ev = Evaluator(pr.get("functions"), pr.get("tables"))
    return RowCodec(types[name], types[name].codec, int(pr.get("note_offset", 0)), ev)


def written(raw_row, c):
    return {k: note_text(*v) if k == "note" else v for k, v in c.encode(raw_row).items()}


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
        ("filttab", {"l": 0x90, "r": 193, "cut": 128}, {"mode": 16, "resfilt": 193, "cut": 1024}),
        ("filttab", {"l": 0x90, "r": 193, "cut": -1}, {"mode": 16, "resfilt": 193}),
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
        ("filttab", {"mode": 16, "resfilt": 1, "cut": 4}, "values of"),
        ("ctrltab", {"ctrl": 0xE5}, "values of"),
        ("ctrltab", {"ctrl": 9, "wait": 1}, "values of"),
        ("ctrltab", {"note": "=0"}, "values of"),
        ("ctrltab", {"hold": 2}, "keys"),
        ("pulsetab", {"pw": 4097}, "values of"),
    ],
)
def test_goattracker_rejects_what_it_cannot_express(name, shared, match):
    with pytest.raises(CodecError, match=match):
        codec("goattracker2", name).decode(shared)


@pytest.mark.parametrize(
    "pr_name,name,raw,shared,back",
    [
        (
            "goattracker2",
            "filttab",
            {"l": 0x90, "r": 193, "x": 3},
            {"mode": 16, "resfilt": 193, "cut": ("x", 3)},
            {"l": 0x90, "r": 193, "cut": -1, "x": 3},
        ),
        (
            "goattracker2",
            "filttab",
            {"l": 0x90, "r": 1, "cut": 128},
            {"mode": 16, "resfilt": 1, "cut": 1024},
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
    assert c.encode(raw) == shared
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
        ("goattracker2", "ctrltab", {}),
        ("sidwizard", "flttab", {"cut": True}),
        ("sidwizard", "pwtab", {}),
        ("sidwizard", "wftab", {}),
        ("defmon", "sidtab", {"pw": False, "cut": False, "freq": False}),
        ("defmon", "vinit", {}),
    ],
)
def test_expression_keys(pr_name, name, keys):
    assert codec(pr_name, name).expr_keys() == keys


DM_UNSET = {"re": -1, "fv": -1, "cp": -1}


def test_defmon_row_and_freeze():
    c = codec("defmon", "sidtab")
    raw = {"dl": 1, "wgh": 80, "wgl": 5, "ad": 0, "sr": 68, "tr": 12, "af": 0x10, "xp": 2}
    shared = {
        "hold": 2,
        "ctrl": 80,
        "note": "+12",
        "ad": 0,
        "sr": 68,
        "pw": ("x", 2),
        "wgl": 5,
        "af": 0x10,
    }
    assert written(raw, c) == shared
    assert c.decode(shared) == {**DM_UNSET, **raw}
    assert written({"dl": 242, "tr": 180}, c) == {"note": "=28"}
    c.check_end({"dl": 0x80, "tr": 180}, {"note": "=28"})
    with pytest.raises(CodecError, match="can't end"):
        c.check_end({"dl": 0x80}, {"hold": 3})


def test_defmon_pitch_expression_runs_the_slide_mode():
    c = codec("defmon", "sidtab")
    raw = {"dl": 1, "wgh": 64, "wgl": 1, "af": 0x80, "xf": 2, "xc": 5}
    shared = {"hold": 2, "ctrl": 64, "wgl": 1, "freq": ("x", 2), "cut": ("x", 5)}
    assert written(raw, c) == shared
    assert c.decode(shared) == {**DM_UNSET, "tr": -1, "ad": -1, "sr": -1, **raw}
    assert c.decode({"freq": ("x", 1)})["af"] == 0x80


def test_defmon_note_and_offset_columns_round_trip_every_byte():
    c = codec("defmon", "sidtab")
    for byte in range(256):
        assert c.decode(written({"tr": byte}, c))["tr"] == byte
    for byte in range(0x80):
        assert written({"af": byte}, c) == {"af": byte}
        assert c.decode({"af": byte})["af"] == byte


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
        ({"freq": ("x", 1), "af": 0x10}, "keys"),
        ({"ps": 0x85}, "keys"),
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
        "unitracker": 1.6,
        "playroutine": {"ref": "defmon"},
        "song": [
            {"def": {"sidtab": {"t1": [{"ctrl": 65}, {"hold": 3}, "end"]}}, "play": ["~"] * 3}
        ],
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
    for name, value in [("cut", 1025), ("tempo", 2), ("run", 5), ("vib", ("ctrltab", 1))]:
        with pytest.raises(CodecError, match="can't express"):
            cmds.decode(name, value)
    dm = CommandCodec(DM["commands"], DM_TYPES)
    assert dm.encode(1, 4) == ("run2", ("sidtab", 4))
    with pytest.raises(CodecError, match="can't express"):
        dm.decode("run2", "none")


@pytest.mark.parametrize(
    "pr_name,code,arg,shared",
    [
        ("goattracker2", 16, 3, ("freq", ("x", 3))),
        ("goattracker2", 16, 0, ("freq", "none")),
        ("goattracker2", 17, 0, ("legato", 0)),
        ("sidwizard", 16, 3, ("freq", ("x", 3))),
        ("sidwizard", 16, 0, ("freq", "none")),
        ("sidwizard", 63, 0, ("legato", 0)),
        ("sidwizard", 63, 1, ("legato", 1)),
    ],
)
def test_freq_and_legato_commands(pr_name, code, arg, shared):
    pr, types = LIBS[pr_name]
    cmds = CommandCodec(pr["commands"], types)
    assert cmds.ref(16) == "x"
    assert cmds.encode(code, arg) == shared
    assert cmds.decode(*shared) == (code, arg)


@pytest.mark.parametrize("pr_name", ["goattracker2", "sidwizard"])
def test_retired_and_unplayable_commands_are_refused(pr_name):
    pr, types = LIBS[pr_name]
    cmds = CommandCodec(pr["commands"], types)
    for name, value in [("noteglide", 1), ("freq", 5), ("freq", ("pwtab", 1))]:
        with pytest.raises(CodecError, match="can't express"):
            cmds.decode(name, value)
    with pytest.raises(CodecError, match="retired in v1.5"):
        cmds.decode("mod", 0)
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


PROGRAM_TYPES = {
    "goattracker2": {"ctrl": "ctrltab", "pw": "pulsetab", "filter": "filttab"},
    "sidwizard": {"ctrl": "wftab", "pw": "pwtab", "filter": "flttab"},
}
INS_KEYS = {
    "goattracker2": {"ad", "sr", "ctrl", "pw", "filter", "speed", "vd", "gt", "fw"},
    "sidwizard": {
        "flags",
        "hrad",
        "hrsr",
        "ad",
        "sr",
        "vibrato",
        "vibdelay",
        "arpspeed",
        "chord",
        "octave",
        "firstctrl",
        "ctrl",
        "pw",
        "filter",
        "freq",
    },
}
DM_KEEP = {"wgl", "af", "ps", "re", "fv", "cp", "acid"}
# Player-specific keys a program row may carry besides the shared ones.
ROW_KEEP = {"defmon": DM_KEEP, "sidwizard": {"res"}}
# Program types that may hold list rows, with the rows each allows.
LIST_ROWS = {
    "ctrltab": lambda r: r[0] >= 0xF0,
    "pulsetab": lambda r: r[0] < 0x80,
    "filttab": lambda r: 0 < r[0] < 0x80 and len(r) > 2,
    "wftab": lambda r: r[0] < 0x10 or r[1:2] == [0x7F] or len(r) > 2,
    "pwtab": lambda r: len(r) > 2,
    "flttab": lambda r: len(r) > 2,
}
# Outside program rows, a shared key may appear only where it keeps its shared meaning:
# envelope and control-register values in init records, the $D417 value as resfilt, and in an
# instrument ctrl names the program its voice runs on the control register, pw and freq the
# program or expression driving the pulse width and the frequency.
SHARED_OUTSIDE_ROWS = {
    ("goattracker2", "ins"): {"ad", "sr", "ctrl", "pw"},
    ("sidwizard", "ins"): {"ad", "sr", "ctrl", "pw", "freq"},
    ("defmon", "vinit"): {"ctrl", "note", "ad", "sr", "pw"},
    ("defmon", "ginit"): {"resfilt"},
}
INS_FORMS = {"ctrl": (list, str), "pw": (list, str, int), "freq": (str, int)}
INIT_KEYS = {
    "vinit": {"flo", "fhi", "sacc", "tbase", "rowA", "cntA", "rowB", "cntB"} | DM_KEEP,
    "ginit": {"fv", "cp", "acc", "astep", "sub"},
}


def _check_rows(name, rows, keep, exprs=frozenset()):
    for row in rows:
        if isinstance(row, dict) and set(row) != {"loop"}:
            assert set(row) <= set(ROW_KEYS) | keep, (name, row)
            text = {k for k, v in row.items() if isinstance(v, str) and k != "note"}
            assert text <= exprs, (name, row)
        elif isinstance(row, list):
            assert name in LIST_ROWS and LIST_ROWS[name](row), (name, row)


def _check_program(pr_name, tname, value):
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
            assert isinstance(value[key], int) or key == "note", (tname, key, value)


def _check_def(pr_name, tname, value):
    if tname == "ins":
        assert set(value) <= INS_KEYS[pr_name]
        _check_shared_outside_rows(pr_name, tname, value)
        for key, rtype in PROGRAM_TYPES[pr_name].items():
            if isinstance(value.get(key), list):
                _check_program(pr_name, rtype, value[key])
    elif tname in INIT_KEYS:
        assert set(value) <= INIT_KEYS[tname] | SHARED_OUTSIDE_ROWS[(pr_name, tname)], value
        _check_shared_outside_rows(pr_name, tname, value)
    elif tname in ("ctrltab", "pulsetab", "filttab", "sidtab", "wftab", "pwtab", "flttab"):
        _check_program(pr_name, tname, value)
    elif tname == "chordtab":
        _check_rows(tname, value, set())
    elif tname == "x":
        assert isinstance(value, (str, int)), value
    else:
        assert not set(value if isinstance(value, dict) else {}) & set(ROW_KEYS), (tname, value)


def vocabulary(doc):
    """Assert a song uses only the fixed v1.6 vocabulary, each shared key in its one meaning."""
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
    gt_doc = yaml.safe_load(songio.dumps(ir))
    gt_cmds = vocabulary(gt_doc)
    assert gt_cmds == {"run", "freq", "legato", "tempo"}
    dm_doc = yaml.safe_load(songio.dumps(lift_defmon(crosseyed.read_bytes())))
    dm_cmds = vocabulary(dm_doc)
    assert dm_cmds == {"run2"}
    sw_doc = yaml.safe_load(songio.dumps(lift_sw(csillag.read_bytes())))
    sw_cmds = vocabulary(sw_doc)
    assert sw_cmds == {
        "freq",
        "attack",
        "sustain",
        "chord",
        "legato",
        "isustain",
        "irelease",
        "ichord",
    }
    # A key two players both use is a shared key; every other key belongs to one player.
    keys = [_song_keys(d) - {"loop"} for d in (gt_doc, dm_doc, sw_doc)]
    for i, mine in enumerate(keys):
        for j, theirs in enumerate(keys):
            if i != j:
                assert mine & theirs <= set(ROW_KEYS) | {"filter"}
    assert not keys[2] & (set(COMMAND_NAMES) - {"chord"} - set(ROW_KEYS))


def defmon_doc(vinit, ginit):
    return songio.loads(
        yaml.safe_dump(
            {
                "unitracker": 1.6,
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


def test_defmon_init_keys_mean_what_they_mean_in_rows():
    ir = defmon_doc(
        {"pw": 0x0567, "ctrl": 0x41, "ad": 0x12, "sr": 0x34, "wgl": 1, "note": "=36"},
        {"resfilt": 0xF1},
    )
    voice = ir["records"]["vinit"][0]
    assert (voice["pw"], voice["ctrl"], voice["wgl"], voice["note"]) == (0x0567, 0x41, 1, 60)
    grid = SongPlayer(ir).render_grid(2)
    assert list(grid[0][2:7]) == [0x67, 0x05, 0x41 ^ 1, 0x12, 0x34]
    assert grid[0][0x17] == 0xF1
    word = DM["tables"]["note_lo"][60] | DM["tables"]["note_hi"][60] << 8
    assert int(grid[1][0]) | int(grid[1][1]) << 8 == word


@pytest.mark.parametrize(
    "vinit,match",
    [({"pw": 0x1100}, r"values of \['pw'\]"), ({"note": "+3"}, r"values of \['note'\]")],
)
def test_defmon_init_rejects_what_has_no_written_form(vinit, match):
    with pytest.raises(songio.SongFormatError, match=match):
        defmon_doc(vinit, {})


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
