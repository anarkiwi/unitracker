"""The direct GoatTracker 2 transcription plays the reference player's registers."""

import pytest
from pygoattracker import constants as GT
from unitracker_grammar import code, parse

from conftest import gt_tune
from unitracker.direct import goattracker2 as g

TICKS = 600
REST = (None, 0, 0, 0)


def exact(tune, ticks: int = TICKS) -> str:
    """The tune's document, asserting its grid is the reference player's."""
    cells, text = g.compare(tune[0], ticks)
    parse(text)
    assert not cells.size, g.summary(cells, 3) + "\n" + text
    return text


def test_notes():
    assert [g.note_name(k) for k in (0, 24, 37, 95)] == ["C0", "C2", "C#3", "B7"]


def test_loop_and_runs():
    assert g.repeat([1, 2, 3, 2, 3, 2], [5, 6, 7, 6, 7, 6]) == (
        1,
        2,
    )
    assert g.repeat([1, 2, 3], [1, 2, 3]) is None
    assert g.rests(["C2", "-", "-", "-", "off", "-"]) == [
        "C2",
        "-*3",
        "off",
        "-",
    ]


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
}


@pytest.mark.parametrize("case", CASES)
def test_synthetic_tunes_play_exactly(case):
    exact(gt_tune(**CASES[case]))


@pytest.mark.parametrize(
    "case, state",
    [
        ("a plain note", "{freq: {t: 1, m: 1}}"),
        ("a pulse table", "{freq: {t: 1, m: 1}, pw: {r: 1, q: 1}}"),
    ],
)
def test_state_and_code_of_the_templates_used(case, state):
    """The state line declares the variables the used routines name; each ``mod:`` record is
    a code block, its routine's one text."""
    text = exact(gt_tune(**CASES[case]))
    assert f"\norder: {g.ORDER}\nstate: {state}\ntables:" in text
    templates = code.templates(parse(text))
    assert templates and all(code.printed(t) == g.CODE[name] for name, t in templates.items())
    assert f"\nclock: {{cycles_per_tick: 19656, clock_hz: {g.CLOCK['clock_hz']}}}\n" in text
    tables = text[text.index("\ntables:\n") : text.index("\nmod:\n  ")]
    assert "  G:" not in tables and "wave.freq: note()" in text


@pytest.mark.parametrize(
    "case, value",
    [
        ("an arpeggio down", "{note: -2}, {note: -64}"),
        ("a filter table", "{hold: 4, cut: v - 32}"),
        ("vibrato", "!tick.freq:vib(0,3,48)"),
        ("porta down, then command 0 holds", "!tick.freq:porta(-1,48)"),
    ],
)
def test_values_as_played(case, value):
    """Relative notes and steps are signed, and bindings are calls."""
    assert value in exact(gt_tune(**CASES[case]))


def test_holds_follow_the_build():
    text = exact(gt_tune(WAVE))
    assert (
        "slots: {freq: {wave: {reset: [row.note, row.freq]}, tick: {hold: [step-1, row]}}, "
        "pw: {pulse: {hold: [read, note-1, last-1]}}}\n" in text
    )


def test_unsupported_wavetable_commands_are_refused():
    with pytest.raises(g.TranscribeError, match="wavetable command 4"):
        g.Transcriber(gt_tune([(0xF4, 0x01), (0xFF, 0x00)])[0]).document()


def test_metal_warrior(metal_warrior):
    cells, text = g.verify(metal_warrior.read_bytes(), frames=2 * 3840)
    assert not cells.size, g.summary(cells, 3)
    assert "{loop: " in text


def test_cli(metal_warrior, capsys):
    assert g.main([str(metal_warrior)]) == 0
    assert capsys.readouterr().out.startswith(f"unitracker: {g.VERSION}\n")
    assert g.main(["--verify", "--frames", "400", str(metal_warrior)]) == 0
    assert ": exact" in capsys.readouterr().out
