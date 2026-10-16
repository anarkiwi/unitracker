"""Lifted, YAML round-tripped songs play identically to their native players."""

import numpy as np
from pydefmon import DefmonPlayer
from pygoattracker import decompile_sid

from unitracker import defmon, songio
from unitracker.goattracker import lift
from unitracker.vm import SongPlayer

LOOP_TICKS = 3840


def test_metal_warrior_two_loops_exact(metal_warrior):
    dec = decompile_sid(metal_warrior.read_bytes(), subtune=0)
    song = songio.loads(songio.dumps(lift(dec)))
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


def test_crosseyed_intro_and_two_loops_exact(crosseyed):
    raw = crosseyed.read_bytes()
    song = songio.loads(songio.dumps(defmon.lift(raw)))
    n = 95 + 2 * 1152 + 100
    ours = SongPlayer(song).render_grid(n)
    theirs = np.asarray(DefmonPlayer(raw).render_grid(n), dtype=np.uint8)
    assert len(np.unique(theirs, axis=0)) > 500
    np.testing.assert_array_equal(ours, theirs)
