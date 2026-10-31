"""The Music Assembler lift plays the spec player's registers exactly."""

import dataclasses
from pathlib import Path

import numpy as np
import pymusicassembler as ma
import pytest
from pymusicassembler import constants as MA
from pymusicassembler import playspec
from pymusicassembler.model import (
    Arpeggio,
    ArpeggioRow,
    Instrument,
    OrderEntry,
    Orderlist,
    Pattern,
)
from pysidtracker.testing import resolve_tune

from unitracker.lift import musicassembler as lift

BASE = "DEMOS/0-9/3LUX_Intro.sid"
LATER = "DEMOS/A-F/Coma_1.sid"
CORPUS = {
    "plain": "DEMOS/0-9/7_Years.sid",
    "order jump": BASE,
    "program jump and order tempo": LATER,
    "relative pitch only": "DEMOS/M-R/Mutant_Refrigerator.sid",
    "arpeggio after a program, tied vibrato": "MUSICIANS/G/Greg/Twilight_Zone.sid",
    "a voice primed with a trigger": "DEMOS/A-F/Acid_Mix.sid",
    "an empty pattern": "DEMOS/A-F/Dr_Doom_01.sid",
    "filter pokes from the program": "MUSICIANS/F/Fuben/Grafix_part_4.sid",
}
REFUSED = {
    "interrupt": "MUSICIANS/F/Feekzoid/Falling.sid",
    "play routine": "DEMOS/M-R/Raccoon_intro.sid",
    "silence": "DEMOS/A-F/Bloodpit.sid",
    "layout": "MUSICIANS/A/Amadeus_Meka_Design/Remember.sid",
    "multi-SID": "MUSICIANS/B/Bayliss_Richard/Nyaaaah_2000.sid",
    "program": "DEMOS/A-F/Agyrem_001.sid",
    "init": "GAMES/A-F/Attack_of_the_Mutant_Ninja_Blueberries.sid",
}
FRAMES = 1500


def tune(name: str) -> bytes:
    """The bytes of HVSC tune ``name``; the test skips where it is unavailable."""
    path = resolve_tune(name)
    if path is None:
        pytest.skip(f"{name} unavailable")
    return Path(path).read_bytes()


@pytest.mark.parametrize("name", CORPUS.values(), ids=CORPUS.keys())
def test_a_corpus_tune_lifts_exactly(name):
    cells, text = lift.verify(tune(name), 0, FRAMES)
    assert not cells.size
    assert text.startswith("unitracker:2.5\n")


@pytest.mark.parametrize("kind,name", REFUSED.items(), ids=REFUSED.keys())
def test_a_tune_the_document_cannot_state_is_refused_by_class(kind, name):
    with pytest.raises(lift.Refusal) as caught:
        lift.verify(tune(name), 0, 100)
    assert caught.value.kind == kind


def test_the_lift_returns_its_checked_text_and_the_cli_runs_it(tmp_path, capsys):
    raw = tune(CORPUS["plain"])
    text = lift.lift(raw, 0, 300)
    sid = tmp_path / "t.sid"
    sid.write_bytes(raw)
    assert lift.main([str(sid), "--frames", "300"]) == 0
    assert capsys.readouterr().out == lift.lift(raw, 0)
    assert lift.main(["--verify", "--frames", "300", str(sid)]) == 0
    assert "exact" in capsys.readouterr().out
    assert text == lift.document_text(lift.core_of_sid(raw))


def test_a_differing_document_is_a_divergence(monkeypatch):
    raw = tune(CORPUS["plain"])
    real = lift.reference
    monkeypatch.setattr(lift, "reference", lambda *a, **k: real(*a, **k) + np.uint8(1))
    cells, _ = lift.verify(raw, 0, 50)
    assert cells.size
    with pytest.raises(lift.Divergence, match="frame 0"):
        lift.checked((cells, ""), 50)


def test_the_reference_plays_the_standard_notes_or_the_tunes_own():
    raw = tune(CORPUS["plain"])
    standard, own = lift.reference(raw, 0, 400), lift.reference(raw, 0, 400, notes=False)
    assert standard.shape == own.shape == (400, 25)
    assert (standard != own).any()


# ---- songs built over a real player: every path of the play routine --------------------------

INSTRUMENTS = {
    1: Instrument(0x09, 0xA8, 0x41, 0x08, 0x13, 0x00, 0x00, 0x20),
    2: Instrument(0x06, 0x88, 0x41, 0x30, 0x05, 0x00, 0x00, 0x40),
    3: Instrument(0x0A, 0x98, 0x21, 0x08, 0x00, 0x62, 0x11, 0x10),
    4: Instrument(0x0A, 0x98, 0x21, 0x08, 0x00, 0x22, 0x0F, 0x10),
    5: Instrument(0x0A, 0x98, 0x41, 0x08, 0x31, 0x11, 0x21, 0x10 | 0x20 | 1),
    6: Instrument(0x0A, 0x98, 0x41, 0x08, 0x00, 0x00, 0x00, 0x40 | 2),
    7: Instrument(0x0A, 0x98, 0x11, 0x08, 0x00, 0x00, 0x00, 3),
    8: Instrument(0x0A, 0x98, 0x11, 0x08, 0x00, 0x00, 0x00, 4),
    9: Instrument(0x0A, 0x98, 0x21, 0x08, 0x00, 0x01, 0xFF, 0x10),
    10: Instrument(0x0A, 0x98, 0x21, 0x08, 0x00, 0x02, 0x60, 0x10),
}
PROGRAMS = {
    1: Arpeggio(
        [
            ArpeggioRow(0x81, 0xFF, 0x20),
            ArpeggioRow(0x41, 5, 0),
            ArpeggioRow(0x10, 5, 0x44),
            ArpeggioRow(0x10, 0, 0),
        ],
        MA.PROGRAM_STOP,
    ),
}


def note(n, dur, tied=False, filt=None, vib=None):
    """A note row; ``filt`` is (cutoff, length), ``vib`` (low step, high step)."""
    ctl = dur | (MA.CTL_TRIGGERED if tied else 0)
    if filt:
        return [n, ctl | MA.CTL_FILTER, *filt]
    if vib:
        return [n, ctl | MA.CTL_VIBARP, *vib]
    return [n, ctl]


def inst(i):
    """An instrument select."""
    return [MA.INSTR_MIN + i]


def rest(dur):
    """A rest of ``dur + 1`` rows."""
    return [MA.DUR_MIN | dur]


def hold(dur):
    """A hold of ``dur + 1`` rows."""
    return [MA.LONGDUR_MIN | dur]


PATTERNS = {
    "tri": inst(1)
    + note(36, 1)
    + note(40, 1, vib=(1, 0))
    + note(43, 2, vib=(2, 1))
    + note(40, 1, tied=True)
    + hold(3)
    + note(48, 1, filt=(0x85, 0x20))
    + rest(2)
    + note(48, 1, filt=(0x95, 0))
    + note(36, 2),
    "pulse": inst(2)
    + note(30, 1)
    + rest(1)
    + note(34, 2)
    + note(36, 1, filt=(0x40, 0x11))
    + hold(2),
    "arp": inst(3)
    + note(40, 3)
    + note(43, 3, vib=(3, 2))
    + note(45, 2, tied=True)
    + inst(4)
    + note(47, 3)
    + rest(1)
    + hold(1),
    "program": inst(5)
    + note(36, 3)
    + note(38, 3, tied=True)
    + inst(6)
    + note(38, 3)
    + inst(7)
    + note(40, 3)
    + inst(8)
    + note(42, 3),
    "carry": inst(9) + note(0, 15) + note(12, 15) + inst(10) + note(0, 15) + note(24, 15),
    "high": inst(10) + note(2, 15) + hold(15) + note(60, 15) + note(70, 15),
    "select": inst(1)
    + rest(3)
    + inst(2)
    + hold(2)
    + note(40, 2)
    + inst(3)
    + note(41, 1, tied=True),
    "after": inst(5) + note(40, 5, vib=(2, 0)) + inst(9) + rest(2) + note(41, 5, tied=True),
}
IDS = {name: i for i, name in enumerate(PATTERNS)}
ORDERS = [
    ([("tri", 0, 1), ("pulse", 2, 0), ("program", 0, 0), ("carry", 0, 0)], MA.ORDER_END),
    ([("arp", 0, 0), ("tri", 3, 2), ("pulse", 0, 0), ("after", 0, 1)], MA.ORDER_END),
    ([("program", 0, 1), ("arp", 12, 0), ("high", 0, 0), ("select", 0, 0), (None, 0, 0)], 0xFE),
]


def entries(order):
    """The orderlist entries of ``(pattern name, transpose, repeat)``."""
    return [
        OrderEntry(MA.SILENCE_PATTERN if name is None else IDS[name], t, r) for name, t, r in order
    ]


def synthesize(base: bytes, orders=None, patterns=None):
    """The song of ``orders`` over the player and layout of the PSID ``base``."""
    song = ma.parse(base)
    orders, patterns = orders or ORDERS, patterns or PATTERNS
    song.patterns += [Pattern()] * (len(patterns) - len(song.patterns))
    for pid, data in enumerate(patterns.values()):
        song.patterns[pid] = Pattern(data)
    song.orderlists = [Orderlist(entries(o), end) for o, end in orders]
    for i, instrument in INSTRUMENTS.items():
        song.instruments += [Instrument()] * (i + 1 - len(song.instruments))
        song.instruments[i] = instrument
    for i, program in PROGRAMS.items():
        song.inner_programs[i] = program
    return dataclasses.replace(song, image=ma.build(song))


def variant(**cells):
    """A spec binder that replaces layout cells ``cells``."""

    def bind(view, entry):
        spec, found = playspec.bind(view, entry)
        return spec, {**found, **cells}

    return bind


@pytest.fixture(scope="module", name="song")
def song_fixture():
    return synthesize(tune(BASE))


@pytest.fixture(scope="module", name="plain")
def plain_fixture():
    """Songs with no silence."""
    orders = [([n for n in o if n[0] is not None], MA.ORDER_END) for o, _ in ORDERS]
    return synthesize(tune(BASE), orders)


def exact(core, frames=1200):
    cells, _ = lift.compare(core, frames)
    assert not cells.size, cells[:3]


def test_a_built_song_plays_every_path_exactly(song):
    exact(lift.core_of_song(song))


@pytest.mark.parametrize(
    "cells",
    [{"pulse_always": 1}, {"pitch_skip": 0}, {"pitch_skip": 4}, {"dur_long": 0xC000}],
    ids=["pulse always", "relative pitches", "absolute pitches", "a hold off the counter"],
)
def test_a_build_variant_plays_exactly(plain, cells):
    exact(lift.core_of_song(plain, bind=variant(**cells)))


def test_a_silence_in_a_build_that_leaves_the_counter_is_refused(song):
    core = lift.core_of_song(song, bind=variant(dur_long=0xC000))
    with pytest.raises(lift.Refusal, match="silence"):
        lift.document_text(core)


@pytest.mark.parametrize(
    "cells,kind",
    [
        ({"arp_down_cmp": 1}, "arpeggio code"),
        ({"arp_speed": 0xC000}, "relocated arpeggio operands"),
        ({"output_voices": (0, 2)}, "voice dispatch"),
        ({"sid": 0xD420}, "SID mirror"),
        ({"sweep_sta": 0xC000}, "layout"),
        ({"order_restart": 3}, "orderlist"),
    ],
)
def test_a_layout_the_document_cannot_state_is_refused(plain, cells, kind):
    with pytest.raises(lift.Refusal) as caught:
        lift.document_text(lift.core_of_song(plain, bind=variant(**cells)))
    assert caught.value.kind == kind


# ---- the later build: orderlist tempo and jump commands, program row jumps -------------------


@pytest.fixture(scope="module", name="later")
def later_fixture():
    return tune(LATER)


def reworked(later, tempo=None, jump=None):
    """The later build's own song with its first orderlist's second entry a tempo command
    ``tempo`` and its ending a jump to the start, and its programs ending in a row jump to
    ``jump``, each a row shorter."""
    song = ma.parse(later)
    order = song.orderlists[0]
    if tempo is not None:
        order.entries[1] = OrderEntry(MA.ORDER_TEMPO, tempo >> 4, tempo & 15)
        order.entries[-1] = OrderEntry(MA.ORDER_JUMP, 0, 0)
        order.terminator = MA.ORDER_JUMP
    for k, program in enumerate(song.inner_programs):
        if jump is not None and program is not None and len(program.rows) > 2:
            song.inner_programs[k] = Arpeggio(program.rows[:-1], MA.PROGRAM_JUMP, jump)
    return dataclasses.replace(song, image=ma.build(song))


def test_orderlist_tempo_commands_that_keep_the_tempo_and_a_jump_play_exactly(later):
    keep = lift.core_of_song(ma.parse(later)).cell("tempo_reload")
    exact(lift.core_of_song(reworked(later, tempo=keep)), 1500)


def test_a_tempo_change_is_refused(later):
    with pytest.raises(lift.Refusal, match="tempo change"):
        lift.document_text(lift.core_of_song(reworked(later, tempo=9)))


def test_an_orderlist_of_nothing_but_commands_reaches_no_pattern(later):
    song = ma.parse(later)
    keep = lift.core_of_song(song).cell("tempo_reload")
    song.orderlists[0] = Orderlist([OrderEntry(MA.ORDER_TEMPO, keep >> 4, keep & 15)], MA.ORDER_END)
    song = dataclasses.replace(song, image=ma.build(song))
    with pytest.raises(lift.Refusal, match="does not reach a pattern"):
        lift.document_text(lift.core_of_song(song))


@pytest.mark.parametrize("jump", [0, 1, 2], ids=["to the start", "to the second", "to the third"])
def test_a_program_row_jump_loops_exactly(later, jump):
    exact(lift.core_of_song(reworked(later, jump=jump)), 1500)


# ---- first rows, refusals and guards ----------------------------------------------------------

STARTS = {
    "tied": inst(1) + note(40, 1, tied=True) + rest(1) + note(36, 1),
    "rest": rest(2) + inst(2) + note(36, 1),
    "hold": hold(2) + inst(3) + note(36, 1) + rest(1),
    "ends in a rest": inst(1) + note(36, 1) + rest(2),
    "tied filter": inst(1) + note(36, 1) + note(40, 1, tied=True, filt=(0x85, 0x20)),
}


def started(base, first, silent=False):
    """A song whose voices play the ``first`` patterns, the last then the silence if ``silent``."""
    patterns = {**PATTERNS, **STARTS}
    ids = {name: len(PATTERNS) + i for i, name in enumerate(STARTS)}
    orders = [([(first[v], 0, 0), ("tri", 0, 0)], MA.ORDER_END) for v in range(3)]
    if silent:
        orders[2] = ([(first[2], 0, 0), (None, 0, 0)], MA.SILENCE_PATTERN)
    global IDS  # pylint: disable=global-statement
    saved, IDS = IDS, {**IDS, **ids}
    try:
        return synthesize(base, orders, patterns)
    finally:
        IDS = saved


@pytest.mark.parametrize(
    "first",
    [("tied", "rest", "hold"), ("rest", "hold", "tied"), ("hold", "tied", "rest")],
    ids=["tied rest hold", "rest hold tied", "hold tied rest"],
)
def test_a_voice_that_starts_without_a_note_plays_exactly(first):
    exact(lift.core_of_song(started(tune(BASE), first)), 900)


@pytest.mark.parametrize("last", ["ends in a rest", "hold", "tied"])
def test_a_voice_parks_on_the_silence_after_any_row_exactly(last):
    exact(lift.core_of_song(started(tune(BASE), ("tied", "rest", last), True)), 1500)


def test_a_tied_note_with_a_filter_command_is_refused():
    song = started(tune(BASE), ("tied filter", "rest", "hold"))
    with pytest.raises(lift.Refusal, match="legato filter"):
        lift.document_text(lift.core_of_song(song))


def test_a_pattern_the_song_lacks_is_refused(plain):
    song = dataclasses.replace(plain, orderlists=[Orderlist([OrderEntry(200, 0, 0)])] * 3)
    with pytest.raises(lift.Refusal, match="200 is not a pattern"):
        lift.document_text(lift.core_of_song(song))


def test_an_orderlist_that_never_settles_is_refused(plain, monkeypatch):
    monkeypatch.setattr(lift, "MAX_ITEMS", 1)
    with pytest.raises(lift.Refusal, match="does not settle"):
        lift.document_text(lift.core_of_song(plain))


def test_an_empty_pattern_with_nothing_after_it_is_refused(plain, monkeypatch):
    song = dataclasses.replace(plain, patterns=[Pattern()] + plain.patterns[1:])
    core = lift.core_of_song(song)

    def fail(*_):
        raise lift.MusicAssemblerError("no pattern")

    monkeypatch.setattr(lift.GRAMMAR, "parse_prefix", fail)
    with pytest.raises(lift.Refusal, match="no pattern follows"):
        lift.Lift(core).events(0)


def test_a_core_the_document_cannot_state_is_refused(plain):
    core = lift.core_of_song(plain)
    view = bytearray(core.view)
    view[core.found["voice_reg"] + 1] = 8
    cases = {
        "native": dataclasses.replace(core, spec=playspec.HOOK_SPEC),
        "voice dispatch": dataclasses.replace(core, view=bytes(view)),
        "init": dataclasses.replace(
            core,
            view=core.view[: core.found["tempo"]] + b"\x05" + core.view[core.found["tempo"] + 1 :],
        ),
    }
    for kind, bad in cases.items():
        with pytest.raises(lift.Refusal) as caught:
            lift.document_text(bad)
        assert caught.value.kind == kind


def test_a_program_row_jump_bound_apart_from_the_cursor_is_refused(later):
    core = lift.core_of_song(reworked(later, jump=1), bind=variant(program_jump=0x0400))
    with pytest.raises(lift.Refusal, match="bound apart"):
        lift.document_text(core)


def test_a_trigger_of_the_image_with_a_sweep_is_refused(plain):
    one = lift.Lift(lift.core_of_song(plain))
    with pytest.raises(lift.Refusal, match="trigger of the image"):
        one.primed(3, 0, True)
    assert one.trigger(1, 0, mask=False)


def test_a_voice_that_starts_on_the_silence_plays_exactly():
    orders = [([("tri", 0, 0)], MA.ORDER_END)] * 2 + [([(None, 0, 0)], MA.SILENCE_PATTERN)]
    exact(lift.core_of_song(synthesize(tune(BASE), orders)), 1200)


def test_a_program_row_jump_past_its_rows_is_refused(later):
    one = lift.Lift(lift.core_of_song(reworked(later, jump=1)))
    rows = [ArpeggioRow(0x41, 0, 0)] * 3
    assert one.jump(Arpeggio(rows, MA.PROGRAM_JUMP, 0)) == 1
    assert one.jump(Arpeggio(rows, MA.PROGRAM_JUMP, 2)) == 3
    with pytest.raises(lift.Refusal, match="past the 3 rows"):
        one.jump(Arpeggio(rows, MA.PROGRAM_JUMP, 4))
