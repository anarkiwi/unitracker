"""Expressions in lifted songs and the lift-time conversion of native modulations to them."""

import copy
from types import SimpleNamespace

import irs
import numpy as np
import pytest
import sympy
import z3
from conftest import vm_mismatch
from pydefmon import DefmonPlayer
from pysidtracker import SidImage
from unitracker_grammar import expr as xexpr

from unitracker import cache, document
from unitracker.document.events.lifetime import selected
from unitracker.lift import defmon
from unitracker.modfit import convert
from unitracker.modfit import exprs as modfit_exprs
from unitracker.modfit import grammar as G
from unitracker.modfit import phase
from unitracker.modfit.form import Form
from unitracker.player import reach
from unitracker.player.modulation import INIT, Occurrence
from unitracker.player.vm import SongError, SongPlayer
from unitracker.playroutine.atoms import note_index
from unitracker.playroutine.library import SongFormatError, library

V, PHI = G.V, G.PHI
GT = library("goattracker2")["tables"]
SW = library("sidwizard")["tables"]
# A plain gated instrument whose hard restart turns the gate off two ticks ahead.
GT_RECORDS = {
    "ins": {1: {"ctrl": 1, "restart": 1, "sr": 240}},
    "ctrltab": {1: {"w": 65, "n": 0, "next": 0}},
    "restart": {
        1: {"hold": 2, "gate": 0, "ad": 15, "sr": 0, "ctrl": -1, "next": 2},
        2: {"ctrl": 9, "gate": 1, "ad": -1, "sr": -1, "next": 0},
    },
}
SW_RECORDS = {
    "ins": {1: {"ctrl": 1, "sr": 240}},
    "wftab": {1: {"w": 65, "a": 0, "d": 0, "h": 0, "next": 0}},
}
DM_ROW = dict.fromkeys(("ad", "af", "fv", "re", "sr", "tr", "wgh", "wgl"), -1)
DM_INIT = {
    "vinit": {v: {"pw": 0, "sacc": 0, "tbase": 0} for v in range(3)},
    "ginit": {0: {}},
}
TEMPO = {"goattracker2": 7}


def regs(ir, ticks):
    return SongPlayer(ir).render_grid(ticks).astype(int)


def voice0(ir, ticks):
    """Voice 0's frequency and pulse-width words and control register per tick."""
    grid = regs(ir, ticks)
    return grid[:, 0] | grid[:, 1] << 8, grid[:, 2] | grid[:, 3] << 8, grid[:, 4]


def moves(words):
    """The nonzero tick-to-tick changes of a register word."""
    return [int(d) for d in np.diff(words) if d]


def edited(ir, old, new, whole=False) -> dict:
    """``ir`` with ``old`` replaced by ``new`` in the one expression that holds it, or is it."""
    [key] = [k for k, text in ir["exprs"].items() if (text == old if whole else old in text)]
    ir["exprs"][key] = ir["exprs"][key].replace(old, new, 1)
    return ir


def generic(ir, ticks) -> str:
    """The generic document of ``ir`` over ``ticks`` ticks, whether or not its state recurs: the
    writer refuses a row that keeps a native field."""
    return document.write(ir, document.native_state(ir, ticks, ticks))


def unlegato(ir) -> dict:
    """``ir`` with the legato dropped from the first legato note naming a freq expression."""
    event = next(
        e
        for phrase in ir["phrases"].values()
        for e in phrase
        if "note" in e and len(e.get("commands", ())) == 2 and [17, 0] in e["commands"]
    )
    [event["command"]] = [c for c in event.pop("commands") if c != [17, 0]]
    return ir


def notes(playroutine, *specs) -> list:
    """Phrase events of ``(note, steps[, instrument])``: a note name, ``off`` or ``-``."""
    offset = library(playroutine)["note_offset"]
    out = []
    for note, steps, *ins in specs:
        event = {"instrument": ins[0]} if ins else {}
        if note != "-":
            event["note"] = note if note == "off" else note_index(note) + offset
        if steps != 1:
            event["steps"] = steps
        out.append(event)
    return out


def song(playroutine, records, phrase, silent=True, **extra) -> dict:
    """A song playing ``phrase`` on voice 0, the other voices silent, or on every voice."""
    phrases = {"p1": phrase}
    if silent:
        phrases["p2"] = [{"note": "off", "steps": 8}]
    play = ["p1", "p2", "p2"] if silent else ["p1"] * 3
    if playroutine in TEMPO:
        extra.setdefault("tempo", TEMPO[playroutine])
    records = copy.deepcopy(records)
    return irs.song(playroutine, records=records, phrases=phrases, play=play, **extra)


def gt_ir(*specs, **extra):
    """A one-voice goattracker2 song of :data:`GT_RECORDS`, the other voices silent."""
    return song("goattracker2", GT_RECORDS, notes("goattracker2", *specs), **extra)


def sw_ir(*specs, **extra):
    """A one-voice sidwizard song of :data:`SW_RECORDS`, the other voices silent."""
    return song("sidwizard", SW_RECORDS, notes("sidwizard", *specs), **extra)


def with_filter(ir, row) -> dict:
    """``ir`` with instrument 1 running the one-row filter program ``row``."""
    ir["records"]["ins"][1]["filter"] = 1
    ir["records"]["filttab"] = {1: {"l": 144, "r": 193, "next": 0, **row}}
    return ir


def gt_song():
    """Voice 0's pulse expression, freq commands, a legato note and a freq reset, on every voice."""
    records = copy.deepcopy(GT_RECORDS)
    records["ins"][1]["pw"] = 1
    records["pulsetab"] = {1: {"x": 1, "next": 0}}
    phrase = [
        {"instrument": 1, "note": 48},
        {"command": [16, 2]},
        {"command": [16, 2]},
        {"note": 50, "commands": [[16, 3], [17, 0]]},
        {"command": [16, 0]},
    ]
    exprs = {1: "1024 + 16*min(3, φ)", 2: "(v + 300) % 65536", 3: "0"}
    return song("goattracker2", records, phrase, silent=False, exprs=exprs)


def sw_song():
    """An instrument freq and pulse expression and a freq command, on every voice."""
    records = copy.deepcopy(SW_RECORDS)
    records["ins"][1].update(pw=1, freq=3)
    records["pwtab"] = {1: {"x": 2, "next": 0}}
    phrase = [{"instrument": 1, "note": 49, "steps": 4}, {"command": [16, 1], "steps": 4}]
    exprs = {1: "(v - 7) % 65536", 2: "2048 + 2*min(4, φ)", 3: "(φ >= 2)*W[N + 25]"}
    return song("sidwizard", records, phrase, silent=False, exprs=exprs)


def dm_song():
    """A defMON row's pulse and pitch expressions, on every voice."""
    row = {**DM_ROW, "af": 128, "dl": 128, "next": 0, "wgh": 64, "xf": 1, "xp": 2}
    records = {"sidtab": {0: row}, **copy.deepcopy(DM_INIT)}
    exprs = {1: "(v - 139) % 65536", 2: "256 + 10*φ"}
    phrase = notes("defmon", ("C4", 8, 1))
    return song("defmon", records, phrase, silent=False, exprs=exprs)


def dm_af():
    """Two gated defMON instruments, sidTAB rows 0 and 2, on every voice."""
    gated = [{**DM_ROW, "wgh": 64, "wgl": 1}, {**DM_ROW, "dl": 128, "next": 0, "wgl": 0}]
    sidtab = {
        0: {**gated[0], "dl": 3, "next": 1},
        1: gated[1],
        2: {**gated[0], "dl": 2, "next": 3},
        3: gated[1],
    }
    phrase = notes("defmon", ("C4", 12, 1), ("E4", 12), ("G4", 12, 3), ("A4", 12, 1))
    return song("defmon", {"sidtab": sidtab, **copy.deepcopy(DM_INIT)}, phrase, silent=False)


# ---- expressions in lifted songs --------------------------------------------------------


def test_goattracker_freq_command_pulse_expression_and_legato():
    freq, pulse, ctrl = voice0(gt_song(), 34)
    c4, d4 = GT["ftab"][48], GT["ftab"][50]
    assert moves(freq) == [c4] + [300] * 12 + [d4 - c4 - 3600]
    assert moves(pulse)[:4] == [1024, 16, 16, 16]
    assert set(ctrl[9:34]) == {65}
    dropped = gt_song()
    event = dropped["phrases"]["p1"][3]
    event["command"] = event.pop("commands")[0]
    assert {64, 9} <= set(voice0(dropped, 34)[2][9:34])


def test_sidwizard_instrument_freq_and_pulse_and_a_freq_command():
    freq, pulse, _ = voice0(sw_song(), 40)
    c4 = 48 + 1
    base = SW["freqlo"][c4] | SW["freqhi"][c4] << 8
    vib = SW["exptab"][c4 + 24] if c4 + 24 < 107 else 0
    assert vib and vib in moves(freq) and moves(freq)[-6:] == [-7] * 6
    assert int(freq[23]) - base in (vib, vib + 1)
    assert [int(d) for d in np.diff(pulse)[3:9] if d] == [2, 2, 2, 2]


def test_a_sidwizard_pulse_expression_reads_the_keyboard_track_table():
    records = copy.deepcopy(SW_RECORDS)
    records["ins"][1]["pw"] = 1
    records["pwtab"] = {1: {"x": 1, "next": 0}}
    phrase = notes("sidwizard", ("C2", 8, 1), ("C6", 8))
    exprs = {1: "2048 + 256*(E[N + 6] - E[N + 5])"}
    _, pulse, _ = voice0(song("sidwizard", records, phrase, silent=False, exprs=exprs), 120)
    step = [SW["freqhi"][n + 6 - 11] - SW["freqhi"][n + 5 - 11] for n in (24, 72)]
    assert step[0] != step[1]
    assert {2048 + 256 * d for d in step} <= {int(p) for p in pulse}


def test_defmon_row_expressions_drive_pulse_and_pitch():
    freq, pulse, _ = voice0(dm_song(), 10)
    assert moves(pulse)[1:5] == [10] * 4
    assert moves(freq)[1:5] == [-139] * 4


@pytest.mark.parametrize(
    "text,match",
    [
        ("v //", "x1: bad expression"),
        ("v / 2", "outside the grammar"),
        ("W[N]", "unknown table 'W'"),
        ("(0 < v < 9)", "not one comparison"),
        ("[v > 9]*v", r"a comparison is written \(a op b\)"),
        ("sqrt(v)", "calls no grammar function"),
    ],
)
def test_the_player_refuses_an_expression_outside_the_grammar(text, match):
    ir = gt_song()
    ir["exprs"][1] = text
    with pytest.raises(SongError, match=match):
        SongPlayer(ir)


def test_a_selected_expression_must_exist():
    ir = gt_song()
    ir["exprs"] = {1: "v"}
    with pytest.raises(SongError, match="undefined"):
        SongPlayer(ir).render_grid(20)


@pytest.mark.parametrize(
    "name,ticks,edit",
    [
        ("metal_warrior", 3840, lambda ir: edited(ir, "v + 32", "v + 33", whole=True)),
        ("metal_warrior", 3840, unlegato),
        ("csillag", 3 * 2304, lambda ir: edited(ir, "(φ >= 15)", "(φ >= 14)")),
        ("crosseyed", None, lambda ir: edited(ir, "- 2048)*(v % 32768", "- 1792)*(v % 32768")),
    ],
)
def test_a_wrong_constant_or_a_dropped_flag_in_a_reference_breaks_its_grid(
    references, name, ticks, edit
):
    ref = references[name]
    assert vm_mismatch(edit(ref.ir()), ref.native[:ticks]) is not None


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
    ir = with_filter(gt_ir(("C4", 4, 1), ("D4", 4)), {"cut": 128})
    ft = ir["records"]["filttab"]
    ft[1]["next"], ft[2], ft[3] = 2, {"l": ramp[0], "r": ramp[1], "next": 3}, fall
    want = regs(ir, 200)
    assert len(set(want[:, 22])) > 20
    out = convert.convert(ir, cache=None, workers=1)
    assert list(out["exprs"].values()) == [text]
    assert np.array_equal(regs(out, 200), want)
    assert generic(out, len(want))
    assert not np.array_equal(regs(edited(out, *wrong), 200), want)


def test_a_sweep_that_turns_at_a_row_boundary_plays_as_written():
    ir = with_filter(gt_ir(("C4", 16, 1), ("D4", 16)), {"cut": 128})
    ft = ir["records"]["filttab"]
    ft[1]["next"], ft[2] = 2, {"l": 0, "r": 102, "next": 3}
    ft[3], ft[4], ft[5] = (
        {"l": 30, "r": 253, "next": 4},
        {"l": 1, "r": 3, "next": 5},
        {
            "l": 1,
            "r": 255,
            "next": 4,
        },
    )
    want = regs(ir, 200)
    out = convert.convert(ir, cache=None, workers=1)
    [text] = out["exprs"].values()
    assert "*((-(φ % 2) + 2*(φ // 2) - 17) % 256) - 96)*(φ >= 32)" in text
    assert np.array_equal(regs(out, 200), want)
    assert generic(out, len(want))
    assert not np.array_equal(regs(edited(out, "+ 2*(φ // 2)", "- 2*(φ // 2)"), 200), want)


def test_the_player_floors_a_scaled_expression_to_the_cell():
    ir = with_filter(gt_ir(("C4", 8, 1), exprs={1: "1024 + 12*min(φ, 6)"}), {"cut": -1, "x": 1})
    cut = regs(ir, 40)[:, 22]
    run = [int(x) for x in cut[np.argmax(cut > 0) :][:8]]
    assert run == [(1024 + 12 * min(phi, 6)) // 8 for phi in range(8)]


# ---- conversion: rewriting from occurrences ---------------------------------------------


def occurrence(unit, instance, start, index, label=()):
    occ = Occurrence(unit, instance[0], 0, label, start, ())
    occ.instance, occ.index, occ.fetched = instance, index, instance
    return occ


def obs_of(occs, events=None) -> SimpleNamespace:
    return SimpleNamespace(occurrences=occs, events=events or {}, plays=[[0], [0], [0]])


def pulse_clones(ir):
    """Two pulse occurrences of one program on the phrase's first two steps."""
    ir["records"]["pulsetab"] = {1: {"l": 0x88, "r": 0, "next": 0}}
    return [
        occurrence("pulse", (0, 0, 0), {"pptr": 1}, 0, (1,)),
        occurrence("pulse", (0, 0, 4), {"pptr": 1}, 1, (1,)),
    ]


def test_a_record_with_two_expressions_is_cloned_into_a_phrase_variant():
    ir = gt_ir(("C4", 4, 1), ("D4", 4))
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
    ir = sw_ir(("C4", 4, 1), ("D4", 4))
    start = {"svib": 0, "pnfx": 0, "ci": 1}
    occs = [occurrence("pitch", (0, 0, 0), start, 0), occurrence("pitch", (0, 0, 4), start, 1)]
    out = convert.Rewrite(ir, obs_of(occs), ["v + 1", "v + 2"]).run()
    freq = {i: out["exprs"][r["freq"]] for i, r in out["records"]["ins"].items()}
    (variant,) = [p["phrase"] for p in out["tracks"][0]["plays"]]
    named = [freq[e["instrument"]] for e in out["phrases"][variant]]
    assert sorted(freq.values()) == ["v + 1", "v + 2"] and named == ["v + 1", "v + 2"]


def filter_clones(ir):
    """Two filter occurrences of one program on the phrase's first two steps."""
    ir["records"]["ins"][1]["filter"] = 1
    ir["records"]["flttab"] = {1: {"a": 0x90, "b": 0, "k": 0, "next": 0}}
    return [
        occurrence("filter", (0, 0, 0), {"fpos": 1}, 0),
        occurrence("filter", (0, 0, 4), {"fpos": 1}, 1),
    ]


def test_a_program_copy_selects_the_instrument_the_event_inherits():
    ir = sw_ir(("C4", 4, 1), ("D4", 4))
    ramp, steep = "640 + 16*min(4, φ)", "1280 + 16*min(4, φ)"
    out = convert.Rewrite(ir, obs_of(filter_clones(ir)), [ramp, steep]).run()
    exprs = dict(out["exprs"].items())
    at = {i: exprs[r["x"]] for i, r in out["records"]["flttab"].items() if "x" in r}
    assert set(at.values()) == {ramp, steep} and len(at) == 2
    (variant,) = [p["phrase"] for p in out["tracks"][0]["plays"]]
    named = [e.get("instrument") for e in out["phrases"][variant]]
    ins = out["records"]["ins"]
    assert len({i for i in named if i is not None}) == 2
    assert {ins[named[0]]["filter"], ins[named[1]]["filter"]} == set(at)
    cut = [int(c) for c in regs(out, 60)[:, 22]]
    assert list(dict.fromkeys(cut)) == [0, 80, 82, 84, 86, 88, 160, 162, 164, 166, 168]
    assert cut.index(160) >= 24


def test_a_program_copy_the_instrument_does_not_select_is_refused():
    ir = sw_ir(("C4", 4, 1), ("D4", 4))
    ir["records"]["flttab"] = {1: {"a": 0x90, "b": 0, "k": 0, "next": 0}}
    occs = [
        occurrence("filter", (0, 0, 0), {"fpos": 1}, 0),
        occurrence("filter", (0, 0, 4), {"fpos": 1}, 1),
    ]
    with pytest.raises(convert.ConversionError, match="does not name"):
        convert.Rewrite(ir, obs_of(occs), ["1024 + 16*min(4, φ)", "2048 + 16*min(4, φ)"]).run()


def test_one_event_playing_two_program_copies_is_refused():
    ir = sw_ir(("C4", 4, 1), ("D4", 4))
    occs = filter_clones(ir)
    occs[1].instance = (0, 0, 0)
    with pytest.raises(convert.ConversionError, match="one event plays two"):
        convert.Rewrite(ir, obs_of(occs), ["1024 + 16*min(4, φ)", "2048 + 16*min(4, φ)"]).run()


def test_a_clone_without_a_naming_event_is_refused():
    ir = gt_ir(("C4", 4, 1), ("D4", 4))
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
        ((("C4", 4, 1), ("D4", 4), ("E4", 4)), ["v + 2", "v + 1", "v + 1"], [2, 1, None]),
        ((("C4", 4), ("D4", 4), ("E4", 4)), ["v + 1", "v + 1", "v + 2"], [None, None, 2]),
    ],
)
def test_a_command_on_a_note_naming_no_instrument_selects_its_lifetimes_copy(phrase, texts, named):
    ir = gt_ir(*phrase)
    start = {"command": 0, "onset": 1, "fired": 0, "ii": 1}
    occs = [occurrence("command", (0, 0, 4 * k), start, k, (0, 0)) for k in range(3)]
    out = convert.Rewrite(ir, obs_of(occs), texts).run()
    assert lifetime_freqs(out) == list(zip((48, 50, 52), texts))
    (play,) = out["tracks"][0]["plays"]
    assert [e.get("instrument") for e in out["phrases"][play["phrase"]]] == named


def test_a_start_without_modulation_plays_the_identity_where_its_site_holds_an_expression():
    ir = gt_ir(("C4", 4, 1), ("D4", 4, 2), ("E4", 4), ("F4", 4))
    records = ir["records"]
    records["ins"][2] = {"ctrl": 2, "restart": 3, "sr": 240}
    records["ctrltab"][2] = dict(records["ctrltab"][1])
    records["restart"][3] = {**records["restart"][1], "next": 4}
    records["restart"][4] = dict(records["restart"][2])
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
    ir = gt_ir(("C4", 4, 1), ("D4", 4), ("E4", 4))
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


@pytest.mark.parametrize(
    "ctrltab, glides",
    [
        ({1: {"w": 65, "n": 0, "next": 0}}, True),
        ({1: {"w": 65, "n": 0, "next": 2}, 2: {"w": 65, "n": 12, "next": 1}}, False),
    ],
)
def test_a_command_its_unit_never_fires_on_is_the_identity(ctrltab, glides):
    ir = gt_ir(("C4", 4, 1), ("D4", 4), ("E4", 4))
    ir["records"]["ctrltab"] = ctrltab
    ir["phrases"]["p1"][1]["commands"] = [[17, 0], [3, 1]]
    ir["records"]["speedtab"] = {1: {"l": 0, "r": 64}}
    got = convert.analyse(ir, None, 1)
    (variant,) = [p["phrase"] for p in got.out["tracks"][0]["plays"]]
    assert got.out["phrases"][variant][1]["commands"] == [[17, 0], [16, 1]]
    assert ("F(N)" in got.out["exprs"][1]) == glides and (got.out["exprs"][1] == "v") != glides
    want = regs(ir, got.ticks)
    assert np.array_equal(regs(got.out, got.ticks), want)
    assert generic(got.out, len(want))


def test_a_phrase_every_play_of_which_plays_a_variant_is_dropped():
    ir = gt_ir(("C4", 4, 1), ("D4", 4), ("E4", 4))
    ir["phrases"]["p1"][1]["command"] = [3, 0]
    ir["phrases"]["unplayed"] = copy.deepcopy(ir["phrases"]["p1"][:1])
    want = regs(ir, 80)
    out = convert.convert(ir, cache=None, workers=1)
    (variant,) = [p["phrase"] for p in out["tracks"][0]["plays"]]
    assert variant == "p1~1" and "p1" not in out["phrases"] and "unplayed" in out["phrases"]
    assert np.array_equal(regs(out, 80), want)
    assert generic(out, len(want))


def test_a_started_event_without_a_command_gets_the_identity_and_legato():
    ir = gt_ir(("C4", 4, 1), ("D4", 4))
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
    ir = gt_ir(("C4", 4, 1), ("D4", 4))
    ir["phrases"]["p1"][0]["command"] = [9, 1]
    ir["records"]["pulsetab"] = {1: {"l": 0x88, "r": 0, "next": 2}, 2: {"l": 16, "r": 4}}
    with pytest.raises(convert.ConversionError, match=r"pulsetab program 1: row 1 .*'l': 16"):
        convert.Rewrite(ir, obs_of([]), []).run()
    ir["records"]["pulsetab"][2] = {"l": 0x89, "r": 0}
    assert convert.Rewrite(ir, obs_of([]), []).run()["records"]["pulsetab"][2]["l"] == 0x89


def test_a_program_looping_to_its_first_row_is_written_as_its_one_expression():
    ir = gt_ir(("C4", 16, 1), ("D4", 16))
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
    an = convert.Analysis(gt_ir(("C4", 4, 1), ("D4", 4)))
    occ = occurrence("pulse", (0, 0, 0), {"pptr": 3}, 0, (3,))
    occ.tick, occ.key, occ.error = 40, None, "no phase"
    with pytest.raises(
        convert.ConversionError, match=r"pulse program pulsetab 3, start \[pptr 3\]"
    ):
        convert.group_texts(an, [occ], {})
    freq = occurrence("command", (0, 0, 0), {"command": 1, "cmddata": 2}, 0, (1, 2))
    assert convert.diagnosis(an.unit("command"), freq).startswith("command program the event's")


def pulse_ir():
    """A one-voice goattracker2 song with a looping pulse program, so a start is derived."""
    ir = gt_ir(("C4", 16, 1), ("D4", 16))
    ir["records"]["ins"][1]["pw"] = 1
    ir["records"]["pulsetab"] = {1: {"l": 0x88, "r": 0, "next": 1}}
    return ir


@pytest.mark.parametrize(
    "exc,marker",
    [
        (RecursionError, "maximum recursion depth"),
        (TypeError, "cannot determine truth value of Relational"),
    ],
)
def test_a_failure_during_derive_is_a_named_conversion_error(monkeypatch, exc, marker):
    """A recursion, or a relational TypeError, in the derivation is a named refusal."""

    def boom(*args, **kwargs):
        raise exc(marker)

    monkeypatch.setattr(modfit_exprs, "derive", boom)
    with pytest.raises(convert.ConversionError) as err:
        convert.analyse(pulse_ir(), None, 1)
    assert exc.__name__ in str(err.value)
    assert marker in str(err.value)


def test_a_failure_in_the_start_key_is_a_named_conversion_error(monkeypatch):
    """The same conversion applies when the start's key cannot be computed."""

    def boom(*args, **kwargs):
        raise RecursionError("maximum recursion depth")

    monkeypatch.setattr(phase.Machine, "start", boom)
    monkeypatch.setattr(convert, "_ANALYSES", {})
    with pytest.raises(convert.ConversionError) as err:
        convert.analyse(pulse_ir(), None, 1)
    assert "RecursionError" in str(err.value)


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
    ir = make(("C4", 4, 1), ("D4", 4))
    occs = [occurrence(unit, (0, 0, 1), start, 0)]
    events = {(unit, (0, 0, s)): 0 for s in (1, 2, 3)}
    out = convert.Rewrite(ir, obs_of(occs, events), ["v + 1"]).run()
    (variant,) = {p["phrase"] for p in out["tracks"][0]["plays"]}
    steps = convert._steps(out["phrases"][variant])  # pylint: disable=protected-access
    assert [k for k, e in enumerate(steps) if e.get("command") == [16, 1]] == written


@pytest.mark.parametrize("flags,cleared,driven", [(0x50, 0x50, True), (0x60, 0x50, False)])
def test_sidwizard_vibrato_bytes_become_freq_expressions_and_are_cleared(flags, cleared, driven):
    ir = sw_ir(("C4", 8, 1), ("-", 4), ("-", 4))
    ir["records"]["ins"][1].update(vibrato=0x33, vibdelay=4, flags=flags)
    ir["phrases"]["p1"][2]["command"] = [0x260, 5]
    want = regs(ir, 120)
    out = convert.convert(ir, cache=None, workers=1)
    ins = out["records"]["ins"][1]
    assert (ins["vibrato"], ins["vibdelay"], ins["flags"]) == (0, 0, cleared)
    assert bool(ins.get("freq")) == driven
    assert np.array_equal(regs(out, 120), want)
    assert generic(out, len(want))


@pytest.mark.parametrize("row,af", [(0, 12), (2, 1)])
def test_defmon_fixed_pitch_offset_becomes_its_rows_freq_expression(row, af):
    ir = dm_af()
    ir["records"]["sidtab"][row]["af"] = af
    with pytest.raises(SongFormatError, match="no shared-vocabulary form"):
        document.write(ir)
    want = regs(ir, 120)
    out = convert.convert(ir, cache=None, workers=1)
    rows = [r for r in out["records"]["sidtab"].values() if "xf" in r]
    assert [out["exprs"][r["xf"]] for r in rows] == [f"S[N + {24 + af}]"]
    assert [r["af"] for r in rows] == [0x80]
    assert np.array_equal(regs(out, 120), want)
    assert generic(out, len(want))
    assert not np.array_equal(regs(dm_af(), 120), want)


def test_a_song_that_ends_is_written_to_its_end():
    ir = dm_af()
    ir["records"]["sidtab"][0]["af"] = 12
    out = convert.convert(ir, cache=None, workers=1)
    with pytest.raises(reach.ReachabilityError):
        reach.recurrence(SongPlayer(out), 2000)
    native = document.native_state(out, 2000)
    assert native.ticks == sum(e.get("steps", 1) for e in out["phrases"]["p1"])
    text = document.write(out)
    assert text == document.write(out, native) and text.endswith("  - end\n")


# ---- defMON's ACID accumulator and its offset cp -----------------------------------------


def acid_ir(cp: int):
    """:func:`dm_af` with two ACID programs on layer B: one sets the accumulator (and offset
    ``cp``) and then steps it down, looping; one steps it up and freezes."""
    ir = dm_af()
    st = ir["records"]["sidtab"]
    st[4] = {"dl": 0, "next": 5, "re": 0xF7, "fv": 16, "acid": 0x4000, "cp": cp}
    st[5] = {"dl": 0, "next": 6, "acid": 0xC200}
    st[6] = {"dl": 0, "next": 5, "acid": 0xC200}
    st[7] = {"dl": 0xF0, "next": 8, "fv": 32, "acid": 0x8A00}
    st[8] = {"dl": 0x80}
    st[0].update(pw=8, ps=0)
    steps = ir["phrases"]["p1"]
    steps[0]["command"], steps[2]["command"] = [1, 4], [1, 7]
    return ir


@pytest.fixture(name="acid", scope="module")
def acid_fixture():
    """The conversion of :func:`acid_ir` with an offset that wraps the cutoff byte past $FF."""
    return convert.analyse(acid_ir(0xD0), cache=None, workers=1)


def test_acid_starts_carry_nothing_in_and_pulse_is_proved_under_the_chip_invariants(acid):
    an, seen, pulse = acid.analysis, set(), []
    for occ in acid.obs.occurrences:
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
    cuts = [r for r in acid.out["records"]["sidtab"].values() if "xc" in r]
    assert cuts and all(r["cp"] == r["acid"] == -1 for r in cuts)
    assert np.array_equal(regs(acid.out, 400), want)
    assert generic(acid.out, len(want))


def test_an_acid_sweep_without_an_offset_reads_the_accumulator_and_its_low_byte_only():
    out = convert.convert(acid_ir(0), cache=None, workers=1)
    cuts = [G.parse(x) for x in out["exprs"].values() if "v" in x]
    assert len(cuts) == 2
    assert all({int(m.args[1]) for m in e.atoms(sympy.Mod)} == {256, 32768} for e in cuts)
    assert sorted(sympy.count_ops(e) for e in cuts) == [11, 23]


def test_an_offset_outside_the_acid_program_fails_naming_its_row():
    ir = acid_ir(0)
    ir["records"]["sidtab"][2]["cp"] = 0x10
    with pytest.raises(SongFormatError, match=r"ctrl: row \{.*'cp': 16.*\} has no shared"):
        generic(convert.convert(ir, cache=None, workers=1), 400)


SUB = "(v % 32768 + 65535)*(v % 32768 >= 1) + 767*(v % 32768 < 1)"


@pytest.mark.parametrize("sub", [1, 0])
def test_the_acid_direction_through_init_is_the_inits_cut_expression(sub):
    ir = dm_af()
    ir["records"]["ginit"][0]["sub"] = sub
    want = regs(ir, 800)
    assert set(want[:, 22]) == ({2, 3} if sub else {2})
    conv = convert.analyse(ir, cache=None, workers=1)
    (init,) = [o for o in conv.obs.occurrences if o.hook == INIT]
    assert (init.unit, init.tick, init.label, init.v_in, init.start["sub"]) == (
        "acid B",
        0,
        (),
        0,
        sub,
    )
    assert np.array_equal(regs(conv.out, 800), want)
    assert generic(conv.out, len(want))
    assert conv.out["records"]["ginit"][0] == ({"sub": 0, "xc": 1} if sub else {"sub": 0})
    assert conv.out["exprs"].get(1) == (SUB if sub else None)
    with pytest.raises(SongFormatError, match="ginit: row .* no shared-vocabulary form"):
        document.write({**ir, "records": {**ir["records"], "ginit": {0: {"sub": 1}}}})


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
    out, rep = bytearray(raw), defmon.Replay(mem)
    for row, extra in columns.items():
        eff = defmon.resolve_row(rep, row)
        addr = rep.word(defmon.SIDTAB_LO, defmon.SIDTAB_HI, eff)
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
    ir = defmon.DefmonLift.from_sid(raw, expressions=False).run()
    assert {r.get("cp") for r in ir["records"]["sidtab"].values()} == {None, 5, 0xD0}
    assert vm_mismatch(ir, native) is None


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


def test_the_folded_step_is_the_replays_add_with_carry_for_every_state_it_reaches():
    def claim(z, after, wrap=True):
        stored, out = replay(z, wrap)
        same = z3.And((after["acc"] - 256 * z["cp"]) & 0xFFFF == stored, after["ghost"] == out)
        return z3.Implies(z3.ULE((z["acc"] - 256 * z["cp"]) & 0xFFFF, 0x7FFF), same)

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


def test_a_derivation_is_cached_only_for_the_code_that_derived_it(tmp_path, monkeypatch):
    ir, start, calls = gt_ir(("C4", 4, 1)), ("start",), []

    def derived(key):
        calls.append(key)
        return len(calls)

    monkeypatch.setattr(convert, "_analysis", lambda _ir, _key: SimpleNamespace(derived=derived))

    def derive(identity):
        monkeypatch.setattr(cache, "code_identity", lambda: identity)
        return convert.derive_task(ir, convert.ir_key(ir), start, tmp_path)

    assert [derive("a"), derive("a"), derive("b"), derive("a")] == [1, 1, 2, 1]
    assert calls == [start, start] and len(list((tmp_path / "derived").glob("*.pkl"))) == 2


def test_the_code_identity_covers_the_import_closure_and_the_playroutine_library():
    files, versions = cache.code_sources("unitracker.modfit")
    names = {"/".join(p.parts[-2:]) for p in files}
    assert {"modfit/fit.py", "modfit/specs.yaml", "playroutines/goattracker2.yaml"} <= names
    assert {"player/reach.py", "playroutine/expr.py", "playroutine/library.py"} <= names
    assert {"events/lifetime.py", "player/trace.py"} <= names and "document/writer.py" not in names
    assert any(v.startswith("unitracker-grammar==") for v in versions)
    files, versions = cache.code_sources("unitracker.optimise")
    assert "document/writer.py" in {"/".join(p.parts[-2:]) for p in files}
    assert any(v.startswith("unitracker-player==") for v in versions)
    assert cache.code_identity() != cache.code_identity("unitracker.optimise")
