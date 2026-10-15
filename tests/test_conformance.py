"""The lifted, YAML round-tripped song plays identically to the native GoatTracker player."""

import numpy as np
from pygoattracker import decompile_sid

from unitracker import songio
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
