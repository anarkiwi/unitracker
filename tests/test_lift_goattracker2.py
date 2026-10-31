"""The GoatTracker 2 lift plays the spec player's registers exactly."""

import numpy as np
import pytest
from pygoattracker import PlayerState
from pygoattracker import constants as GT

from conftest import CORPUS, REFUSED, gt_tune
from unitracker.lift import goattracker2 as g

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
    core = gt_tune(**CASES[case])
    try:
        cells, _ = g.compare(core, TICKS)
    except g.Refusal as refusal:
        pytest.skip(str(refusal))
    assert not cells.size, g.summary(cells, 3)


def test_a_refusal_names_its_class():
    core = gt_tune([(0x41, 0), (0xFF, 4), (0x41, 7), (0xFF, 3), (0xFF, 1)])
    with pytest.raises(g.Refusal) as caught:
        g.document_text(core)
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
    cells, text = g.verify(raw, 0, 4000)
    assert not cells.size
    assert text.startswith("unitracker:2.5\n") and "|loop:" in text
    assert g.lift(raw, 0, 400) == g.lift(raw, 0, 400)


@pytest.mark.parametrize("tune", list(CORPUS))
def test_corpus_tunes_lift_exactly(tune, request):
    raw = request.getfixturevalue(tune).read_bytes()
    cells, _ = g.verify(raw, 0, 3000)
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
