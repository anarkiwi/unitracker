"""The direct Music Assembler transcription plays the reference player's registers."""

from dataclasses import replace

import pymusicassembler
import pytest
from pysidtracker import gridcmp
from unitracker_grammar.document import VERSION, printed, read
from unitracker_player.player import play

from unitracker import cli
from unitracker.direct import musicassembler as m
from unitracker.direct.cli import summary

# pylint: disable=protected-access

TICKS = 4000


def standard_cells(one, text: str, ticks: int = TICKS):
    """The cells where ``text``'s grid differs from the reference player's at the standard
    note table."""
    ours = play(text, ticks=ticks)
    return gridcmp.cell_diff(ours, m.standard(one.song).render_grid(len(ours)))


@pytest.mark.parametrize("name", ["space_invadarz", "eastern_promise"])
def test_tunes_play_exactly(name, request):
    """Inner programs, an arpeggio, pulse and cutoff sweeps; a native song with transposes."""
    one = m.transcriber(request.getfixturevalue(name).read_bytes())
    text = one.document()
    assert printed(read(text)) == text
    cells = standard_cells(one, text)
    assert not cells.size, summary(cells, 3)
    assert "{loop:" in text and " run:c" in text


def test_the_templates_bound(space_invadarz):
    text = m.transcribe(space_invadarz.read_bytes())
    for bound in ("vib(", "pong(", "pulse(", " cut:csweep(", "pw:2056"):
        assert bound in text


@pytest.mark.parametrize("name", ["space_invadarz", "eastern_promise"])
def test_against_the_native_table_only_the_pitch_differs(name, request):
    one = m.transcriber(request.getfixturevalue(name).read_bytes())
    cells = m.compare(one.song, one.document(), TICKS, native=True)
    assert cells.size and set(cells[:, 1].tolist()) <= {0, 1, 7, 8, 14, 15}


@pytest.fixture(name="one")
def fixture_one(space_invadarz):
    """A transcriber of a fresh player, whose memory a test may write."""
    return m.Transcriber(pymusicassembler.read(space_invadarz.read_bytes()))


def write(one, at: int, *values):
    for k, value in enumerate(values):
        one.ref._wr(at + k, value)


def pattern(one, voice: int = 0) -> int:
    """The address of ``voice``'s first pattern."""
    pid = one._rd(one.lay.pattern_id + voice)
    return one._ptr(one.lay.pattern_lo, one.lay.pattern_hi, pid)


def program(one, n: int) -> int:
    return one._ptr(one.lay.inner_lo, one.lay.inner_hi, n)


def order(one, voice: int = 0) -> int:
    return one._ptr(one.lay.order_lo, one.lay.order_hi, voice)


def test_player_refusals(one, monkeypatch):
    lay = one.lay
    one.song.play_addr += 1
    with pytest.raises(m.TranscribeError, match="not the player's entry"):
        one._check()
    one.song.play_addr -= 1
    for at, value, match in (
        (lay.voice_reg + 1, 0, "voice dispatch"),
        (lay.tempo, 1, "reads no row"),
        (lay.dur + 2, 3, "reads no row"),
    ):
        saved = one._rd(at)
        write(one, at, value)
        with pytest.raises(m.TranscribeError, match=match):
            one._check()
        write(one, at, saved)
    one.rowlen = 1
    with pytest.raises(m.TranscribeError, match="tempo 0"):
        one._check()
    one.rowlen = 3
    one.lay = replace(lay, transrep_byte=lay.transrep + 1)
    with pytest.raises(m.TranscribeError, match="transpose cell"):
        one._check()
    one.lay = lay
    monkeypatch.setattr(one.ref, "snapshot", lambda: [1] * 25)
    with pytest.raises(m.TranscribeError, match="writes a voice"):
        one._check()


EDITS = {
    "note control \\$81": lambda one: (pattern(one) + 2, (0x81,), {}),
    "an instrument selected without a note": lambda one: (pattern(one) + 3, (0x83, 0x61), {}),
    "a voice whose first row plays no note": lambda one: (pattern(one), (0x61,), {}),
    "an inner program's filter byte": lambda one: (program(one, 4) + 2, (1,), {}),
    "an inner program jump": lambda one: (program(one, 4) + 3, (0xFD,), {"program_jump": 1}),
    "an arpeggio on other cells": lambda one: (pattern(one), (0x82,), {"arp_down_cmp": True}),
    "orderlist command \\$FC": lambda one: (order(one) + 2, (0xFC,), {"order_tempo": (1,)}),
    "the silence pattern": lambda one: (order(one) + 2, (0xFE, 0), {}),
    "start the orderlists": lambda one: (one.lay.pat_cursor, (1,), {}),
}


@pytest.mark.parametrize("match", EDITS)
def test_song_refusals(one, match):
    at, values, changes = EDITS[match](one)
    write(one, at, *values)
    one.lay = replace(one.lay, **changes)
    with pytest.raises(m.TranscribeError, match=match):
        one.document()


def test_a_pattern_of_no_end_is_refused(one):
    pid = one._rd(one.lay.pattern_id)
    write(one, one.lay.pattern_lo + pid, 0x00)
    write(one, one.lay.pattern_hi + pid, 0xC0)
    write(one, 0xC000, *(0x7F,) * 0x100)
    with pytest.raises(m.TranscribeError, match="a pattern of no end"):
        one.document()


def test_cli(space_invadarz, capsys):
    assert m.main([str(space_invadarz)]) == 0
    assert capsys.readouterr().out.startswith(f"unitracker:{VERSION}\nmeta:{{title:Space")
    assert m.main(["--verify", "--frames", "400", str(space_invadarz)]) == 0
    assert f"{space_invadarz}: exact" in capsys.readouterr().out


def test_unitracker_lift_dispatches_to_it(eastern_promise):
    raw = eastern_promise.read_bytes()
    assert cli.lifter(raw)[0] == "musicassembler"
    assert cli.lift(raw) == m.transcribe(raw)


def test_rests_and_gate_offs_play_exactly(one):
    """A long duration keeps the gate; a bare one, and an instrument's, close it."""
    edit = [(pattern(one) + 3, (0xA1, 0x61, 0x82, 0x62))]
    for at, values in edit:
        write(one, at, *values)
    text = one.document()
    ref = m.standard(one.song)
    for at, values in edit:
        for k, value in enumerate(values):
            ref._wr(at + k, value)
    ours = play(text, ticks=1000)
    cells = gridcmp.cell_diff(ours, ref.render_grid(len(ours)))
    assert not cells.size, summary(cells, 3)
    assert "- run:c2*6" in text and "off run:c3*9" in text
