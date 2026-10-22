"""A song's recorded run: it is the song's own play to its recurrence, its occurrences are those a
separate observer sees, and it is recorded once per song and bound."""

import numpy as np
import pytest
from test_checkpoint import LATE_DOC
from test_player import counter_song

from unitracker import document
from unitracker.document import writer
from unitracker.player import reach, trace
from unitracker.player.vm import SongPlayer


def occurrences(occs) -> list:
    return [(o.unit, o.voice, o.tick, o.instance, o.fetched, o.firings) for o in occs]


def test_a_trace_is_the_songs_own_play_to_its_recurrence():
    run = trace.record(LATE_DOC)
    player = SongPlayer(LATE_DOC)
    log = trace.fired_log(player)
    assert (run.tick, run.period) == reach.recurrence(player)
    document.play_to(player, run.ticks)
    assert run.frames == run.ticks == player.ticks
    assert run.starts == tuple(player.starts)
    assert run.firings(run.ticks) == log and len(log) > 0
    assert np.array_equal(run.grid(3 * run.ticks), SongPlayer(LATE_DOC).render_grid(3 * run.ticks))
    assert run.baseline.found == (run.tick, run.period)


def test_a_song_with_expressions_is_observed_with_every_cell_at_each_start():
    run = trace.record(LATE_DOC)
    player = SongPlayer(LATE_DOC)
    cells = set(player._index[0]) | set(player._index[1])  # pylint: disable=protected-access
    assert run.occurrences and all(cells <= set(o.start) for o in run.occurrences)
    assert {o.instance for o in run.occurrences} - {None}
    assert sum(o.firings for o in run.occurrences) >= len(run.fired) > 0


def test_a_native_state_over_a_bound_reads_what_the_bounded_play_does():
    player = SongPlayer(LATE_DOC, record_reads=True)
    log = trace.fired_log(player)
    document.play_to(player, 21)
    native = document.native_state(LATE_DOC, 20, 20)
    assert native.ticks == 20 and native.starts == tuple(player.starts)
    assert native.fired == writer._firings(LATE_DOC, log, 20)[0]  # pylint: disable=protected-access
    with pytest.raises(ValueError, match="past the 21"):
        document.native_state(LATE_DOC, 20, 22)
    with pytest.raises(reach.ReachabilityError):
        document.native_state(LATE_DOC, 20)


def test_a_trace_is_recorded_once_and_again_only_past_its_bound(tmp_path, monkeypatch):
    ir, played = counter_song(None), []
    record = trace.record
    monkeypatch.setattr(
        trace, "record", lambda ir, limit: played.append(limit) or record(ir, limit)
    )
    short = trace.trace(ir, 50, tmp_path)
    assert (short.tick, short.frames) == (None, 51) and short.covers(50) and not short.covers(51)
    assert trace.trace(ir, 40, tmp_path) is short
    trace._KEPT.clear()  # pylint: disable=protected-access
    assert trace.trace(ir, 50, tmp_path).frames == 51 and played == [50]
    assert trace.trace(ir, 80, tmp_path).frames == 81 and played == [50, 80]
    with pytest.raises(reach.ReachabilityError):
        short.grid(52)
    assert trace.trace(counter_song(4), cache=None).recurs()
