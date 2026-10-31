"""The Music Assembler lift plays the spec player's registers exactly."""

import dataclasses
import json
from functools import cached_property
from pathlib import Path

import numpy as np
import pymusicassembler as ma
import pytest
from pymusicassembler import constants as MA
from pymusicassembler import playspec
from pymusicassembler.model import (
    Arpeggio,
    ArpeggioRow,
    InitState,
    Instrument,
    OrderEntry,
    Orderlist,
    Pattern,
)
from pysidtracker.testing import resolve_tune
from unitracker_grammar.document import read

from unitracker.lift import invariants
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
    "silence after a stopping program": "DEMOS/A-F/Bloodpit.sid",
    "silence, orderlist tempo": "DEMOS/UNKNOWN/Mr_Normal.sid",
    "a program of the bytes the pointer leads to": "DEMOS/A-F/Agyrem_001.sid",
    "a program the bytes after the table end": "MUSICIANS/F/Fuben/Grafix_part_1.sid",
    "a vibrato running from the image": "GAMES/A-F/Attack_of_the_Mutant_Ninja_Blueberries.sid",
    "an instrument past the file's end": "MUSICIANS/D/Defbeat/Moonlight.sid",
}
REFUSED = {
    "interrupt": "MUSICIANS/F/Feekzoid/Falling.sid",
    "play routine": "DEMOS/M-R/Raccoon_intro.sid",
    "layout": "MUSICIANS/A/Amadeus_Meka_Design/Remember.sid",
    "multi-SID": "MUSICIANS/B/Bayliss_Richard/Nyaaaah_2000.sid",
}
FRAMES = 1500
SIZES = Path(__file__).with_name("lift_sizes_musicassembler.json")


def tune(name: str) -> bytes:
    """The bytes of HVSC tune ``name``; the test skips where it is unavailable."""
    path = resolve_tune(name)
    if path is None:
        pytest.skip(f"{name} unavailable")
    return Path(path).read_bytes()


@pytest.mark.parametrize("name", CORPUS.values(), ids=CORPUS.keys())
def test_a_corpus_tune_names_the_native_object_of_every_object_it_holds(name):
    raw = tune(name)
    text = lift.document_text(lift.core_of_sid(raw))
    report = lift.measure(raw, 0, text)
    for kind, (objects, native) in report["kinds"].items():
        assert native <= objects, kind
    assert all(have <= most for have, most in zip(*report["song"]))
    assert sum(report["bytes"].values()) == len(text.encode())


SUBTUNES = [
    ("MUSICIANS/F/Feekzoid/Shoot_em_Up.sid", 1),
    ("MUSICIANS/B/Beat_Machine/Chris/Megablastic.sid", 3),
]


def test_no_fixture_lifts_to_more_song_bytes_than_its_baseline():
    have = {}
    for name, subtune in [*((n, 0) for n in CORPUS.values()), *SUBTUNES]:
        text = lift.document_text(lift.core_of_sid(tune(name), subtune))
        have[name + (f"#{subtune}" if subtune else "")] = invariants.sizes(text)["song"]
    assert have.keys() == json.loads(SIZES.read_text(encoding="utf-8")).keys()
    assert invariants.grown(have, json.loads(SIZES.read_text(encoding="utf-8"))) == []


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


@pytest.mark.parametrize("name,subtune", SUBTUNES, ids=["Shoot_em_Up 2", "Megablastic 4"])
def test_a_subtune_whose_init_points_the_voices_at_other_lists_lifts_exactly(name, subtune):
    raw = tune(name)
    assert (
        lift.core_of_sid(raw, subtune).song.orderlists != lift.core_of_sid(raw, 0).song.orderlists
    )
    cells, _ = lift.verify(raw, subtune, FRAMES)
    assert not cells.size


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
    """The orderlist entries of ``(pattern name, transpose, repeat)``; a name that is a number
    is the tempo command of that control byte."""
    out = []
    for item in order:
        if isinstance(item, int):
            out.append(OrderEntry(MA.ORDER_TEMPO, item >> 4, item & 15))
        else:
            name, t, r = item
            out.append(OrderEntry(MA.SILENCE_PATTERN if name is None else IDS[name], t, r))
    return out


def rebuilt(song):
    """``song`` parsed again from the image it builds, its init state that image's."""
    return ma.parse(song.container_header + ma.build(song))


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
    return rebuilt(song)


def varied(core, **cells):
    """``core`` with the layout cells ``cells`` replaced, in the state the lift reads and in the
    binder the reference player is built with."""

    def bind(view, entry):
        spec, found = playspec.bind(view, entry)
        return spec, {**found, **cells}

    class Varied(InitState):
        """An init state whose layout has the replaced cells."""

        @cached_property
        def _bound(self):
            return bind(self.memory, self.entry)

    state = core.state
    return dataclasses.replace(
        core, state=Varied(state.subtune, state.memory, state.entry), bind=bind
    )


def poked(core, **cells):
    """``core`` whose memory has each ``name: bytes`` written at the layout cell ``name``."""
    memory = bytearray(core.state.memory)
    for name, data in cells.items():
        memory[core.found[name] : core.found[name] + len(data)] = data
    return dataclasses.replace(core, state=dataclasses.replace(core.state, memory=bytes(memory)))


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
    exact(varied(lift.core_of_song(plain), **cells))


def test_a_silence_in_a_build_that_leaves_the_counter_is_refused(song):
    core = varied(lift.core_of_song(song), dur_long=0xC000)
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
        lift.document_text(varied(lift.core_of_song(plain), **cells))
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
    return rebuilt(song)


def test_orderlist_tempo_commands_that_keep_the_tempo_and_a_jump_play_exactly(later):
    keep = lift.core_of_song(ma.parse(later)).cell("tempo_reload")
    core = lift.core_of_song(reworked(later, tempo=keep))
    assert lift.Lift(core).steady
    assert lift.document_text(core).count("tempo:") == 1
    exact(core, 1500)


def test_an_orderlist_of_nothing_but_commands_restarts_onto_a_command(later):
    song = ma.parse(later)
    keep = lift.core_of_song(song).cell("tempo_reload")
    song.orderlists[0] = Orderlist([OrderEntry(MA.ORDER_TEMPO, keep >> 4, keep & 15)], MA.ORDER_END)
    with pytest.raises(lift.Refusal, match="restart onto a tempo command"):
        lift.document_text(lift.core_of_song(rebuilt(song)))


def test_a_restart_past_the_entries_reaches_no_pattern(later):
    core = varied(lift.core_of_song(ma.parse(later)), order_restart=100)
    with pytest.raises(lift.Refusal, match="does not reach a pattern"):
        lift.document_text(core)


@pytest.mark.parametrize("jump", [0, 1, 2], ids=["to the start", "to the second", "to the third"])
def test_a_program_row_jump_loops_exactly(later, jump):
    exact(lift.core_of_song(reworked(later, jump=jump)), 1500)


# ---- time: a global tempo, a row an event of steps ---------------------------------------------


def phrase_events(text):
    """Every event of the document's phrase definitions."""
    doc = read(text)
    return [
        e for section in doc.defs if section.kind == "phrase" for _, v in section.records for e in v
    ]


def test_the_tempo_is_global_and_a_row_is_an_event_of_its_duration_in_steps(plain):
    core = lift.core_of_song(plain)
    text = lift.document_text(core)
    doc = read(text)
    assert doc.header.tempo == core.cell("tempo_reload") + 1
    assert "vtempo" not in text
    assert doc.header.holds == (("ctrl", ("event",)),)
    steps = {e.steps for e in phrase_events(text)}
    assert {2, 3, 4, 16} <= steps
    assert all(slot.hold == ("event",) for slot in doc.header.slots[0][1] if slot.name != "c")


def test_a_voice_row_longer_than_the_clock_lasts_one_row_off_the_counter(plain):
    core = varied(lift.core_of_song(plain), dur_long=0xC000)
    assert {e.steps for e in phrase_events(lift.document_text(core))} >= {None, 2}
    assert lift.Lift(core).steps(5, "hold") is None
    assert lift.Lift(core).steps(5, "note") == 6


# ---- orderlist tempo changes ---------------------------------------------------------------

CHANGES = {
    "one voice mid-list": [[("tri", 0, 1), 9, ("pulse", 2, 0), ("program", 0, 0)], [], []],
    "after a repeated entry": [[("tri", 0, 3), 9, ("pulse", 0, 0)], [], []],
    "two voices": [[("tri", 0, 0), 2, ("pulse", 0, 0)], [("arp", 0, 1), 0, ("tri", 3, 0)], []],
    "a command twice": [[("tri", 0, 1), 9, 4, ("pulse", 2, 0), ("carry", 0, 0)], [], []],
    "the end of the list": [[("tri", 0, 0), ("pulse", 0, 0), 12], [], []],
    "to the same tempo": [[("tri", 0, 0), 3, ("pulse", 0, 0)], [], []],
}


def changed(base, orders):
    """A song over ``base`` whose voices play ``orders``, a voice's empty list the plain ones."""
    plain_orders = [(o, MA.ORDER_END) for o, _ in ORDERS[:2]] + [([("high", 0, 0)], MA.ORDER_END)]
    given = [(o or plain_orders[v][0], MA.ORDER_END) for v, o in enumerate(orders)]
    return synthesize(base, given)


@pytest.mark.parametrize("orders", CHANGES.values(), ids=CHANGES.keys())
def test_a_tempo_change_in_an_orderlist_plays_exactly(later, orders):
    core = lift.core_of_song(changed(later, orders))
    exact(core, 2500)


def test_a_tempo_change_is_a_command_of_the_last_row_of_its_entrys_last_run(later):
    core = lift.core_of_song(changed(later, CHANGES["after a repeated entry"]))
    assert not lift.Lift(core).steady
    doc = read(lift.document_text(core))
    records = [v for s in doc.defs if s.kind == "phrase" for _, v in s.records]
    carriers = [v for v in records if any(("tempo", 10) in e.commands for e in v)]
    assert len(carriers) == 1
    assert [("tempo", 10) in e.commands for e in carriers[0]].count(True) == 1
    assert ("tempo", 10) in carriers[0][-1].commands
    steady = lift.document_text(lift.core_of_song(changed(later, [[], [], []])))
    assert "tempo:10" not in steady


def test_a_tempo_change_moves_the_rows_it_follows(later):
    slowed = lift.player_of(lift.core_of_song(changed(later, CHANGES["one voice mid-list"])))
    steady = lift.player_of(lift.core_of_song(changed(later, [[], [], []])))
    assert (np.asarray(slowed.render_grid(2500)) != np.asarray(steady.render_grid(2500))).any()


def test_a_tempo_change_under_a_silence_with_a_running_program_is_refused(later, monkeypatch):
    program = Arpeggio([ArpeggioRow(0x41, 5, 0)] * 3, MA.PROGRAM_STOP)
    monkeypatch.setitem(PROGRAMS, 1, program)
    patterns = {**PATTERNS, "last": inst(5) + note(36, 1)}
    ids = {name: i for i, name in enumerate(patterns)}
    orders = [([("tri", 0, 0), 9, ("pulse", 0, 0)], MA.ORDER_END)] + [
        ([("tri", 0, 0)], MA.ORDER_END)
    ]
    orders += [([("last", 0, 0), (None, 0, 0)], MA.SILENCE_PATTERN)]
    global IDS  # pylint: disable=global-statement
    saved, IDS = IDS, ids
    try:
        song = synthesize(later, orders, patterns)
    finally:
        IDS = saved
    with pytest.raises(lift.Refusal, match="across a tempo change"):
        lift.document_text(lift.core_of_song(song))


def test_a_leading_tempo_command_the_init_does_not_pass_is_refused(later):
    orders = [([9, ("tri", 0, 0), ("pulse", 0, 0)], MA.ORDER_END)] + list(ORDERS[1:2])
    orders += [([("high", 0, 0)], MA.ORDER_END)]
    with pytest.raises(lift.Refusal, match="leading tempo command"):
        lift.document_text(lift.core_of_song(synthesize(later, orders)))


def test_a_restart_onto_a_tempo_command_is_refused(later):
    core = varied(lift.core_of_song(changed(later, CHANGES["one voice mid-list"])), order_restart=2)
    with pytest.raises(lift.Refusal, match="restart onto a tempo command"):
        lift.document_text(core)


# ---- provenance and the reduced walk -----------------------------------------------------------


def test_every_named_object_stands_for_a_native_one_and_a_context_only_among_copies(song):
    core = lift.core_of_song(song)
    text, origins = lift.build(core)
    counts = invariants.provenance(text, origins)
    for kind, named in origins.items():
        natives = [o.native for o in named.values()]
        for origin in named.values():
            assert bool(origin.context) == (natives.count(origin.native) > 1), kind
    assert counts["phrases"][0] >= counts["phrases"][1] > 0
    assert {o.native[0] for o in origins["phrases"].values()} == {"pattern"}
    assert {o.native[0] for o in origins["instruments"].values()} == {"instrument"}


def test_a_lift_that_names_an_object_twice_for_one_origin_is_refused(plain, monkeypatch):
    real = lift.Names.origins

    def doubled(self):
        out = real(self)
        out["phrases"] = {n: invariants.Origin(("pattern", 0)) for n in out["phrases"]}
        return out

    monkeypatch.setattr(lift.Names, "origins", doubled)
    with pytest.raises(invariants.ModelError, match="one origin"):
        lift.document_text(lift.core_of_song(plain))


def test_song_entries_past_the_orderlists_are_refused(plain, monkeypatch):
    monkeypatch.setattr(lift, "native_counts", lambda core: {"song": (0, 0, 0)})
    with pytest.raises(invariants.ModelError, match="song"):
        lift.document_text(lift.core_of_song(plain))


@pytest.mark.parametrize(
    "visits,loop,want",
    [
        ("ABCBC", 1, (1, "ABC")),
        ("XBCXBC", 3, (0, "XBC")),
        ("ABAB", 0, (0, "AB")),
        ("AAAA", 2, (0, "A")),
        ("ABCD", 2, (2, "ABCD")),
    ],
)
def test_the_walk_loops_at_the_shortest_cycle_that_plays_the_same_visits(visits, loop, want):
    got_loop, got = lift.Lift.reduced(list(visits), loop)
    assert (got_loop, "".join(got)) == want


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
    with pytest.raises(lift.Refusal, match="pattern"):
        lift.document_text(lift.core_of_song(rebuilt(song)))


def test_an_orderlist_that_never_settles_is_refused(plain, monkeypatch):
    monkeypatch.setattr(lift, "MAX_ITEMS", 1)
    with pytest.raises(lift.Refusal, match="does not settle"):
        lift.document_text(lift.core_of_song(plain))


def test_an_empty_pattern_with_nothing_after_it_is_refused(plain, monkeypatch):
    song = dataclasses.replace(plain, patterns=[Pattern()] + plain.patterns[1:])
    core = lift.core_of_song(rebuilt(song))

    def fail(*_):
        raise lift.MusicAssemblerError("no pattern")

    monkeypatch.setattr(lift.GRAMMAR, "parse_prefix", fail)
    with pytest.raises(lift.Refusal, match="no pattern follows"):
        lift.Lift(core).events(0)


def test_a_core_the_document_cannot_state_is_refused(plain, monkeypatch):
    core = lift.core_of_song(plain)
    cases = {
        "voice dispatch": poked(core, voice_reg=bytes([0, 8, 14])),
        "init": poked(core, tempo=b"\x05"),
    }
    for kind, bad in cases.items():
        with pytest.raises(lift.Refusal) as caught:
            lift.document_text(bad)
        assert caught.value.kind == kind
    monkeypatch.setattr(lift.playspec, "has_native", lambda _: True)
    with pytest.raises(lift.Refusal, match="native"):
        lift.document_text(core)


def test_a_program_row_jump_bound_apart_from_the_cursor_is_refused(later):
    core = varied(lift.core_of_song(reworked(later, jump=1)), program_jump=0x0400)
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


# ---- silence while a stopping program runs, programs read from memory, image vibrato ---------


def ends_in_silence(base, pattern):
    """A song whose third voice plays ``pattern`` then the silence."""
    patterns = {**PATTERNS, "last": pattern}
    ids = {name: i for i, name in enumerate(patterns)}
    orders = [([("tri", 0, 0)], MA.ORDER_END)] * 2
    orders += [([("last", 0, 0), (None, 0, 0)], MA.SILENCE_PATTERN)]
    global IDS  # pylint: disable=global-statement
    saved, IDS = IDS, ids
    try:
        return synthesize(base, orders, patterns)
    finally:
        IDS = saved


@pytest.mark.parametrize("tail", [[], rest(0), hold(1)], ids=["note", "rest", "hold"])
@pytest.mark.parametrize("dur", [0, 1, 3])
@pytest.mark.parametrize("rows", [1, 2, 4])
def test_a_silence_while_a_stopping_program_runs_plays_exactly(monkeypatch, rows, dur, tail):
    program = Arpeggio([ArpeggioRow(0x41, 5, 0)] * rows, MA.PROGRAM_STOP)
    monkeypatch.setitem(PROGRAMS, 1, program)
    song = ends_in_silence(tune(BASE), inst(5) + note(36, dur) + tail)
    core = lift.core_of_song(song)
    exact(core, 1400)


def test_the_gate_stays_up_until_the_parse_that_follows_the_programs_end():
    one = lift.Lift(lift.core_of_song(synthesize(tune(BASE))))
    assert one.rowlen == 3
    assert one.gate_start(age=0, rows=2) == 4
    assert one.gate_start(age=2, rows=2) == 2
    assert one.gate_start(age=3, rows=2) == 0
    assert one.gate_start(age=0, rows=4) == 6


def walked(core, data, slot=1, at=0x0400, program=None):
    """The walk of program ``slot`` whose pointer leads to ``data`` at ``at``, the model's
    program there ``program``."""
    memory = bytearray(core.state.memory)
    memory[at : at + len(data)] = data
    memory[core.found["inner_lo"] + slot] = at & 255
    memory[core.found["inner_hi"] + slot] = at >> 8
    programs = list(core.song.inner_programs)
    programs[slot] = program
    song = dataclasses.replace(core.song, inner_programs=programs)
    state = dataclasses.replace(core.state, memory=bytes(memory))
    return lift.Lift(dataclasses.replace(core, song=song, state=state)).walk(slot)


@pytest.fixture(name="core")
def core_fixture(plain):
    return lift.core_of_song(plain)


def test_a_program_with_no_rows_is_the_bytes_its_pointer_leads_to(core):
    stop = walked(core, bytes([0xFE, 0x12, 0x34, 0xFE]))
    assert [(r.wave, r.note, r.filter) for r in stop.rows] == [(0xFE, 0x12, 0x34)]
    assert stop.loop is None
    restart = walked(core, bytes([0x00, 0x00, 0xFF, 0xFF]))
    assert [(r.wave, r.note, r.filter) for r in restart.rows] == [(0, 0, 0xFF)]
    assert restart.loop == 0
    onward = walked(core, bytes([1, 2, 3, 4, 5, 6, 7, 8, 9, 0xFE]))
    assert [r.wave for r in onward.rows] == [1, 4, 7]
    assert onward.loop is None


def test_a_program_that_never_ends_walks_the_cursor_round_the_byte(core):
    walk = walked(core, bytes([0xEA] * 256))
    assert len(walk.rows) == 256
    assert walk.loop == 0


def test_a_row_jump_may_land_past_the_rows(core):
    bound = varied(core, program_jump=core.found["instr_cursor"])
    rows = [ArpeggioRow(0x41, 0, 0), ArpeggioRow(0x42, 0, 0)]
    data = bytearray(32)
    data[0:3], data[3:6], data[6:8] = [0x41, 0, 0], [0x42, 0, 0], [0xFD, 5]
    data[15], data[18] = 0x77, 0xFE
    for jump, loop in ((5, None), (1, 1), (0, 0)):
        data[7] = jump
        walk = walked(bound, bytes(data), program=Arpeggio(rows, MA.PROGRAM_JUMP, jump))
        assert [r.wave for r in walk.rows][:2] == [0x41, 0x42]
        assert walk.loop == loop
    assert [r.wave for r in walked(bound, bytes(data[:7]) + b"\x05" + bytes(data[8:])).rows] == [
        0x41,
        0x42,
        0x77,
    ]


def test_a_program_the_model_carries_is_the_walk_without_reading_the_memory(core):
    program = Arpeggio([ArpeggioRow(0x41, 5, 0), ArpeggioRow(0x10, 0, 0)], MA.PROGRAM_STOP)
    walk = walked(core, bytes(300), program=program)
    assert [(r.wave, r.note) for r in walk.rows] == [(0x41, 5), (0x10, 0)]
    assert walk.loop is None


def spec_vibrato(word, start, toggle, calls):
    """The frequency words the spec's output writes on ``calls`` calls from the image."""
    out, written = start, []
    for _ in range(calls):
        toggle = toggle ^ 0xFF if word & 1 else 0
        if toggle == 0 or not word & 1:
            out = (out + word) & 0xFFFF
            written.append(out)
        else:
            written.append(None)
    return written


@pytest.mark.parametrize("lo,toggle", [(4, 0xFF), (5, 0xFF), (5, 0), (5, 7)])
def test_a_vibrato_running_from_the_image_steps_the_word_init_left(core, lo, toggle):
    cells = {"vib_lo_step": lo, "vib_hi_step": 1, "vib_out_lo": 0x10, "vib_out_hi": 0x20}
    cells["vib_toggle"] = toggle
    memory = bytearray(core.state.memory)
    for name, value in cells.items():
        memory[core.found[name] + 1] = value
    core = dataclasses.replace(core, state=dataclasses.replace(core.state, memory=bytes(memory)))
    expr = lift.Lift(core).running_vibrato(1)
    want = spec_vibrato(0x100 | lo, 0x2010, toggle, 12)
    if expr is None:
        assert not any(want)
        return
    held = -1
    for phi, written in enumerate(want):
        env = {"phi": phi, "v": held, "ite": lambda c, a, b: a if c else b}
        got = eval(str(expr), env)  # pylint: disable=eval-used
        assert got & 0xFFFF == (held & 0xFFFF if written is None else written)
