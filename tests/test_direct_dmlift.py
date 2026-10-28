"""The defMON transliteration of synthetic worktunes packed by defMON's own packer."""

import re

import numpy as np
import pytest
from pydefmon import PatternEvent
from pysidtracker import SidImage

from conftest import pack_worktune
from unitracker import cli
from unitracker.direct import dmcode, dmlift, invariants
from unitracker.direct.dmimage import LiftError

E = PatternEvent
CTRL = (4, 11, 18)


def test_a_song_is_transliterated_exactly(defmon_song):
    cells, text = dmlift.verify(defmon_song)
    assert not cells.size
    assert text.startswith("unitracker:2.4\n") and "layers:{ctrl:[a,b]}" in text
    assert re.search(r" b\.run:c\d+", text) and "{loop:0}]" in text
    assert text.count(" - voice:[p256,") == 3 and "|loop:2]" in text


def test_the_counts_are_the_tunes(defmon_song):
    text = dmlift.transcribe(defmon_song)
    native = dmlift.native_counts(defmon_song)
    assert native == {"ctrl_layers": (2, 2), "phrases": 4, "song": (3, 3, 3)}
    assert invariants.exceeded(invariants.lifted(text), native) is None
    assert invariants.lifted(text)["ctrl_layers"] == (2, 2)


def test_a_row_resolves_its_nibble_from_its_program(defmon_song):
    text = dmlift.transcribe(defmon_song)
    assert re.search(r"c\d+:\[\{hold:3,ctrl:65,ad:9,sr:240,.*\},\{ctrl:64\},end\]", text)


def test_a_nibble_outside_the_program_is_a_gap(defmon_gap):
    lift = dmlift.Lift(dmlift.Tune(defmon_gap))
    text = lift.document()
    (entry, row, wgs), *rest = lift.gaps
    assert not rest and entry == row and wgs == ((None, 0),)
    assert f" c{row}:[{{}},end]" in text
    cells, _ = dmlift.verify(defmon_gap)
    assert cells.size and set(cells[:, 1]) <= set(CTRL)


def test_a_loop_resolving_a_nibble_two_ways_is_a_gap(defmon_editor):
    rows = {
        9: (0, {"WGh": 0x40, "WGl": 0x01}),
        10: (0, {"WGl": 0x04}),
        11: (0, {"WGh": 0x20}),
        12: 10,
    }
    raw = pack_worktune(
        defmon_editor,
        rows,
        [[E.note_on(0x30, slot_a=9, duration=7), E.alt_end(1)]],
        [(0, 0, 0), (0, 0)],
    )
    lift = dmlift.Lift(dmlift.Tune(raw))
    lift.document()
    assert [g[2] for g in lift.gaps] == [((32, 4), (64, 4))]


def test_a_counted_jump_is_a_group(defmon_editor):
    patterns = [[E.note_on(0x30 + k, duration=k), E.alt_end(1)] for k in range(3)]
    steps = [(0, 0, 0), (1, 1, 1), (0, 2), (2, 2, 2), (3, 0)]
    raw = pack_worktune(defmon_editor, {}, patterns, steps)
    cells, text = dmlift.verify(raw)
    assert not cells.size
    assert re.search(r"voice:\[p256,\[p\d+,p\d+\]x3,p\d+\|loop:2\]", text)
    assert dmlift.native_counts(raw)["song"] == (4, 4, 4)


def test_an_alt_cuts_the_other_voices_patterns(defmon_editor):
    long = [E.note_on(0x30, duration=15), E.note_on(0x32, duration=15), E.alt_end(1)]
    short = [E.note_on(0x40, duration=3), E.alt_end(0)]
    raw = pack_worktune(defmon_editor, {}, [long, short], [(0, 1, 1), (0, 0, 0), (0, 0)])
    cells, text = dmlift.verify(raw)
    assert not cells.size
    assert re.search(r" p(25[7-9]|2[6-9]\d):", text)


def test_init_leaves_a_note_apart_from_its_base(defmon_song):
    tune = dmlift.Tune(defmon_song)
    image = SidImage.from_bytes(defmon_song)
    at = tune.at[dmlift.BASE] - image.header.real_load_address
    raw = bytearray(defmon_song)
    raw[len(defmon_song) - len(image.image) + at] = 0x30
    text = dmlift.transcribe(bytes(raw))
    assert "i2:{ctrl:c512}" in text and "c512:[{note:C-2},end]" in text
    assert text.count("C2.i2") == 1
    assert dmlift.native_counts(bytes(raw))["ctrl_layers"] == (3, 2)
    assert not dmlift.verify(bytes(raw))[0].size


@pytest.mark.parametrize(
    "patch,where,message",
    [
        ({0x1B00: 0xFF, 0x1C00: 1, 0x1B01: 0xFF}, "arranger", "a jump onto a jump"),
        ({0x1B01: 0xFF, 0x1C01: 5, 0x1D01: 1}, "arranger", "a counted jump forward to 5"),
        ({0x1902: 0, 0x1802: 3, 0x1903: 0}, "walk", "jumps onto jump row"),
    ],
)
def test_what_the_replay_cannot_play_is_refused(defmon_song, patch, where, message):
    tune = dmlift.Tune(defmon_song)
    for addr, value in patch.items():
        tune.mem[tune.at[addr & 0xFF00] + (addr & 0xFF)] = value
    with pytest.raises(LiftError, match=message):
        getattr(tune, where)(*() if where == "arranger" else (2,))


def test_a_counted_jump_over_a_counted_jump_never_ends(defmon_song):
    tune = dmlift.Tune(defmon_song)
    patch = {0x1B02: 0xFF, 0x1C02: 1, 0x1D02: 1, 0x1B03: 0, 0x1B04: 0xFF, 0x1C04: 0, 0x1D04: 1}
    for addr, value in patch.items():
        tune.mem[tune.at[addr & 0xFF00] + (addr & 0xFF)] = value
    assert tune.arranger() == ((0, ((1,), 2), 3), 0)


def test_a_pattern_with_no_alt_and_an_unarmed_init_are_refused(defmon_song):
    tune = dmlift.Tune(defmon_song)
    for base in dmlift.PATTERNS:
        tune.mem[tune.at[base] + 5] = 0xC0 if base == dmlift.PATTERNS[1] else 0x00
    tune.mem[0xC000 : 0xC000 + dmlift.MAX_EVENTS] = [0] * dmlift.MAX_EVENTS
    with pytest.raises(LiftError, match="reach no ALT"):
        tune.step((5, 5, 5))
    tune.mem[tune.at[dmlift.ARMED]] = 0
    with pytest.raises(LiftError, match="unarmed"):
        dmlift.Lift(tune).lead()


def test_a_multi_sid_tune_is_refused(defmon_song):
    raw = bytearray(defmon_song)
    raw[5], raw[0x7A] = 3, 0x42
    with pytest.raises(LiftError, match="multi-SID"):
        dmlift.Tune(bytes(raw))


def test_the_templates_read_the_replays_note_table():
    code = dmcode.native(dmcode.CODE["pitch"])
    assert "W.lo,y" in code and "F.lo" not in code and "and #127" in code
    assert "and #127" not in dmcode.native(dmcode.CODE["pitch"], absolute=False)


def test_the_defmon_lifter_is_the_transliteration(defmon_song, tmp_path, capsys):
    assert cli.lifter(defmon_song)[1] is dmlift
    path = tmp_path / "song.sid"
    path.write_bytes(defmon_song)
    assert dmlift.main([str(path)]) == 0
    assert capsys.readouterr().out == dmlift.transcribe(defmon_song)
    assert dmlift.main(["--verify", str(path)]) == 0
    assert "exact" in capsys.readouterr().out
    assert cli.lift(defmon_song) == dmlift.transcribe(defmon_song)
    assert not np.asarray(cli.verify(defmon_song, 0, 400)).size
