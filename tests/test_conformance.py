"""Lifted songs, written as v1.2 text and read back, play identically to their native players."""

import numpy as np
import pytest
from pydefmon import DefmonPlayer
from pygoattracker import decompile_sid

from unitracker import defmon, songio
from unitracker.goattracker import lift
from unitracker.vm import SongPlayer

LOOP_TICKS = 3840


@pytest.mark.parametrize("embed", [False, True])
def test_metal_warrior_two_loops_exact(metal_warrior, embed):
    dec = decompile_sid(metal_warrior.read_bytes(), subtune=0)
    text = songio.dumps(lift(dec), embed=embed)
    song = songio.loads(text)
    assert songio.dumps(song, embed=embed) == text
    ours = SongPlayer(song).render_grid(2 * LOOP_TICKS)
    theirs = np.asarray(dec.player().render_grid(2 * LOOP_TICKS), dtype=np.uint8)
    assert len(np.unique(theirs, axis=0)) > 1000
    np.testing.assert_array_equal(ours, theirs)


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
    ours = SongPlayer(song).render_grid(n)
    theirs = np.asarray(DefmonPlayer(raw).render_grid(n), dtype=np.uint8)
    assert len(np.unique(theirs, axis=0)) > 500
    np.testing.assert_array_equal(ours, theirs)


def test_lift_is_deterministic(metal_warrior, crosseyed):
    dec = decompile_sid(metal_warrior.read_bytes(), subtune=0)
    assert songio.dumps(lift(dec)) == songio.dumps(lift(dec))
    raw = crosseyed.read_bytes()
    assert songio.dumps(defmon.lift(raw)) == songio.dumps(defmon.lift(raw))
