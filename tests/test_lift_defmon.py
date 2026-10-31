"""The defMON lift plays the spec player's registers exactly, and refuses what it cannot state."""

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from pydefmon import Tune
from pydefmon.tune import Jump
from unitracker_grammar import frequency
from unitracker_grammar.document import Call, Expr, Once, Signed
from unitracker_grammar.expr import compile_expr

from unitracker.lift import defmon as d
from unitracker.lift import invariants
from unitracker.lift.dmmem import Replay

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


class TuneReplay:
    """The replay interface over a parsed song, for tests that state a song as text."""

    def __init__(self, tune: Tune):
        self.tune, self.counted = tune, tune.counted

    def arranger(self, y):
        node = self.tune.chips[0][y]
        return (0xFF, node.target, node.count) if isinstance(node, Jump) else node.voices

    def end(self, p):
        return next((ev.at, ev.end) for ev in self.tune.patterns[p] if ev.end is not None)

    def events(self, p, last):
        return tuple(ev for ev in self.tune.patterns[p] if ev.at <= last)

    def row(self, y):
        return self.tune.rows[y]


def core(text: str, flags=None, **init) -> d.Core:
    """A core of the song ``text`` (the ``defmon.lark`` grammar) over the standard init state."""
    tune = Tune.parse(text)
    return d.Core(
        TuneReplay(tune),
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
def test_corpus_tunes_play_exactly_within_the_native_counts(tune, request):
    raw = request.getfixturevalue(tune).read_bytes()
    cells, text = d.verify(raw, 0, TICKS)
    assert not cells.size, d.summary(cells, 3)
    assert text.startswith("unitracker:2.5\n") and "lag:1" in text and "|loop:" in text
    core_ = d.core_of_sid(raw)
    assert invariants.check(text, d.native_counts(core_))["song"][0] > 0


@pytest.mark.parametrize("tune", CORPUS)
def test_every_subtune_of_a_corpus_tune_is_exact(tune, request):
    raw = request.getfixturevalue(tune).read_bytes()
    songs = d.SidImage.from_bytes(raw).header.songs
    for subtune in range(songs):
        cells, _ = d.verify(raw, subtune, 600)
        assert not cells.size, (subtune, d.summary(cells, 3))


def test_a_schedule_of_sub_tick_calls_is_a_calls_line(fading_lights, higher_scores, automatas):
    texts = [d.document_text(d.core_of_sid(t.read_bytes())) for t in (fading_lights, higher_scores)]
    assert "calls:{frame:1,full:[1,0]}" in texts[0] and "calls:" not in texts[1]
    assert d.core_of_sid(automatas.read_bytes()).calls == (1, 0, 0, 0, 0, 0, 0, 0)


def test_the_build_flags_choose_the_document(ponky, glow_worm, higher_scores):
    cores = [d.core_of_sid(t.read_bytes()) for t in (ponky, glow_worm, higher_scores)]
    assert [c.flags["or_ctrl"] for c in cores] == [True, False, False]
    assert [c.flags["plain_tr"] for c in cores] == [True, False, True]
    assert [c.replay.counted for c in cores] == [False, True, False]
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
    steps, loop = d.Lift(core(SONG.replace("jumps counted", "jumps uncounted"))).walk()
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
    assert d.chain(rows.__getitem__, 1) == ([1, 2], None)
    assert d.chain(rows.__getitem__, 3) == ([3, 1, 2], None)
    again = Tune.parse(SONG.replace("stop", "hold 1 -> $01")).rows
    assert d.chain(again.__getitem__, 1) == ([1, 2], 0)


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


TABLES = dict(v1=0x1000, v2=0x1100, v3=0x1200, pat_lo=0x1300, pat_hi=0x1400, rows_lo=0x1500)
TABLES |= dict(rows_hi=0x1600, dl=0x1700, rowptr_lo=0xFB, rowptr_hi=0xFC, scratch=0x96)


def memory(arranger=(), patterns=(), rows=(), fixed=False, counted=True) -> Replay:
    """A replay over a memory with ``arranger`` rows, ``patterns`` as byte strings laid end to
    end from $2000 and ``rows`` as ``(page, low, dl)``."""
    mem = bytearray(0x10000)
    for y, row in enumerate(arranger):
        for table, value in zip(("v1", "v2", "v3"), row):
            mem[TABLES[table] + y] = value
    at = 0x2000
    for p, body in enumerate(patterns):
        mem[TABLES["pat_lo"] + p], mem[TABLES["pat_hi"] + p] = at & 255, at >> 8
        mem[at : at + len(body)] = body
        at += len(body)
    for y, (page, low, dl) in enumerate(rows):
        mem[TABLES["rows_hi"] + y], mem[TABLES["rows_lo"] + y], mem[TABLES["dl"] + y] = (
            page,
            low,
            dl,
        )
    return Replay(bytes(mem), TABLES, fixed, counted)


def test_a_pattern_is_the_stream_at_its_pointer_up_to_its_alt_step_wherever_that_is():
    body = bytes([0x40, 5]) + bytes([0x01]) * 40 + bytes([0x12, 0x30]) + bytes([0x8F, 2])
    replay = memory(patterns=[body], rows=[(1, 0, 0)] * 6)
    assert replay.end(0) == (126, 15) and replay.size(0) == 45
    assert [s[0] for s in replay.stream(0)] == [0, 122, 126]
    assert [(e.at, e.a, e.note) for e in replay.events(0, 122)] == [(0, 5, None), (122, None, 0x30)]


def test_a_packed_stream_has_a_payload_byte_a_gate_and_a_fixed_one_four_bytes():
    packed = bytes([0x70, 1, 2, 3, 0x00, 0x8A])
    fixed = bytes([0x70, 1, 2, 3, 0x00, 0, 0, 0, 0x8A, 0, 0, 0])
    one, two = memory(patterns=[packed]), memory(patterns=[fixed], fixed=True)
    for replay in (one, two):
        assert replay.stream(0)[0] == (0, 0x70, 1, 2, 3)
        assert replay.end(0) == (4, 10)
    assert (one.size(0), two.size(0)) == (6, 12)


def test_a_pattern_index_past_the_tables_reads_the_memory_after_them():
    replay = memory(patterns=[b"\x8f"])
    mem = bytearray(replay.mem)
    mem[TABLES["pat_lo"] + 130], mem[TABLES["pat_hi"] + 130] = 0x00, 0x30
    mem[0x3000:0x3002] = b"\x8a\x00"
    assert Replay(bytes(mem), TABLES, False, True).end(130) == (0, 10)


def test_a_pattern_that_never_ends_is_refused():
    with pytest.raises(d.Refusal) as caught:
        memory(patterns=[b"\x00"]).stream(0)
    assert caught.value.kind == "no ending pattern"


def test_a_jp_row_names_its_target_and_a_jp_onto_a_jp_row_is_the_page_zero_row():
    rows = [(0x20, 0, 0x80), (0, 0, 0), (0, 5, 0)]
    replay = memory(rows=rows)
    assert replay.resolve(0) == 0 and replay.resolve(1) == 0 and replay.resolve(2) == 5
    quiet = memory(rows=[(0, 7, 3), (2, 0, 0)])
    row = quiet.row(0)
    assert row.columns == () and row.hold == 4 and row.next == 1


def test_a_row_read_from_cells_the_interpreter_writes_is_refused():
    with pytest.raises(d.Refusal) as caught:
        memory(rows=[(0, 0xF0, 0)]).row(0)
    assert caught.value.kind == "jp onto jp"


def test_a_stop_row_has_neither_hold_nor_next():
    mem = memory(rows=[(0x20, 0, 0x80)])
    row = mem.row(0)
    assert (row.hold, row.next) == (None, None)


def test_the_arranger_reaches_a_jump_once_whatever_its_count():
    rows = [(0, 1, 2), (1, 2, 3), (0x80, 0, 2), (4, 4, 4)]
    plays, jumps = memory(arranger=rows).reach(0)
    assert jumps == {2} and plays >= {0, 1, 3}
    plain, again = memory(arranger=rows, counted=False).reach(0)
    assert again == {2} and 3 not in plain


def test_a_document_reports_its_size_against_its_song_data(glow_worm):
    sizes = d.report(glow_worm.read_bytes())
    assert set(sizes) == {"lifted", "native", "bytes", "native_bytes"}
    assert all(sizes["lifted"][k] <= sizes["native"][k] for k in invariants.KINDS[:-1])
    assert sizes["native_bytes"] > 0 and sizes["bytes"] > sizes["native_bytes"]


def test_a_document_over_the_native_counts_is_refused(glow_worm, monkeypatch):
    monkeypatch.setattr(d, "native_counts", lambda _: dict.fromkeys(invariants.KINDS[:-1], 0))
    with pytest.raises(invariants.ModelError):
        d.verify(glow_worm.read_bytes(), 0, 50)
