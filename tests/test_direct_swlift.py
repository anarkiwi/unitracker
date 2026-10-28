"""The SID-Wizard transliteration: one document object per native object, its grid the
reference player's where the grammar says the tune, the rest recorded as gaps."""

import pytest
from pysidwizard import (
    End,
    Instrument,
    Loop,
    Pattern,
    PlayPattern,
    Row,
    SWMFile,
    TempoOverride,
    Transpose,
    straight_tempo,
)
from unitracker_grammar.document import printed, read

from unitracker.direct import invariants
from unitracker.direct import swlift as sw
from unitracker.direct.cli import summary

E = Row()
META = (("title", "t"),)
TICKS = 900


def lead(**fields) -> Instrument:
    """A pulse lead: frame-1 waveform, hard restart on TICK_0, a vibrato, its tables."""
    base = {
        "control": 0x1A,
        "hr_decay": 0xF,
        "attack": 1,
        "sustain": 0xC,
        "release": 4,
        "vibrato": 0x24,
        "vibrato_delay": 4,
        "wf_table": bytes.fromhex("410c00 11f400 21c000 41800c 410000"),
        "pw_table": bytes.fromhex("880000 041000 040000 fe2300"),
    }
    return Instrument(**(base | fields))


def bass(**fields) -> Instrument:
    """A filtered bass: a hold row and an absolute row, the filter set then swept."""
    base = {
        "control": 0x19,
        "hr_decay": 0xF,
        "sustain": 0x9,
        "release": 2,
        "wf_table": bytes.fromhex("810000 03b000 410000"),
        "pw_table": bytes.fromhex("840000 02f000"),
        "filter_table": bytes.fromhex("9f8000 08f800 fe2b00"),
    }
    return Instrument(**(base | fields))


def tune(rows, instruments, others=(), chords=b"", sequences=None) -> SWMFile:
    """An Extra-build module: ``rows`` on voice 1, ``others`` on voices 2 and 3 (else rests),
    each orderlist looping its pattern unless ``sequences`` are given."""
    patterns = [Pattern(rows=list(r)) for r in (rows, *others)]
    patterns += [Pattern(rows=[E] * len(rows)) for _ in range(3 - len(patterns))]
    return SWMFile(
        sequences=sequences or [[PlayPattern(k + 1), Loop(0)] for k in range(3)],
        patterns=patterns,
        instruments=list(instruments),
        driver_type=3,
        subtune_tempos=[(straight_tempo(6), straight_tempo(6))],
        chord_table=chords,
    )


def lifted(swm) -> tuple:
    """``(lift, text)``, the text reading back to itself."""
    lift = sw.Lift(swm, META, sw.start_regs(swm))
    text = lift.document()
    assert printed(read(text)) == text
    return lift, text


def exact(swm, ticks: int = TICKS) -> str:
    """The module's document, asserting its grid is the reference player's from the first
    row's TICK_2, it records no gap and holds no more objects than the module."""
    lift, text = lifted(swm)
    cells, _ = sw.compare(swm, text, ticks)
    assert not cells.size, summary(cells, 3) + "\n" + text
    assert not lift.gaps.seen, lift.gaps.seen
    assert not invariants.exceeded(invariants.lifted(text), sw.counts(swm))
    return text


def gaps(swm) -> dict:
    """The gaps a module's lift records."""
    return lifted(swm)[0].gaps.seen


def test_notes_gate_offs_slides_and_vibrato_play_exactly():
    """WF relative, absolute and NOP rows with detunes, a pulse sweep loop, a filter sweep,
    hard restarts, a gate off, a slide, a vibrato FX and a volume small effect."""
    rows = [Row(note=49, instrument=1), E, Row(note=52), E, Row(note=0x7E), E]
    rows += [Row(note=47, fx=0x01, fx_value=0x20), E, Row(fx=0x08, fx_value=0x35), E]
    rows += [Row(note=50, fx=0xA8), E, Row(note=0x7E), E, E, E]
    low = [Row(note=25, instrument=2), E, E, E, Row(note=0x7E), E, Row(note=27), E] * 2
    text = exact(tune(rows, [lead(), bass()], [low]))
    assert "layers:{ctrl:[wf,g]}" in text and "regs:[" in text
    assert "restart:" in text and "filter:" in text and " - voice:[p1|loop:0]" in text


def test_a_keyboard_tracked_pulse_and_cutoff_play_exactly():
    """A pulse table and a filter table naming a track amount: their walks in code."""
    ins = lead(pw_table=bytes.fromhex("880010 041004 fe2300"))
    low = bass(filter_table=bytes.fromhex("9f8008 08f804 fe2b00"))
    rows = [Row(note=49, instrument=1), E, E, Row(note=37), E, E, E, E]
    text = exact(tune(rows, [ins, low], [[Row(note=25, instrument=2)] + [E] * 7]))
    assert "pst(" in text and "fst(" in text


def test_a_looping_chord_and_chord_effects_play_exactly():
    """A WF row playing the default chord for good at an arp speed; $07 and $7x select
    others on notes."""
    chords = bytes.fromhex("00 04 07 7f 00 03 07 7f 00 05 7f")
    ins = lead(wf_table=bytes.fromhex("410c00 417f00"), arp_speed=1, default_chord=1)
    rows = [Row(note=49, instrument=1), E, E, E, Row(note=49, fx=0x07, fx_value=2), E, E, E]
    rows += [Row(note=52, fx=0x73), E, E, E]
    text = exact(tune(rows, [ins], chords=chords))
    assert "\n C:[0,0,4,7,127," in text and "ch.freq:chs(" in text


def test_orderlists_transpose_and_tempo_effects_play_exactly():
    """Two patterns with an orderlist transpose, a funktempo and a main tempo effect."""
    one = [Row(note=49, instrument=1, fx=0x11, fx_value=0x45), E, Row(note=52), E]
    two = [Row(note=49, fx=0x10, fx_value=5), E, Row(note=0x7E), E]
    sequences = [[PlayPattern(1), Transpose(3), PlayPattern(2), Transpose(0), Loop(0)]]
    sequences += [[PlayPattern(3), Loop(0)], [PlayPattern(3), End()]]
    swm = tune(one, [lead()], [two, [E] * 4], sequences=sequences)
    text = exact(swm)
    assert " - voice:[p1,p2+3|loop:0]" in text and "|end]" in text and "tempo:4/5" in text


def test_hard_restart_timers_and_the_frame_one_waveform():
    """A TICK_1 hard restart, none, and a first waveform: one restart template per shape."""
    rows = [Row(note=49, instrument=1), E, E, Row(note=50, instrument=2), E, E]
    rows += [Row(note=51, instrument=3), E, E]
    ins = [lead(control=0x19), lead(control=0x18), lead(control=0x1A, first_waveform=0x81)]
    text = exact(tune(rows, ins))
    assert text.count("restart:h") == 3


def test_adsr_and_detune_effects_play_exactly():
    """Small ADSR effects merge into the live nibbles; $05, $06 and $Dx write their bytes."""
    rows = [Row(note=49, instrument=1, fx=0x52), E, Row(fx=0x05, fx_value=0x22), E]
    rows += [Row(fx=0x06, fx_value=0x8A), E, Row(note=50, fx=0xD3), E, Row(instrument=0x65), E]
    exact(tune(rows, [lead()]))


@pytest.mark.parametrize(
    "rows,instruments,chords,kind",
    [
        (
            [Row(note=49, instrument=1), E, Row(note=50), E],
            [lead(control=0x5A)],
            b"",
            "a note keeping its pulse walk, which pauses",
        ),
        (
            [Row(note=49, instrument=1), E, Row(note=50, fx=0x03, fx_value=8), E],
            [lead()],
            b"",
            "a legato or portamento note: the walks pause",
        ),
        (
            [Row(note=49, instrument=1), E],
            [lead(control=0x12)],
            b"",
            "no frame-1 waveform: the gate waits for the WF row",
        ),
        (
            [Row(note=49, instrument=1), E],
            [lead(wf_table=bytes.fromhex("417f00 400000"), default_chord=1)],
            bytes.fromhex("00 07 7e"),
            "chord returning to the WF table",
        ),
        (
            [E, Row(instrument=1), Row(note=49), E],
            [lead()],
            b"",
            "instrument selected without a note",
        ),
        ([Row(note=49, instrument=1), Row(note=0x79), E, E], [lead()], b"", "note effect $79"),
        (
            [Row(note=49, instrument=1), Row(fx=0x0E, fx_value=3), E, E],
            [lead()],
            b"",
            "big effect $0E",
        ),
        ([Row(note=49, instrument=1), Row(fx=0xE1), E, E], [lead()], b"", "small effect $E0"),
        (
            [Row(note=49, instrument=1), E],
            [lead(control=0x1E)],
            b"",
            "staccato hard restart: its tick walks no table",
        ),
    ],
)
def test_what_the_grammar_cannot_say_is_recorded(rows, instruments, chords, kind):
    """Each construct with no one-to-one form is named as a gap."""
    assert kind in gaps(tune(rows, instruments, chords=chords))


def test_an_orderlist_tempo_and_a_pattern_in_two_states_are_gaps():
    """An orderlist tempo has no place in a play entry; a pattern whose first note glides from
    one occurrence's NPORTAM and not another's has two phrases' worth of events."""
    one = [Row(note=49, instrument=1), Row(note=0x78)]
    two = [Row(note=52), E]
    sequences = [[PlayPattern(2), PlayPattern(1), TempoOverride(5), PlayPattern(2), Loop(0)]]
    sequences += [[PlayPattern(3), Loop(0)]] * 2
    seen = gaps(tune(one, [lead()], [two, [E, E]], sequences=sequences))
    assert "orderlist command" in seen and "pattern played from different voice states" in seen


def test_the_counts_bound_the_lift():
    """Every instrument's start and gate-off entry, each position effect's, the patterns."""
    ins = [lead(gateoff_wf=sw.INST_WF_TABLE_POS + 3), bass()]
    rows = [Row(note=49, instrument=1, fx=0x0A, fx_value=1), Row(note=0x7E)]
    rows += [Row(note=25, instrument=2, fx=0x09, fx_value=1), E]
    swm = tune(rows, ins)
    native = sw.counts(swm)
    assert native == {
        "instruments": 2,
        "ctrl": 2 + 1 + 1 + 2,
        "pw": 1 + 1 + 1,
        "filter": 2,
        "phrases": 3,
        "song": (2, 2, 2),
    }
    _, text = lifted(swm)
    assert not invariants.exceeded(invariants.lifted(text), native)


def test_refusals():
    """Multispeed, a module without instruments."""
    with pytest.raises(sw.LiftError, match="multispeed"):
        swm = tune([E], [lead()])
        swm.frame_speed = 2
        sw.Lift(swm)
    with pytest.raises(sw.LiftError, match="no instruments"):
        sw.Lift(tune([E], []))


def test_a_sid_lifts_verifies_and_counts(monkeypatch):
    """The raw entry points read the module through pysidwizard and the PSID header."""
    swm = tune([Row(note=49, instrument=1), E, E, E], [lead()])
    monkeypatch.setattr(sw, "parse_sid_song", lambda raw, song: swm)
    monkeypatch.setattr(sw, "_meta", lambda raw: META)
    cells, text, _ = sw.verify(b"", 0, 200)
    assert not cells.size and text == sw.transcribe(b"")
    assert sw.native_counts(b"")["instruments"] == 1
