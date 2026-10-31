"""The defMON lift plays the spec player's registers exactly, and refuses what it cannot state."""

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from pydefmon import Tune
from pydefmon.defmon import DefmonError
from unitracker_grammar import frequency
from unitracker_grammar.document import Call, Expr, Once, Signed
from unitracker_grammar.expr import compile_expr

from unitracker.lift import defmon as d

TICKS = 3000
FLAGS = dict.fromkeys("uncounted plain_tr or_ctrl floored shifts fixed reduced".split(), False)
STANDARD = {
    **{n: (0, 0, 0) for n in "pw_lo pw_hi freq_lo freq_hi sr ad wgh wgl slide_lo slide_hi".split()},
    **{n: (0, 0, 0) for n in "mode depth note note_base".split()},
    "detune": (0, 1, 2),
    "vbit": (1, 2, 4),
    "vmask": (254, 253, 251),
    **dict.fromkeys("res fv cut_lo cut_hi cut_step_lo cut_step_hi extra owner count".split(), 0),
    "op_lo": 0x69,
    "op_hi": 0x69,
    "floor": 2,
    "shift": 0x0A,
    "trigger": 0x80,
    "cursor": 0,
}


def core(text: str, flags=None, **init) -> d.Core:
    """A core of the song ``text`` (the ``defmon.lark`` grammar) over the standard init state."""
    tune = Tune.parse(text)
    return d.Core(
        tune,
        FLAGS | (flags or {}),
        STANDARD | init,
        tuple(range(128)),
        d.PAL_CLOCK_HZ,
        ("title", "author", "released"),
        calls=(1,),
    )


SONG = """
jumps counted
start $00
sid 1
  $00: $01 $02 $03
  $01: jump $00 x2
  $02: $04 $05 $06
  $03: jump $00
pattern $01
  0: a $01
  4: n $30 end 2
pattern $02
  2: b $02 n $31 end 1
pattern $03
  1: n $32
  4: end 3
pattern $04
  3: end 0
pattern $05
  3: end 5
pattern $06
  3: end 5
row $01 AD $11 SR $f0 hold 2 -> $02
row $02 WGh $41 TR $03 stop
"""


@pytest.mark.parametrize(
    "tune",
    ["glow_worm", "tetrapapak", "higher_scores", "ponky", "fading_lights", "automatas", "syrapaog"],
)
def test_corpus_tunes_play_exactly(tune, request):
    raw = request.getfixturevalue(tune).read_bytes()
    cells, text = d.verify(raw, 0, TICKS)
    assert not cells.size, d.summary(cells, 3)
    assert text.startswith("unitracker:2.5\n") and "lag:1" in text and "|loop:" in text


def test_a_schedule_of_sub_tick_calls_is_a_calls_line(fading_lights, higher_scores, automatas):
    texts = [d.document_text(d.core_of_sid(t.read_bytes())) for t in (fading_lights, higher_scores)]
    assert "calls:{frame:1,full:[1,0]}" in texts[0] and "calls:" not in texts[1]
    assert d.core_of_sid(automatas.read_bytes()).calls == (1, 0, 0, 0, 0, 0, 0, 0)


def test_the_build_flags_choose_the_document(ponky, glow_worm, higher_scores):
    cores = [d.core_of_sid(t.read_bytes()) for t in (ponky, glow_worm, higher_scores)]
    assert [c.flags["or_ctrl"] for c in cores] == [True, False, False]
    assert [c.flags["plain_tr"] for c in cores] == [True, False, True]
    assert [c.tune.counted for c in cores] == [False, True, False]
    assert "l.ctrl:" in d.document_text(cores[0]) and "|" in d.document_text(cores[0])


def test_subtunes_are_read_by_their_number(glow_worm):
    raw = glow_worm.read_bytes()
    assert d.lift(raw, 0, 200) == d.lift(raw, 0, 200)
    assert d.reference(raw, 0, 50).shape == (50, 25)


@pytest.mark.parametrize(
    "tune, kind",
    [
        ("timewarp", "multi-SID"),
        ("rent_a_cop", "pattern past 32 steps"),
        ("in_memory_of", "pattern past $7f"),
        ("techno_aha", "no ending pattern"),
        ("triangle", "multispeed"),
        ("metal_warrior", "format"),
    ],
)
def test_a_refusal_names_its_class(tune, kind, request):
    raw = request.getfixturevalue(tune).read_bytes()
    with pytest.raises(d.Refusal) as caught:
        d.document_text(d.core_of_sid(raw))
    assert caught.value.kind == kind


def test_a_tune_off_the_player_clock_source_is_a_digi_tune(glow_worm, monkeypatch):
    monkeypatch.setattr(d, "cadence_info", lambda *_: {"trigger": "nmi_timer"})
    with pytest.raises(d.Refusal, match="digi") as caught:
        d.core_of_sid(glow_worm.read_bytes())
    assert caught.value.kind == "digi"


def test_a_jump_onto_a_jump_row_is_named():
    assert d.cause(DefmonError("sidTAB row $01 is a JP onto JP row $02")).kind == "jp onto jp"
    assert d.cause(DefmonError("something else")).kind == "song"


def test_a_spec_with_native_blocks_is_refused(glow_worm, monkeypatch):
    monkeypatch.setattr(d, "spec", lambda: SimpleNamespace(source="native $1000()"))
    with pytest.raises(d.Refusal) as caught:
        d.document_text(d.core_of_sid(glow_worm.read_bytes()))
    assert caught.value.kind == "native"


@pytest.mark.parametrize(
    "change",
    [
        {"trigger": 0},
        {"vbit": (1, 2, 8)},
        {"depth": (1, 0, 0)},
        {"cut_lo": 1},
        {"op_lo": 0x6D, "op_hi": 0x6D},
        {"extra": 3},
    ],
)
def test_an_init_that_leaves_other_state_is_refused(change):
    with pytest.raises(d.Refusal) as caught:
        d.Lift(core(SONG, **change))
    assert caught.value.kind == "init"


def test_a_note_past_the_wrap_of_the_absolute_build_is_refused():
    text = SONG.replace("n $30", "n $90")
    with pytest.raises(d.Refusal) as caught:
        d.Lift(core(text))
    assert caught.value.kind == "note"
    d.Lift(core(text, {"plain_tr": True}))


def test_a_sub_tick_first_call_is_refused():
    sub = replace(core(SONG), calls=(0, 1))
    with pytest.raises(d.Refusal, match="sub-tick"):
        d.Lift(sub)


def test_the_walk_counts_jumps_by_their_owner():
    steps, loop = d.Lift(core(SONG)).walk()
    one, two = (1, 2, 3), (4, 5, 6)
    assert [s[0] for s in steps] == [one, one, one, two, one]
    assert loop == 1


def test_the_walk_takes_a_jump_row_always_when_uncounted():
    steps, loop = d.Lift(core(SONG.replace("jumps counted", "jumps uncounted"))).walk()
    assert [s[0] for s in steps] == [(1, 2, 3), (1, 2, 3)] and loop == 1


def test_the_walk_starts_from_the_owner_and_count_init_leaves():
    steps, _ = d.Lift(core(SONG, owner=1, count=1)).walk()
    assert [s[0] for s in steps][:3] == [(1, 2, 3), (4, 5, 6), (1, 2, 3)]


def test_the_walk_refuses_a_row_outside_the_song():
    with pytest.raises(d.Refusal, match="arranger"):
        d.Lift(core(SONG, cursor=9)).walk()


def test_a_step_ends_at_the_first_alt_step_and_the_last_voice_sets_the_delay():
    lift = d.Lift(core(SONG))
    assert lift.length((1, 2, 3)) == (2, 1)
    assert lift.length((4, 5, 6)) == (3, 5)
    assert lift.length((1, 1, 1)) == (4, 2)


def test_events_fall_a_delay_and_a_call_into_the_phrase():
    lift = d.Lift(core(SONG))
    name = lift.phrase(2, 1, 3, 3)
    late = dict(lift.names.section("phrase"))[name]
    assert [(e.note, e.steps, dict(e.commands)) for e in late] == [
        ("-", 6, {}),
        ("C#2", None, {"b.run": "c1"}),
        ("-", None, {}),
    ]
    name = lift.phrase(1, 0, 0, 3)
    early = dict(lift.names.section("phrase"))[name]
    assert [(e.note, e.steps, dict(e.commands)) for e in early] == [
        ("-", None, {}),
        ("-", None, {"run": "c2"}),
        ("-", 3, {}),
    ]


def test_sidtab_columns_become_row_keys():
    tune = Tune.parse(SONG)
    lift = d.Lift(core(SONG))
    assert lift.keys(tune.rows[1], "a", 0) == {"ad": 0x11, "sr": 0xF0, "hold": 2}
    assert lift.keys(tune.rows[2], "a", 0) == {"ctrl": 0x41, "note": Signed(3)}
    text = SONG.replace("TR $03", "TR $83")
    absolute = d.Lift(core(text)).keys(Tune.parse(text).rows[2], "b", 1)
    assert absolute["note"] == "D#-2"


def test_resonance_and_routing_apply_in_order_through_the_voice_slots():
    out = {}
    d.Lift.filt(0, out, "a", 2)
    d.Lift.filt(0xF8, out, "b", 0)
    d.Lift.filt(0xF1, out, "b", 1)
    assert out["r3a.route"] == Once(Expr("11 & v"))
    assert out["r1b.route"] == Once(Expr("9 | v"))
    assert out["res"] == 15 and out["r2b.route"] == Once(Expr("(0 & v) + 1"))


def spec_pitch(note: int, mode: int, detune: int) -> tuple:
    """The frequency and carry the pitch step of defmon.play gives note index ``note``."""
    word = frequency(note - 24, d.PAL_CLOCK_HZ)
    if mode == 0:
        return word & 0xFF00 | (word + detune) & 0xFF, (word & 255) + detune > 255
    y = (mode + note) & 255
    total = word + ((frequency(y - 48, d.PAL_CLOCK_HZ) - frequency(y - 49, d.PAL_CLOCK_HZ)) & 65535)
    return total & 65535, total > 65535


@pytest.mark.parametrize("flags, span", [({"plain_tr": True}, 256), ({}, 128)])
@pytest.mark.parametrize("mode", [0, 1, 7, 127])
@pytest.mark.parametrize("voice", [0, 2])
def test_pitch_expressions_are_the_pitch_step(flags, span, mode, voice):
    lift = d.Lift(core(SONG, flags))
    space = {"_F": lambda k: frequency(k, d.PAL_CLOCK_HZ), "_T": {}}
    freq = compile_expr(lift.pitch(voice, mode).text, space, zero=True)
    carry = compile_expr(lift.carry(voice, mode).text, space, zero=True)
    for note in range(span):
        want, cy = spec_pitch(note, mode, STANDARD["detune"][voice])
        assert (freq(0, 0, note - 24), carry(0, 0, note - 24)) == (want, int(cy)), note


def test_slide_modes_are_templates_over_the_rate_table():
    lift = d.Lift(core(SONG, {"plain_tr": True}))
    rate = lift.core.rates
    assert lift.pitch(0, 0x81) == Call("up", (rate[2],))
    assert lift.pitch(0, 0xC1) == Call("dn", (rate[2],))
    assert lift.carry(0, 0xC1) == Call("cdn", (rate[2],))
    assert d.Lift(core(SONG)).pitch(0, 0xFF) == Call("dn7", (rate[126],))


def test_the_acid_column_sets_the_accumulator_or_a_step():
    lift = d.Lift(core(SONG))
    out = {}
    lift.acid_keys(0x0408, out, "a", 0)
    lift.acid_keys(0xC102, out, "b", 1)
    lift.acid_keys(0x8203, out, "a", 2)
    assert out["k1a.cut"] == Once(Call("acc", (8, 4)))
    assert out["k2b.cut"] == Once(Call("sub", (2, 1)))
    assert out["k3a.cut"] == Once(Call("add", (3, 2)))


def test_the_first_cutoff_step_clears_or_borrows_through_the_accumulator():
    assert d.first_cutoff(False, 2, True) == (0, 0, 4)
    assert d.first_cutoff(True, 2, False) == (255, 2, 2)


def test_a_chain_follows_next_to_a_stop_or_a_loop():
    rows = Tune.parse(SONG + "row $03 hold 1 -> $01\n").rows
    assert d.chain(rows, 1) == ([1, 2], None)
    assert d.chain(rows, 3) == ([3, 1, 2], None)
    again = Tune.parse(SONG.replace("stop", "hold 1 -> $01")).rows
    assert d.chain(again, 1) == ([1, 2], 0)


def test_the_header_carries_the_clock_and_the_title():
    head = d.Lift(replace(core(SONG), clock=d.NTSC_CLOCK_HZ)).header()
    assert head.clock == (17095, d.NTSC_CLOCK_HZ) and head.lag == 1 and head.calls is None
    assert dict(head.meta) == {"title": "title", "author": "author", "released": "released"}
    assert d.Lift(replace(core(SONG), calls=(1, 0, 0))).header().calls == (1, (1, 0, 0))


def test_a_divergence_names_its_first_cell():
    with pytest.raises(d.Divergence, match="frame 3 register 5"):
        d.checked((np.array([[3, 5, 0, 1]]), ""), 10)
    assert d.checked((np.array([]), "text"), 10) == "text"


def test_the_command_line_verifies_and_lifts(glow_worm, capsys):
    assert d.main(["--verify", "--frames", "300", str(glow_worm)]) == 0
    assert ": exact" in capsys.readouterr().out
    assert d.main([str(glow_worm)]) == 0
    assert capsys.readouterr().out.startswith("unitracker:2.5\n")
