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

import numpy as np
import yaml
from conftest import gt_tune

from unitracker import goattracker as gt, songio
from unitracker.modfit.convert import ConversionError
from unitracker.vm import SongPlayer


def table(*rows):
    return Table(left=[r[0] for r in rows], right=[r[1] for r in rows])


def test_wave_records():
    recs = gt.wave_records(
        table((0x41, 0x00), (0x02, 0x81), (0xE1, 0x80), (0xF5, 0x40), (0xFF, 0x01))
    )
    assert recs == {
        1: {"w": 0x41, "n": 0x00, "next": 2},
        2: {"w": 0x02, "n": 0x81, "next": 3},
        3: {"w": 0xE1, "n": 0x80, "next": 4},
        4: {"w": 0xF5, "n": 0x40, "next": 1},
    }


def test_pulse_records():
    recs = gt.pulse_records(table((0x84, 0x00), (0x20, 0xF0), (0x10, 0x10), (0xFF, 0x02)))
    assert recs == {
        1: {"l": 0x84, "r": 0x00, "next": 2},
        2: {"l": 0x20, "r": 0xF0, "next": 3},
        3: {"l": 0x10, "r": 0x10, "next": 2},
    }


def test_filter_records_absorb_cutoff_row():
    recs = gt.filter_records(
        table((0x90, 0xF1), (0x00, 0x40), (0x10, 0x02), (0x00, 0x22), (0xA0, 0x10), (0xFF, 0x00))
    )
    assert recs[1] == {"l": 0x90, "r": 0xF1, "cut": 0x40, "next": 3}
    assert recs[2] == {"l": 0x00, "r": 0x40, "next": 3}
    assert recs[3] == {"l": 0x10, "r": 0x02, "next": 4}
    assert recs[5] == {"l": 0xA0, "r": 0x10, "next": 0}


def test_speed_records():
    assert gt.speed_records(table((0x04, 0x20), (0x81, 0x02))) == {
        1: {"l": 4, "r": 32},
        2: {"l": 0x81, "r": 2},
    }


@pytest.mark.parametrize(
    "timer,first,rows",
    [
        (0x04, 0x09, [{"hold": 4, "gate": 0, "ad": 0x0F, "sr": 0}, {"ctrl": 9, "gate": 1}]),
        (0x83, 0xFE, [{"hold": 3, "gate": 0}, {"gate": 0}]),
        (0xC1, 0xFF, [{"hold": 1}, {"gate": 1}]),
        (0x42, 0x00, [{"hold": 2}, {}]),
    ],
)
def test_restart_rows_follow_the_gate_timer_and_first_wave(timer, first, rows):
    assert gt.restart_rows(timer, first, 0x0F00) == rows


def test_instruments_and_default_tempo():
    instruments = [
        Instrument(attack_decay=0x12, wave_ptr=3, gateoff_timer=0x82),
        Instrument(gateoff_timer=0x82),
        Instrument(),
        Instrument(sustain_release=0xF0, gateoff_timer=0x40),
        Instrument(gateoff_timer=0x82, first_wave=0x41),
    ]
    recs, restarts = gt.instrument_records(instruments, 0x0F00)
    assert recs == {
        1: {"ad": 0x12, "ctrl": 3, "restart": 1},
        2: {"restart": 1},
        3: {},
        4: {"sr": 0xF0},
        5: {"restart": 3},
    }
    assert restarts == {
        1: {"hold": 2, "gate": 0, "next": 2},
        2: {"next": 0},
        3: {"hold": 2, "gate": 0, "next": 4},
        4: {"ctrl": 0x41, "gate": 1, "next": 0},
    }
    for bad in (Instrument(attack_decay=1), Instrument(first_wave=9, gateoff_timer=0x40)):
        with pytest.raises(gt.LiftError, match="gate timer 0"):
            gt.instrument_records([bad], 0x0F00)
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
        ([(0xF, 16)], 11, 16),
        ([(0xF, 16), (0xF, 16)], 11, 16),
        ([(0xF, 16), (0xF, 8)], 11, None),
        ([(0xF, 0x90)], 11, None),
        ([(0xE, 1)], 11, None),
        ([(0x1, 1), (0xF, 16)], 11, None),
        ([(0x1, 1), (0xF, 12)], 11, 12),
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


COMMANDS_SONG = """
unitracker: 1.15
playroutine: {ref: goattracker2, constants: {deftempo: 6}}
song:
  - def:
      pulsetab:
        pw1: [{pw: 2048}, end]
      filttab:
        f1: [{cut: 512, res: 3, route: 1, mode: 16}, end]
      ins:
        i1: {sr: 240, pw: pw1, ctrl: [{ctrl: 65, note: +0}, {ad: 25}, {sr: 200}, {run: pw1},
            {run: f1},
            {res: 12, route: 2}, {cut: 1024}, {vol: 9}, end]}
      phrase:
        p1: [C4.i1*8]
        p2: [off*8]
    play: [p1, p2, p2]
loop: ~
"""


def test_wavetable_commands_are_their_shared_keys():
    ir = songio.loads(COMMANDS_SONG)
    rows = ir["records"]["ctrltab"]
    assert [(r["w"], r["n"]) for r in rows.values()][1:] == [
        (0xF5, 25), (0xF6, 200), (0xF9, 1), (0xFA, 1), (0xFB, 0xC2), (0xFC, 128), (0xFD, 9)
    ]  # fmt: skip
    grid = SongPlayer(ir).render_grid(20).astype(int)
    last = grid[-1]
    assert (last[5], last[6], last[2] | last[3] << 8) == (25, 200, 2048)
    assert (last[22], last[23], last[24]) == (128, 0xC2, 0x10 | 9)
    text = songio.dumps(ir)
    assert "{run: pw1}, {run: f1}" in text and songio.dumps(songio.loads(text)) == text
    assert np.array_equal(SongPlayer(songio.loads(text)).render_grid(20), grid)


def test_a_wavetable_command_names_a_program_of_its_kind():
    with pytest.raises(songio.SongFormatError, match="'i1' names no filttab or pulsetab"):
        songio.loads(COMMANDS_SONG.replace("{run: pw1}", "{run: i1}"))


SPEEDS = [(0, 0x20), (0x80, 1), (0x02, 0x10)]
TWO_NOTES = [(24, 1, 0, 0)] + [(None, 0, 0, 0)] * 7 + [(31, 1, 0, 0)] + [(None, 0, 0, 0)] * 7


def lifted(dec, native, ticks=400) -> str:
    """The canonical text of a synthetic tune's lift, asserting it plays as the native player."""
    text = songio.dumps(gt.lift(dec))
    song = songio.loads(text)
    assert songio.dumps(song) == text
    theirs = np.asarray(native.render_grid(ticks), dtype=np.uint8)
    assert SongPlayer(song).mismatch(theirs) is None
    return text


def test_wavetable_steps_are_their_rows_freq():
    steps = [(0x41, 0), (0xF4, 3), (0xF4, 3), (0xF3, 1), (0xF2, 2), (0xF1, 1), (0x41, 0x80)]
    text = lifted(*gt_tune(steps + [(0xFF, 7)], rows=TWO_NOTES, speed=SPEEDS))
    rows = yaml.safe_load(text)["song"][0]["def"]["ins"]["i1"]["ctrl"]
    assert rows[1:3] == [{"freq": "(v + 16) % 65536"}] * 2
    assert rows[4:6] == [
        {"freq": "(v - ((-F(N) + F((N + 1) % 128)) % 65536) // 2) % 65536"},
        {"freq": "(v + 32) % 65536"},
    ]
    assert all(isinstance(r, dict) for r in rows) and "speedtab" not in text


def test_a_wavetable_step_on_changing_phases_is_named():
    dec, _ = gt_tune([(0x41, 0), (0xF4, 3), (0xFF, 2)], speed=SPEEDS)
    with pytest.raises(ConversionError, match="ctrltab row 2: its runs need several freq"):
        gt.lift(dec)
