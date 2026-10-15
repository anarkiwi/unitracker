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


def test_wave_records():
    recs = gt.wave_records(
        table((0x41, 0x00), (0x02, 0x81), (0xE1, 0x80), (0xF5, 0x40), (0xFF, 0x01))
    )
    assert recs[1] == {
        "next": 2,
        "do": [{"set": {"wave": 0x41}}, {"call": "wnote", "with": {"n": "note & 0x7F"}}],
    }
    assert recs[2] == {
        "next": 3,
        "delay": 1,
        "wait": 2,
        "do": [{"call": "wnote", "with": {"n": 1}}],
    }
    assert recs[3] == {"next": 4, "do": [{"set": {"wave": 1}}]}
    assert recs[4]["next"] == 1
    assert recs[4]["do"][1] == {"run": {"record": "wavecmd", "index": 5, "field": "do"}}
    assert 5 not in recs
    assert gt.wave_records(table((0x03, 0x80)))[1] == {"next": 2, "delay": 1, "wait": 3}
    assert gt.wave_records(table((0x11, 0x05)))[1]["do"][1]["with"]["n"] == "(note + 5) & 0x7F"


def test_pulse_records():
    recs = gt.pulse_records(table((0x84, 0x00), (0x20, 0xF0), (0x10, 0x10), (0xFF, 0x02)))
    assert recs[1] == {"set": 1, "do": [{"set": {"pulse": 0x400}}], "next": 2}
    assert recs[2] == {"time": 0x20, "each": [{"accumulate": "pulse", "step": -16}], "next": 3}
    assert recs[3]["next"] == 2


def test_filter_records():
    recs = gt.filter_records(
        table((0x90, 0xF1), (0x00, 0x40), (0x10, 0x02), (0x00, 0x22), (0xA0, 0x10), (0xFF, 0x00))
    )
    assert recs[1] == {
        "set": 1,
        "do": [{"set": {"filtertype": 0x10, "filterctrl": 0xF1}}, {"set": {"filtercutoff": 0x40}}],
        "next": 3,
    }
    assert recs[3] == {"time": 0x10, "each": [{"accumulate": "filtercutoff", "step": 2}], "next": 4}
    assert recs[4] == {"set": 1, "do": [{"set": {"filtercutoff": 0x22}}], "next": 5}
    assert recs[5]["next"] == 0 and len(recs[5]["do"]) == 1


def test_instruments_and_default_tempo():
    recs = gt.instrument_records([Instrument(attack_decay=0x12, wave_ptr=3, gateoff_timer=0x82)])
    assert recs == {1: {"ad": 0x12, "wave_ptr": 3, "gatetimer": 0x82}}
    song = Song(instruments=[Instrument() for _ in range(63)])
    assert gt._default_tempo(song) == 5  # pylint: disable=protected-access
    song.instruments[62] = Instrument(attack_decay=7)
    assert gt._default_tempo(song) == 6  # pylint: disable=protected-access


def test_phrase_folds_empty_rows():
    rows = [
        Row(),
        Row(note=0x60 + 24, instrument=2, command=1, data=3),
        Row(),
        Row(),
        Row(note=0xBE),
        Row(note=0xBF),
    ]
    assert gt.phrase(rows) == [
        {},
        {"note": 24, "instrument": 2, "command": [1, 3], "steps": 3},
        {"note": "off"},
        {"note": "on"},
    ]
    with pytest.raises(gt.LiftError, match="empty"):
        gt.phrase([])
    with pytest.raises(gt.LiftError, match="note byte"):
        gt.phrase([Row(note=0x10)])


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
