"""Lifted songs, written as v1.16 text and read back, play identically to their native players."""

import dataclasses

import numpy as np
import pytest
from pydefmon import DefmonPlayer
from pygoattracker import decompile_sid
from pysidwizard import SWMPlayer, parse_sid_song

from unitracker import defmon, sidwizard, songio
from unitracker.goattracker import lift
from unitracker.vm import SongPlayer

LOOP_TICKS = 3840


@pytest.mark.parametrize("embed", [False, True])
def test_metal_warrior_two_loops_exact(metal_warrior, embed):
    dec = decompile_sid(metal_warrior.read_bytes(), subtune=0)
    text = songio.dumps(lift(dec), embed=embed)
    song = songio.loads(text)
    assert songio.dumps(song, embed=embed) == text
    theirs = np.asarray(dec.player().render_grid(2 * LOOP_TICKS), dtype=np.uint8)
    assert len(np.unique(theirs, axis=0)) > 1000
    assert SongPlayer(song).mismatch(theirs) is None


def test_flushes_rebuild_the_grid(metal_warrior):
    dec = decompile_sid(metal_warrior.read_bytes(), subtune=0)
    song = lift(dec)
    regs = [0] * 25
    grid = SongPlayer(song).render_grid(500)
    for tick, flush in enumerate(SongPlayer(song).iter_flushes(500)):
        for reg, value in flush:
            regs[reg] = value
        assert regs == list(grid[tick])


@pytest.mark.parametrize("embed", [False, True])
def test_crosseyed_intro_and_two_loops_exact(crosseyed, embed):
    raw = crosseyed.read_bytes()
    text = songio.dumps(defmon.lift(raw), embed=embed)
    song = songio.loads(text)
    assert songio.dumps(song, embed=embed) == text
    n = 95 + 2 * 1152 + 100
    theirs = np.asarray(DefmonPlayer(raw).render_grid(n), dtype=np.uint8)
    assert len(np.unique(theirs, axis=0)) > 500
    assert SongPlayer(song).mismatch(theirs) is None


CSILLAG_LOOP = 2304 * 6


@pytest.mark.parametrize("embed", [False, True])
def test_csillag_loop_exact(csillag, embed):
    raw = csillag.read_bytes()
    text = songio.dumps(sidwizard.lift(raw), embed=embed)
    song = songio.loads(text)
    assert songio.dumps(song, embed=embed) == text
    n = CSILLAG_LOOP + 600
    theirs = np.asarray(SWMPlayer(parse_sid_song(raw, 1)).render_grid(n), dtype=np.uint8)
    assert len(np.unique(theirs, axis=0)) > 5000
    assert SongPlayer(song).mismatch(theirs) is None


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


def test_lift_is_deterministic(metal_warrior, crosseyed, csillag):
    raw = csillag.read_bytes()
    assert songio.dumps(sidwizard.lift(raw)) == songio.dumps(sidwizard.lift(raw))
    dec = decompile_sid(metal_warrior.read_bytes(), subtune=0)
    assert songio.dumps(lift(dec)) == songio.dumps(lift(dec))
    raw = crosseyed.read_bytes()
    assert songio.dumps(defmon.lift(raw)) == songio.dumps(defmon.lift(raw))
