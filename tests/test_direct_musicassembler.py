"""The direct Music Assembler transcription plays the reference player's registers."""

from dataclasses import replace

import pymusicassembler
import pytest
from pysidtracker import gridcmp
from unitracker_grammar.document import VERSION, printed, read
from unitracker_player.player import play

from unitracker import cli
from unitracker.direct import musicassembler as m
from unitracker.direct.musicassembler import MA, VOICES
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
    "note control \\$21": lambda one: (pattern(one) + 2, (0x21,), {}),
    "legato note before": lambda one: (pattern(one) + 2, (0x40,), {}),
    "an instrument selected without a note": lambda one: (pattern(one) + 3, (0x83, 0x61), {}),
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


def filtered(one, voice: int = 0):
    """Voice ``voice``'s first pattern replaced by a filter note, a rest and a plain note;
    the sweep init left unowned."""
    pid = one._rd(one.lay.pattern_id + voice)
    write(one, one.lay.pattern_lo + pid, 0x00)
    write(one, one.lay.pattern_hi + pid, 0xC0)
    write(one, 0xC000, 0x30, 0x83, 0x45, 0x07, 0xA1, 0x32, 0x03, MA.PATTERN_END)
    one.start = (VOICES, 0, 0, 0)
    return pid


def test_a_filter_command_binds_the_sweep(one):
    pid = filtered(one)
    tokens = list(one._tokens(pid))
    assert tokens[0] == ("note", 3, None, 0x30, (0x45, 0x07), False)
    assert tokens[2] == ("note", 3, None, 0x32, None, False)
    one._filters(100)
    assert one.sweeps[0] == ((1, 0), 0, 0x45, 0x07, 0xFA)
    voice = m.Voice(0, 0)
    bind = one.call("csweep", u=0x45, c=7, s=0xFA)
    first = dict(one.event(voice, tokens[0], 0, 1).commands)
    preset = one._rd(one.lay.res_presets)
    assert (first["res"], first["route"], first["cut"]) == (preset >> 4, preset & 0xF, bind)
    again = dict(one.event(voice, tokens[2], 0, 10).commands)
    assert "cut" not in again and "res" not in again
    assert "cut" not in dict(one.event(m.Voice(0, 1), tokens[2], 0, 10).commands)


def test_a_filter_sweep_on_two_voices_is_refused(one):
    filtered(one)
    one.start = (1, 0x10, 5, 0xF0)
    with pytest.raises(m.TranscribeError, match="more than one voice"):
        one._filters(100)


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


def test_a_voice_may_start_without_a_note_if_its_image_does_not_gate(one):
    """The image's cells are the init events', the token an ordinary ``off``; a gated image is
    refused."""
    write(one, pattern(one), 0x61)
    write(one, one.lay.ctrl, 0x40)
    write(one, one.lay.freq_lo_w, 0x10)
    write(one, one.lay.freq_hi_w, 0x27)
    write(one, one.lay.pw_lo, 0x4E)
    write(one, one.lay.pw_hi, 0x1C)
    text = one.document()
    assert "freq:10000" in text and "pw:3150" in text and "ctrl:64" in text
    assert "off pw:3150 ctrl:64 freq:10000" in text
    write(one, one.lay.ctrl, 0x41)
    with pytest.raises(m.TranscribeError, match="a voice whose first row plays no note"):
        one.document()


def legato_notes(one, every: int) -> int:
    """Sets the legato bit of every ``every``-th note after the first of each pattern the
    voices play, and returns how many it set."""
    done, seen = 0, set()
    for v in range(VOICES):
        starts = set()
        for y, pid, _ in one._entries(v):
            if y in starts:
                break
            starts.add(y)
            seen.add(pid)
    for pid in seen:
        at = one._ptr(one.lay.pattern_lo, one.lay.pattern_hi, pid)
        y, count = 0, 0
        while True:
            val, selected = one._rd(at + y), False
            if MA.INSTR_MIN <= val < MA.LONGDUR_MIN:
                y, selected = y + 1, True
                val = one._rd(at + y)
            if val < MA.DUR_MIN:
                y += 1
                ctl, count = one._rd(at + y), count + 1
                if count % every == 0 and not selected and not ctl & 0xE0:
                    write(one, at + y, ctl | 0x40)
                    done += 1
                y += 2 if ctl & MA.CTL_FILTER else 0
            y += 1
            if one._rd(at + y) == MA.PATTERN_END:
                break
    return done


@pytest.mark.parametrize("every", [2, 3])
def test_legato_notes_play_exactly(one, every):
    """A note with the player's trigger latch set is a ``legato`` note that plays on."""
    assert legato_notes(one, every)
    text = one.document()
    assert " legato" in text
    cells = standard_cells(one, text)
    assert not cells.size, summary(cells, 3)
