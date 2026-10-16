"""Lifting GoatTracker 2 bytes to unitracker records, phrases and tracks."""

from types import SimpleNamespace

import pytest
from pygoattracker import decompile_sid
from pygoattracker.model import (
    Instrument,
    Orderlist,
    PlayPattern,
    Repeat,
    Row,
    Song,
    Table,
    Transpose,
)

from unitracker import goattracker as gt


def table(*rows):
    return Table(left=[r[0] for r in rows], right=[r[1] for r in rows])


def fx(*speed_rows):
    return gt.Effects(table(*speed_rows) if speed_rows else Table())


def test_wave_records():
    recs = gt.wave_records(
        table((0x41, 0x00), (0x02, 0x81), (0xE1, 0x80), (0xF5, 0x40), (0x00, 0x7F), (0xFF, 0x01)),
        fx(),
    )
    assert recs == {
        1: {"ctrl": 0x41, "note": 0, "next": 2},
        2: {"wait": 2, "noteabs": 1, "next": 3},
        3: {"ctrl": 1, "next": 4},
        4: {"ad": 0x40, "next": 5},
        5: {"note": -1, "next": 1},
    }


@pytest.mark.parametrize(
    "row,expected",
    [
        ((0xF1, 1), {"freq": {"step": 1056, "for": 1}}),
        ((0xF2, 1), {"freq": {"step": -1056, "for": 1}}),
        ((0xF3, 1), {"freq": {"step": 1056, "toward": "note", "width": 16, "for": 1}}),
        ((0xF3, 0), {"freq": {"toward": "note", "for": 1}}),
        ((0xF4, 1), {"freq": {"step": 32, "flip": [3, 6], "for": 1}}),
        ((0xF6, 9), {"sr": 9}),
        ((0xF7, 0xE5), {"ctrl": 0xE5}),
        ((0xF9, 2), {"pwprog": 2}),
        ((0xFA, 2), {"filtprog": 2}),
        ((0xFB, 0x31), {"resfilt": 0x31}),
        ((0xFC, 0x80), {"cut": 0x80}),
        ((0xFD, 0x0C), {"vol": 0x0C}),
    ],
)
def test_wave_command_rows(row, expected):
    recs = gt.wave_records(table(row), fx((4, 32)))
    assert recs[1] == {**expected, "next": 2}


@pytest.mark.parametrize("row", [(0xF7, 0x41), (0xFD, 0x20), (0xF0, 0), (0xF8, 1), (0xFE, 0)])
def test_wave_commands_not_covered(row):
    with pytest.raises(gt.LiftError, match="not covered"):
        gt.wave_records(table(row), fx())


def test_pulse_records():
    recs = gt.pulse_records(table((0x84, 0x00), (0x20, 0xF0), (0x10, 0x10), (0xFF, 0x02)))
    assert recs == {
        1: {"pw": 0x400, "next": 2},
        2: {"pw": {"step": -16, "for": 0x20}, "next": 3},
        3: {"pw": {"step": 16, "for": 0x10}, "next": 2},
    }


def test_filter_records_absorb_cutoff_row():
    recs = gt.filter_records(
        table((0x90, 0xF1), (0x00, 0x40), (0x10, 0x02), (0x00, 0x22), (0xA0, 0x10), (0xFF, 0x00))
    )
    assert recs[1] == {"filtmode": 0x10, "resfilt": 0xF1, "cut": 0x40, "next": 3}
    assert recs[2] == {"cut": 0x40, "next": 3}
    assert recs[3] == {"cut": {"step": 2, "for": 0x10}, "next": 4}
    assert recs[5] == {"filtmode": 0x20, "resfilt": 0x10, "next": 0}


def test_effect_specs():
    effects = fx((4, 32), (0x81, 3), (0x80, 0), (0x7E, 5))
    assert effects.rate(0) == 0 and effects.rate(1) == 0x0420
    assert effects.rate(2) == "semi>>3" and effects.rate(3) == "semi"
    assert effects.row(9) == (0, 0)
    assert effects.slide(2, -1) == {"step": "-(semi>>3)", "reset": ["command"]}
    assert effects.slide(0, -1) == {"step": 0, "reset": ["command"]}
    assert effects.porta(2) == {"step": "semi>>3", "toward": "note", "width": 16}
    assert effects.vibrato(2) == {"step": "semi>>3", "flip": [1, 3]}
    assert effects.vibrato(4) == {"step": 5}
    assert effects.vibrato(0) == {"step": 0, "flip": [1, 2]}


@pytest.mark.parametrize(
    "cmd,data,expected",
    [
        (0, 0, None),
        (0, 5, None),
        (1, 1, ["freq", {"step": 1056, "reset": ["command"]}]),
        (3, 1, ["freq", {"step": 1056, "toward": "note", "width": 16}]),
        (4, 1, ["freq", {"step": 32, "flip": [3, 6]}]),
        (5, 9, ["ad", 9]),
        (7, 0x41, ["ctrl", 0x41]),
        (8, 2, ["ctrlprog", 2]),
        (0xD, 0x20, ["vol", 0x20]),
        (0xE, 0, ["funk", None]),
        (0xE, 1, ["funk", [4, 32]]),
        (0xF, 16, ["tempo", 16]),
        (0xF, 2, ["tempo", 3]),
        (0xF, 0x84, ["vtempo", 4]),
    ],
)
def test_commands(cmd, data, expected):
    assert gt.command(cmd, data, fx((4, 32))) == expected


def test_instruments_and_default_tempo():
    recs = gt.instrument_records(
        [
            Instrument(
                attack_decay=0x12,
                wave_ptr=3,
                gateoff_timer=0xC2,
                vibrato_param=1,
                vibrato_delay=4,
                first_wave=0xFE,
            ),
            Instrument(first_wave=0x41),
        ],
        fx((4, 32)),
    )
    assert recs == {
        1: {
            "ad": 0x12,
            "ctrlprog": 3,
            "delay": 4,
            "freq": {"step": 32, "flip": [3, 6]},
            "gateoff": 2,
            "keepgate": 1,
            "nohr": 1,
            "firstgate": 0,
        },
        2: {"firstctrl": 0x41},
    }
    song = Song(instruments=[Instrument() for _ in range(63)])
    assert gt._default_tempo(song) == 5  # pylint: disable=protected-access
    song.instruments[62] = Instrument(attack_decay=7)
    assert gt._default_tempo(song) == 6  # pylint: disable=protected-access


def _tempo_phrases(*commands):
    return {"a": [{"command": list(c)} for c in commands]}


@pytest.mark.parametrize(
    "commands,deftempo,expected",
    [
        ([], 5, 6),
        ([], 1, None),
        ([("tempo", 16)], 11, 16),
        ([("tempo", 16), ("tempo", 16)], 11, 16),
        ([("tempo", 16), ("tempo", 8)], 11, None),
        ([("vtempo", 16)], 11, None),
        ([("funk", [4, 4])], 11, None),
        ([("ad", 1), ("tempo", 16)], 11, None),
        ([("ad", 1), ("tempo", 12)], 11, 12),
    ],
)
def test_ticks_per_step(commands, deftempo, expected):
    tracks = [{"plays": [{"phrase": "a"}]}]
    assert gt.ticks_per_step(_tempo_phrases(*commands), tracks, deftempo) == expected


def test_phrase_folds_empty_rows():
    rows = [
        Row(),
        Row(note=0x60 + 24, instrument=2, command=1, data=3),
        Row(),
        Row(),
        Row(note=0xBE),
        Row(note=0xBF),
    ]
    effects = fx()
    assert gt.phrase(rows, effects) == [
        {},
        {
            "note": 24,
            "instrument": 2,
            "command": ["freq", {"step": 0, "reset": ["command"]}],
            "steps": 3,
        },
        {"note": "off"},
        {"note": "on"},
    ]
    with pytest.raises(gt.LiftError, match="empty"):
        gt.phrase([], effects)
    with pytest.raises(gt.LiftError, match="note byte"):
        gt.phrase([Row(note=0x10)], effects)


def test_track_transpose_repeat_loop():
    ol = Orderlist(
        entries=[
            Transpose(2),
            Repeat(1),
            PlayPattern(3),
            PlayPattern(4),
            Transpose(0),
            PlayPattern(3),
        ],
        restart=4,
    )
    assert gt.track(ol) == {
        "plays": [
            {"phrase": "p03", "transpose": 2, "repeat": 2},
            {"phrase": "p04", "transpose": 2},
            {"phrase": "p03"},
        ],
        "loop": 2,
    }


@pytest.mark.parametrize(
    "entries,restart,match",
    [
        ([Transpose(2), PlayPattern(0), Transpose(0), PlayPattern(1)], 1, "different transpose"),
        ([PlayPattern(0)], 5, "outside"),
        ([Repeat(1), Transpose(1), PlayPattern(0)], 0, "not \\[transpose\\]"),
    ],
)
def test_track_errors(entries, restart, match):
    with pytest.raises(gt.LiftError, match=match):
        gt.track(Orderlist(entries=entries, restart=restart))


def test_unsupported_builds_are_named():
    info = SimpleNamespace(
        simplepulse=True, state=object(), init_regs=((0, 1),), live_vibrato=False
    )
    with pytest.raises(gt.LiftError, match="SIMPLEPULSE.*full-ghosting.*init-routine"):
        gt.lift(SimpleNamespace(info=info))


def test_lift_document(metal_warrior):
    song = gt.lift(decompile_sid(metal_warrior.read_bytes(), subtune=0))
    assert song["meta"]["title"] == "Metal Warrior Title Remix"
    assert song["clock"] == gt.PAL_CLOCK
    assert song["playroutine"]["constants"] == {"adparam": 0x0F00, "deftempo": 11}
    assert [len(t["plays"]) for t in song["tracks"]] == [5, 5, 5]
    assert len(song["tables"]["ftab"]) == 128
    assert song["records"]["ins"][1]["sr"] == 0x88
    assert set(song["records"]) == {"ins", "ctrlprog", "pwprog", "filtprog"}
