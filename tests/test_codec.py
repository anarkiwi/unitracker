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
    RowCodec,
    evaluate,
    note_text,
    note_value,
)
from unitracker.defmon import lift as lift_defmon
from unitracker.goattracker import lift as lift_gt
from unitracker.records import record_types

GT = songio.library("goattracker2")
DM = songio.library("defmon")
GT_TYPES = record_types(GT)
DM_TYPES = record_types(DM)


def codec(pr, types, name):
    return RowCodec(types[name], types[name].codec, int(pr.get("note_offset", 0)))


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
    c = codec(GT, GT_TYPES, "wavetab")
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
    c = codec(GT, GT_TYPES, name)
    assert written(raw, c) == shared
    assert c.decode(shared) == raw


@pytest.mark.parametrize(
    "name,raw",
    [("wavetab", {"w": 0xF1, "n": 1}), ("pulsetab", {"l": 32, "r": 16}), ("filttab", {"l": 5})],
)
def test_goattracker_leftover_rows_have_no_shape(name, raw):
    assert codec(GT, GT_TYPES, name).encode(raw) is None


@pytest.mark.parametrize(
    "name,shared,match",
    [
        ("filttab", {"cut": 1025}, "values of"),
        ("filttab", {"mode": 16, "resfilt": 1, "cut": 4}, "values of"),
        ("wavetab", {"ctrl": 0xE5}, "values of"),
        ("wavetab", {"ctrl": 9, "wait": 1}, "values of"),
        ("wavetab", {"note": "=0"}, "values of"),
        ("wavetab", {"hold": 2}, "keys"),
        ("pulsetab", {"pw": 4097}, "values of"),
    ],
)
def test_goattracker_rejects_what_it_cannot_express(name, shared, match):
    with pytest.raises(CodecError, match=match):
        codec(GT, GT_TYPES, name).decode(shared)


def test_defmon_row_and_freeze():
    c = codec(DM, DM_TYPES, "sidtab")
    raw = {"dl": 1, "wgh": 80, "wgl": 5, "ad": 0, "sr": 68, "tr": 12, "pw": 1, "ps": 0}
    shared = {"hold": 2, "ctrl": 80, "note": "+12", "ad": 0, "sr": 68, "pw": 256, "wgl": 5, "ps": 0}
    assert written(raw, c) == shared
    assert c.decode(shared) == {**{f: -1 for f in ("re", "fv", "cp", "acid", "af")}, **raw}
    assert written({"dl": 242, "tr": 180}, c) == {"note": "=28"}
    c.check_end({"dl": 0x80, "tr": 180}, {"note": "=28"})
    with pytest.raises(CodecError, match="can't end"):
        c.check_end({"dl": 0x80}, {"hold": 3})


def test_defmon_pw_and_note_columns_round_trip_every_byte():
    c = codec(DM, DM_TYPES, "sidtab")
    for byte in range(256):
        for col in ("pw", "tr"):
            shared = written({col: byte}, c)
            assert c.decode(shared)[col] == byte


def test_defmon_document_ends_on_freeze():
    doc = {
        "unitracker": 1.1,
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
        (8, 3, ("run", ("wavetab", 3))),
        (10, 2, ("run", ("filttab", 2))),
        (1, 1, ("slideup", ("speedtab", 1))),
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
    for name, value in [("cut", 1025), ("tempo", 2), ("run", 5), ("vib", ("wavetab", 1))]:
        with pytest.raises(CodecError, match="can't express"):
            cmds.decode(name, value)
    dm = CommandCodec(DM["commands"], DM_TYPES)
    assert dm.encode(1, 4) == ("run2", ("sidtab", 4))
    with pytest.raises(CodecError, match="can't express"):
        dm.decode("run2", "none")


@pytest.mark.parametrize(
    "bad",
    [
        {"keys": {"wave": 1}},
        {"keep": ["ctrl"]},
    ],
)
def test_codec_declarations_are_checked(bad):
    with pytest.raises(CodecError):
        RowCodec(GT_TYPES["wavetab"], [bad], 0)
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


PROGRAM_TYPES = {"wave": "wavetab", "pulse": "pulsetab", "filter": "filttab"}
GT_INS = {"ad", "sr", "wave", "pulse", "filter", "speed", "vd", "gt", "fw"}
DM_KEEP = {"wgl", "af", "ps", "re", "fv", "cp", "acid"}


def _check_rows(name, rows, keep):
    for row in rows:
        if isinstance(row, dict) and set(row) != {"loop"}:
            assert set(row) <= set(ROW_KEYS) | keep, (name, row)
        elif isinstance(row, list):
            assert name in ("wavetab", "pulsetab", "filttab"), (name, row)
            first = row[0]
            assert {"wavetab": first >= 0xF0, "pulsetab": first < 0x80}.get(
                name, 0 < first < 0x80
            ), (name, row)


def _check_program(pr_name, tname, value):
    keep = DM_KEEP if pr_name == "defmon" else set()
    _check_rows(tname, value, keep)


def _commands(atoms):
    return {a.split("!")[1].split(":")[0] for a in atoms if isinstance(a, str) and "!" in a}


def _check_def(pr_name, tname, value):
    if tname == "ins":
        assert set(value) <= GT_INS
        for key, rtype in PROGRAM_TYPES.items():
            if isinstance(value.get(key), list):
                _check_program(pr_name, rtype, value[key])
    elif tname in ("wavetab", "pulsetab", "filttab", "sidtab"):
        _check_program(pr_name, tname, value)


def vocabulary(doc):
    """Assert a song uses only the fixed v1.1 vocabulary; the commands it uses."""
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


def test_both_songs_use_only_the_shared_vocabulary(metal_warrior, crosseyed):
    ir = lift_gt(decompile_sid(metal_warrior.read_bytes(), subtune=0))
    gt_cmds = vocabulary(yaml.safe_load(songio.dumps(ir)))
    assert gt_cmds == {"run", "slideup", "porta", "vib", "tempo"}
    dm_cmds = vocabulary(yaml.safe_load(songio.dumps(lift_defmon(crosseyed.read_bytes()))))
    assert dm_cmds == {"run2"}
