"""The lift framework's passes, through the GoatTracker 2 lifter on synthetic tunes."""

import struct

import numpy as np
import pytest
from conftest import gt_tune

from unitracker.lift import base
from unitracker.lift.goattracker import GoatTrackerLift
from unitracker.player.vm import SongPlayer

REST = (None, 0, 0, 0)
NORMALISATIONS = ("unpause", "explicit_swing", "unmark")


def every_pass_tune():
    """A tune each GoatTracker 2 normalisation rewrites: a waveform pause, a stored swing and a
    $D10, as ``(decompiled tune, native player)``."""
    rows = [(24, 1, 14, 0)] + [REST] * 3 + [(None, 0, 0xD, 0x10)] + [REST] * 3
    rows += [(None, 0, 8, 3)] + [REST] * 7
    wave = [(0x41, 0), (0xFF, 0), (0xF7, 0x21), (0xF7, 0x05), (0xFF, 3)]
    return gt_tune(wave, rows=rows, speed=[(6, 9)])


def test_the_passes_are_named_in_the_order_they_run():
    assert GoatTrackerLift.passes() == ("structure", *NORMALISATIONS, "convert")
    names = [name for name, _ in GoatTrackerLift(every_pass_tune()[0]).stages()]
    assert tuple(names) == GoatTrackerLift.passes()


def test_each_normalisation_rewrites_the_song_before_it_and_the_lift_plays_the_player():
    dec, native = every_pass_tune()
    stages = list(GoatTrackerLift(dec, expressions=False).stages())
    assert [name for name, _ in stages] == ["structure", *NORMALISATIONS]
    assert all(before != after for (_, before), (_, after) in zip(stages, stages[1:]))
    theirs = np.asarray(native.render_grid(400), dtype=np.uint8)
    assert SongPlayer(stages[-1][1]).mismatch(theirs) is None


def test_a_lift_without_expressions_ends_on_the_last_normalisation(monkeypatch):
    def refused(_lift, _song):
        raise AssertionError("converted")

    monkeypatch.setattr(GoatTrackerLift, "convert", refused)
    lift = GoatTrackerLift(every_pass_tune()[0], expressions=False)
    assert lift.run() == list(lift.stages())[-1][1]
    assert "exprs" not in lift.run()


def test_a_normalisation_receives_the_lift_and_the_song_before_it():
    seen = []

    def record(lift, song):
        seen.append((lift, song))
        return {**song, "voices": 3}

    recorded = type(
        "Recorded",
        (GoatTrackerLift,),
        {"normalisations": (*GoatTrackerLift.normalisations, record)},
    )
    lift = recorded(every_pass_tune()[0], expressions=False)
    stages = dict(lift.stages())
    assert recorded.passes()[-2:] == ("record", "convert")
    assert seen == [(lift, stages["unmark"])] and stages["record"]["voices"] == 3


def test_a_build_the_playroutine_does_not_cover_stops_before_the_structure(monkeypatch):
    def unreached(_lift):
        raise AssertionError("structured")

    dec, _ = every_pass_tune()
    dec.info.simplepulse, dec.info.init_regs = True, ((0, 1),)
    monkeypatch.setattr(GoatTrackerLift, "structure", unreached)
    with pytest.raises(base.LiftError, match="SIMPLEPULSE pulse tables, init-routine SID writes"):
        next(GoatTrackerLift(dec).stages())


def psid(start: int, songs: int = 3) -> bytes:
    """A version 2 PSID header of ``songs`` subtunes starting on 1-based ``start``, and an RTS."""
    fields = struct.pack(">HHHHHHHI", 2, 0x7C, 0x1000, 0x1000, 0x1000, songs, start, 0)
    return b"PSID" + fields + bytes(102) + b"\x60"


@pytest.mark.parametrize("raw,subtune", [(psid(2), 1), (psid(0), 0), (b"\x00\x10\x60", 0)])
def test_the_start_song_is_the_headers_or_the_first(raw, subtune):
    assert base.start_song(raw) == subtune


def test_from_sid_decodes_the_start_song_unless_given_one(monkeypatch):
    monkeypatch.setattr(GoatTrackerLift, "decode", staticmethod(lambda raw, sub: (raw, sub)))
    assert GoatTrackerLift.from_sid(psid(3)).tune == (psid(3), 2)
    lift = GoatTrackerLift.from_sid(psid(3), 0, expressions=False)
    assert lift.tune == (psid(3), 0) and not lift.expressions
