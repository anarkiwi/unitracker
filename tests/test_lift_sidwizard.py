"""SID-Wizard lifting: tables, chords, orderlists, events, the playroutine's limits, and
Csillag (its reference lift, and patched cuts of it) playing its native player."""

import dataclasses
import itertools
import re
from types import SimpleNamespace

import numpy as np
import pytest
from conftest import oracle, sw_fx, sw_table_byte
from pysidwizard import (
    End,
    Loop,
    MainVolume,
    PlayPattern,
    Row,
    SWMPlayer,
    Transpose,
    parse_sid_song,
)
from pysidwizard.features import features_for_driver
from pysidwizard.model import encode_sequence

from unitracker.format import songio
from unitracker.format.codec import Evaluator
from unitracker.lift import sidwizard
from unitracker.lift.sidwizard import NO_DETUNE as KEEP
from unitracker.lift.sidwizard import LiftError
from unitracker.player.vm import SongPlayer

TICKS = 2000


def test_table_rows_stop_at_the_end_marker_and_refuse_jumps():
    tables = sidwizard.Tables()
    entry = tables.program(
        "pwtab", bytes([0x41, 0x00, 0x00, 0x21, 0x0C, 0x00, 0xFF]), sidwizard.pw_record
    )
    assert entry == 1
    assert tables.records["pwtab"] == {
        1: {"a": 0x41, "b": 0, "k": 0, "next": 2},
        2: {"a": 0x21, "b": 0x0C, "k": 0, "next": 0},
    }
    assert tables.program("wftab", b"", sidwizard.pw_record) == 0
    assert tables.allocate("pwtab", [{"a": 1}, {"a": 2}], loop=1) == 3
    assert tables.records["pwtab"][4] == {"a": 2, "next": 4}
    for data, match in (
        (bytes([0x41, 0, 0, 0xFE, 0, 0]), "jumps"),
        (bytes([0x41, 0, 0, 0x11]), "partial"),
    ):
        with pytest.raises(LiftError, match=match):
            sidwizard.table_rows("wftab", data)


def test_a_wf_row_that_keeps_the_detune_is_given_the_one_it_keeps():
    rows = [
        {"w": 0x41, "a": 0, "d": KEEP, "h": 0},
        {"w": 0x41, "a": 0, "d": 5, "h": 0},
        {"w": 0x02, "a": 0x80, "d": KEEP, "h": 1},
        {"w": 0x41, "a": 0, "d": 0, "h": 1},
        {"w": 0x41, "a": 0, "d": KEEP, "h": 1},
    ]
    out = sidwizard.settled(rows, None)
    assert [(r["d"], r["h"]) for r in out] == [(0, 0), (5, 0), (5, 2), (0, 1), (0, 1)]
    assert sidwizard.settled(rows[1:2] + rows[4:], 1)[1]["d"] == 5
    with pytest.raises(LiftError, match="differs between loop passes"):
        sidwizard.settled([rows[0], rows[1]], 0)
    with pytest.raises(LiftError, match="hold row that ends its program"):
        sidwizard.settled(rows[:3], None)


def testfilter_records_split_mode_and_resonance():
    assert sidwizard.filter_record(bytes([0xBB, 0x14, 0])) == {
        "a": 0xB0,
        "res": 0x0B,
        "b": 0x14,
        "k": 0,
    }
    assert sidwizard.filter_record(bytes([0x04, 0xE0, 0])) == {"a": 4, "b": 0xE0, "k": 0}
    assert sidwizard.filter_record(bytes([0x04, 0xE0, 0x90]))["k"] == 0x90
    with pytest.raises(LiftError, match=r"routing byte \$83"):
        sidwizard.filter_record(bytes([0x04, 0xE0, 0x83]))


def test_chords_loop_return_and_open_end():
    assert sidwizard.chords(bytes([0, 3, 7, 0x7F, 0, 4, 0x7E, 0x7F, 5, 9])) == {
        1: ((0, 3, 7), True),
        2: ((0, 4), False),
        4: ((5, 9), True),
    }


def native(wf, arp_speed=1, chord=1):
    return SimpleNamespace(wf_table=bytes(wf), arp_speed=arp_speed, default_chord=chord)


@pytest.mark.parametrize(
    "wf,arp_speed,chord,loop,rows",
    [
        (
            [0x11, 0, 0, 0x50, 0x7F, 0, 0x41, 0, 0],
            1,
            ((0, 3), True),
            1,
            [(0x11, 0, 0, 1), (0x50, 0, 0, 1), (0x50, 3, 0, 1)],
        ),
        (
            [0x11, 0, 5, 0x50, 0x7F, 0, 0x41, 0, 0xFF],
            0x42,
            ((4, 7), False),
            None,
            [(0x11, 0, 5, 2), (0x50, 4, 5, 2), (0x50, 7, 5, 2), (0x41, 0, 5, 2)],
        ),
    ],
    ids=["looping chord is the program's tail", "returning chord keeps a detune"],
)
def test_a_chord_is_inlined_at_its_row(wf, arp_speed, chord, loop, rows):
    got, at = sidwizard.wf_rows(native(wf, arp_speed=arp_speed), chord)
    assert at == loop
    assert [(r["w"], r["a"], r["d"], r["h"]) for r in got] == rows


@pytest.mark.parametrize(
    "wf,chord,match",
    [
        ([0x50, 0x7F, 0], None, "does not have"),
        ([0x50, 0x7F, 0], ((0, 0x90), True), "offset"),
        ([0x05, 0x7F, 0], ((0, 3), False), "hold row"),
        ([0x50, 0x7F, 0, 0x02, 0, 0], ((0, 3), False), "hold row"),
    ],
)
def test_wf_rows_reject(wf, chord, match):
    with pytest.raises(LiftError, match=match):
        sidwizard.wf_rows(native(wf), chord)


def waves():
    instruments = [native([0x11, 0, 0, 0x50, 0x7F, 0]), native([0x41, 0, 0], chord=2)]
    return sidwizard.Waves(instruments, bytes([0, 3, 0x7F, 0, 4, 0x7F]), sidwizard.Tables())


def test_waves_share_a_program_per_instrument_and_chord():
    w = waves()
    first = w.entry(1, 1)
    assert w.entry(1, 9) == first
    assert w.entry(1, 2) != first
    assert w.entry(1, 2) == w.entry(1, 2)
    assert w.entry(2, 1) == w.entry(2, 2)
    assert w.entry(3, 1) == 0
    assert w.tables.records["wftab"][first + 2] == {"w": 0x50, "a": 3, "d": 0, "h": 1, "next": 2}


def chord_song(*rows, plays=1):
    tracks = [{"plays": [{"phrase": "a"}] * plays, "loop": 0}]
    return tracks, {"a": [dict(e) for e in rows]}


def test_chord_fx_runs_the_program_its_note_starts():
    tracks, phrases = chord_song(
        {"note": 30, "instrument": 1, "command": [0x70, 2]},
        {"note": 31, "commands": [[0x170, 2], [0x50, 8]]},
        {"note": 32, "instrument": 2, "command": [0x70, 1]},
    )
    w = waves()
    sidwizard.chord_runs(tracks, phrases, w)
    got = phrases["a"]
    assert got[0]["command"] == got[1]["commands"][0] == [0x70, w.entry(1, 2)]
    assert got[1]["commands"][1] == [0x50, 8]
    assert got[2]["command"] == [0x70, w.entry(2, 1)]


@pytest.mark.parametrize(
    "rows,match",
    [
        ([{"note": 30, "instrument": 1}, {"command": [0x70, 2]}], "starts no note"),
        ([{"note": 30, "instrument": 1, "commands": [[0x3F, 0], [0x70, 2]]}], "starts no note"),
        (
            [
                {"note": 30, "instrument": 1},
                {"command": [0x278, 0]},
                {"note": 3, "command": [0x170, 2]},
            ],
            "starts no note",
        ),
        (
            [{"note": 30, "instrument": 1}, {"note": 30, "command": [0x70, 2]}],
            "without an instrument",
        ),
        (
            [{"note": 31, "command": [0x170, 2]}, {"note": 30, "instrument": 2}],
            "different instruments",
        ),
    ],
)
def test_chord_runs_reject(rows, match):
    tracks, phrases = chord_song(*rows, plays=2)
    with pytest.raises(LiftError, match=match):
        sidwizard.chord_runs(tracks, phrases, waves())


def octave_records():
    return {1: {"sr": 240, "octave": -12}, 2: {"sr": 240}, 3: {"sr": 240, "octave": -12}}


def test_octave_shifts_move_into_the_notes_they_play():
    tracks, phrases = chord_song(
        {"note": 30, "instrument": 1},
        {"note": 31},
        {"instrument": 3, "note": "off"},
        {"note": 32, "instrument": 2},
        {"note": "off"},
    )
    records = octave_records()
    sidwizard.fold_octaves(tracks, phrases, records)
    assert [e.get("note") for e in phrases["a"]] == [18, 19, "off", 32, "off"]
    assert all("octave" not in r for r in records.values())


@pytest.mark.parametrize(
    "rows,match",
    [
        ([{"note": 30, "instrument": 1}, {"instrument": 2}], "octave change without a note"),
        ([{"note": 30}, {"note": 30, "instrument": 2}], "different octaves"),
        ([{"note": 5, "instrument": 1}], "shifted by -12 semitones sounds below C0"),
    ],
)
def test_fold_octaves_reject(rows, match):
    tracks, phrases = chord_song(*rows, plays=2)
    tracks[0]["plays"].insert(0, {"phrase": "b"})
    phrases["b"] = [{"note": 40, "instrument": 1}]
    with pytest.raises(LiftError, match=match):
        sidwizard.fold_octaves(tracks, phrases, octave_records())


def test_track_plays_loop_and_end():
    seq = [PlayPattern(2), PlayPattern(1), Loop(1)]
    assert sidwizard.track(seq, 2) == {"plays": [{"phrase": "p02"}, {"phrase": "p01"}], "loop": 1}
    assert sidwizard.track([PlayPattern(1), End()], 1) == {
        "plays": [{"phrase": "p01"}],
        "loop": None,
    }


@pytest.mark.parametrize(
    "seq,match",
    [
        ([PlayPattern(3)], "missing pattern"),
        ([PlayPattern(1), Loop(5)], "not a pattern"),
        ([Transpose(2), PlayPattern(1)], "Transpose"),
        ([MainVolume(4), PlayPattern(1)], "MainVolume"),
        ([End()], "no pattern"),
    ],
)
def test_track_rejects(seq, match):
    with pytest.raises(LiftError, match=match):
        sidwizard.track(seq, 2)


def events():
    return sidwizard.Events(lambda ref: ref)


@pytest.mark.parametrize(
    "row,event",
    [
        (Row(note=61, instrument=2), {"note": 61, "instrument": 2}),
        (Row(note=0x7E), {"note": "off"}),
        (Row(note=0x7D), {"note": "on"}),
        (Row(note=30, instrument=0x3F), {"note": 30, "command": [0x3F, 0]}),
        (Row(note=0x68), {"command": [0x260, 8]}),
        (Row(note=0x78), {"command": [0x278, 0]}),
        (Row(note=40, fx=0x03, fx_value=0x40), {"note": 40, "commands": [[63, 0], [3, 0x40]]}),
        (Row(fx=0x2D), {"command": [0x20, 0x0D]}),
        (Row(fx=0x72), {"command": [0x70, 2]}),
        (Row(instrument=0x65), {"command": [0x160, 5]}),
        (Row(note=50, instrument=0x72, fx=0x58), {"note": 50, "commands": [[0x170, 2], [0x50, 8]]}),
        (Row(fx=0x77), {"command": [0x70, 7]}),
        (Row(fx=0x10, fx_value=4), {"command": [0x12, 4]}),
        (Row(fx=0x10), {"command": [0x12, 0]}),
        (Row(fx=0x11, fx_value=0x57), {"command": [0x11, 0x57]}),
        (
            Row(note=40, instrument=1, fx=0x13, fx_value=0x88),
            {"note": 40, "instrument": 1, "command": [0x13, 0x88]},
        ),
        (Row(fx=0x14, fx_value=0x35), {"command": [0x14, 0x35]}),
    ],
)
def test_row_events(row, event):
    assert events().event(row) == event


@pytest.mark.parametrize(
    "row,match",
    [
        (Row(fx=0xA5), "small FX"),
        (Row(fx=0x09, fx_value=1), "big FX"),
        (Row(note=0x79), "note-column"),
        (Row(instrument=0x52, note=0x68, fx=0x54), "three effects"),
        (Row(fx=0x14, fx_value=0x25), r"tempo FX \$14 \$25 is not covered"),
        (Row(fx=0x10, fx_value=2), r"tempo FX \$10 \$02"),
        (Row(fx=0x13, fx_value=0x81), r"tempo FX \$13 \$81"),
        (Row(fx=0x11, fx_value=0x72), r"tempo FX \$11 \$72"),
    ],
)
def test_row_events_reject(row, match):
    with pytest.raises(LiftError, match=match):
        events().event(row)


def test_phrases_fold_emptytable_rows():
    pattern = SimpleNamespace(rows=[Row(note=10, instrument=1), Row(), Row(), Row(note=0x7E)])
    assert events().phrase(pattern) == [
        {"note": 10, "instrument": 1, "steps": 3},
        {"note": "off"},
    ]
    with pytest.raises(LiftError, match="empty"):
        events().phrase(SimpleNamespace(rows=[]))
    with pytest.raises(LiftError, match=r"^pattern \$1A row \$02: tempo FX \$10 \$01"):
        events().phrase(SimpleNamespace(rows=[Row(), Row(), Row(fx=0x10, fx_value=1)]), 0x1A)


@pytest.mark.parametrize(
    "pair,tempo",
    [((0x86, 0x83), 6), ((5, 7), [5, 7]), ((0, 6), 6), ((6, 0x86), 6), ((0x84, 9), [4, 9])],
)
def test_the_subtune_tempo_is_the_initial_tempo(pair, tempo):
    assert sidwizard.tempo(SimpleNamespace(subtune_tempos=[pair])) == tempo


def test_a_subtune_tempo_the_player_never_applies_a_row_at_is_not_covered():
    with pytest.raises(LiftError, match=r"subtune's tempo \$02 \$06"):
        sidwizard.tempo(SimpleNamespace(subtune_tempos=[(2, 6)]))


def test_first_note_must_select_aninstrument_record():
    tracks = [{"plays": [{"phrase": "a"}]}]
    sidwizard.first_note_selects(tracks, {"a": [{"note": "off"}, {"note": 3, "instrument": 1}]})
    with pytest.raises(LiftError, match="selects no instrument"):
        sidwizard.first_note_selects(tracks, {"a": [{"note": 3}]})


def test_build_and_header_checks():
    good = SimpleNamespace(frame_speed=1, sid_init=bytes(25), instruments=[object()])
    normal = SimpleNamespace(name="normal")
    sidwizard.check_build(good, normal)
    bad = SimpleNamespace(frame_speed=2, sid_init=bytes([1] + [0] * 24), instruments=[])
    with pytest.raises(LiftError, match="light player build, multispeed, init-routine"):
        sidwizard.check_build(bad, SimpleNamespace(name="light"))
    marked = SimpleNamespace(filter_table=bytes([0xFF]))
    with pytest.raises(LiftError, match="unfiltered"):
        sidwizard.instrument_record(marked, sidwizard.Tables(), 0, sidwizard.Restarts())
    with pytest.raises(LiftError, match="both ticks"):
        sidwizard.restart_rows(0x03, 0x0F, 0, 9)


@pytest.mark.parametrize(
    "control,rows",
    [
        (0x1A, [{"hold": 2, "gate": 0, "ad": 0x0F, "sr": 0x10}, {"ctrl": 9, "gate": 1}]),
        (0x05, [{"hold": 1, "ctrl": 0x18, "gate": 0, "ad": 0x0F, "sr": 0x10}, {"gate": 1}]),
        (0x08, [{"ctrl": 9, "gate": 1}]),
        (0x14, []),
    ],
)
def test_restart_rows_follow_the_control_byte(control, rows):
    got = sidwizard.restart_rows(control, 0x0F, 0x10, 9)
    assert [dict(r) for r in got] == rows
    restarts = sidwizard.Restarts()
    entry = restarts.program(got)
    assert restarts.program(got) == entry == (1 if rows else 0)
    assert len(restarts.records) == len(rows)


def test_the_reference_lift_holds_the_subtune_tempo(references):
    ir = references["csillag"].ir()
    assert ir["playroutine"]["name"] == "sidwizard"
    assert not ir["playroutine"].get("constants") and ir["tempo"] == 6


def voice_instrument(ir: dict, voice: int) -> int:
    """The instrument record ``voice``'s first event selects."""
    return ir["phrases"][ir["tracks"][voice]["plays"][0]["phrase"]][0]["instrument"]


def test_a_wf_hold_row_lasts_its_hold(references):
    ir = references["csillag"].ir()
    wf, entry = ir["records"]["wftab"], ir["records"]["ins"][voice_instrument(ir, 1)]["ctrl"]
    nxt, end = wf[entry]["next"], len(wf) + 1
    wf[entry].update(w=0x41, a=0x80, h=0)
    wf[nxt].update(w=2, a=0x80, h=2, next=end)
    wf[end] = {"w": 0x21, "a": 0x80, "d": 0, "h": 0, "next": 0}
    grid = SongPlayer(ir).render_grid(TICKS)
    wave = [int(x) for x in grid[:, 7 + 4] & 0xF0]
    start = wave.index(0x40)
    assert wave[start : start + 5] == [0x40] * 4 + [0x20]
    text = songio.dumps(ir)
    assert "ctrl: [{ctrl: 65}, {hold: 3}, {ctrl: 33}, end]" in text
    assert np.array_equal(SongPlayer(songio.loads(text)).render_grid(TICKS), grid)


def test_a_wf_detune_is_written_as_the_rows_constant_freq(references):
    ir = references["csillag"].ir()
    wf, entry = ir["records"]["wftab"], ir["records"]["ins"][voice_instrument(ir, 1)]["ctrl"]
    plain = SongPlayer(ir).render_grid(TICKS)
    wf[entry]["d"], wf[wf[entry]["next"]]["d"] = 0xFB, KEEP
    grid = SongPlayer(ir).render_grid(TICKS)
    assert not np.array_equal(grid, plain)
    wf[wf[entry]["next"]]["d"] = 0xFB
    assert np.array_equal(SongPlayer(ir).render_grid(TICKS), grid)
    text = songio.dumps(ir)
    assert text.count(", freq: -5}") == 2
    assert np.array_equal(SongPlayer(songio.loads(text)).render_grid(TICKS), grid)


CSILLAG_FREQS, CSILLAG_EMPTY_STEP_COMMANDS = 91, 16


def test_a_vibrato_across_empty_steps_is_written_once(references):
    ref = references["csillag"]
    played = [e for p in ref.ir()["phrases"].values() for e in p]
    cmds = [(e, c) for e in played for c in e.get("commands") or [e.get("command")] if c]
    assert ref.text.count("!freq") <= CSILLAG_FREQS
    assert sum("note" not in e for e, _ in cmds) <= CSILLAG_EMPTY_STEP_COMMANDS


# Csillag's last eight patterns on each voice, which play every instrument.
WINDOW = slice(28, 36)
WINDOW_TICKS = 2 * 8 * 64 * 6 + 300


def window(raw: bytes) -> bytes:
    """SID-Wizard tune ``raw`` with each orderlist cut to its :data:`WINDOW` plays, looping to
    the first."""
    out = bytearray(raw)
    for seq in parse_sid_song(raw, 1).sequences:
        new = encode_sequence([*seq[WINDOW], Loop(0)])
        pos = raw.index(encode_sequence(seq))
        out[pos : pos + len(new)] = new
    return bytes(out)


def swm_grid(swm) -> np.ndarray:
    return np.asarray(SWMPlayer(swm).render_grid(WINDOW_TICKS), dtype=np.uint8)


def lifted(raw: bytes) -> tuple[dict, str]:
    """The lift of ``raw`` and its canonical text, asserting it round-trips."""
    text = songio.dumps(sidwizard.lift(raw))
    song = songio.loads(text)
    assert songio.dumps(song) == text
    return song, text


def kept(swm):
    """``swm`` with its instruments' control bits keeping the pulse or filter program."""
    swm.instruments = [
        dataclasses.replace(ins, control=ins.control | (0x40 if k % 2 else 0x80))
        for k, ins in enumerate(swm.instruments)
    ]
    return swm


# Tempo FX that keep the voices in step: every voice's tempo and swing, restored to the
# subtune's, and each voice's own tempo and equal swing while it is the subtune's.
STEP_TEMPOS = {
    (20, 16): (0x10, 4),
    (20, 32): (0x10, 0),
    (32, 40): (0x11, 0x57),
    (32, 56): (0x10, 0x86),
    (33, 0): (0x13, 6),
    (21, 0): (0x14, 0x66),
}


def test_kept_programs_tempo_fx_and_a_tracked_pulse_play_the_player(csillag, monkeypatch):
    base = window(csillag.read_bytes())
    tracked = sw_table_byte(base, 7, "pw", 2, 0x05)
    swm = kept(sw_fx(tracked, STEP_TEMPOS))
    monkeypatch.setattr(sidwizard, "parse_sid_song", lambda *_: swm)
    song, text = lifted(tracked)
    assert "!pw" in text and "!filter" in text and "flags" not in text
    assert "!tempo:4" in text and "!tempo:5/7" in text and "!vtempo:6" in text
    assert "E[" in text and not re.search(r"\bk:", text)
    plain = swm_grid(parse_sid_song(base, 1))
    for one in (
        kept(parse_sid_song(base, 1)),
        sw_fx(base, STEP_TEMPOS),
        parse_sid_song(tracked, 1),
    ):
        assert not np.array_equal(swm_grid(one), plain)
    assert SongPlayer(song).mismatch(swm_grid(swm)) is None


# A keyboard-track amount on the row that sets an instrument's pulse (7) or filter (4, 8), up
# and down, adds a term of the note to the register until the next row's amount (0) replaces
# it; pysidwizard's player does not model it for the filter, so the 6502 oracle plays these.
TRACKED = {(7, "pw"): 0xF4, (4, "filter"): 0x05, (8, "filter"): 0xF4}


def test_keyboard_tracked_pulse_and_filter_rows_play_the_player(csillag):
    base = raw = window(csillag.read_bytes())
    plain = oracle(base, WINDOW_TICKS)
    for (instrument, which), amount in TRACKED.items():
        one = sw_table_byte(base, instrument, which, 2, amount)
        cols = 0x16 if which == "filter" else slice(None)
        assert not np.array_equal(oracle(one, WINDOW_TICKS)[:, cols], plain[:, cols])
        raw = sw_table_byte(raw, instrument, which, 2, amount)
    song, text = lifted(raw)
    assert "E[" in text and not re.search(r"\bk:", text)
    assert SongPlayer(song).mismatch(oracle(raw, WINDOW_TICKS)) is None


# Tempo FX on rows no voice plays in step: one voice's tempo and swing, every voice's tempo
# (0: 6) and swing, on rows that start a note and rows that do not. Pattern 1 plays on voice 1
# mid-window, where its first row also selects an instrument past the last.
TEMPO_ROWS = {
    (2, 0): (0x13, 8),
    (8, 4): (0x14, 0x55),
    (8, 12): (0x14, 0x35),
    (20, 16): (0x10, 4),
    (35, 8): (0x11, 0x57),
    (3, 8): (0x10, 0),
    (34, 2): (0x11, 0x3F),
}


def test_out_of_step_tempo_fx_and_the_empty_instrument_play_the_player(csillag, monkeypatch):
    raw = window(csillag.read_bytes())
    swm = sw_fx(raw, TEMPO_ROWS)
    swm.patterns[0].rows[0].instrument = len(swm.instruments) + 3
    theirs = swm_grid(swm)
    assert not np.array_equal(theirs, swm_grid(sw_fx(raw, {})))
    monkeypatch.setattr(sidwizard, "parse_sid_song", lambda *_: swm)
    ir = sidwizard.lift(raw, expressions=False)
    empty = ir["records"]["ins"][len(ir["records"]["ins"])]
    assert empty == {"flags": 0x10, "sr": 0xF0, "restart": empty["restart"]}
    path, entry = [], empty["restart"]
    while entry:
        row = ir["records"]["restart"][entry]
        path.append({k: v for k, v in row.items() if k != "next"})
        entry = row["next"]
    assert path == [{"hold": 2, "gate": 0, "ad": 0x0F, "sr": 0}, {"ctrl": 9, "gate": 1}]
    assert SongPlayer(ir).mismatch(theirs) is None


def test_frequency_tables():
    bare = SimpleNamespace(freq_lo_image=b"", freq_hi_image=b"", freq_table_tail=b"")
    lo, hi = sidwizard.freq(bare, features_for_driver(0))
    assert len(lo) == len(hi) == 256


@pytest.mark.parametrize("name", ["pulse", "filter"])
def test_a_keyboard_tracked_chip_map_inverts_its_store(name):
    pr = songio.library("sidwizard")
    chip = next(u for u in pr["modulators"]["units"] if u["name"] == name)["chip"]
    ev = Evaluator(pr["functions"], pr["tables"])
    width = {c: int(pr["voice"][c]["width"]) for c in chip["store"]}
    for amount, note in itertools.product((0, 1, 5, 0x7F, 0xF4, 0xFF), range(0, 96, 5)):
        env = {"pwkt": amount, "fkt": amount, "note": note, "oct": 0}
        for v in range(0, 1 << int(chip["width"]), 7):
            cells = {
                c: ev(e, {**env, "value": v}) % (1 << width[c]) for c, e in chip["store"].items()
            }
            assert ev(chip["value"], {**env, **cells}) == v
