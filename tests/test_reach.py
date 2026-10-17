"""Table entries a song can read, and the writer dropping patch entries it never reads."""

import numpy as np
import pytest
import yaml
from pygoattracker import decompile_sid

from unitracker.format import songio, tablepatch
from unitracker.lift.goattracker import lift
from unitracker.player.reach import ReachabilityError, table_reads
from unitracker.player.vm import SongPlayer


def counter_song(width) -> dict:
    """One voice reading ``t[1 + n % 2]`` while a global counts ticks in ``width`` bits."""
    return {
        "unitracker": "ir",
        "clock": {"cycles_per_tick": 19656, "clock_hz": 985248},
        "voices": 1,
        "tables": {"t": [10, 11, 12, 13]},
        "playroutine": {
            "name": "counter",
            "global": {"n": {} if width is None else {"width": width}},
            "tick": [{"set": {"n": "n + 1"}}],
            "ghost": {"global": {0x18: "t[1 + n % 2]"}},
        },
        "tracks": [{"plays": [{"phrase": "a"}], "loop": 0}],
        "phrases": {"a": [{}]},
    }


def test_reads_cover_reached_entries_only():
    reads, ticks, period = table_reads(counter_song(4), progress=True)
    assert reads == {"t": {1, 2}}
    assert period == 16
    assert ticks >= period


def test_no_recurrence_raises():
    with pytest.raises(ReachabilityError, match="did not recur within 50 ticks"):
        table_reads(counter_song(None), max_ticks=50)


def test_state_covers_cells_cursors_and_registers():
    player = SongPlayer(counter_song(4))
    player.play_frame()
    before = player.state()
    player.play_frame()
    assert player.state() != before
    assert player.table_reads is None


def patched(metal_warrior):
    ir = lift(decompile_sid(metal_warrior.read_bytes(), subtune=0))
    ftab = ir["tables"]["ftab"]
    ftab[0] += 1
    ftab[24] += 1
    return ir


def test_writer_keeps_read_entries_and_drops_unread(metal_warrior):
    ir = patched(metal_warrior)
    text = songio.dumps(ir)
    assert yaml.safe_load(text)["playroutine"]["tables"] == {"ftab": {24: ir["tables"]["ftab"][24]}}
    back = songio.loads(text)
    assert back["tables"]["ftab"][24] == ir["tables"]["ftab"][24]
    assert back["tables"]["ftab"][0] != ir["tables"]["ftab"][0]
    np.testing.assert_array_equal(
        SongPlayer(back).render_grid(4000), SongPlayer(ir).render_grid(4000)
    )
    assert songio.dumps(back) == text


def test_writer_keeps_whole_patch_without_recurrence(metal_warrior, monkeypatch):
    def unreachable(_ir):
        raise ReachabilityError("no recurrence")

    monkeypatch.setattr(tablepatch, "table_reads", unreachable)
    ir = patched(metal_warrior)
    patch = yaml.safe_load(songio.dumps(ir))["playroutine"]["tables"]["ftab"]
    assert {0, 24} <= set(patch)
