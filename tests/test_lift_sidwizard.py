"""The SID-Wizard lift plays the spec player's registers exactly, and refuses what it cannot
state."""

import numpy as np
import pytest
from pysidtracker import find_format
from pysidwizard import (
    End,
    Instrument,
    Loop,
    Pattern,
    PlayPattern,
    Row,
    SWMFile,
    Transpose,
    build_swm,
    sidmaker,
    straight_tempo,
)
from pysidwizard.constants import GATE_OFF_FX
from unitracker_grammar.document import read
from unitracker_player.player import play

from unitracker.lift import invariants, shared
from unitracker.lift import sidwizard as sw

FRAMES = 4000
DUMMY = Instrument(
    name=b"DUMMY",
    control=0x1A,
    hr_decay=15,
    attack=1,
    sustain=15,
    first_waveform=0x41,
    wf_table=bytes([0x41, 0, 0]),
)


def instrument(**fields) -> Instrument:
    """A sustained pulse instrument with ``fields`` overridden."""
    return Instrument(**{"name": b"TEST", **DUMMY.__dict__, **fields})


def note(pitch: int, ins: int | None = None, **fields) -> Row:
    """A pattern row playing ``pitch`` on instrument ``ins``."""
    return Row(note=pitch, instrument=ins, **fields)


def raw_of(instruments, rows, tempo=6, sequences=None, **fields) -> bytes:
    """The SID of a module playing ``rows`` on every voice (the first instrument is the empty
    one the player acts on at a first note)."""
    swm = SWMFile(
        sequences=sequences or [[PlayPattern(1), End()]] * 3,
        patterns=[Pattern(rows=rows, length=len(rows))],
        instruments=instruments,
        subtune_tempos=[(straight_tempo(tempo), straight_tempo(tempo))],
        **fields,
    )
    return sidmaker.export(build_swm(swm))


def exact(raw: bytes, frames: int = 200) -> str:
    """The document of ``raw``, played against the spec player for ``frames`` frames."""
    cells, text = sw.verify(raw, 0, frames)
    assert not cells.size, sw.summary(cells, 3)
    return text


def refused(raw: bytes) -> str:
    """The class of the refusal lifting ``raw``."""
    with pytest.raises(sw.Refusal) as caught:
        sw.document_of(sw.core_of_sid(raw))
    return caught.value.kind


@pytest.mark.parametrize("tune", ["must_dash", "gubbrock"])
def test_corpus_tunes_play_exactly_and_every_object_has_an_origin(tune, request):
    raw = request.getfixturevalue(tune).read_bytes()
    cells, text = sw.verify(raw, 0, FRAMES)
    assert not cells.size, sw.summary(cells, 3)
    assert text.startswith("unitracker:2.5\n") and ("|loop:" in text or "|end]" in text)
    again = sw.lift(raw, 0, 200)
    assert again == text and read(text).defs
    report = sw.report(raw)
    assert set(report["counts"]) == set(invariants.KINDS[:-1]) and report["song"] > 0


def test_two_voices_taking_over_the_filter_on_one_call_diverge(friendly_fire):
    raw = friendly_fire.read_bytes()
    cells, _ = sw.verify(raw, 0, 2600)
    assert cells.size and cells[0][0] == 2547 and cells[0][1] == 22
    with pytest.raises(shared.Divergence, match="2547"):
        sw.lift(raw, 0, 2600)


@pytest.mark.parametrize("tune", ["phonky", "club64", "sirius", "beltram"])
def test_a_tune_the_lift_cannot_state_is_refused_by_class(tune, request):
    from conftest import SIDWIZARD_REFUSED  # pylint: disable=import-outside-toplevel

    raw = request.getfixturevalue(tune).read_bytes()
    with pytest.raises(sw.Refusal) as caught:
        sw.core_of_sid(raw)
        sw.document_of(sw.core_of_sid(raw))
    assert caught.value.kind == SIDWIZARD_REFUSED[tune][1]


def test_a_plain_note_is_a_hard_restart_and_a_first_frame():
    text = exact(raw_of([DUMMY], [note(49, 1)] + [Row()] * 7))
    assert "restart:" in text and "wf.ctrl" in text and "|end]" in text


@pytest.mark.parametrize(
    "fields",
    [
        {"vibrato": 0x44, "vibrato_delay": 2},
        {"vibrato": 0x24, "control": 0x0A},
        {"vibrato": 0x34, "control": 0x3A},
        {"control": 0x19, "hr_attack": 3, "hr_sustain": 5},
        {"control": 0x1C},
        {"control": 0x13, "octave_shift": -12},
    ],
)
def test_instrument_fields_play_exactly(fields):
    rows = [note(49, 1), Row(), note(53, 2)] + [Row()] * 13
    exact(raw_of([DUMMY, instrument(**fields)], rows), 100)


@pytest.mark.parametrize(
    "tables",
    [
        {"pw_table": bytes([0x88, 0, 0, 4, 0x10, 0, 0xFE, 0x10 + 3 + 1 + 3, 0])},
        {"filter_table": bytes([0x94, 0x20, 0, 0x04, 0x10, 0, 0x94, 0x40, 0])},
        {"wf_table": bytes([0x41, 0, 0, 0x41, 4, 0, 0x41, 7, 0, 0xFE, 0x10, 0]), "arp_speed": 1},
        {"wf_table": bytes([0x41, 0, 0, 0x02, 3, 0, 0xFE, 0x13, 0])},
        {"wf_table": bytes([0x41, 0x81 + 48, 0, 0x41, 0x80, 0])},
        {"wf_table": bytes([0x41, 0xFC, 0]), "arp_speed": 2},
    ],
)
def test_tables_play_exactly(tables):
    rows = [note(49, 1), Row(), note(53, 2)] + [Row()] * 13
    exact(raw_of([DUMMY, instrument(**tables)], rows), 120)


def test_a_chord_row_plays_each_interval_in_turn():
    table = bytes([0x41, 0x7F, 0])
    rows = [note(49, 1), Row(), note(53, 2)] + [Row()] * 13
    raw = raw_of([DUMMY, instrument(wf_table=table)], rows, chord_table=bytes([4, 7, 0x7E]))
    assert "note:+4" in exact(raw, 100)


def test_gate_off_rows_and_their_table_positions():
    rows = [note(49, 1)] + [Row()] * 3 + [Row(note=GATE_OFF_FX)] + [Row()] * 3
    exact(raw_of([DUMMY], rows))
    pointer = instrument(wf_table=bytes([0x41, 0, 0, 0x40, 0, 0]), gateoff_wf=0x10 + 3)
    exact(
        raw_of(
            [DUMMY, pointer],
            [note(49, 1), Row(), note(53, 2)] + [Row()] * 4 + [Row(note=GATE_OFF_FX)] + [Row()] * 4,
        )
    )


def test_orderlist_transposes_and_loops():
    sequences = [[Transpose(3), PlayPattern(1), Transpose(-2), PlayPattern(1), Loop(0)]] * 3
    text = exact(raw_of([DUMMY], [note(49, 1)] + [Row()] * 7, sequences=sequences), 300)
    assert "+3" in text and "-2" in text and "|loop:" in text


@pytest.mark.parametrize(
    "rows",
    [
        [note(49, 1), Row(), Row(fx=1, fx_value=8), Row(), Row(), Row(fx=2, fx_value=20)],
        [note(49, 1), Row(), Row(fx=3, fx_value=9), note(53), Row(), Row()],
        [note(49, 1), Row(), Row(), Row(note=53, instrument=0x3F), Row(), Row()],
        [note(49, 1), Row(), Row(fx=8, fx_value=0x35), Row(), Row(note=0x64), Row(), Row()],
        [note(49, 1), Row(note=0x78), note(53), Row(), Row(), Row()],
        [note(49, 1), Row(note=53, fx=0x52), Row(fx=5, fx_value=0x33), Row(fx=6, fx_value=0x44)],
        [note(49, 1), Row(note=0x79), Row(note=0x7A), Row(note=0x7B), Row(note=0x7C), Row()],
        [note(49, 1), Row(fx=0x10, fx_value=8), Row(), note(53), Row(), Row(), Row(), Row()],
    ],
)
def test_pattern_effects_play_exactly(rows):
    exact(raw_of([DUMMY, instrument(vibrato=0x24)], [note(49, 1), *rows[1:]] + [Row()] * 6), 120)


@pytest.mark.parametrize(
    "fields, kind",
    [
        ({"wf_table": bytes([0x41, 0, 5])}, "detune"),
        ({"pw_table": bytes([4, 0x10, 0])}, "pulse sweep"),
        ({"pw_table": bytes([0x88, 0, 0x20])}, None),
        ({"filter_table": bytes([0x94, 0x20, 0x20])}, "filter keytrack"),
        ({"filter_table": bytes([0, 0, 0])}, "filter without control"),
        ({"control": 0x9A, "filter_table": bytes([0x94, 0x20, 0])}, None),
    ],
)
def test_a_table_the_lift_cannot_state_is_refused_by_class(fields, kind):
    rows = [note(49, 1), Row(), note(53, 2)] + [Row()] * 5
    raw = raw_of([DUMMY, instrument(**fields)], rows)
    if kind is None:
        exact(raw, 80)
    else:
        assert refused(raw) == kind


@pytest.mark.parametrize(
    "row, kind",
    [
        (Row(fx=0x0C, fx_value=3), "pattern effect"),
        (Row(fx=0x0D, fx_value=3), "pattern effect"),
        (Row(fx=0x42), "pattern effect"),
        (Row(note=49, instrument=2, fx=3, fx_value=4), "instrument change"),
        (Row(instrument=2), "instrument change"),
    ],
)
def test_a_pattern_the_lift_cannot_state_is_refused_by_class(row, kind):
    rows = [note(49, 1), Row(), row, Row(), Row()]
    assert refused(raw_of([DUMMY, instrument()], rows)) == kind


def test_a_note_before_any_instrument_is_refused():
    assert refused(raw_of([DUMMY], [Row(note=49), Row()])) == "note without instrument"


def test_the_voices_must_run_one_tempo():
    sequences = [[PlayPattern(1), End()]] * 3
    raw = raw_of([DUMMY], [note(49, 1), Row()] + [Row()] * 4, sequences=sequences)
    core = sw.core_of_sid(raw)
    assert sw.cadence([6, 6, 6]) == 6 and sw.cadence([3, 5, 3, 5]) == (3, 5)
    with pytest.raises(sw.Refusal, match="tempo program"):
        sw.cadence([3, 4, 5, 6])
    assert sw.row_lengths(core, 0, 4) == [6, 6, 6, 6]


def test_an_orderlist_runs_the_walk_the_player_does():
    raw = raw_of([DUMMY], [note(49, 1)] + [Row()] * 3)
    core = sw.core_of_sid(raw)
    assert sw.plays(core, 0) == ([(1, 0)], None)
    swm = core.swm
    swm.sequences[0] = [Transpose(2), PlayPattern(1), Transpose(-1), PlayPattern(1), Loop(0)]
    assert sw.plays(core, 0) == ([(1, 2), (1, -1)], 0)
    swm.sequences[0] = [PlayPattern(1), Transpose(1), Loop(0)]
    with pytest.raises(sw.Refusal, match="effect before a loop"):
        sw.plays(core, 0)
    swm.sequences[0] = [PlayPattern(9), End()]
    with pytest.raises(sw.Refusal, match="pattern 9"):
        sw.plays(core, 0)


def test_the_player_start_acts_on_a_voice_first_note():
    text = exact(raw_of([DUMMY, instrument()], [Row()] * 4 + [note(49, 2)] + [Row()] * 4), 120)
    assert "boot.freq" in text


def test_a_reference_grid_has_the_edges_of_its_gates():
    raw = raw_of([DUMMY], [note(49, 1)] + [Row()] * 7)
    grid, edges = sw.reference(raw, 0, 40, both=True)
    assert grid.shape == (40, 25) and edges.shape == (40, 3) and np.any(edges)
    assert find_format(raw).name == "sidwizard"


def test_the_document_is_what_the_cli_verifies(tmp_path):
    raw = raw_of([DUMMY], [note(49, 1)] + [Row()] * 7)
    path = tmp_path / "t.sid"
    path.write_bytes(raw)
    assert sw.main([str(path)]) == 0
    assert sw.main([str(path), "--verify", "--frames", "100"]) == 0
    cells = sw.verify(raw, 0, 60)[0]
    assert not cells.size and np.asarray(play(sw.lift(raw, 0, 60), 20)).shape == (20, 25)
