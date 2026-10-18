"""Lifted songs, written as v1.19 text and read back, play identically to their native players."""

import dataclasses

import numpy as np
import pytest
from conftest import gt_tune, oracle, sw_fx, sw_table_byte
from pysidwizard import SWMPlayer, parse_sid_song

from unitracker.format import songio
from unitracker.lift import sidwizard
from unitracker.lift.goattracker import lift
from unitracker.player.vm import SongPlayer

CSILLAG_LOOP = 2304 * 6


def test_csillag_with_kept_programs_exact(csillag, monkeypatch):
    raw = csillag.read_bytes()
    swm = parse_sid_song(raw, 1)
    swm.instruments = [
        dataclasses.replace(ins, control=ins.control | (0x40 if k % 2 else 0x80))
        for k, ins in enumerate(swm.instruments)
    ]
    monkeypatch.setattr(sidwizard, "parse_sid_song", lambda *_: swm)
    text = songio.dumps(sidwizard.lift(raw))
    assert "!pulse:0" in text and "!filter:0" in text and "flags" not in text
    song = songio.loads(text)
    assert songio.dumps(song) == text
    n = CSILLAG_LOOP + 600
    theirs = np.asarray(SWMPlayer(swm).render_grid(n), dtype=np.uint8)
    plain = np.asarray(SWMPlayer(parse_sid_song(raw, 1)).render_grid(n), dtype=np.uint8)
    assert not np.array_equal(theirs, plain)
    assert SongPlayer(song).mismatch(theirs) is None


GT_REST = (None, 0, 0, 0)
GT_FILTER_TUNES = {
    "wavetable": (
        {"wave": [(0x41, 0), (0xFB, 0xA1), (0xFC, 0x40), (0xFD, 0x07), (0x21, 0x80), (0xFF, 5)]},
        {0x16: 0x40, 0x17: 0xA1, 0x18: 0x07},
    ),
    "pattern": (
        {
            "wave": [(0x41, 0), (0xFF, 0)],
            "rows": [(24, 1, 0xB, 0xA1), *[GT_REST] * 3, (None, 0, 0xC, 0x50), *[GT_REST] * 3]
            + [(None, 0, 0xD, 0x09), *[GT_REST] * 7],
        },
        {0x16: 0x50, 0x17: 0xA1, 0x18: 0x09},
    ),
    "program-stop": (
        {
            "wave": [(0x41, 0), (0xFA, 1), (0x03, 0x80), (0xFB, 0), (0x41, 0x80), (0xFF, 5)],
            "filt": [(0x90, 0xF1), (0x00, 0x40), (0x10, 0x02), (0xFF, 3)],
        },
        {0x17: 0xF1, 0x18: 0x1F},
    ),
}


@pytest.mark.parametrize("tune,regs", GT_FILTER_TUNES.values(), ids=GT_FILTER_TUNES)
def test_goattracker_filter_writes_sound_after_the_filter_step(tune, regs):
    dec, native = gt_tune(**tune)
    text = songio.dumps(lift(dec))
    song = songio.loads(text)
    assert songio.dumps(song) == text
    theirs = np.asarray(native.render_grid(200), dtype=np.uint8)
    player = SongPlayer(song)
    assert player.mismatch(theirs) is None
    begun = min(s for s in player.starts if s is not None)
    for reg, value in regs.items():
        assert value in theirs[begun:, reg]


# Tempo FX that keep the voices in step: every voice's tempo and swing, restored to the
# subtune's, and each voice's own tempo and equal swing while it is the subtune's.
STEP_TEMPOS = {
    (10, 16): (0x10, 4),
    (10, 32): (0x10, 0),
    (11, 40): (0x11, 0x57),
    (11, 56): (0x10, 0x86),
    (9, 0): (0x13, 6),
    (12, 0): (0x14, 0x66),
}


def test_csillag_with_tempo_changes_exact(csillag, monkeypatch):
    raw = csillag.read_bytes()
    swm = sw_fx(raw, STEP_TEMPOS)
    monkeypatch.setattr(sidwizard, "parse_sid_song", lambda *_: swm)
    text = songio.dumps(sidwizard.lift(raw))
    assert "!tempo:4" in text and "!funk:5/7" in text and "!vtempo:6" in text
    song = songio.loads(text)
    assert songio.dumps(song) == text
    n = CSILLAG_LOOP + 600
    theirs = np.asarray(SWMPlayer(sw_fx(raw, STEP_TEMPOS)).render_grid(n), dtype=np.uint8)
    plain = np.asarray(SWMPlayer(parse_sid_song(raw, 1)).render_grid(n), dtype=np.uint8)
    assert not np.array_equal(theirs, plain)
    assert SongPlayer(song).mismatch(theirs) is None


# Csillag's instrument 7 sets its pulse at its first row; a keyboard-track amount there, up and
# down, adds a term of the note to the register until the next row's amount (0) replaces it.
@pytest.mark.parametrize("amount", [0x05, 0xF4])
def test_csillag_with_a_keyboard_tracked_pulse_row_matches_its_player(csillag, amount):
    raw = sw_table_byte(csillag.read_bytes(), 7, "pw", 2, amount)
    text = songio.dumps(sidwizard.lift(raw))
    assert "E[" in text and "k:" not in text
    song = songio.loads(text)
    assert songio.dumps(song) == text
    n = CSILLAG_LOOP + 600
    theirs = oracle(raw, n)
    plain = np.asarray(SWMPlayer(parse_sid_song(csillag.read_bytes(), 1)).render_grid(n))
    assert not np.array_equal(theirs[: len(plain)], plain)
    assert SongPlayer(song).mismatch(theirs) is None


# Instruments 4 and 8 set the filter at their first row; a keyboard-track amount there adds a
# term of the controlling voice's note to $D416, which pysidwizard's player does not model.
@pytest.mark.parametrize("instrument, amount", [(4, 0x05), (8, 0x05), (8, 0xF4)])
def test_csillag_with_a_keyboard_tracked_filter_row_matches_its_player(csillag, instrument, amount):
    raw = sw_table_byte(csillag.read_bytes(), instrument, "filter", 2, amount)
    text = songio.dumps(sidwizard.lift(raw))
    assert "E[" in text and "k:" not in text
    song = songio.loads(text)
    assert songio.dumps(song) == text
    n = CSILLAG_LOOP + 600
    theirs = oracle(raw, n)
    plain = np.asarray(SWMPlayer(parse_sid_song(raw, 1)).render_grid(n))
    assert not np.array_equal(theirs[: len(plain), 0x16], plain[:, 0x16])
    assert SongPlayer(song).mismatch(theirs) is None
