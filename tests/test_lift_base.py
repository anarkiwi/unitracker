"""The lift framework's passes, through the GoatTracker 2 lifter on synthetic tunes, the
SID-Wizard lifter on Csillag and patched cuts of it, and the defMON lifter on its builds."""

import struct

import numpy as np
import pytest
from conftest import gt_tune, sw_fx
from pydefmon import DefmonPlayer
from pysidwizard import SWMPlayer, parse_sid_song

from unitracker.lift import backends, base, defmon
from unitracker.lift.defmon import DefmonLift
from unitracker.lift.goattracker import GoatTrackerLift
from unitracker.lift.sidwizard import Decoded, SidWizardLift
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
    assert GoatTrackerLift.passes() == ("structure", *NORMALISATIONS, "convert", "canonical")
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
    assert recorded.passes()[-3:] == ("record", "convert", "canonical")
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


SW_NORMALISATIONS = ("first_note_selects", "chord_runs", "fold_octaves")


def test_the_sidwizard_passes_run_in_order_and_convert_last(csillag, monkeypatch):
    monkeypatch.setattr(SidWizardLift, "convert", lambda _lift, song: {**song, "exprs": {}})
    monkeypatch.setattr(SidWizardLift, "canonical", lambda _lift, song: {**song, "voices": 3})
    assert SidWizardLift.passes() == ("structure", *SW_NORMALISATIONS, "convert", "canonical")
    stages = list(SidWizardLift.from_sid(csillag.read_bytes()).stages())
    assert tuple(name for name, _ in stages) == SidWizardLift.passes()
    assert stages[-2][1] == {**stages[-3][1], "exprs": {}}
    assert stages[-1][1] == {**stages[-2][1], "voices": 3}


def test_each_sidwizard_normalisation_receives_the_song_before_it(csillag):
    seen = []

    def recording(normalise):
        def record(lift, song):
            seen.append(song)
            return normalise(lift, song)

        record.__name__ = normalise.__name__
        return record

    recorded = type(
        "Recorded",
        (SidWizardLift,),
        {"normalisations": tuple(map(recording, SidWizardLift.normalisations))},
    )
    lift = recorded.from_sid(csillag.read_bytes(), expressions=False)
    stages = list(lift.stages())
    assert [name for name, _ in stages] == ["structure", *SW_NORMALISATIONS]
    assert all(got is song for got, (_, song) in zip(seen, stages))
    first, chorded, folded = (song for _, song in stages[1:])
    assert chorded["phrases"] != first["phrases"] and folded["phrases"] != chorded["phrases"]
    assert chorded["records"]["wftab"] != first["records"]["wftab"]
    assert any("octave" in rec for rec in chorded["records"]["ins"].values())
    assert not any("octave" in rec for rec in folded["records"]["ins"].values())
    assert stages[0][1] == SidWizardLift.from_sid(csillag.read_bytes()).structure()


def test_a_sidwizard_lift_without_expressions_skips_convert(csillag, monkeypatch):
    def refused(_lift, _song):
        raise AssertionError("converted")

    monkeypatch.setattr(SidWizardLift, "convert", refused)
    raw = csillag.read_bytes()
    lift = SidWizardLift.from_sid(raw, expressions=False)
    assert lift.run() == list(lift.stages())[-1][1] == backends.lift(raw, expressions=False)


def test_a_decoded_sidwizard_module_lifts_and_plays_as_given(csillag):
    raw = csillag.read_bytes()
    swm = sw_fx(raw, {(9, 8): (0x10, 4)})
    lift = SidWizardLift(Decoded(swm, base.SidImage.from_bytes(raw).header, 0), expressions=False)
    assert lift.run()["phrases"]["p09"][1] == {"note": "off", "command": [0x12, 4], "steps": 24}
    ours = lift.native().render_grid(400)
    assert np.array_equal(ours, SWMPlayer(swm).render_grid(400))
    assert not np.array_equal(ours, backends.native_player(raw).render_grid(400))
    theirs = SWMPlayer(parse_sid_song(raw, 1)).render_grid(400)
    assert np.array_equal(backends.native_player(raw).render_grid(400), theirs)


def test_the_defmon_passes_run_in_order_and_convert_last(rent_a_cop, monkeypatch):
    monkeypatch.setattr(DefmonLift, "convert", lambda _lift, song: {**song, "exprs": {}})
    monkeypatch.setattr(DefmonLift, "canonical", lambda _lift, song: {**song, "voices": 3})
    assert DefmonLift.passes() == ("structure", "unheard_init", "convert", "canonical")
    stages = list(DefmonLift.from_sid(rent_a_cop.read_bytes()).stages())
    assert tuple(name for name, _ in stages) == DefmonLift.passes()
    assert stages[-2][1] == {**stages[-3][1], "exprs": {}}
    assert stages[-1][1] == {**stages[-2][1], "voices": 3}


def test_the_defmon_normalisation_receives_the_structure(rent_a_cop, monkeypatch):
    seen = []

    def record(lift, song):
        seen.append(song)
        return defmon.unheard_init(lift, song)

    record.__name__ = "unheard_init"
    monkeypatch.setattr(DefmonLift, "normalisations", (record,))
    raw = rent_a_cop.read_bytes()
    structured, song = [s for _, s in DefmonLift.from_sid(raw, expressions=False).stages()]
    assert seen == [structured] and seen[0] is structured
    assert structured == DefmonLift.from_sid(raw).structure()
    heard, carried = song["records"].pop("vinit"), structured["records"].pop("vinit")
    assert heard.keys() == carried.keys() and song == structured
    assert all(heard[v].items() <= carried[v].items() for v in carried)


def test_a_defmon_lift_without_expressions_skips_convert(rent_a_cop, monkeypatch):
    def refused(_lift, _song):
        raise AssertionError("converted")

    monkeypatch.setattr(DefmonLift, "convert", refused)
    raw = rent_a_cop.read_bytes()
    lift = DefmonLift.from_sid(raw, expressions=False)
    assert lift.run() == list(lift.stages())[-1][1] == backends.lift(raw, expressions=False)
    grid = backends.native_player(raw).render_grid(200)
    assert np.array_equal(lift.native().render_grid(200), grid)
    assert np.array_equal(DefmonPlayer(raw).render_grid(200), grid)


def test_a_defmon_replay_grid_keeps_the_song_steps_period(persei):
    lift = DefmonLift.from_sid(persei.read_bytes(), expressions=False)
    with pytest.raises(base.LiftError, match="sub-tick calls do not repeat"):
        lift.grid(64)
    lift.structure()
    assert lift.replay.cycle == 2 and lift.grid(64).shape == (64, 25)
