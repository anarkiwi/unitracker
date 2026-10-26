"""The direct GoatTracker 2 transcription plays the reference player's registers."""

import pytest
from pygoattracker import constants as GT
from pygoattracker.packed import PlayerState
from unitracker_grammar import code, parse
from unitracker_grammar.document import VERSION, Event

from conftest import gt_tune
from unitracker.direct import goattracker2 as g
from unitracker.direct.traps import Trap

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


def test_a_read_past_the_note_table_is_trapped_to_f():
    """The editor's table has B7's $FFFF where F(95) is 0: the reference answers F, logging it."""
    tune = gt_tune([(0x41, 0xDF), (0xFF, 0)])[0]
    grid, found = g.trapped(tune, 40)
    assert found == (Trap(8, 0, 95, 0xFFFF, 0),)
    assert not grid[8:, :2].any()
    assert not g.trapped(gt_tune([(0x41, 0x80 + 30), (0xFF, 0)])[0], 40)[1]


def test_loop_and_runs():
    assert g.repeat([1, 2, 3, 2, 3, 2], [5, 6, 7, 6, 7, 6]) == (
        1,
        2,
    )
    assert g.repeat([1, 2, 3], [1, 2, 3]) is None
    events = [Event(n) for n in ("C2", "-", "-", "-", "off", "-")]
    assert g.rests(events) == [Event("C2"), Event("-", steps=3), Event("off"), Event("-")]


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
    exact(gt_tune(**CASES[case]))


@pytest.mark.parametrize(
    "case, state",
    [
        ("a plain note", "{freq:{t:1,m:1}}"),
        ("a pulse table", "{freq:{t:1,m:1},pw:{r:1,q:1}}"),
    ],
)
def test_state_and_code_of_the_templates_used(case, state):
    """The state line declares the variables the used routines name; each ``mod:`` record is
    a code block, its routine's one text."""
    text = exact(gt_tune(**CASES[case]))
    assert f"\norder:[{','.join(g.PHASES)}]\nstate:{state}\ntables:" in text
    templates = code.templates(parse(text))
    assert templates and all(code.printed(t) == g.CODE[name] for name, t in templates.items())
    assert f"\nclock:{{cycles_per_tick:19656,clock_hz:{g.CLOCK['clock_hz']}}}\n" in text
    tables = text[text.index("\ntables:\n") : text.index("\nmod:\n ")]
    assert " G:" not in tables and "wave.freq:note()" in text


@pytest.mark.parametrize(
    "case, value",
    [
        ("an arpeggio down", "{note:-2},{note:-64}"),
        ("a filter table", "{hold:4,filter.cut:v - 32}"),
        ("vibrato", " tick.freq:vib(0,3,48)"),
        ("porta down, then command 0 holds", " tick.freq:porta(-1,48)"),
    ],
)
def test_values_as_played(case, value):
    """Relative notes and steps are signed, and bindings are calls."""
    assert value in exact(gt_tune(**CASES[case]))


def test_holds_follow_the_build():
    text = exact(gt_tune(WAVE))
    assert (
        "slots:{freq:{wave:{reset:[row.note,row.freq]},tick:{hold:[step-1,row]}},"
        "pw:{pulse:{hold:[read,note-1,last-1]}}}\n" in text
    )


V2 = [(31, 1, 0, 0)] + [REST] * 15


@pytest.mark.parametrize(
    "build, voices",
    [
        ({"voices": (0, 7)}, 2),
        ({"voices": (7, 0, 14)}, None),
        ({"idle": (2,)}, 2),
        ({"voices": (0, None, 7), "idle": (1,)}, 2),
        ({"voices": (0,)}, 1),
    ],
)
def test_voice_layouts(build, voices):
    """A build's played channels are the voices they write, in voice order."""
    text = exact(gt_tune(WAVE, voice2=V2, **build))
    assert ("\nvoices:" in text) == (voices is not None)
    if voices:
        assert f"\nvoices:{voices}\n" in text


@pytest.mark.parametrize(
    "build, match",
    [
        ({"calls": 2}, "multispeed: 2"),
        ({"overlay": ((0xFE, 0), None, None)}, "masked by an overlay"),
        ({"voices": (0, 7, 0x20)}, "more than one SID"),
        ({"voices": (0, 7, None)}, "writes no SID voice"),
        ({"voices": (0, 14)}, "not the first voices"),
    ],
)
def test_unsupported_layouts_are_refused(build, match):
    """Multispeed and control-masked builds, and channels on a second SID, on no voice or past a
    gap, are refused."""
    with pytest.raises(g.TranscribeError, match=match):
        g.Transcriber(gt_tune(WAVE, voice2=V2, **build)[0]).document()


def test_unsupported_wavetable_commands_are_refused():
    with pytest.raises(g.TranscribeError, match="wavetable command 0"):
        g.Transcriber(gt_tune([(0xF0, 0x01), (0xFF, 0x00)])[0]).document()


def test_wavetable_chip_commands_apply_a_tick_late():
    """A wavetable ``$FD`` row's volume moves to a row of no hold after it, where the next play
    call's filter routine writes it, and the loop keeps its row."""
    text = g.Transcriber(
        gt_tune([(0x11, 0x80), (0xFD, 0x05), (0x21, 0x80), (0xFF, 0x02)])[0]
    ).document()
    assert "c1:[{hold:0,gate:0},{ctrl:17},{},{hold:0,vol:5},{ctrl:33},{loop:2}]" in text


def test_metal_warrior(metal_warrior):
    cells, text, _ = g.verify(metal_warrior.read_bytes(), frames=2 * 3840)
    assert not cells.size, g.summary(cells, 3)
    assert "{loop:" in text


def test_cli(metal_warrior, capsys):
    assert g.main([str(metal_warrior)]) == 0
    assert capsys.readouterr().out.startswith(f"unitracker:{VERSION}\n")
    assert g.main(["--verify", "--frames", "400", str(metal_warrior)]) == 0
    assert ": exact" in capsys.readouterr().out


def test_native_table_differs_from_the_standard_only_in_pitch():
    model = gt_tune(WAVE, voice2=V2)[0]
    assert not g.compare(model, TICKS)[0].size
    cells, _ = g.compare(model, TICKS, native=True)
    assert set(cells[:, 1].tolist()) <= {0, 1, 7, 8, 14, 15}


def test_init_routine_writes_are_the_header_registers():
    """The SID writes of the tune's init routine are the registers before tick 0."""
    text = exact(gt_tune(WAVE, voice2=V2, init_regs=((0, 0x34), (1, 0x12), (23, 0x01))))
    assert "\nregs:[" in text.replace("regs: [", "regs:[") or "regs:" in text


def test_start_state_registers_are_the_header_registers():
    """The registers a build's init leaves, as its first tick writes them from."""
    regs = tuple([0x34, 0x12, 0, 0, 0, 0, 0xF0] + [0] * 17 + [0x0F])
    state = PlayerState(regs, cutoff=0x40, filttype=0x10, volume=0x0F, funk=(8, 5), ghost=False)
    assert "regs:" in exact(gt_tune(WAVE, voice2=V2, state=state))


@pytest.mark.parametrize("ghost", [False, True])
def test_the_start_vibrato_phase_last_note_and_delay(ghost):
    """A channel's vibrato phase, last note and vibrato delay as the image leaves them are the
    freq cell's start state and its intro vibrato's delay."""
    regs = tuple([0x34, 0x12, 0, 0, 0, 0, 0xF0] + [0] * 17 + [0x0F])
    channels = (("vibtime", (254, 3, 0)), ("lastnote", (40, 0, 0)), ("vibdelay", (3, 1, 1)))
    state = PlayerState(regs, 0, 0, 0x0F, (8, 5), ghost=ghost, channels=channels)
    ins = [{"wave_ptr": 1, "gateoff_timer": 2, "vibrato_param": 1, "vibrato_delay": 1}]
    rows = [REST] * 2 + [(None, 0, 4, 2)] + [REST] * 13
    text = exact(gt_tune(WAVE, ins, rows, speed=[(3, 48), (0x82, 1)], voice2=V2, state=state))
    first = "freq:seed(254,40)!" if ghost else "freq:start(52,18,254,40)!"
    assert f"{first} tick.freq:vib(2,3,48),-.i1 freq:seed(3,0)! tick.freq:vib(0,3,48)," in text


def test_the_editor_start_state_has_no_header_registers():
    assert "regs:" not in exact(gt_tune(WAVE, voice2=V2))


def test_a_start_pulse_width_is_set_on_the_first_tick():
    """The start pulse width is the pw cell's on the first tick, which skips the pulse table."""
    regs = tuple([0x34, 0x12, 0x80, 0x07, 0, 0x0B, 0xAB] + [0] * 15 + [0x10, 0, 0x1F])
    init = ((4, 0), (11, 0), (18, 0), (21, 0))
    state = PlayerState(regs, 0x10, 0x10, 0x0F, (8, 5), ghost=False, init=init)
    assert "set.pw:1920!" in exact(gt_tune(WAVE, voice2=V2, state=state))


GHOST = tuple([0x34, 0x12, 0x80, 0x07, 0, 0, 0] * 3 + [0, 0, 0, 0])


def test_a_ghosting_build_lags_a_tick_more_from_its_ghost():
    """Each call outputs the ghost the previous one left, the first the image's."""
    state = PlayerState(GHOST, 0, 0, 0x0F, (8, 5), ghost=True)
    text = exact(gt_tune(WAVE, voice2=V2, state=state))
    assert "\nlag:2\n" in text and f"\nregs:[{','.join(map(str, GHOST))}]\n" in text


def test_a_ghost_image_the_init_call_changes_is_refused():
    regs = (0, 0, 0, 0, 0x41) + GHOST[5:]
    state = PlayerState(regs, 0, 0, 0x0F, (8, 5), ghost=True)
    with pytest.raises(g.TranscribeError, match="ghost image the init call changes"):
        g.Transcriber(gt_tune(WAVE, voice2=V2, state=state)[0]).document()


def test_funk_lengths_are_those_the_row_plays_with():
    """Command F 0 reads the funk lengths another voice's command E set earlier in the song."""
    rows = [(24, 1, 0, 0)] + [REST] * 3 + [(None, 0, 0xF, 0)] + [REST] * 11
    voice2 = [(31, 1, 0xE, 1)] + [REST] * 15
    assert "tempo:5/3" in exact(gt_tune(WAVE, rows=rows, voice2=voice2, speed=[(5, 3)]))


def test_one_filter_program_for_the_song():
    """A filter table another voice starts replaces the running one; a cutoff command sets the
    cutoff it steps from, and another voice's filter control 0 stops it, keeping the cutoff."""
    instruments = [
        {"wave_ptr": 1, "filter_ptr": 1, "gateoff_timer": 2},
        {"wave_ptr": 1, "filter_ptr": 4, "gateoff_timer": 2},
    ]
    filt = [(0x90, 0xF1), (1, 2), (0xFF, 2), (0xA0, 0x12), (2, 0xFF), (0xFF, 5)]
    rows = [(24, 1, 0, 0)] + [REST] * 7 + [(None, 0, 12, 0x20)] + [REST] * 7
    voice2 = [REST] * 4 + [(31, 2, 0, 0)] + [REST] * 3 + [(None, 0, 11, 0)] + [REST] * 7
    text = exact(gt_tune(WAVE, instruments, rows, filt=filt, voice2=voice2))
    assert "cut:{set:{},filter:{}}" in text and "cut:hold" not in text


@pytest.mark.parametrize("opts", [{"pulse_step": 3}, {"simplepulse": True, "pulse_step": 2}])
def test_a_pulse_step_it_cannot_scale_is_refused(opts):
    tune = gt_tune(**CASES["a pulse table"], **opts)
    with pytest.raises(g.TranscribeError, match="pulse step"):
        g.Transcriber(tune[0]).document()
