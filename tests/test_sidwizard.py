"""SID-Wizard lifting: tables, chords, orderlists, events and the playroutine's limits."""

from types import SimpleNamespace

import numpy as np
import pytest
from pysidwizard import End, Loop, MainVolume, PlayPattern, Row, Transpose
from pysidwizard.features import features_for_driver

from unitracker import backends, sidwizard, songio
from unitracker.sidwizard import NO_DETUNE as KEEP, LiftError
from unitracker.vm import SongPlayer

TICKS = 15000


def test_table_rows_stop_at_the_end_marker():
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


def test_a_wf_hold_row_lasts_its_hold(csillag):
    ir = backends.lift(csillag.read_bytes())
    wf, entry = ir["records"]["wftab"], ir["records"]["ins"][6]["ctrl"]
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


def test_a_wf_detune_is_written_as_the_rows_constant_freq(csillag):
    ir = backends.lift(csillag.read_bytes())
    wf, entry = ir["records"]["wftab"], ir["records"]["ins"][6]["ctrl"]
    plain = SongPlayer(ir).render_grid(TICKS)
    wf[entry]["d"], wf[wf[entry]["next"]]["d"] = 0xFB, KEEP
    grid = SongPlayer(ir).render_grid(TICKS)
    assert not np.array_equal(grid, plain)
    wf[wf[entry]["next"]]["d"] = 0xFB
    assert np.array_equal(SongPlayer(ir).render_grid(TICKS), grid)
    text = songio.dumps(ir)
    assert text.count(", freq: -5}") == 2
    assert np.array_equal(SongPlayer(songio.loads(text)).render_grid(TICKS), grid)


@pytest.mark.parametrize(
    "data,match",
    [(bytes([0x41, 0, 0, 0xFE, 0, 0]), "jumps"), (bytes([0x41, 0, 0, 0x11]), "partial")],
)
def test_table_rows_reject(data, match):
    with pytest.raises(LiftError, match=match):
        sidwizard.table_rows("wftab", data)


def testfilter_records_split_mode_and_resonance():
    assert sidwizard.filter_record(bytes([0xBB, 0x14, 0])) == {
        "a": 0xB0,
        "res": 0x0B,
        "b": 0x14,
        "k": 0,
    }
    assert sidwizard.filter_record(bytes([0x04, 0xE0, 0])) == {"a": 4, "b": 0xE0, "k": 0}


def test_chords_loop_return_and_open_end():
    assert sidwizard.chords(bytes([0, 3, 7, 0x7F, 0, 4, 0x7E, 0x7F, 5, 9])) == {
        1: ((0, 3, 7), True),
        2: ((0, 4), False),
        4: ((5, 9), True),
    }


def native(wf, arp_speed=1, chord=1):
    return SimpleNamespace(wf_table=bytes(wf), arp_speed=arp_speed, default_chord=chord)


def test_a_looping_chord_is_the_programs_tail_from_its_row():
    rows, loop = sidwizard.wf_rows(native([0x11, 0, 0, 0x50, 0x7F, 0, 0x41, 0, 0]), ((0, 3), True))
    assert loop == 1
    assert rows == [
        {"w": 0x11, "a": 0, "d": 0, "h": 1},
        {"w": 0x50, "a": 0, "d": 0, "h": 1},
        {"w": 0x50, "a": 3, "d": 0, "h": 1},
    ]


def test_a_returning_chord_runs_into_the_next_row_and_keeps_a_detune():
    wf = [0x11, 0, 5, 0x50, 0x7F, 0, 0x41, 0, 0xFF]
    rows, loop = sidwizard.wf_rows(native(wf, arp_speed=0x42), ((4, 7), False))
    assert loop is None
    assert [(r["w"], r["a"], r["d"], r["h"]) for r in rows] == [
        (0x11, 0, 5, 2),
        (0x50, 4, 5, 2),
        (0x50, 7, 5, 2),
        (0x41, 0, 5, 2),
    ]


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


@pytest.mark.parametrize(
    "control,rows",
    [
        (0x1A, [{"hold": 2, "gate": 0, "ad": 0x0F, "sr": 0x10}, {"ctrl": 9}]),
        (0x05, [{"hold": 1, "ctrl": 0x18, "gate": 0, "ad": 0x0F, "sr": 0x10}, {}]),
        (0x08, [{"ctrl": 9}]),
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


def test_a_restart_on_both_ticks_is_refused():
    with pytest.raises(LiftError, match="both ticks"):
        sidwizard.restart_rows(0x03, 0x0F, 0, 9)


def test_csillag_lifts_and_is_detected(csillag):
    raw = csillag.read_bytes()
    assert backends.detect(raw) == "sidwizard"
    ir = backends.lift(raw)
    assert ir["playroutine"]["name"] == "sidwizard"
    assert ir["playroutine"]["constants"] == {"tempol": 6, "tempor": 6}
    assert ir["hints"] == {"ticks_per_step": 6}
    player = backends.native_player(raw)
    assert list(player.render_grid(50)[-1]) == list(SongPlayer(ir).render_grid(50)[-1])


def test_a_reference_past_the_instruments_plays_the_emptyinstrument_record(csillag, monkeypatch):
    parse = sidwizard.parse_sid_song

    def patched(raw, song):
        swm = parse(raw, song)
        first = swm.sequences[0][0].pattern
        row = swm.patterns[first - 1].rows[0]
        row.instrument = len(swm.instruments) + 3
        return swm

    monkeypatch.setattr(sidwizard, "parse_sid_song", patched)
    ir = sidwizard.lift(csillag.read_bytes(), expressions=False)
    empty = ir["records"]["ins"][len(ir["records"]["ins"])]
    assert empty == {"flags": 0x10, "sr": 0xF0, "restart": empty["restart"]}
    path, entry = [], empty["restart"]
    while entry:
        row = ir["records"]["restart"][entry]
        path.append({k: v for k, v in row.items() if k != "next"})
        entry = row["next"]
    assert path == [{"hold": 2, "gate": 0, "ad": 0x0F, "sr": 0}, {"ctrl": 9}]
    SongPlayer(ir).render_grid(20)


def test_frequency_tables():
    bare = SimpleNamespace(freq_lo_image=b"", freq_hi_image=b"", freq_table_tail=b"")
    lo, hi = sidwizard.freq(bare, features_for_driver(0))
    assert len(lo) == len(hi) == 256


CSILLAG_FREQS, CSILLAG_EMPTY_STEP_COMMANDS = 91, 16


def test_a_vibrato_across_empty_steps_is_written_once(csillag):
    text = songio.dumps(sidwizard.lift(csillag.read_bytes()))
    played = [e for p in songio.loads(text)["phrases"].values() for e in p]
    cmds = [(e, c) for e in played for c in e.get("commands") or [e.get("command")] if c]
    assert text.count("!freq") <= CSILLAG_FREQS
    assert sum("note" not in e for e, _ in cmds) <= CSILLAG_EMPTY_STEP_COMMANDS
