"""SID-Wizard lifting: tables, chords, orderlists, events and the playroutine's limits."""

from types import SimpleNamespace

import pytest
from pysidwizard import End, Loop, MainVolume, PlayPattern, Row, Transpose
from pysidwizard.features import features_for_driver

from unitracker import backends, sidwizard
from unitracker.sidwizard import LiftError
from unitracker.vm import SongPlayer


def test_table_rows_stop_at_the_end_marker():
    tables = sidwizard.Tables()
    entry = tables.program(
        "wftab", bytes([0x41, 0x00, 0x00, 0x21, 0x0C, 0x00, 0xFF]), sidwizard.wf_record
    )
    assert entry == 1
    assert tables.records["wftab"] == {
        1: {"w": 0x41, "a": 0, "d": 0, "next": 2},
        2: {"w": 0x21, "a": 0x0C, "d": 0, "next": 0},
    }
    assert tables.program("pwtab", b"", sidwizard.pw_record) == 0
    second = tables.program("wftab", bytes([0x11, 0x80, 0x00]), sidwizard.wf_record)
    assert second == 3


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
    records, starts = sidwizard.chords(bytes([0, 3, 7, 0x7F, 0, 4, 0x7E, 0x7F, 5, 9]))
    assert starts == {1: 1, 2: 4, 4: 6}
    assert records[3] == {"n": 7, "next": 1}
    assert records[5] == {"n": 4, "next": 0}
    assert records[7] == {"n": 9, "next": 6}


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
    return sidwizard.Events(lambda ref: ref, {1: 1, 2: 4})


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
        (Row(fx=0x72), {"command": [0x70, 4]}),
        (Row(instrument=0x65), {"command": [0x160, 5]}),
        (Row(note=50, instrument=0x72, fx=0x58), {"note": 50, "commands": [[0x170, 4], [0x50, 8]]}),
        (Row(fx=0x77), {"command": [0x70, 0]}),
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
        sidwizard.instrument_record(marked, sidwizard.Tables(), {})


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
    assert empty == {"flags": 0x1A, "hrad": 0x0F, "sr": 0xF0, "chord": 1}
    SongPlayer(ir).render_grid(20)


def test_frequency_tables():
    bare = SimpleNamespace(freq_lo_image=b"", freq_hi_image=b"", freq_table_tail=b"")
    lo, hi = sidwizard.freq(bare, features_for_driver(0))
    assert len(lo) == len(hi) == 256
