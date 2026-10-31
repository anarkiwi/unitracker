"""The defMON lift plays the spec player's registers exactly, and refuses what it cannot state."""

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from pydefmon import DefmonError, Tune
from pydefmon.tune import Jump, Play, Row
from unitracker_grammar import frequency
from unitracker_grammar.document import Call, Expr, Once, Signed
from unitracker_grammar.expr import compile_expr

from unitracker.lift import defmon as d
from unitracker.lift import invariants, shared

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
    return d.Core(
        Tune.parse(text),
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


CORPUS = "glow_worm tetrapapak higher_scores ponky fading_lights automatas syrapaog".split()
CORPUS += "rent_a_cop in_memory_of techno_aha triangle svenska anticitizen".split()


@pytest.mark.parametrize("tune", CORPUS)
def test_corpus_tunes_play_exactly_and_every_object_has_an_origin(tune, request):
    raw = request.getfixturevalue(tune).read_bytes()
    cells, text = d.verify(raw, 0, TICKS, whole=False)
    assert not cells.size, d.summary(cells, 3)
    assert text.startswith("unitracker:2.5\n") and "lag:1" in text and "|loop:" in text
    _, again, lift = d.lifted(raw)
    assert again == text and invariants.lifted(text)["song"][0] > 0
    assert set(lift.origins) == set(invariants.KINDS[:-1])


@pytest.mark.parametrize("tune", CORPUS)
def test_every_subtune_of_a_corpus_tune_is_exact(tune, request):
    raw = request.getfixturevalue(tune).read_bytes()
    songs = d.SidImage.from_bytes(raw).header.songs
    for subtune in range(songs):
        cells, _ = d.verify(raw, subtune, 600, whole=False)
        assert not cells.size, (subtune, d.summary(cells, 3))


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
    text = SONG.replace("n $32", "n $92")
    with pytest.raises(d.Refusal) as caught:
        d.Lift(core(text))
    assert caught.value.kind == "note"
    d.Lift(core(text, {"plain_tr": True}))


def test_a_sub_tick_first_call_gets_a_full_call_of_the_documents_own():
    lift = d.Lift(replace(core(SONG), calls=(0, 0, 1)))
    assert lift.header().calls == (1, (1, 0, 1))
    assert [lift.tick(t) for t in range(4)] == [1, 3, 5, 7]
    assert lift.steps[0].now == 0
    plain = d.Lift(replace(core(SONG), calls=(0, 1)))
    assert plain.header().calls is None and [plain.tick(t) for t in range(3)] == [1, 3, 5]


def test_the_first_step_is_its_own_where_calls_come_before_its_events():
    lift = d.Lift(replace(core(SONG), calls=(0, 1)))
    assert lift.loop > 0
    flat = d.Lift(core(SONG))
    assert flat.loop == 1 and flat.steps[0].delay == 0


def test_the_walk_counts_jumps_by_their_owner():
    steps, loop = d.Lift(core(SONG)).walk()
    one, two = (1, 2, 3), (4, 5, 6)
    assert [s[0] for s in steps] == [one, one, one, two, one]
    assert loop == 1


def test_the_walk_takes_a_jump_row_always_when_uncounted():
    text = SONG.replace("jumps counted", "jumps uncounted").replace("x2", "x0")
    steps, loop = d.Lift(core(text)).walk()
    assert [s[0] for s in steps] == [(1, 2, 3), (1, 2, 3)] and loop == 1


def test_the_walk_starts_from_the_owner_and_count_init_leaves():
    steps, _ = d.Lift(core(SONG, owner=1, count=1)).walk()
    assert [s[0] for s in steps][:3] == [(1, 2, 3), (4, 5, 6), (1, 2, 3)]


def test_the_walk_refuses_a_row_outside_the_song():
    with pytest.raises(d.Refusal, match="arranger"):
        d.Lift(core(SONG, cursor=9))


def test_a_step_ends_at_the_first_alt_step_and_the_last_voice_sets_the_delay():
    lift = d.Lift(core(SONG))
    assert lift.length((1, 2, 3)) == (2, 1)
    assert lift.length((4, 5, 6)) == (3, 5)
    assert lift.length((1, 1, 1)) == (4, 2)


def test_events_fall_a_call_after_the_delay_and_the_next_delay_pads_the_phrase():
    lift = d.Lift(core(SONG))
    late = lift.phrase(2, 1, d.Step((1, 2, 3), 3, 0, 3, 2))
    late = dict(lift.names.section("phrase"))[late]
    assert [(e.note, e.steps, dict(e.commands)) for e in late] == [
        ("-", 3, {}),
        ("C#2", None, {"b.run": "c1"}),
        ("-", 3, {}),
    ]
    early = lift.phrase(1, 0, d.Step((1, 2, 3), 0, 0, 3, 0))
    early = dict(lift.names.section("phrase"))[early]
    assert [(e.note, e.steps, dict(e.commands)) for e in early] == [
        ("-", None, {}),
        ("-", None, {"run": "c2"}),
        ("-", 3, {}),
    ]
    first = lift.phrase(1, 0, d.Step((1, 2, 3), 2, 0, 3, 0), first=True)
    assert len(dict(lift.names.section("phrase"))[first]) == 3 and first != early


def test_repeated_blocks_fold_into_groups_and_nested_ones_into_nested_groups():
    keys = [("a",), ("b",), ("a",), ("b",), ("c",)]
    assert d.fold(keys) == [d.Repeat((("a",), ("b",)), 2), ("c",)]
    nested = d.fold([("x",)] * 3 + [("y",)] + [("x",)] * 3 + [("y",)])
    assert nested == [d.Repeat((d.Repeat((("x",),), 3), ("y",)), 2)]
    assert d.fold([("a",), ("b",)]) == [("a",), ("b",)]


def test_a_counted_jump_is_a_group_and_the_return_a_loop():
    lift = d.Lift(core(SONG))
    voice = lift.voices()[0]
    assert voice.loop is not None and any(isinstance(p, d.Group) for p in voice.play)
    assert invariants.lifted(d.document_text(core(SONG)))["song"][0] < len(lift.steps)


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
def test_pitch_expressions_are_the_pitch_step(flags, span, mode):
    lift = d.Lift(core(SONG, flags, detune=(0, 0, 0)))
    space = {"_F": lambda k: frequency(k, d.PAL_CLOCK_HZ), "_T": {}}
    freq = compile_expr(lift.pitch(mode).text, space, zero=True)
    carry = compile_expr(lift.carry(mode).text, space, zero=True)
    for note in range(span):
        want, cy = spec_pitch(note, mode, 0)
        assert (freq(0, 0, note - 24), carry(0, 0, note - 24)) == (want, int(cy)), note


@pytest.mark.parametrize(
    "flags, names", [({}, ("det7", "cdet7")), ({"plain_tr": True}, ("det", "cdet"))]
)
def test_a_detune_is_a_state_a_template_reads_and_the_init_event_sets(flags, names):
    lift = d.Lift(core(SONG, flags))
    assert lift.pitch(0) == Call(names[0], ()) and lift.carry(0) == Call(names[1], ())
    text = d.document_text(core(SONG, flags))
    assert "tset(1)" in text and "tset(2)" in text and "tset(0)" not in text
    assert text.count("i.freq:") == 2 and "t.freq" not in text
    assert "i.freq" not in d.document_text(core(SONG, flags, detune=(0, 0, 0)))


def test_the_absolute_build_masks_the_note_of_the_detune_templates():
    assert d.Lift(core(SONG)).pitch(0) == Call("det7", ())
    assert d.Lift(core(SONG, {"plain_tr": True})).pitch(0) == Call("det", ())


def test_slide_modes_are_templates_over_the_rate_table():
    lift = d.Lift(core(SONG, {"plain_tr": True}))
    rate = lift.core.rates
    assert lift.pitch(0x81) == Call("up", (rate[2],))
    assert lift.pitch(0xC1) == Call("dn", (rate[2],))
    assert lift.carry(0xC1) == Call("cdn", (rate[2],))
    assert d.Lift(core(SONG)).pitch(0xFF) == Call("dn7", (rate[126],))


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
    assert d.chain(rows.__getitem__, 1) == ([1, 2], None)
    assert d.chain(rows.__getitem__, 3) == ([3, 1, 2], None)
    again = Tune.parse(SONG.replace("stop", "hold 1 -> $01")).rows
    assert d.chain(again.__getitem__, 1) == ([1, 2], 0)


def test_the_header_carries_the_clock_and_the_title():
    head = d.Lift(replace(core(SONG), clock=d.NTSC_CLOCK_HZ)).header()
    assert head.clock == (17095, d.NTSC_CLOCK_HZ) and head.lag == 1 and head.calls is None
    assert dict(head.meta) == {"title": "title", "author": "author", "released": "released"}
    assert d.Lift(replace(core(SONG), calls=(1, 0, 0))).header().calls == (1, (1, 0, 0))


def test_the_command_line_verifies_and_lifts(glow_worm, capsys):
    assert d.main(["--verify", "--frames", "300", str(glow_worm)]) == 0
    assert ": exact" in capsys.readouterr().out
    assert d.main([str(glow_worm)]) == 0
    assert capsys.readouterr().out.startswith("unitracker:2.5\n")


@pytest.mark.parametrize(
    "message, kind",
    [
        ("no defMON player: not found", "format"),
        ("pattern $05 has no ending ALT step", "no ending pattern"),
        ("row $07 is read from cells the replay writes", "jp onto jp"),
    ],
)
def test_a_model_error_is_a_typed_refusal(message, kind):
    got = d.refusal(DefmonError(message))
    assert isinstance(got, d.Refusal) and got.kind == kind and message in str(got)


def test_a_replay_the_model_cannot_read_is_refused_by_its_class(glow_worm, monkeypatch):
    def fail(*_):
        raise DefmonError("pattern $01 has no ending ALT step")

    monkeypatch.setattr(d.Tune, "from_replay", fail)
    with pytest.raises(d.Refusal) as caught:
        d.core_of_sid(glow_worm.read_bytes())
    assert caught.value.kind == "no ending pattern"


def test_a_reduced_entry_tune_is_read_through_its_full_entry(triangle):
    core_ = d.core_of_sid(triangle.read_bytes())
    assert 0 in core_.calls and 1 in core_.calls and core_.tune.init.cursor == core_.init["cursor"]
    assert core_.tune.chips[0] and core_.native_bytes > 0


def test_song_bytes_are_the_streams_the_byte_grammar_parses():
    mem = bytearray(0x10000)
    mem[0x1300:0x1303], mem[0x1400:0x1403] = bytes([0x00, 0x00, 0x00]), bytes([0x20, 0x21, 0x22])
    mem[0x2000:0x2007] = bytes([0x40, 5, 0x8F, 0xEE, 0xEE, 0xEE, 0xEE])
    mem[0x2100:0x2107] = bytes([0, 0x12, 0x30, 0xC0, 7, 9, 9])
    mem[0x1501], mem[0x1601] = 0x00, 0x30
    mem[0x3000:0x3004] = bytes([0x30, 0x11, 0xF0, 0x00])
    tables = {"pat_lo": 0x1300, "pat_hi": 0x1400, "rows_lo": 0x1500, "rows_hi": 0x1600}
    facts = SimpleNamespace(tables=tables, flags={"fixed": False})
    tune = Tune(
        chips=({0: Play((0, 1, 1)), 1: Jump(0, 2), 2: Play((0x80, 0, 0))},),
        patterns={0: (), 1: ()},
        rows={1: Row()},
        landed=({2: Play((0x80, 0, 0))},),
    )
    assert d.song_bytes(tune, bytes(mem), facts) == 3 * 3 + 3 + 5 + (4 + 1)
    tune = replace(tune, landed=({1: Play((0, 0, 0))},), patterns={0: ()}, rows={})
    assert d.song_bytes(tune, bytes(mem), facts) == 3 * 4 + 3


def test_a_programs_scope_is_the_voice_and_layer_its_slots_name():
    assert not d.scope(["ad", "p.freq", "m.pw"])
    assert d.scope(["w b.pw".split()[0] + "b.pw"]) == (("layer", "b"),)
    assert d.scope(["r3a.route", "w a.pw".replace(" ", "")]) == (("layer", "a"), ("voice", 2))
    assert d.scope(["k1b.cut", "e2a.cut"]) == (
        ("layer", "a"),
        ("layer", "b"),
        ("voice", 0),
        ("voice", 1),
    )


def test_origins_name_the_native_object_and_the_context_a_copy_needs(glow_worm):
    _, _, lift = d.lifted(glow_worm.read_bytes())
    origins = lift.origins
    assert set(origins["instruments"].values()) == {invariants.Origin(("voice", 0))}
    inits = [o for o in origins["ctrl"].values() if o.native[0] == "init"]
    assert inits == [invariants.Origin(("init", 0, "a"))]
    rows = [o for o in origins["ctrl"].values() if o.native[0] == "row"]
    assert all(set(c for c, _ in o.context) <= {"voice", "layer"} for o in rows)
    patterns = list(origins["phrases"].values())
    assert all(
        o.native[0] == "pattern" and ("last", o.context[0][1]) == o.context[0] for o in patterns
    )
    assert all(not o.context or o.context[0][0] == "last" for o in patterns)


def test_a_voice_free_row_is_one_program_whatever_voice_and_layer_arm_it():
    lift = d.Lift(core(SONG))
    names = {lift.program(1, layer, voice) for layer in "ab" for voice in range(3)}
    assert len(names) == 1
    origin = lift.names.origins["ctrl"][names.pop()]
    assert origin == invariants.Origin(("row", 1), ())


def test_a_row_that_routes_is_a_program_a_voice_and_layer():
    text = SONG.replace("row $02 WGh $41 TR $03 stop", "row $02 RE $f1 stop")
    lift = d.Lift(core(text))
    names = {(l, v): lift.program(2, l, v) for l in "ab" for v in (0, 2)}
    assert len(set(names.values())) == 4
    origin = lift.names.origins["ctrl"][names["b", 2]]
    assert origin == invariants.Origin(("row", 2), (("layer", "b"), ("voice", 2)))


def test_the_report_counts_objects_against_native_objects_and_bytes(glow_worm):
    sizes = d.report(glow_worm.read_bytes())
    assert list(sizes["counts"]) == list(invariants.KINDS)
    assert all(have >= native for k, (have, native) in sizes["counts"].items() if k != "song")
    assert sizes["counts"]["instruments"] == (1, 1) and sizes["counts"]["song"][0] > 0
    assert 0 < sizes["native_bytes"] < sizes["song"]
    assert sizes["fixed"] > 0


def test_a_document_with_an_unnamed_or_unattributed_object_is_refused(glow_worm, monkeypatch):
    monkeypatch.setattr(d.Lift, "origins", property(lambda _: {"ctrl": {}}))
    with pytest.raises(invariants.ModelError):
        d.lifted(glow_worm.read_bytes())


def test_the_whole_song_is_played_through_the_walk_and_its_loop_once_more():
    lift = d.Lift(core(SONG))
    last = lift.steps[-1]
    end = last.now + last.delay + last.last + 2
    assert lift.calls_to_play() >= 2 * end - lift.steps[lift.loop].now
    slow = d.Lift(replace(core(SONG), calls=(1, 0, 0, 0)))
    assert slow.calls_to_play() >= 4 * (slow.steps[-1].now + 1)


def test_a_whole_song_check_plays_at_least_the_minimum(glow_worm, monkeypatch):
    played = []
    monkeypatch.setattr(shared, "play", lambda _, n: played.append(n) or np.zeros((n, 25)))
    monkeypatch.setattr(d, "reference", lambda *_: np.zeros((played[-1], 25), dtype=np.uint8))
    raw = glow_worm.read_bytes()
    assert not d.verify(raw)[0].size and not d.verify(raw, 0, 10, whole=False)[0].size
    assert played == [max(d.MINIMUM, played[0]), 10]
