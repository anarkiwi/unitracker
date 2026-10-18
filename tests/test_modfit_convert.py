"""v1.20 expressions in documents and the lift-time conversion of native modulations to them."""

import numpy as np
import pytest
import sympy
import z3
from pydefmon import DefmonPlayer
from pysidtracker import SidImage

from unitracker.format import songio, xexpr
from unitracker.format.events.lifetime import selected
from unitracker.format.songio import library
from unitracker.lift import defmon
from unitracker.modfit import convert
from unitracker.modfit import grammar as G
from unitracker.modfit.form import Form
from unitracker.player.modulation import Occurrence
from unitracker.player.vm import SongError, SongPlayer

V, PHI = G.V, G.PHI
GT = library("goattracker2")["tables"]
SW = library("sidwizard")["tables"]
# A plain gated instrument whose hard restart turns the gate off two ticks ahead.
INS = (
    "{sr: 240, ctrl: [{ctrl: 65, note: +0}, end], "
    "restart: [{hold: 2, gate: 0, ad: 15, sr: 0}, {ctrl: 9}, end]"
)


def regs(ir, ticks):
    return SongPlayer(ir).render_grid(ticks).astype(int)


def voice0(text, ticks):
    """Voice 0's frequency and pulse-width words and control register per tick."""
    grid = regs(songio.loads(text), ticks)
    return grid[:, 0] | grid[:, 1] << 8, grid[:, 2] | grid[:, 3] << 8, grid[:, 4]


def moves(words):
    """The nonzero tick-to-tick changes of a register word."""
    return [int(d) for d in np.diff(words) if d]


TEMPO = ("\nsong:", "\ntempo: 7\nsong:")


def doc(playroutine, defs, phrase, extra="", silent=False):
    """A v1.20 document playing phrase ``p1`` on every voice, or on voice 0 alone (``silent``)."""
    rest = "\n        p2: [off*8]" if silent else ""
    play = "[p1, p2, p2]" if silent else "[p1, p1, p1]"
    return f"""
unitracker: 1.20
playroutine: {{ref: {playroutine}}}
song:
  - def:
{extra}{defs}
      phrase:
        p1: {phrase}{rest}
    play: {play}
  - end
"""


def gt_ir(phrase: str, ins: str = INS + "}"):
    """A one-voice goattracker2 song, the other voices silent."""
    defs = f"      ins:\n        i1: {ins}"
    return songio.loads(doc("goattracker2", defs, phrase, silent=True).replace(*TEMPO))


def sw_ir(phrase: str):
    """A one-voice sidwizard song, the other voices silent."""
    defs = "      ins:\n        i1: {sr: 240, ctrl: [{ctrl: 65, note: +0}, end]}"
    return songio.loads(doc("sidwizard", defs, phrase, silent=True))


GT_SONG = doc(
    "goattracker2",
    """      ins:
        i1: """
    + INS
    + """, pw: '1024 + 16*min(3, φ)'}
      x:
        x1: (v + 300) % 65536
        x2: 0""",
    "[C4.i1, -!freq:x1, -!freq:x1, D4!legato!freq:x2, -]",
).replace(*TEMPO)
SW_INS = "freq: '(φ >= 2)*W[N + 25]'"
SW_SONG = doc(
    "sidwizard",
    """      ins:
        i1: {sr: 240, ctrl: [{ctrl: 65, note: +0}, end], pw: '2048 + 2*min(4, φ)',
          """ + SW_INS + "}",
    "[C4.i1*4, -!freq:x1*4]",
    "      x:\n        x1: (v - 7) % 65536\n",
)
DM_VOICES = "      vinit: {0: {}, 1: {}, 2: {}}\n      ginit: {0: {}}\n"
DM_SONG = doc(
    "defmon",
    "      ins:\n        i1: {ctrl: [{ctrl: 65, pw: 256 + 10*φ, freq: (v - 139) % 65536}, end]}",
    "[C4.i1*8]",
    DM_VOICES,
)
DM_AF = doc(
    "defmon",
    """      ins:
        i1: {ctrl: [{ctrl: 65, gate: 1, hold: 4}, {gate: 0}, end]}
        i2: {ctrl: [{ctrl: 65, gate: 1, hold: 3}, {gate: 0}, end]}""",
    "[C4.i1*12, E4*12, G4.i2*12, A4.i1*12]",
    DM_VOICES,
)


# ---- expressions in documents ------------------------------------------------------------


def test_swing_arguments_share_their_speed_table_rows():
    text = doc(
        "goattracker2", "      ins:\n        i1: {sr: 240}", "[C4.i1!tempo:6/9, -!tempo:6/9]"
    )
    song = songio.loads(text.replace("-!tempo:6/9]", "-!tempo:6/9, -!vtempo:3/4, -!tempo:7]"))
    assert song["records"]["speedtab"] == {1: {"l": 6, "r": 9}, 2: {"l": 3, "r": 4}}
    cmds = [e.get("command") or e["commands"][-1] for e in song["phrases"]["p1"]]
    assert cmds == [[14, 1], [14, 1], [18, 2], [15, 7]]
    assert "-!vtempo:3/4, -!tempo:7]" in songio.dumps(song)
    with pytest.raises(songio.SongFormatError, match="can't express !tempo:none"):
        songio.loads(text.replace("-!tempo:6/9]", "-!tempo:none]"))


def test_goattracker_freq_command_pulse_expression_and_legato():
    freq, pulse, ctrl = voice0(GT_SONG, 34)
    c4, d4 = GT["ftab"][48], GT["ftab"][50]
    assert moves(freq) == [c4] + [300] * 12 + [d4 - c4 - 3600]
    assert moves(pulse)[:4] == [1024, 16, 16, 16]
    assert set(ctrl[9:34]) == {65}
    assert sorted(songio.loads(GT_SONG)["exprs"].values()) == [
        "(v + 300) % 65536",
        "0",
        "1024 + 16*min(3, φ)",
    ]
    dropped = GT_SONG.replace("D4!legato!freq:x2", "D4!freq:x2")
    assert {64, 9} <= set(voice0(dropped, 34)[2][9:34])


def test_sidwizard_instrument_freq_and_pulse_and_a_freq_command():
    freq, pulse, _ = voice0(SW_SONG, 40)
    c4 = 48 + 1
    base = SW["freqlo"][c4] | SW["freqhi"][c4] << 8
    vib = SW["exptab"][c4 + 24] if c4 + 24 < 107 else 0
    assert vib and vib in moves(freq) and moves(freq)[-6:] == [-7] * 6
    assert int(freq[23]) - base in (vib, vib + 1)
    assert [int(d) for d in np.diff(pulse)[3:9] if d] == [2, 2, 2, 2]


def test_a_sidwizard_pulse_expression_reads_the_keyboard_track_table():
    tracked = doc(
        "sidwizard",
        "      ins:\n        i1: {sr: 240, ctrl: [{ctrl: 65, note: +0}, end], "
        "pw: '2048 + 256*(E[N + 6] - E[N + 5])'}",
        "[C2.i1*8, C6.i1*8]",
    )
    _, pulse, _ = voice0(tracked, 120)
    step = [SW["freqhi"][n + 6 - 11] - SW["freqhi"][n + 5 - 11] for n in (24, 72)]
    assert step[0] != step[1]
    assert {2048 + 256 * d for d in step} <= {int(p) for p in pulse}


def test_defmon_row_expressions_drive_pulse_and_pitch():
    freq, pulse, _ = voice0(DM_SONG, 10)
    assert moves(pulse)[1:5] == [10] * 4
    assert moves(freq)[1:5] == [-139] * 4


@pytest.mark.parametrize(
    "song,old,new,kept",
    [
        (GT_SONG, "", "", ["pw: '1024 + 16*min(3, φ)'", "!legato!freq:x2"]),
        (GT_SONG, "pw: '1024 + 16*min(3, φ)'", "pw: 2048", ["pw: 2048"]),
        (
            GT_SONG,
            INS + ",",
            INS + ", filter: [{cut: '1024', res: 12, route: 1, mode: 16}, {cut: 512}, end],",
            ["cut: 1024", "cut: 512"],
        ),
        (
            GT_SONG,
            INS + ",",
            INS + ", filter: [{cut: '0', res: 12, route: 1, mode: 16}, {cut: 512}, end],",
            ["cut: 0"],
        ),
        (
            GT_SONG,
            INS + ",",
            INS + ", filter: [{cut: '1027', res: 12, route: 1, mode: 16}, {cut: 512}, end],",
            ["cut: '1027'"],
        ),
        (
            SW_SONG,
            SW_INS,
            SW_INS
            + ", filter: [{cut: '1024', res: 0, mode: 16}, {cut: 512, res: 3, mode: 32}, end]",
            ["cut: 1024", "cut: 512"],
        ),
    ],
)
def test_constant_and_text_expressions_read_back_as_written(song, old, new, kept):
    song = songio.loads(song.replace(old, new, 1) if old else song)
    text = songio.dumps(song)
    assert all(k in text for k in kept) and songio.dumps(songio.loads(text)) == text


MANY = "".join(f"        y{k}: {k}\n" for k in range(300))


@pytest.mark.parametrize(
    "edits,match",
    [
        ([("unitracker: 1.20", "unitracker: 1.8")], "not a unitracker v1.20 document"),
        ([("-!freq:x1, -!freq:x1,", "-!noteglide:0, -!freq:x1,")], "unknown command !noteglide"),
        (
            [("pw: '1024 + 16*min(3, φ)'", "pw: [{pw: 1024}, {step: 16, for: 3}, end]")],
            "unknown key for in pw",
        ),
        ([("x1: (v + 300) % 65536", "x1: v / 2")], "outside the grammar"),
        ([("x1: (v + 300) % 65536", "x1: W[N]")], "unknown table 'W'"),
        ([("x1: (v + 300) % 65536", "x1: '(0 < v < 9)'")], "not one comparison"),
        ([("x1: (v + 300) % 65536", "x1: '[v > 9]*v'")], r"a comparison is written \(a op b\)"),
        ([("x1: (v + 300) % 65536", "x1: sqrt(v)")], "calls no grammar function"),
        ([("x1: (v + 300) % 65536", "x1: (v + ")], "bad expression"),
        ([("-!freq:x1, -!freq:x1,", "-!freq:x9, -!freq:x1,")], "unknown name 'x9'"),
        (
            [("-!freq:x1, -!freq:x1,", "-!tempo:6/9/7, -!freq:x1,")],
            r"!tempo: the argument is 2 values",
        ),
        (
            [("-!freq:x1, -!freq:x1,", "-!tempo:6/256, -!freq:x1,")],
            r"!tempo: each value is a byte",
        ),
        (
            [("-!freq:x1, -!freq:x1,", "-!run:6/9, -!freq:x1,")],
            r"can't express !run:6/9 in",
        ),
        (
            [
                ("      x:\n", "      x:\n" + MANY),
                ("-!freq:x1, -!freq:x1", "-!freq:y1, -!freq:y299"),
            ],
            "argument is a byte",
        ),
    ],
)
def test_the_reader_refuses_old_forms_bad_arguments_and_text_outside_the_grammar(edits, match):
    text = GT_SONG
    for old, new in edits:
        assert old in text
        text = text.replace(old, new, 1)
    with pytest.raises(songio.SongFormatError, match=match):
        songio.loads(text)


def test_expressions_compile_to_the_grammar_semantics():
    ns = {"_F": lambda k: 1000 + k, "_T": {"W": lambda i: 2 * i}}
    f = xexpr.compile_expr("(v >= 3)*(F(N) % 7) - abs(-φ) + max(W[2], 1) + min(v, -3)", ns)
    assert f(3, 2, 4) == (1004 % 7) - 2 + 4 - 3 and f(2, 0, 4) == 4 - 3 + 1 - 1
    floor = xexpr.compile_expr("φ // 30 - v // 4", {"_T": {}})
    assert [floor(-5, phi, 0) for phi in (0, 29, 30, 61)] == [2, 2, 3, 4]
    assert xexpr.written("-5") == -5 and xexpr.written("v") == "v"
    with pytest.raises(xexpr.ExpressionError, match="string or an integer"):
        xexpr.text_of(True)
    with pytest.raises(xexpr.ExpressionError, match="takes 2"):
        xexpr.source("min(v)")


def test_a_selected_expression_must_exist():
    song = songio.loads(GT_SONG)
    song["exprs"] = {1: "v"}
    with pytest.raises(SongError, match="undefined"):
        SongPlayer(song).render_grid(20)
    song["exprs"] = {1: "v //"}
    with pytest.raises(SongError, match="x1"):
        SongPlayer(song)


CSILLAG_GATE = "(-W[N + 33]*abs((φ + 3) % 8 - 4) + 2*W[N + 33])*(φ >= 15)"
ACID_STEP = "v % 65536 + 2560)*(v % 65536 < 62976)"


@pytest.mark.parametrize(
    "name,ticks,old,new",
    [
        ("metal_warrior", 3840, "x1: v + 32", "x1: v + 33"),
        ("metal_warrior", 3840, "C4!legato!freq:x2", "C4!freq:x2"),
        ("csillag", 3 * 2304, CSILLAG_GATE, CSILLAG_GATE.replace("(φ >= 15)", "(φ >= 14)")),
        ("crosseyed", None, ACID_STEP, ACID_STEP.replace("2560", "2304", 1)),
    ],
)
def test_a_wrong_constant_or_a_dropped_flag_in_a_reference_breaks_its_grid(
    references, name, ticks, old, new
):
    ref = references[name]
    assert old in ref.text
    song = songio.loads(ref.text.replace(old, new, 1))
    assert SongPlayer(song).mismatch(ref.native[:ticks]) is not None


# ---- conversion: chip units -------------------------------------------------------------


@pytest.mark.parametrize(
    "e,r,m,lo,hi",
    [
        (8 * sympy.floor(V / 8) + 24, 0, 8, 0, 2040),
        (sympy.Mod(256 * sympy.floor(V / 8) - 513, 65536), 0, 8, 16, 1016),
        (4 * sympy.floor((V + 9) / 4) + 3 * sympy.Mod(V, 16), 4, 16, 4, 4084),
        (sympy.Mod(V + 3, 4096) + G.ge(V, 100) * 5, 0, 1, 0, 4000),
        (sympy.Mod(V - 7 * G.ge(PHI, 4) + 2 * PHI, 256), 0, 1, 0, 255),
    ],
)
def test_on_domain_agrees_on_every_chip_value(e, r, m, lo, hi):
    f, g = G.compiled(e), G.compiled(G.on_domain(e, r, m, lo, hi))
    for v in range(lo, hi + 1):
        if v % m == r:
            assert all(g(v, phi, 0) == f(v, phi, 0) for phi in range(8))


def test_on_domain_takes_floors_and_remainders_as_linear():
    assert G.on_domain(8 * sympy.floor(V / 8) + 24, 0, 8) == V + 24
    assert G.on_domain(8 * sympy.Mod(sympy.floor(V / 8) + 3, 256), 0, 8) == sympy.Mod(V + 24, 2048)
    assert G.on_domain(sympy.Mod(V + 3, 4096), 0, 1, 0, 4000) == V + 3
    assert G.on_domain(sympy.Mod(V + 3, 4096), 0, 1, 0, 4095) == sympy.Mod(V + 3, 4096)
    assert G.on_domain(sympy.Mod(V * V, 8), 0, 8) == sympy.Mod(V * V, 8)


def test_a_phase_that_is_not_one_per_firing_is_refused():
    derived = type("D", (), {"order": type("O", (), {"kind": "time"})()})()
    assert convert.checked(derived, sympy.Integer(1), V + PHI) == "v + φ"
    with pytest.raises(convert.ConversionError, match="not one per firing"):
        convert.checked(derived, sympy.Integer(2), V + PHI)


FILTER = INS + ", filter: [{cut: 1024, res: 12, route: 1, mode: 16}, end]}"


@pytest.mark.parametrize(
    "ramp,fall,text,wrong",
    [
        (
            (20, 3),
            {"l": 10, "r": 250},
            "-48*min(10, max(0, φ - 20)) + 24*min(20, φ) + 1024",
            ("24*min(20, φ)", "32*min(20, φ)"),
        ),
        (
            (4, 24),
            {"l": 4, "r": 248, "next": 2},
            "64*((-2*abs(φ % 8 - 4) + φ % 8 + 8*(φ // 8) - 8) % 32)",
            ("φ // 8", "φ // 9"),
        ),
    ],
)
def test_a_filter_sweep_is_written_in_cutoff_units(ramp, fall, text, wrong):
    ir = gt_ir("[C4.i1*4, D4*4]", FILTER)
    ft = ir["records"]["filttab"]
    ft[1]["next"], ft[2], ft[3] = 2, {"l": ramp[0], "r": ramp[1], "next": 3}, fall
    want = regs(ir, 200)
    assert len(set(want[:, 22])) > 20
    out = convert.convert(ir, cache=None, workers=1)
    assert list(out["exprs"].values()) == [text]
    written = songio.dumps(out)
    assert np.array_equal(regs(songio.loads(written), 200), want)
    assert not np.array_equal(regs(songio.loads(written.replace(*wrong)), 200), want)


def test_the_player_floors_a_scaled_expression_to_the_cell():
    ins = INS + ", filter: [{cut: '1024 + 12*min(φ, 6)', res: 12, route: 1, mode: 16}, end]}"
    cut = regs(gt_ir("[C4.i1*8]", ins), 40)[:, 22]
    run = [int(x) for x in cut[np.argmax(cut > 0) :][:8]]
    assert run == [(1024 + 12 * min(phi, 6)) // 8 for phi in range(8)]


# ---- conversion: rewriting from occurrences ---------------------------------------------


def occurrence(unit, instance, start, index, label=()):
    occ = Occurrence(unit, instance[0], 0, label, start, ())
    occ.instance, occ.index, occ.fetched = instance, index, instance
    return occ


def obs_of(occs, events=None) -> dict:
    return {"occurrences": occs, "events": events or {}, "plays": [[0], [0], [0]]}


def pulse_clones(ir):
    """Two pulse occurrences of one program on the phrase's first two steps."""
    ir["records"]["pulsetab"] = {1: {"l": 0x88, "r": 0, "next": 0}}
    return [
        occurrence("pulse", (0, 0, 0), {"pptr": 1}, 0, (1,)),
        occurrence("pulse", (0, 0, 4), {"pptr": 1}, 1, (1,)),
    ]


def test_a_record_with_two_expressions_is_cloned_into_a_phrase_variant():
    ir = gt_ir("[C4.i1*4, D4*4]")
    events = ir["phrases"]["p1"]
    events[0]["command"], events[1]["command"] = [9, 1], [9, 1]
    ramp, flat = "1024 + 16*min(4, φ)", "3000"
    out = convert.Rewrite(ir, obs_of(pulse_clones(ir)), [ramp, flat]).run()
    x = {t: i for i, t in out["exprs"].items()}
    assert set(x) == {ramp, flat}
    at = {r.get("x"): i for i, r in out["records"]["pulsetab"].items()}
    (variant,) = [p["phrase"] for p in out["tracks"][0]["plays"]]
    assert variant == "p1~1"
    assert [e["command"] for e in out["phrases"][variant]] == [[9, at[x[ramp]]], [9, at[x[flat]]]]
    grid = regs(out, 60)
    pw = [int(x) for x in grid[:, 2] | grid[:, 3] << 8]
    assert list(dict.fromkeys(pw)) == [0, 1024, 1040, 1056, 1072, 1088, 3000]
    assert pw.index(3000) >= 24


def test_an_instrument_with_two_expressions_is_cloned_and_its_event_names_the_clone():
    ir = sw_ir("[C4.i1*4, D4.i1*4]")
    start = {"svib": 0, "pnfx": 0, "ci": 1}
    occs = [occurrence("pitch", (0, 0, 0), start, 0), occurrence("pitch", (0, 0, 4), start, 1)]
    out = convert.Rewrite(ir, obs_of(occs), ["v + 1", "v + 2"]).run()
    freq = {i: out["exprs"][r["freq"]] for i, r in out["records"]["ins"].items()}
    (variant,) = [p["phrase"] for p in out["tracks"][0]["plays"]]
    named = [freq[e["instrument"]] for e in out["phrases"][variant]]
    assert sorted(freq.values()) == ["v + 1", "v + 2"] and named == ["v + 1", "v + 2"]


def test_a_clone_without_a_naming_event_is_refused():
    ir = gt_ir("[C4.i1*4, D4*4]")
    occs = pulse_clones(ir)
    with pytest.raises(convert.ConversionError, match="does not name"):
        convert.Rewrite(ir, obs_of(occs), ["1024 + φ", "3000 + φ"]).run()
    occs[1].instance = None
    with pytest.raises(convert.ConversionError, match="no event selects"):
        convert.Rewrite(ir, obs_of(occs), ["1024 + φ", "3000 + φ"]).run()
    start = {"command": 0, "onset": 1, "fired": 0, "ii": 1}
    occs = [occurrence("command", (0, 0, 0), start, k, (0, 0)) for k in range(2)]
    with pytest.raises(convert.ConversionError, match="one event plays two"):
        convert.Rewrite(ir, obs_of(occs), ["v + 1", "v + 2"]).run()


def lifetime_freqs(ir: dict) -> list:
    """Voice 0's ``(note, freq expression of the instrument its lifetime plays)``."""
    (play,) = ir["tracks"][0]["plays"]
    events = ir["phrases"][play["phrase"]]
    rows, exprs = ir["records"]["ins"], ir["exprs"]
    return [
        (event["note"], exprs[rows[events[head]["instrument"] if head is not None else 1]["freq"]])
        for _, event, head in selected(enumerate(events))
        if isinstance(event.get("note"), int)
    ]


@pytest.mark.parametrize(
    "phrase,texts,named",
    [
        ("[C4.i1*4, D4*4, E4*4]", ["v + 2", "v + 1", "v + 1"], [2, 1, None]),
        ("[C4*4, D4*4, E4*4]", ["v + 1", "v + 1", "v + 2"], [None, None, 2]),
    ],
)
def test_a_command_on_a_note_naming_no_instrument_selects_its_lifetimes_copy(phrase, texts, named):
    ir = gt_ir(phrase)
    start = {"command": 0, "onset": 1, "fired": 0, "ii": 1}
    occs = [occurrence("command", (0, 0, 4 * k), start, k, (0, 0)) for k in range(3)]
    out = convert.Rewrite(ir, obs_of(occs), texts).run()
    assert lifetime_freqs(out) == list(zip((48, 50, 52), texts))
    (play,) = out["tracks"][0]["plays"]
    assert [e.get("instrument") for e in out["phrases"][play["phrase"]]] == named


def test_a_start_without_modulation_plays_the_identity_where_its_site_holds_an_expression():
    ir = gt_ir("[C4.i1*4, D4.i2*4, E4*4, F4*4]", INS + "}\n        i2: " + INS + "}")
    at_site = {"command": 0, "onset": 1, "fired": 0, "ii": 2}
    occs = [
        occurrence("command", (0, 0, 0), at_site, 0, (0, 5)),
        occurrence("command", (0, 0, 8), at_site, 1, (0, 0)),
        occurrence("command", (0, 0, 12), {**at_site, "fired": 1}, 2, (0, 0)),
    ]
    occs[0].fetched = (0, 0, 4)
    events = {("command", (0, 0, 12)): 2}
    out = convert.Rewrite(ir, obs_of(occs, events), ["v + 1", None, None]).run()
    (play,) = out["tracks"][0]["plays"]
    steps = convert._steps(out["phrases"][play["phrase"]])  # pylint: disable=protected-access
    freq = {i: out["exprs"][r["freq"]] for i, r in out["records"]["ins"].items() if "freq" in r}
    assert [freq.get(steps[k].get("instrument")) for k in (4, 8)] == ["v + 1", "v"]
    assert steps[12]["command"] == [16, {t: i for i, t in out["exprs"].items()}["v"]]
    native = set(regs(ir, 130)[:, 0:2] @ [1, 256])
    _, d4, e4, _ = sorted(native - {0})
    ramp = set(regs(out, 130)[:, 0:2] @ [1, 256]) - native
    assert ramp and all(d4 < w < e4 for w in ramp)


def test_an_event_command_becomes_a_freq_expression_numbered_after_the_songs_own():
    ir = gt_ir("[C4.i1*4, D4*4, E4*4]")
    ir["phrases"]["p1"][2]["command"] = [16, 1]
    ir["exprs"] = {1: "(v + 300) % 65536"}
    ir["phrases"]["p1"][1]["commands"] = [[17, 0], [3, 1]]
    ir["records"]["speedtab"] = {1: {"l": 0, "r": 64}}
    want = regs(ir, 80)
    out = convert.convert(ir, cache=None, workers=1)
    assert out["exprs"][1] == "(v + 300) % 65536" and set(out["exprs"]) == {1, 2}
    (variant,) = [p["phrase"] for p in out["tracks"][0]["plays"]]
    events = out["phrases"][variant]
    assert events[1]["commands"] == [[17, 0], [16, 2]] and events[2]["command"] == [16, 1]
    ftab = GT["ftab"]
    glide = xexpr.compile_expr(out["exprs"][2], {"_F": lambda k: ftab[k], "_T": {}})
    v, offsets = (ftab[48] - ftab[50]) % 65536, []
    for phi in range(40):
        v = glide(v, phi, 50)
        offsets.append(v)
    rise = (ftab[50] - ftab[48] + 63) // 64
    assert offsets[rise - 1 :] == [0] * (41 - rise)
    assert offsets[0] == (ftab[48] - ftab[50] + 64) % 65536
    assert np.array_equal(regs(out, 80), want)
    events[1]["command"] = events[1].pop("commands")[1]
    assert not np.array_equal(regs(out, 80), want)


def test_a_started_event_without_a_command_gets_the_identity_and_legato():
    ir = gt_ir("[C4.i1*4, D4*4]")
    occs = [occurrence("command", (0, 0, 4), {"legato": 1}, 0, (0, 0))]
    obs = obs_of(occs, {("command", (0, 0, 4)): 0})
    out = convert.Rewrite(ir, obs, [None]).run()
    assert out["exprs"] == {} and out["tracks"][0]["plays"][0]["phrase"] == "p1"
    out = convert.Rewrite(ir, obs, ["F(N)"]).run()
    (variant,) = [p["phrase"] for p in out["tracks"][0]["plays"]]
    assert out["phrases"][variant][1]["commands"] == [[17, 0], [16, 1]]
    assert out["exprs"] == {1: "F(N)"}
    event = {}
    convert._set_cmds(event, [[1, 2]])  # pylint: disable=protected-access
    assert event == {"command": [1, 2]}
    with pytest.raises(convert.ConversionError, match="three commands"):
        convert._set_cmds(event, [[1, 2], [3, 4], [5, 6]])  # pylint: disable=protected-access


def test_a_referenced_program_no_start_proves_is_a_named_lift_error():
    ir = gt_ir("[C4.i1*4, D4*4]")
    ir["phrases"]["p1"][0]["command"] = [9, 1]
    ir["records"]["pulsetab"] = {1: {"l": 0x88, "r": 0, "next": 2}, 2: {"l": 16, "r": 4}}
    with pytest.raises(convert.ConversionError, match=r"pulsetab program 1: row 1 .*'l': 16"):
        convert.Rewrite(ir, obs_of([]), []).run()
    ir["records"]["pulsetab"][2] = {"l": 0x89, "r": 0}
    assert convert.Rewrite(ir, obs_of([]), []).run()["records"]["pulsetab"][2]["l"] == 0x89


def test_a_program_looping_to_its_first_row_is_written_as_its_one_expression():
    ir = gt_ir("[C4.i1*16, D4*16]")
    ir["records"]["ins"][1]["pw"] = 1
    ir["records"]["pulsetab"] = {
        1: {"l": 0x88, "r": 0, "next": 2},
        2: {"l": 6, "r": 0x20, "next": 3},
        3: {"l": 0x84, "r": 0, "next": 4},
        4: {"l": 6, "r": 0xE0, "next": 1},
    }
    got = convert.analyse(ir, None, 1)
    entry = got.out["records"]["ins"][1]["pw"]
    row = got.out["records"]["pulsetab"][entry]
    assert row == {"l": 0, "r": 0, "x": row["x"], "next": 0}
    pw = [int(w) for w in regs(got.out, got.ticks)[:, 2:4] @ [1, 256]]
    assert pw == [int(w) for w in regs(ir, got.ticks)[:, 2:4] @ [1, 256]]
    assert {0x800, 0x8C0, 0x400, 0x340} <= set(pw)


def test_an_unproved_start_names_its_program_and_start():
    an = convert.Analysis(gt_ir("[C4.i1*4, D4*4]"))
    occ = occurrence("pulse", (0, 0, 0), {"pptr": 3}, 0, (3,))
    occ.tick, occ.key, occ.error = 40, None, "no phase"
    with pytest.raises(
        convert.ConversionError, match=r"pulse program pulsetab 3, start \[pptr 3\]"
    ):
        convert.group_texts(an, [occ], {})
    freq = occurrence("command", (0, 0, 0), {"command": 1, "cmddata": 2}, 0, (1, 2))
    assert convert.diagnosis(an.unit("command"), freq).startswith("command program the event's")


@pytest.mark.parametrize(
    "ref,unit,falls", [("goattracker2", "command", True), ("sidwizard", "pitch", False)]
)
def test_only_a_selector_naming_its_site_falls_back_to_it(ref, unit, falls):
    units = {u["name"]: u for u in library(ref)["modulators"]["units"]}
    assert convert.falls_back(units[unit]) is falls
    assert not convert.falls_back({k: v for k, v in units[unit].items() if k != "site"})
    assert not convert.falls_back({**units[unit], "x": "ins[0].freq"})


@pytest.mark.parametrize(
    "make,unit,start,written",
    [
        (gt_ir, "command", {"command": 0, "onset": 1, "fired": 1}, [1, 2, 3]),
        (sw_ir, "pitch", {"svib": 0x81, "pnfx": 0, "onset": 1}, [1]),
    ],
)
def test_a_resumed_vibrato_is_each_steps_freq_only_where_a_step_reselects(
    make, unit, start, written
):
    ir = make("[C4.i1*4, D4*4]")
    occs = [occurrence(unit, (0, 0, 1), start, 0)]
    events = {(unit, (0, 0, s)): 0 for s in (1, 2, 3)}
    out = convert.Rewrite(ir, obs_of(occs, events), ["v + 1"]).run()
    (variant,) = {p["phrase"] for p in out["tracks"][0]["plays"]}
    steps = convert._steps(out["phrases"][variant])  # pylint: disable=protected-access
    assert [k for k, e in enumerate(steps) if e.get("command") == [16, 1]] == written


@pytest.mark.parametrize("flags,cleared,driven", [(0x50, 0x50, True), (0x60, 0x50, False)])
def test_sidwizard_vibrato_bytes_become_freq_expressions_and_are_cleared(flags, cleared, driven):
    ir = sw_ir("[C4.i1*8, -*4, -*4]")
    ir["records"]["ins"][1].update(vibrato=0x33, vibdelay=4, flags=flags)
    ir["phrases"]["p1"][2]["command"] = [0x260, 5]
    want = regs(ir, 120)
    out = convert.convert(ir, cache=None, workers=1)
    ins = out["records"]["ins"][1]
    assert (ins["vibrato"], ins["vibdelay"], ins["flags"]) == (0, 0, cleared)
    assert bool(ins.get("freq")) == driven
    text = songio.dumps(out)
    assert "flags" not in text and "vib" not in text
    assert np.array_equal(regs(songio.loads(text), 120), want)


@pytest.mark.parametrize("row,af", [(0, 12), (2, 1)])
def test_defmon_fixed_pitch_offset_becomes_its_rows_freq_expression(row, af):
    ir = songio.loads(DM_AF)
    ir["records"]["sidtab"][row]["af"] = af
    with pytest.raises(songio.SongFormatError, match="no shared-vocabulary form"):
        songio.dumps(ir)
    want = regs(ir, 120)
    text = songio.dumps(convert.convert(ir, cache=None, workers=1))
    assert f"freq: 'S[N + {24 + af}]'" in text and "af" not in text
    back = songio.loads(text)
    assert np.array_equal(regs(back, 120), want)
    assert {r.get("af", -1) for r in back["records"]["sidtab"].values()} == {-1, 0x80}
    assert not np.array_equal(regs(songio.loads(DM_AF), 120), want)


# ---- defMON's ACID accumulator and its offset cp -----------------------------------------


def acid_ir(cp: int):
    """:data:`DM_AF` with two ACID programs on layer B: one sets the accumulator (and offset
    ``cp``) and then steps it down, looping; one steps it up and freezes."""
    ir = songio.loads(DM_AF)
    st = ir["records"]["sidtab"]
    st[4] = {"dl": 0, "next": 5, "re": 0xF7, "fv": 16, "acid": 0x4000, "cp": cp}
    st[5] = {"dl": 0, "next": 6, "acid": 0xC200}
    st[6] = {"dl": 0, "next": 5, "acid": 0xC200}
    st[7] = {"dl": 0xF0, "next": 8, "fv": 32, "acid": 0x8A00}
    st[8] = {"dl": 0x80}
    st[0].update(pw=8, ps=0)
    events = ir["phrases"]["p1"]
    events[0]["command"], events[2]["command"] = [1, 4], [1, 7]
    return ir


@pytest.fixture(name="acid", scope="module")
def acid_fixture():
    """The conversion of :func:`acid_ir` with an offset that wraps the cutoff byte past $FF."""
    return convert.analyse(acid_ir(0xD0), cache=None, workers=1)


def test_acid_starts_carry_nothing_in_and_pulse_is_proved_under_the_chip_invariants(acid):
    an, seen, pulse = acid.analysis, set(), []
    for occ in acid.obs["occurrences"]:
        d = acid.found[occ.key][0]
        if d.identity:
            continue
        if occ.unit == "pulse A":
            pulse.append(d)
            continue
        assert occ.unit == "acid B" and d.exact and d.invariant is None
        assert occ.v_in == occ.start["acc"]
        assert convert.carried(an, occ, d.phase, d.value) == 0
        seen.add((occ.start["sub"], occ.start["astep"]))
    assert seen == {(0, 0), (0, 0x0A00)}
    assert pulse and all(d.exact and d.invariant.ok and d.invariant.unit for d in pulse)


def test_the_offset_is_folded_into_the_cut_expressions(acid):
    want = regs(acid.ir, 400)
    assert not np.array_equal(want[:, 22], regs(acid_ir(0), 400)[:, 22])
    assert len(np.unique(want[:, 22])) > 20
    text = songio.dumps(acid.out)
    assert "cp:" not in text and "acid:" not in text and "{cut: (" in text
    song = songio.loads(text)
    assert songio.dumps(song) == text and np.array_equal(regs(song, 400), want)


def test_an_offset_outside_the_acid_program_fails_naming_its_row():
    ir = acid_ir(0)
    ir["records"]["sidtab"][2]["cp"] = 0x10
    with pytest.raises(songio.SongFormatError, match=r"ctrl: row \{.*'cp': 16.*\} has no shared"):
        songio.dumps(convert.convert(ir, cache=None, workers=1))


def row_body(fields: dict) -> bytes:
    """The sidTAB row body the $16B0 bitmap walk reads as ``fields``."""
    out, half, bitmap, values = bytearray(), 0, 0, bytearray()
    for col_half, bit, size, field in defmon.COLUMNS:
        if col_half != half:
            out += bytes([bitmap]) + values
            half, bitmap, values = col_half, 0, bytearray()
        if field in fields:
            bitmap |= bit
            values += int(fields[field]).to_bytes(size, "little")
    return bytes(out + bytes([bitmap]) + values)


def patched(raw: bytes, columns: dict) -> bytes:
    """``raw`` with each sidTAB row of ``columns`` given those extra columns, its body moved to
    the end of the image."""
    mem = SidImage.from_bytes(raw).mem
    data = int.from_bytes(raw[6:8], "big") + 2
    base = int.from_bytes(raw[data - 2 : data], "little")
    out = bytearray(raw)
    for row, extra in columns.items():
        eff = defmon.resolve_row(mem, row)
        addr = mem[defmon.SIDTAB_LO + eff] | (mem[defmon.SIDTAB_HI + eff] << 8)
        new = base + len(out) - data
        out += row_body({**defmon.row_fields(mem, addr), **extra})
        out[data + defmon.SIDTAB_LO + eff - base] = new & 0xFF
        out[data + defmon.SIDTAB_HI + eff - base] = new >> 8
    return bytes(out)


def test_the_transcription_plays_the_replays_offset(references):
    ref = references["crosseyed"]
    mem = SidImage.from_bytes(ref.raw).mem
    fields = {"wgh": 0x41, "ad": 9, "af": 0x80, "ps": 3, "cp": 0xD0, "acid": 0x4000}
    mem[0x3000:0x3010] = row_body(fields)
    assert defmon.row_fields(mem, 0x3000) == fields
    raw = patched(ref.raw, {16: {"cp": 0xD0}, 13: {"cp": 0x05}})
    native = np.asarray(DefmonPlayer(raw).render_grid(len(ref.native)), dtype=np.uint8)
    assert not np.array_equal(ref.native[:, 0x16], native[:, 0x16])
    ir = defmon.lift(raw, expressions=False)
    assert {r.get("cp") for r in ir["records"]["sidtab"].values()} == {None, 5, 0xD0}
    assert SongPlayer(ir).mismatch(native) is None


def replay(z: dict, wrap: bool = True) -> tuple:
    """``(accumulator, $D416 before the doubling)`` after the replay's $10B5-$10D4 code, in its
    8-bit add-with-carry, from the folded accumulator ``acc`` and the offset ``cp``; without
    ``wrap`` the cutoff byte is the unwrapped sum."""
    acc = (z["acc"] - 256 * z["cp"]) & 0xFFFF
    step = [z3.If(z["sub"] == 1, 0xFF - b, b) for b in (z["astep"] & 0xFF, z["astep"] >> 8)]
    low = (acc & 0xFF) + step[0]
    high = (acc >> 8) + step[1] + (low >> 8)
    stored = z3.If(high & 0x80 == 0, high & 0xFF, z["floor"])
    cut = stored + z["cp"] + (high >> 8)
    cut = cut & 0xFF if wrap else cut
    out = z3.If(z3.Or(z3.UGE(cut, 0x80), z3.ULT(cut, z["floor"])), z["floor"], cut)
    return stored * 256 + (low & 0xFF), out


def refuted(actions: list, claim) -> list:
    """The paths of defMON's ``actions`` on which ``claim(cells, after)`` fails for some
    accumulator, step, direction, offset, floor and row offset."""
    pr = library("defmon")
    pr["constants"] = {"dbl": 0}
    form = Form(pr)
    names = ("acc", "astep", "sub", "cp", "floor", ("fld", "cp", "row"))
    out = []
    for conds, state in form.paths(actions, (), {}):
        smt = form.solver()
        z = {n if isinstance(n, str) else "row.cp": smt.term(n) for n in names}
        smt.solver.add(z3.ULE(z["floor"], 0xFF), z3.ULE(z["row.cp"], 0xFF))
        smt.solver.add(*(smt.cond(c) for c in conds))
        after = {k: smt.term(v) for k, v in state.items()}
        after["ghost"] = smt.term(form.expr(pr["ghost"]["global"][0x16], state)) & 0xFF
        smt.solver.add(z3.Not(claim(z, after)))
        if smt.solver.check() != z3.unsat:
            out.append(smt.solver.model())
    return out


def test_the_folded_step_is_the_replays_add_with_carry_for_every_state():
    def claim(z, after, wrap=True):
        stored, out = replay(z, wrap)
        return z3.And((after["acc"] - 256 * z["cp"]) & 0xFFFF == stored, after["ghost"] == out)

    steps = [{"call": "acid_step"}, {"call": "acid_out"}]
    assert not refuted(steps, claim)
    assert refuted(steps, lambda z, after: claim(z, after, wrap=False))


def test_an_offset_row_keeps_the_accumulator_and_its_carry_for_every_state():
    do = library("defmon")["record_types"]["sidtab"]["do"]
    [row] = [a for a in do if a.get("if") == "row.cp >= 0"]

    def claim(z, after, shift=0):
        before = (z["acc"] - 256 * z["cp"] + shift) & 0xFFFF
        moved = (after["acc"] - 256 * after["cp"]) & 0xFFFF
        return z3.And(moved == before, after["acc"] >> 16 == z["acc"] >> 16)

    assert not refuted([row], claim)
    assert refuted([row], lambda z, after: claim(z, after, 256))
