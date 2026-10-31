"""The GoatTracker 2 lift plays the spec player's registers exactly."""

# pylint: disable=protected-access,too-few-public-methods

import re

import numpy as np
import pytest
from pygoattracker import PlayerState
from pygoattracker import constants as GT

from conftest import CORPUS, REFUSED, gt_tune
from unitracker.lift import goattracker2 as g
from unitracker.lift import invariants

TICKS = 600
REST = (None, 0, 0, 0)


WAVE = [(0x41, 0x00), (0xFF, 0x00)]
CASES = {
    "a plain note": {"wave": WAVE},
    "a looping arpeggio with a delay": {"wave": [(65, 0), (2, 12), (0, 7), (255, 2)]},
    "an arpeggio down": {"wave": [(65, 0), (0, 126), (0, 0x40), (255, 1)]},
    "an absolute wave note and silent wave": {"wave": [(65, 0), (233, 164), (33, 128), (255, 0)]},
    "porta up": {"wave": WAVE, "rows": [(24, 1, 1, 1)] * 4 + [REST] * 12, "speed": [(0, 32)]},
    "porta down, then command 0 holds": {
        "wave": WAVE,
        "rows": [(36, 1, 2, 1), (None, 0, 2, 1), (None, 0, 0, 0)] + [REST] * 13,
        "speed": [(0, 48)],
    },
    "calculated porta speed": {
        "wave": WAVE,
        "rows": [(24, 1, 1, 1)] + [(None, 0, 1, 1)] * 5 + [REST] * 10,
        "speed": [(128, 2)],
    },
    "toneporta": {
        "wave": WAVE,
        "rows": [(24, 1, 0, 0), REST, (31, 0, 3, 1)] + [REST] * 13,
        "speed": [(0, 64)],
    },
    "calculated toneporta": {
        "wave": WAVE,
        "rows": [(24, 1, 0, 0), REST, (31, 0, 3, 1)] + [REST] * 13,
        "speed": [(128, 1)],
    },
    "toneporta at once": {
        "wave": WAVE,
        "rows": [(24, 1, 0, 0), REST, (31, 0, 3, 0)] + [REST] * 13,
    },
    "wavetable portamento": {
        "wave": [(0x41, 0), (0xF1, 1), (0xF3, 2), (0xF2, 1), (0xFF, 2)],
        "speed": [(1, 16), (0, 2)],
    },
    "calculated vibrato": {
        "wave": WAVE,
        "rows": [(24, 1, 4, 1)] + [REST] * 15,
        "speed": [(131, 2)],
    },
    "vibrato": {"wave": WAVE, "rows": [(24, 1, 4, 1)] + [REST] * 15, "speed": [(3, 48)]},
    "instrument vibrato after toneporta and command 0, unoptimised": {
        "wave": WAVE,
        "instruments": [
            {"wave_ptr": 1, "gateoff_timer": 2, "vibrato_param": 1, "vibrato_delay": 8}
        ],
        "rows": [(24, 1, 15, 4), REST, (27, 0, 3, 0), REST, REST, (30, 0, 0, 0)] + [REST] * 10,
        "speed": [(1, 16)],
        "optimize_realtime": False,
        "optimize_pulse": False,
    },
    "instrument vibrato, portamento, then command 0 from phase 0": {
        "wave": WAVE,
        "instruments": [
            {"wave_ptr": 1, "gateoff_timer": 2, "vibrato_param": 1, "vibrato_delay": 2}
        ],
        "rows": [(24, 1, 0, 0), REST, REST, (None, 0, 1, 2), (None, 0, 1, 2)] + [REST] * 11,
        "speed": [(3, 40), (0, 20)],
    },
    "instrument vibrato": {
        "wave": WAVE,
        "instruments": [
            {"wave_ptr": 1, "gateoff_timer": 2, "vibrato_param": 1, "vibrato_delay": 4}
        ],
        "speed": [(2, 32)],
    },
    "a pulse table": {
        "wave": WAVE,
        "instruments": [{"wave_ptr": 1, "pulse_ptr": 1, "gateoff_timer": 2}],
        "pulse": [(136, 0), (32, 16), (16, 240), (255, 2)],
        "rows": [(24, 1, 0, 0), REST, REST, (26, 0, 0, 0)] + [REST] * 12,
    },
    "a pulse jump onto a jump row sets its bytes and goes on from the next row": {
        "wave": WAVE,
        "instruments": [{"wave_ptr": 1, "pulse_ptr": 1, "gateoff_timer": 2}],
        "pulse": [(0x88, 0), (0xFF, 3), (0x89, 0x20), (0xFF, 2)],
        "rows": [(24, 1, 0, 0), REST, REST, (26, 0, 0, 0)] + [REST] * 12,
    },
    "a pulse pointer set onto a jump row runs it as a set": {
        "wave": WAVE,
        "instruments": [{"wave_ptr": 1, "pulse_ptr": 2, "gateoff_timer": 2}],
        "pulse": [(0x88, 0), (0xFF, 3), (0x89, 0x20), (0xFF, 1)],
        "rows": [(24, 1, 0, 0), REST, REST, (26, 0, 0, 0)] + [REST] * 12,
    },
    "a pulse step of 2 doubles the modulation": {
        "wave": WAVE,
        "instruments": [{"wave_ptr": 1, "pulse_ptr": 1, "gateoff_timer": 2}],
        "pulse": [(136, 0), (32, 16), (16, 240), (255, 2)],
        "rows": [(24, 1, 0, 0), REST, REST, (26, 0, 0, 0)] + [REST] * 12,
        "pulse_step": 2,
    },
    "a pulse step of 4 quadruples the modulation": {
        "wave": WAVE,
        "instruments": [{"wave_ptr": 1, "pulse_ptr": 1, "gateoff_timer": 2}],
        "pulse": [(136, 0), (32, 16), (16, 240), (255, 2)],
        "pulse_step": 4,
    },
    "command 9 restarts the running pulse table": {
        "wave": WAVE,
        "instruments": [{"wave_ptr": 1, "pulse_ptr": 1, "gateoff_timer": 2}],
        "pulse": [(136, 0), (32, 16), (16, 240), (255, 2)],
        "rows": [(24, 1, 0, 0), REST, REST, (None, 0, 9, 1)] + [REST] * 12,
    },
    "command 9 on a note row outranks the instrument's pulse table": {
        "wave": WAVE,
        "instruments": [{"wave_ptr": 1, "pulse_ptr": 1, "gateoff_timer": 2}],
        "pulse": [(136, 0), (32, 16), (16, 240), (255, 2), (130, 0), (32, 8), (255, 5)],
        "rows": [(24, 1, 9, 5)] + [REST] * 6 + [(26, 1, 9, 0)] + [REST] * 8,
    },
    "a filter table": {
        "wave": WAVE,
        "instruments": [{"wave_ptr": 1, "filter_ptr": 1, "gateoff_timer": 2}],
        "filt": [(144, 241), (0, 64), (8, 4), (4, 252), (255, 3)],
    },
    "one-shot commands": {
        "wave": WAVE,
        "rows": [
            (24, 1, 5, 34),
            (None, 0, 6, 169),
            (None, 0, 7, 33),
            (None, 0, 13, 9),
            (None, 0, 11, 241),
            (None, 0, 12, 48),
            (None, 0, 11, 0),
        ]
        + [REST] * 9,
    },
    "pointer commands": {
        "wave": WAVE + [(33, 12), (255, 0)],
        "pulse": [(132, 0), (16, 32), (255, 0)],
        "filt": [(160, 242), (0, 128), (255, 0)],
        "rows": [
            (24, 1, 8, 3),
            (None, 0, 9, 1),
            (None, 0, 10, 1),
            (None, 0, 9, 0),
            (None, 0, 10, 0),
            (None, 0, 8, 0),
        ]
        + [REST] * 10,
    },
    "wavetable vibrato": {
        "wave": [(0x41, 0), (0xF4, 1), (0x01, 0x80), (0xF4, 2), (0, 12), (0xFF, 2)],
        "rows": [(24, 1, 0, 0)] + [REST] * 3 + [(28, 1, 0, 0)] + [REST] * 11,
        "speed": [(3, 48), (0x82, 1)],
    },
    "cutoff commands into a running filter table": {
        "wave": WAVE,
        "instruments": [{"wave_ptr": 1, "filter_ptr": 1, "gateoff_timer": 2}],
        "filt": [(0x90, 0xF1), (0, 0x10), (1, 1), (0xFF, 3)],
        "rows": [(24, 1, 0, 0), REST, (None, 0, 12, 0x30), (None, 0, 12, 0x08)] + [REST] * 12,
    },
    "a wavetable cutoff and filter table, from the next tick": {
        "wave": [(0x41, 0), (0xFC, 0x40), (0x21, 0), (0xFA, 1), (0x41, 0), (0xFF, 0)],
        "rows": [(24, 1, 0, 0)] + [REST] * 3 + [(28, 1, 0, 0)] + [REST] * 11,
        "filt": [(0x90, 0xF1), (0, 0x10), (4, 4), (0xFF, 3)],
    },
    "funk tempo": {
        "wave": WAVE,
        "rows": [(24, 1, 0xE, 1), REST, REST, (26, 0, 0xF, 1)] + [REST] * 12,
        "speed": [(0x05, 0x03)],
    },
    "funk tempo from a calculated speed row": {
        "wave": WAVE,
        "rows": [(24, 1, 0xE, 1)] + [REST] * 15,
        "speed": [(131, 6)],
    },
    "a funk byte of one spends a reload": {
        "wave": WAVE,
        "rows": [(24, 1, 0xE, 1)] + [REST] * 15,
        "speed": [(1, 6)],
    },
    "a restart whose fetch falls on the previous note's tick": {
        "wave": WAVE,
        "instruments": [{"wave_ptr": 1, "gateoff_timer": 3, "sustain_release": 0xF0}],
        "rows": [(24, 1, 0, 0), (26, 0, 0, 0), (28, 0, 0, 0)] + [REST] * 13,
        "default_tempo": 3,
    },
    "a tempo recall reads the table another voice loaded in its first row": {
        "wave": WAVE,
        "rows": [(24, 1, 0xE, 1)] + [REST] * 15,
        "voice2": [(None, 0, 0, 0)] * 3 + [(26, 1, 0xF, 0)] + [REST] * 12,
        "speed": [(5, 3)],
    },
    "a tempo command": {"wave": WAVE, "rows": [(24, 1, 15, 4)] + [REST] * 15},
    "key off and on with an instrument": {
        "wave": WAVE,
        "rows": [
            (24, 1, 0, 0),
            (GT.KEYOFF - GT.FIRSTNOTE, 0, 0, 0),
            (GT.KEYON - GT.FIRSTNOTE, 1, 0, 0),
        ]
        + [REST] * 13,
    },
    "a wavetable that plays no note at once keeps the frequency": {
        "wave": WAVE + [(0x21, 0x80), (0x21, 0x0C), (0xFF, 0)],
        "instruments": [
            {"wave_ptr": 1, "gateoff_timer": 2, "sustain_release": 0xF0},
            {"wave_ptr": 3, "gateoff_timer": 2, "sustain_release": 0xF0},
            {"gateoff_timer": 2, "sustain_release": 0xF0},
        ],
        "rows": [(24, 1, 0, 0), REST, (31, 2, 0, 0), REST, (36, 3, 0, 0)] + [REST] * 11,
    },
    "a filter table jump onto itself plays its row once": {
        "wave": WAVE,
        "instruments": [{"wave_ptr": 1, "filter_ptr": 1, "gateoff_timer": 2}],
        "filt": [(0x90, 0xF1), (0, 0x10), (1, 1), (0xFF, 4)],
    },
    "a wavetable jump to a jump row plays it": {
        "wave": [(0x41, 0), (0xFF, 4), (0x41, 7), (0xFF, 3), (0xFF, 1)],
    },
    "command 0 latches the row's instrument's vibrato": {
        "wave": WAVE,
        "instruments": [
            {"wave_ptr": 1, "gateoff_timer": 2, "vibrato_param": 1, "vibrato_delay": 1},
            {"wave_ptr": 1, "gateoff_timer": 2},
        ],
        "rows": [(24, 1, 0, 0), REST, (None, 2, 0xF, 6), REST, (None, 0, 0, 0)] + [REST] * 11,
        "speed": [(3, 48)],
    },
    "command 8 after a wavetable step plays its notes": {
        "wave": [(0x41, 0), (0xF1, 1), (0xFF, 2), (0x21, 7), (0xFF, 0)],
        "rows": [(24, 1, 0, 0), REST, (None, 0, 8, 4)] + [REST] * 13,
        "speed": [(0, 32)],
    },
    "a live-vibrato build's vibrato reads the instrument fetched": {
        "wave": WAVE,
        "instruments": [
            {"wave_ptr": 1, "gateoff_timer": 2, "vibrato_param": 1, "vibrato_delay": 1},
            {"wave_ptr": 1, "gateoff_timer": 3, "vibrato_param": 2, "vibrato_delay": 2},
        ],
        "rows": [(24, 1, 0, 0), (26, 2, 0, 0), (28, 0, 0, 0), (29, 1, 0, 0), (31, 2, 0, 0)]
        + [REST] * 11,
        "speed": [(3, 48), (2, 16)],
        "live_vibrato": True,
    },
}


@pytest.mark.parametrize("case", CASES)
def test_synthetic_tunes_play_exactly(case):
    try:
        cells, _ = g.compare(gt_tune(**CASES[case]), TICKS)
    except g.Refusal as refusal:
        pytest.skip(str(refusal))
    assert not cells.size, g.summary(cells, 3)


@pytest.mark.parametrize(
    "ins, tables, reason",
    [
        ({"filter_ptr": 1}, {"filt": [(0x90, 0xF1), (0, 0x10), (0xFF, 4), (0xFF, 3)]}, "filter"),
        ({}, {"wave": [(0x41, 0), (0xFF, 4), (0x41, 7), (0xFF, 3), (0xFF, 1)]}, "tempo command"),
        ({"pulse_ptr": 2}, {"pulse": [(0x88, 0), (0xFF, 2)]}, "last row"),
    ],
)
def test_a_landing_the_typed_row_cannot_state_is_refused_with_its_reason(ins, tables, reason):
    args = {"wave": WAVE} | tables
    args["instruments"] = [{"wave_ptr": 1, "gateoff_timer": 2} | ins]
    with pytest.raises(g.Refusal, match=reason) as caught:
        g.document_text(gt_tune(**args))
    assert caught.value.kind == "pointer onto a jump row"


def test_a_jump_onto_a_jump_row_nothing_reaches_is_lifted():
    core = gt_tune([(0x41, 0), (0xFF, 0), (0xFF, 4), (0xFF, 3), (0xFF, 1)])
    cells, _ = g.compare(core, TICKS)
    assert not cells.size


def test_each_voice_starts_from_the_channel_variables_the_song_init_left():
    regs = [188, 2, 244, 8, 0, 15, 0] + [0] * 15 + [115, 0, 63]
    state = PlayerState(tuple(regs), 115, 48, 15, (8, 5), False, ((4, 0), (11, 0), (18, 0)))
    cells, text = g.compare(gt_tune(WAVE, state=state), TICKS)
    assert not cells.size
    assert "boot" in text and "lag:1" in text


def test_a_divergence_names_its_first_cell():
    with pytest.raises(g.Divergence, match="frame 3 register 5"):
        g.checked((np.array([[3, 5, 0, 1]]), ""), 10)


def test_metal_warrior(metal_warrior):
    raw = metal_warrior.read_bytes()
    cells, text = g.verify(raw)
    assert not cells.size
    assert text.startswith("unitracker:2.5\n") and "|loop:" in text
    assert g.lift(raw, 0, 400) == g.lift(raw, 0, 400)


@pytest.mark.parametrize("tune", list(CORPUS))
def test_corpus_tunes_lift_exactly(tune, request):
    raw = request.getfixturevalue(tune).read_bytes()
    cells, _ = g.verify(raw)
    assert not cells.size, g.summary(cells, 3)


@pytest.mark.parametrize("tune", list(REFUSED))
def test_corpus_tunes_are_refused_by_class(tune, request):
    raw = request.getfixturevalue(tune).read_bytes()
    with pytest.raises(g.Refusal) as caught:
        g.document_text(g.core_of_sid(raw))
    assert caught.value.kind == REFUSED[tune][1]


def test_cli(metal_warrior, capsys):
    assert g.main(["--verify", "--frames", "300", str(metal_warrior)]) == 0
    assert ": exact" in capsys.readouterr().out


def test_a_recall_with_no_table_loaded_in_a_first_row_is_refused():
    core = gt_tune(
        WAVE,
        rows=[(None, 0, 0, 0)] * 2 + [(24, 1, 0xE, 1)] + [REST] * 13,
        voice2=[(24, 1, 0xF, 0)] + [REST] * 15,
        speed=[(5, 3)],
    )
    with pytest.raises(g.Refusal) as caught:
        g.document_text(core)
    assert caught.value.kind == "funk tempo recall"


def test_a_funk_table_whose_rows_do_not_alternate_from_the_start_is_refused():
    with pytest.raises(g.Refusal, match="alternate"):
        g.Lift.cadence((131, 6), 1)


def test_a_funk_row_that_never_ends_is_a_final_row_lasting_the_check():
    core = gt_tune(WAVE, rows=[(24, 1, 0xE, 1)] + [REST] * 15, speed=[(0, 0)])
    cells, text = g.compare(core, g.CHECK)
    assert not cells.size
    assert max(int(n) for n in re.findall(r"tempo:(\d+)", text)) >= g.CHECK


@pytest.mark.parametrize(
    "pair, start, ends",
    [((0, 0), 0, False), ((1, 6), 0, True), ((130, 3), 0, True), ((255, 255), 0, False)],
)
def test_a_funk_row_never_ends_exactly_when_its_counter_state_comes_round(pair, start, ends):
    assert (g.Lift.cadence(pair, start) is not None) == ends


@pytest.mark.parametrize(
    "pair, lengths",
    [((8, 5), (8, 5)), ((131, 6), (7, 7)), ((1, 6), (7, 7))],
)
def test_a_funk_table_gives_the_row_lengths_the_counter_reload_spends(pair, lengths):
    assert g.Lift.cadence(pair, 0) == lengths


class Ins:
    """An instrument as ``_first_wave`` and the gate helpers read it."""

    def __init__(self, first_wave, legato=False):
        self.first, self.legato = first_wave, legato

    def raw(self):
        """The packed fields."""
        return type("Raw", (), {"first_wave": self.first})


HR = {"fixedparams": False, "zeroskip": True, "nofirstwavecmd": False, "hrinstr": True}
HR |= {"nohrinstr": False, "legato": True, "firstwave": 0}


@pytest.mark.parametrize(
    "first, legato, gate, after",
    [
        (0, True, 0, 0),
        (0, True, 1, 1),
        (0, False, 1, 0),
        (9, True, 0, 1),
        (0xFE, True, 1, 0),
        (0xFF, False, 0, 1),
    ],
)
def test_the_gate_after_a_note_by_its_first_waveform_and_fetch(first, legato, gate, after):
    assert g._gate_after(HR, Ins(first, legato), gate) == after


def test_only_a_note_with_no_first_waveform_and_no_gate_fetch_keeps_the_gate():
    assert g._keeps_gate(HR, Ins(0, True))
    assert not g._keeps_gate(HR, Ins(0, False))
    assert not g._keeps_gate(HR, Ins(9, True))


def test_every_corpus_tune_holds_one_object_per_origin(request):
    for tune in CORPUS:
        raw = request.getfixturevalue(tune).read_bytes()
        text, objects = g.checked_text(g.core_of_sid(raw))
        assert invariants.provenance(text, g.lifted(g.core_of_sid(raw))[1]) == objects
        assert all(a >= b for a, b in objects.values())


def test_a_check_runs_until_every_voice_has_looped_once_at_the_slowest_tempo():
    rows = [(24, 1, 0xF, 15)] + [REST] * 15
    core = gt_tune(WAVE, rows=rows, default_tempo=5)
    slowest = 16 * 15
    assert g.check_frames(core, 10) == slowest
    assert g.check_frames(core, 4000) == 4000


def test_a_check_counts_the_frames_of_a_routine_calling_the_player_twice():
    assert g.check_frames(gt_tune(WAVE, default_tempo=5, calls=1), 10) == 16 * 6
    assert g.check_frames(gt_tune(WAVE, default_tempo=5, calls=2), 10) == 16 * 6 // 2


def test_a_vibrato_state_the_song_start_leaves_is_loaded_before_the_first_effect():
    regs = [0] * 25
    channels = (("vibdelay", (7, 7, 7)), ("vibtime", (3, 3, 3)))
    state = PlayerState(tuple(regs), 0, 0, 15, (8, 5), False, ((4, 0), (11, 0), (18, 0)), channels)
    args = {
        "wave": WAVE,
        "instruments": [
            {"wave_ptr": 1, "gateoff_timer": 2, "vibrato_param": 1, "vibrato_delay": 1}
        ],
        "speed": [(3, 48)],
        "state": state,
    }
    cells, text = g.compare(gt_tune(**args), TICKS)
    assert not cells.size
    assert "sta d" in text and "sta t" in text
