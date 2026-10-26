"""The direct Music Assembler transcription plays the reference player's registers."""

from dataclasses import replace
from types import SimpleNamespace

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
    ref = m.standard(one.song)
    for at, value in getattr(one, "edits", ()):
        ref._wr(at, value)
    return gridcmp.cell_diff(ours, ref.render_grid(len(ours)))


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
    """Writes ``values`` from ``at`` to ``one``'s memory, kept for its reference player."""
    for k, value in enumerate(values):
        one.ref._wr(at + k, value)
        one.edits = getattr(one, "edits", []) + [(at + k, value)]


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
    "legato note before": lambda one: (pattern(one) + 2, (0x40,), {}),
    "an arpeggio on other cells": lambda one: (pattern(one), (0x82,), {"arp_down_cmp": True}),
    "an orderlist jump to itself": lambda one: (order(one) + 2, (0xFD, 1), {"order_jump": 1}),
    "orderlist command \\$FC": lambda one: (order(one) + 2, (0xFC,), {"order_tempo": (1,)}),
    "start the orderlists": lambda one: (one.lay.pat_cursor, (1,), {}),
}


def test_follow_jmp_chains_and_cycles():
    mem = {0x100: 0x4C, 0x101: 0x10, 0x102: 0x02, 0x210: 0x4C, 0x211: 0x00, 0x212: 0x03}
    tr = m.Transcriber.__new__(m.Transcriber)
    tr._rd = lambda at: mem.get(at, 0)
    assert tr._follow_jmp(0x100) == 0x300
    assert tr._follow_jmp(0x300) == 0x300
    mem.update({0x300: 0x4C, 0x301: 0x00, 0x302: 0x01})
    assert tr._follow_jmp(0x100) == 0x100


def test_a_jmp_trampoline_to_the_entry_is_followed(one):
    entry = one.lay.entry
    first, second = 0x0200, 0x0210
    for at, to in ((first, second), (second, entry)):
        for k, byte in enumerate((m.JMP_ABS, to & 0xFF, to >> 8)):
            write(one, at + k, byte)
    one.song.play_addr = first
    one._check()
    write(one, second + 1, first & 0xFF)
    write(one, second + 2, first >> 8)
    with pytest.raises(m.TranscribeError, match="not the player's entry"):
        one._check()


@pytest.mark.parametrize("match", EDITS)
def test_song_refusals(one, match):
    at, values, changes = EDITS[match](one)
    write(one, at, *values)
    one.lay = replace(one.lay, **changes)
    with pytest.raises(m.TranscribeError, match=match):
        one.document()


@pytest.mark.parametrize(
    "to, bound, cursor", [(2, 4, 6), (0, 4, 0), (4, 4, 0), (9, 4, 0), (3, 4, 9)]
)
def test_an_inner_program_jump_follows_the_player(one, to, bound, cursor):
    """``$FD`` continues at row ``to``, or the first row where that is 0 or past the voice's
    bound cell."""
    cell = 0x0300
    one.lay = replace(one.lay, program_jump=cell)
    write(one, cell, bound - 3)
    write(one, program(one, 4) + 3, 0xFD, to)
    voice = SimpleNamespace(flags=4, cursor=0, v=0, index=0, mask=0xFF, image=False)
    one._step(voice, 0)
    assert voice.cursor == cursor


def test_an_inner_program_filter_byte_pokes_the_sweep(one, monkeypatch):
    """The byte fires once into the sweep's accumulator, after the owner's step: on the owner's
    voice or a later one, none where nothing owns the sweep."""
    poke = {"post.cut": m.Once(one.call("poke", u=0x21))}
    monkeypatch.setattr(one, "sweep", lambda tick, v: (0, 0x45, 7, 0xFA))
    assert one._poke(0, 0x21, 5) == poke and one._poke(2, 0x21, 5) == poke
    monkeypatch.setattr(one, "sweep", lambda tick, v: (1, 0x45, 7, 0xFA))
    with pytest.raises(m.TranscribeError, match="before the sweep's owner"):
        one._poke(0, 0x21, 5)
    monkeypatch.setattr(one, "sweep", lambda tick, v: (VOICES, 0, 0, 0))
    assert one._poke(0, 0x21, 5) == {}


def test_an_inner_program_filter_byte_plays_exactly(one):
    write(one, program(one, 4) + 2, 0x21)
    try:
        text = one.document()
    except m.TranscribeError as err:
        assert "before the sweep's owner" in str(err)
        return
    assert "post.cut:poke(33)!" in text and "post:{}" in text
    cells = standard_cells(one, text)
    assert not cells.size, summary(cells, 3)


def test_a_pattern_of_no_end_is_refused(one):
    pid = one._rd(one.lay.pattern_id)
    write(one, one.lay.pattern_lo + pid, 0x00)
    write(one, one.lay.pattern_hi + pid, 0xC0)
    write(one, 0xC000, *(0x7F,) * 0x100)
    with pytest.raises(m.TranscribeError, match="a pattern of no end"):
        one.document()


def filtered(one, voice: int = 0, at: int = 0xC000):
    """Voice ``voice``'s first pattern replaced, at ``at``, by a filter note, a rest and a
    plain note; the sweep init left unowned."""
    pid = one._rd(one.lay.pattern_id + voice)
    write(one, one.lay.pattern_lo + pid, at & 0xFF)
    write(one, one.lay.pattern_hi + pid, at >> 8)
    write(one, at, 0x30, 0x83, 0x45, 0x07, 0xA1, 0x32, 0x03, MA.PATTERN_END)
    write(one, one.lay.trigger_owner, VOICES)
    one.start = one.running = (VOICES, 0, 0, 0)
    return pid


def test_a_filter_command_binds_the_sweep(one):
    pid = filtered(one)
    tokens = list(one._tokens(pid))
    assert tokens[0] == ("note", 3, None, 0x30, (0x45, 0x07), False, None)
    assert tokens[2] == ("note", 3, None, 0x32, None, False, None)
    one._filters(100)
    assert one.sweeps[0] == ((1, 0), 0, 0x45, 0x07, 0xFA)
    voice = m.Voice(0, 0)
    bind = one.call("csweep", u=0x45, c=7, s=0xFA)
    first = dict(one.event(voice, tokens[0], 0, 1).commands)
    preset = one._rd(one.lay.res_presets)
    assert first["cut"] == bind and "res" not in first
    assert one.resonance(0, tokens[0]) == {"res": preset >> 4, "route": preset & 0xF}
    assert one.resonance(0, tokens[2]) == {} and one.resonance(0, None) == {}
    assert "cut" not in dict(one.event(voice, tokens[2], 0, 10).commands)
    assert "cut" not in dict(one.event(m.Voice(0, 1), tokens[2], 0, 10).commands)


def slid(one, *pattern_bytes, voice: int = 0):
    """Voice ``voice``'s first pattern replaced by ``pattern_bytes``."""
    pid = one._rd(one.lay.pattern_id + voice)
    write(one, one.lay.pattern_lo + pid, 0x00)
    write(one, one.lay.pattern_hi + pid, 0xC0)
    write(one, 0xC000, *pattern_bytes, MA.PATTERN_END)
    return pid


def test_a_slide_note_reads_its_two_step_bytes(one):
    pid = slid(one, 0x30, 0x23, 0x11, 0x02, 0x32, 0x03, 0xA3, 0x31, 0xA3, 0x05, 0x06)
    assert [t[3:] for t in one._tokens(pid)] == [
        (0x30, None, False, (0x11, 0x02)),
        (0x32, None, False, None),
        (None, None, False, None),
        (0x31, (0x05, 0x06), False, ()),
    ]


@pytest.mark.parametrize("lo", [0x00, 0x20, 0x80, 0x01, 0x7F, 0xFF])
def test_a_frequency_slide_plays_exactly(one, lo):
    slid(one, 0x30, 0x23, lo, 0x02, 0x3F, 0x83)
    text = one.document()
    assert "vslide(" in text
    cells = standard_cells(one, text)
    assert not cells.size, summary(cells, 3)


@pytest.mark.parametrize(
    "notes",
    [
        (0x30, 0x23, 0x11, 0x02, 0x32, 0x63, 0x05, 0x01),
        (0x30, 0x23, 0x11, 0x02, 0x32, 0x43),
        (0x30, 0x03, 0x32, 0x63, 0x05, 0x01, 0x34, 0x43),
    ],
)
def test_a_legato_note_with_or_after_a_slide_plays_exactly(one, notes):
    """A legato slide starts afresh from its note; a plain legato note after one sounds it."""
    slid(one, *notes)
    text = one.document()
    cells = standard_cells(one, text)
    assert not cells.size, summary(cells, 3)


def test_a_legato_note_before_a_trigger_is_refused(one):
    slid(one, 0x30, 0x63, 0x11, 0x02)
    with pytest.raises(m.TranscribeError, match="before the voice's first trigger"):
        one.document()


def test_a_filter_command_on_a_legato_note_steps_the_running_sweep(one):
    """The accumulator and count run on; only the step changes, then the owner's next trigger
    binds the sweep afresh."""
    slid(one, 0x30, 0x03, 0x32, 0xC3, 0x11, 0x02, 0x34, 0x03, 0x36, 0x03)
    text = one.document()
    assert "csweeps(" in text
    cells = standard_cells(one, text)
    assert not cells.size, summary(cells, 3)


def test_the_sweep_passes_between_voices(one):
    """Each owner's first trigger since the sweep or its owner changed binds it again; one
    taking it from a later voice stops it on its parse tick."""
    filtered(one, voice=2, at=0xC000)
    filtered(one, voice=1, at=0xC100)
    text = one.document()
    assert one.takes and "cut:idle()" in text and text.count("cut:csweep(") >= 2
    cells = standard_cells(one, text)
    assert not cells.size, summary(cells, 3)


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
    assert "off.i" in text and " pw:3150 ctrl:64 freq:9722" in text
    cells = standard_cells(one, text)
    assert not cells.size, summary(cells, 3)
    write(one, one.lay.ctrl, 0x41)
    with pytest.raises(m.TranscribeError, match="a voice whose first row plays no note"):
        one.document()


def idle(one, ctl: int, voice: int = 0, wave: int = 0):
    """Voice ``voice`` starting on a rest with init's note control ``ctl`` and its image set."""
    write(one, pattern(one, voice), 0x61)
    write(one, one.lay.note_ctl + voice, ctl)
    write(one, one.lay.ctrl + voice, wave)
    write(one, one.lay.freq_lo_w + voice, 0x10)
    write(one, one.lay.freq_hi_w + voice, 0x27)
    write(one, one.lay.vib_out_lo + voice, 0x34)
    write(one, one.lay.vib_out_hi + voice, 0x12)


@pytest.mark.parametrize("voice", range(VOICES))
@pytest.mark.parametrize("ctl", [0x00, 0x03, 0x1F])
def test_a_voice_whose_init_left_the_latch_clear_triggers_at_the_image(one, voice, ctl):
    """The first output call triggers init's instrument at the image's frequency, gated image
    or not."""
    idle(one, ctl, voice, wave=0x41)
    write(one, one.lay.instr_x8 + voice, 5 << 3)
    text = one.document()
    assert "freq:" in text
    cells = standard_cells(one, text)
    assert not cells.size, summary(cells, 3)


@pytest.mark.parametrize("ins", range(1, 32))
def test_the_instrument_init_left_runs_its_program_and_sweep(one, ins):
    """Whatever the instrument's inner program or pulse sweep, from the image's frequency."""
    idle(one, 0x03)
    write(one, one.lay.instr_x8, ins << 3)
    flags = one._record(ins)[7]
    if flags & 0x10 and not flags & 0x0F:
        with pytest.raises(m.TranscribeError, match="an arpeggio on the instrument"):
            one.document()
        return
    cells = standard_cells(one, one.document())
    assert not cells.size, summary(cells, 3)


def test_a_slide_and_no_latch_is_refused(one):
    idle(one, 0x23)
    with pytest.raises(m.TranscribeError, match="no trigger latch"):
        one.document()


@pytest.mark.parametrize("voice", range(VOICES))
@pytest.mark.parametrize("steps", [(0x00, 0x00), (0x40, 0x00), (0xFA, 0xFD), (0x06, 0x02)])
def test_a_voice_whose_init_left_a_slide_slides_from_the_image(one, voice, steps):
    """The word init left in the slide's cells stepped by init's step bytes from the first call
    on, the frequency image's cell unused."""
    idle(one, 0x7F, voice)
    write(one, one.lay.vib_lo_step + voice, steps[0])
    write(one, one.lay.vib_hi_step + voice, steps[1])
    text = one.document()
    assert "vslidew(" in text
    cells = standard_cells(one, text)
    assert not cells.size, summary(cells, 3)


def test_a_slide_with_an_odd_low_step_is_refused(one):
    idle(one, 0x7F)
    write(one, one.lay.vib_lo_step, 0x41)
    with pytest.raises(m.TranscribeError, match="an odd low step"):
        one.document()


def test_a_latched_image_that_gates_is_still_refused(one):
    idle(one, 0x5F, wave=0x41)
    with pytest.raises(m.TranscribeError, match="a voice whose first row plays no note"):
        one.document()


def legato_notes(one, every: int, voices=range(VOICES)) -> list:
    """Sets the legato bit of every ``every``-th note after the first of each pattern the
    ``voices`` play, and returns the ``(address, value)`` of each cell it wrote."""
    done, seen = [], set()
    for v in voices:
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
                    done.append((at + y, ctl | 0x40))
                y += 2 if ctl & MA.CTL_FILTER else 0
            y += 1
            if one._rd(at + y) == MA.PATTERN_END:
                break
    return done


@pytest.mark.parametrize("every", [2, 3])
def test_legato_notes_play_exactly(one, every):
    """A note with the player's trigger latch set is a ``legato`` note that plays on."""
    edits = legato_notes(one, every, voices=(2,))
    assert edits
    text = one.document()
    assert " legato" in text
    ref = m.standard(one.song)
    for at, value in edits:
        ref._wr(at, value)
    ours = play(text, ticks=TICKS)
    cells = gridcmp.cell_diff(ours, ref.render_grid(len(ours)))
    assert not cells.size, summary(cells, 3)


@pytest.mark.parametrize("every", [2, 3])
def test_a_legato_note_over_a_running_arpeggio_plays_exactly(one, every):
    """The arpeggio runs on from the legato note's frequency at phase 0, its delay left."""
    edits = legato_notes(one, every, voices=(0,))
    assert edits
    text = one.document()
    assert "vibl(" in text and "reset:[legato]" in text
    cells = standard_cells(one, text)
    assert not cells.size, summary(cells, 3)


@pytest.mark.parametrize(
    "notes",
    [
        (0x30, 0x03, 0x83, 0x61, 0x32, 0x03),
        (0x30, 0x03, 0x84, 0xA2, 0x32, 0x03),
        (0x30, 0x03, 0x84, 0x32, 0x43, 0x82, 0x34, 0x43),
        (0x30, 0x03, 0x84, 0x32, 0x63, 0x05, 0x01),
    ],
)
def test_an_instrument_selected_without_a_trigger_plays_exactly(one, notes):
    """A note-less or legato selection: the trigger's modes, the selected record's steps."""
    for v in range(VOICES):
        slid(one, *notes, voice=v)
    text = one.document()
    cells = standard_cells(one, text)
    assert not cells.size, summary(cells, 3)


def test_orderlist_jump_is_followed():
    """``$FD n`` continues at entry ``n``, as the player does, and loops from there."""
    mem = {0: 5, 1: 0x01, 2: 6, 3: 0x01, 4: 0xFD, 5: 1, 0x80: 5, 0x81: 0x01}
    lay = SimpleNamespace(
        order_lo=0,
        order_hi=0,
        order_jump=0x90,
        order_tempo=0,
        order_end=0xFF,
        order_restart=0,
        pattern_id=0x80,
        transrep=0x81,
        repeat=0x82,
        order_cursor=0x83,
        pat_cursor=0x84,
    )
    mem[0x81] = 0x01
    mem[0x82] = 0x01
    stub = SimpleNamespace(lay=lay, _ptr=lambda lo, hi, v: 0, _rd=lambda at: mem.get(at, 0))
    entries = m.Transcriber._entries(stub, 0)
    assert [next(entries) for _ in range(5)] == [
        (0, 5, 1),
        (2, 6, 1),
        (2, 6, 1),
        (2, 6, 1),
        (2, 6, 1),
    ]


def test_silence_pattern_ends_the_voice():
    """An orderlist reaching ``$FE`` has no loop, and its walk stops there."""
    stub = SimpleNamespace(
        _entries=lambda v: iter([(0, 5, 0), (2, MA.SILENCE_PATTERN, 0), (4, 9, 0)]),
        _ticks=lambda pid: 10,
    )
    assert m.Transcriber._period(stub, 0) == (10, 0)
    assert list(m.Transcriber._walk(stub, 0, 100)) == [(1, 5, 0), (11, MA.SILENCE_PATTERN, 0)]


def test_every_voice_parked_ends_the_song():
    """Voices that all reach ``$FE`` have no loop: their parked events end together on a
    final row that ends the voices."""
    horizons = []
    stub = SimpleNamespace(
        rowlen=6,
        _period=lambda v: (v * 12, 0),
        _filters=horizons.append,
        _starts=lambda v, horizon: [(1 + v * 12, f"s{v}")],
    )
    rows, loop = m.Transcriber.timeline(stub)
    assert loop is None
    assert horizons == [1 + 24 + 6]
    assert rows[-1] == ("end",) * VOICES
    assert len(rows) == 1 + VOICES + 1
