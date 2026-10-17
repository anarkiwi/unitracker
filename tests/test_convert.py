"""Lift-time conversion: record and event sites, clones, phrase variants and chip units."""

import numpy as np
import pytest
import sympy

from unitracker import songio, xexpr
from unitracker.modfit import convert
from unitracker.modfit import grammar as G
from unitracker.modulation import Occurrence
from unitracker.vm import SongPlayer

V, PHI = G.V, G.PHI
# A plain gated instrument whose hard restart turns the gate off two ticks ahead.
INS = (
    "{sr: 240, ctrl: [{ctrl: 65, note: +0}, end], "
    "restart: [{hold: 2, gate: 0, ad: 15, sr: 0}, {ctrl: 9, gate: 1}, end]"
)


def gt_ir(phrase: str, ins: str = INS + "}"):
    """A one-voice goattracker2 song read from v1.17 text, the other voices silent."""
    text = f"""
unitracker: 1.17
playroutine: {{ref: goattracker2, constants: {{deftempo: 6}}}}
song:
  - def:
      ins:
        i1: {ins}
      phrase:
        p1: {phrase}
        p2: [off*8]
    play: [p1, p2, p2]
loop: ~
"""
    return songio.loads(text)


def regs(ir, ticks):
    return SongPlayer(ir).render_grid(ticks).astype(int)


# ---- chip units -------------------------------------------------------------------------


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


def test_a_filter_sweep_is_written_in_cutoff_units():
    ins = INS + ", filter: [{cut: 1024, "
    ir = gt_ir("[C4.i1*4, D4*4]", ins + "res: 12, route: 1, mode: 16}, end]}")
    ft = ir["records"]["filttab"]
    ft[1]["next"], ft[2], ft[3] = 2, {"l": 20, "r": 3, "next": 3}, {"l": 10, "r": 250}
    want = regs(ir, 200)
    assert len(set(want[:, 22])) > 20
    out = convert.convert(ir, cache=None, workers=1)
    assert list(out["exprs"].values()) == ["-48*min(10, max(0, φ - 20)) + 24*min(20, φ) + 1024"]
    text = songio.dumps(out)
    assert np.array_equal(regs(songio.loads(text), 200), want)
    one_cell_off = text.replace("24*min(20, φ)", "32*min(20, φ)")
    assert not np.array_equal(regs(songio.loads(one_cell_off), 200), want)


def test_a_looping_sweep_with_a_drift_is_written_with_floor_division():
    ins = INS + ", filter: [{cut: 1024, "
    ir = gt_ir("[C4.i1*16, D4*16]", ins + "res: 12, route: 1, mode: 16}, end]}")
    ft = ir["records"]["filttab"]
    ft[1]["next"], ft[2], ft[3] = 2, {"l": 20, "r": 3, "next": 3}, {"l": 10, "r": 255, "next": 2}
    want = regs(ir, 400)
    out = convert.convert(ir, cache=None, workers=1)
    (text,) = out["exprs"].values()
    assert "φ // 30" in text
    written = songio.dumps(out)
    assert np.array_equal(regs(songio.loads(written), 400), want)
    slower = written.replace("φ // 30", "φ // 31")
    assert not np.array_equal(regs(songio.loads(slower), 400), want)


def test_the_player_floors_a_scaled_expression_to_the_cell():
    ins = INS + ", filter: [{cut: "
    ir = gt_ir("[C4.i1*8]", ins + "'1024 + 12*min(φ, 6)', res: 12, route: 1, mode: 16}, end]}")
    cut = regs(ir, 40)[:, 22]
    run = [int(x) for x in cut[np.argmax(cut > 0) :][:8]]
    assert run == [(1024 + 12 * min(phi, 6)) // 8 for phi in range(8)]


# ---- rewriting from occurrences ---------------------------------------------------------


def occurrence(unit, instance, start, index, label=()):
    occ = Occurrence(unit, instance[0], 0, label, start, ())
    occ.instance, occ.index = instance, index
    return occ


def test_a_record_with_two_expressions_is_cloned_into_a_phrase_variant():
    ir = gt_ir("[C4.i1*4, D4*4]")
    events = ir["phrases"]["p1"]
    events[0]["command"], events[1]["command"] = [9, 1], [9, 1]
    ir["records"]["pulsetab"] = {1: {"l": 0x88, "r": 0, "next": 0}}
    ramp, flat = "1024 + 16*min(4, φ)", "3000"
    occs = [
        occurrence("pulse", (0, 0, 0), {"pptr": 1}, 0, (1,)),
        occurrence("pulse", (0, 0, 4), {"pptr": 1}, 1, (1,)),
    ]
    obs = {"occurrences": occs, "events": {}, "plays": [[0], [0], [0]]}
    out = convert.Rewrite(ir, obs, [ramp, flat]).run()
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


def test_a_song_with_expressions_keeps_them_and_numbers_new_ones_after():
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
    assert np.array_equal(regs(out, 80), want)


def test_an_event_command_becomes_a_freq_expression_and_keeps_its_legato():
    ir = gt_ir("[C4.i1*4, D4*4]")
    ir["phrases"]["p1"][1]["commands"] = [[17, 0], [3, 1]]
    ir["records"]["speedtab"] = {1: {"l": 0, "r": 64}}
    want = regs(ir, 60)
    out = convert.convert(ir, cache=None, workers=1)
    (variant,) = [p["phrase"] for p in out["tracks"][0]["plays"]]
    assert out["phrases"][variant][1]["commands"] == [[17, 0], [16, 1]]
    ftab = songio.library("goattracker2")["tables"]["ftab"]
    glide = xexpr.compile_expr(out["exprs"][1], {"_F": lambda k: ftab[k], "_T": {}})
    v, offsets = (ftab[48] - ftab[50]) % 65536, []
    for phi in range(40):
        v = glide(v, phi, 50)
        offsets.append(v)
    rise = (ftab[50] - ftab[48] + 63) // 64
    assert (
        offsets[rise - 1 :] == [0] * (41 - rise)
        and offsets[0] == (ftab[48] - ftab[50] + 64) % 65536
    )
    assert np.array_equal(regs(out, 60), want)
    out["phrases"][variant][1]["command"] = out["phrases"][variant][1].pop("commands")[1]
    assert not np.array_equal(regs(out, 60), want)


def test_a_started_event_without_a_command_gets_the_identity_and_legato():
    ir = gt_ir("[C4.i1*4, D4*4]")
    occs = [occurrence("command", (0, 0, 4), {"legato": 1}, 0, (0, 0))]
    obs = {"occurrences": occs, "events": {("command", (0, 0, 4)): 0}, "plays": [[0], [0], [0]]}
    out = convert.Rewrite(ir, obs, [None]).run()
    assert out["exprs"] == {} and out["tracks"][0]["plays"][0]["phrase"] == "p1"
    out = convert.Rewrite(ir, obs, ["F(N)"]).run()
    (variant,) = [p["phrase"] for p in out["tracks"][0]["plays"]]
    assert out["phrases"][variant][1]["commands"] == [[17, 0], [16, 1]]
    assert out["exprs"] == {1: "F(N)"}


def test_an_event_names_at_most_two_commands():
    event = {}
    convert._set_cmds(event, [[1, 2]])  # pylint: disable=protected-access
    assert event == {"command": [1, 2]}
    with pytest.raises(convert.ConversionError, match="three commands"):
        convert._set_cmds(event, [[1, 2], [3, 4], [5, 6]])  # pylint: disable=protected-access


def test_a_clone_without_a_naming_event_is_refused():
    ir = gt_ir("[C4.i1*4, D4*4]")
    ir["records"]["pulsetab"] = {1: {"l": 0x88, "r": 0, "next": 0}}
    occs = [
        occurrence("pulse", (0, 0, 0), {"pptr": 1}, 0, (1,)),
        occurrence("pulse", (0, 0, 4), {"pptr": 1}, 1, (1,)),
    ]
    obs = {"occurrences": occs, "events": {}, "plays": [[0], [0], [0]]}
    with pytest.raises(convert.ConversionError, match="does not name"):
        convert.Rewrite(ir, obs, ["1024 + φ", "3000 + φ"]).run()
    occs[1].instance = None
    with pytest.raises(convert.ConversionError, match="no event selects"):
        convert.Rewrite(ir, obs, ["1024 + φ", "3000 + φ"]).run()


def test_a_referenced_program_no_start_proves_is_a_named_lift_error():
    ir = gt_ir("[C4.i1*4, D4*4]")
    ir["phrases"]["p1"][0]["command"] = [9, 1]
    ir["records"]["pulsetab"] = {1: {"l": 0x88, "r": 0, "next": 2}, 2: {"l": 16, "r": 4}}
    obs = {"occurrences": [], "events": {}, "plays": [[0], [0], [0]]}
    with pytest.raises(convert.ConversionError, match=r"pulsetab program 1: row 1 .*'l': 16"):
        convert.Rewrite(ir, obs, []).run()
    ir["records"]["pulsetab"][2] = {"l": 0x89, "r": 0}
    assert convert.Rewrite(ir, obs, []).run()["records"]["pulsetab"][2]["l"] == 0x89


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


def sw_ir(phrase: str):
    """A one-voice sidwizard song read from v1.17 text, the other voices silent."""
    text = f"""
unitracker: 1.17
playroutine: {{ref: sidwizard}}
song:
  - def:
      ins:
        i1: {{sr: 240, ctrl: [{{ctrl: 65, note: +0}}, end]}}
      phrase:
        p1: {phrase}
        p2: [off*8]
    play: [p1, p2, p2]
loop: ~
"""
    return songio.loads(text)


@pytest.mark.parametrize(
    "ref,unit,falls", [("goattracker2", "command", True), ("sidwizard", "pitch", False)]
)
def test_only_a_selector_naming_its_site_falls_back_to_it(ref, unit, falls):
    units = {u["name"]: u for u in songio.library(ref)["modulators"]["units"]}
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
    obs = {"occurrences": occs, "events": events, "plays": [[0], [0], [0]]}
    out = convert.Rewrite(ir, obs, ["v + 1"]).run()
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
    back = songio.loads(text)
    assert np.array_equal(regs(back, 120), want)


DM_VOICE = "{}"
DM_AF = f"""
unitracker: 1.17
playroutine: {{ref: defmon}}
song:
  - def:
      vinit: {{0: {DM_VOICE}, 1: {DM_VOICE}, 2: {DM_VOICE}}}
      ginit: {{0: {{}}}}
      ins:
        i1: {{ctrl: [{{ctrl: 65, hold: 4}}, {{ctrl: 64}}, end]}}
        i2: {{ctrl: [{{ctrl: 65, hold: 3}}, {{ctrl: 64}}, end]}}
      phrase:
        p1: [C4.i1*12, E4*12, G4.i2*12, A4.i1*12]
    play: [p1, p1, p1]
loop: ~
"""


@pytest.mark.parametrize("row,af", [(0, 12), (2, 1)])
def test_defmon_fixed_pitch_offset_becomes_its_rows_freq_expression(row, af):
    ir = songio.loads(DM_AF)
    ir["records"]["sidtab"][row]["af"] = af
    with pytest.raises(songio.SongFormatError, match="no shared-vocabulary form"):
        songio.dumps(ir)
    want = regs(ir, 120)
    out = convert.convert(ir, cache=None, workers=1)
    text = songio.dumps(out)
    assert f"freq: 'S[N + {24 + af}]'" in text and "af" not in text
    back = songio.loads(text)
    assert np.array_equal(regs(back, 120), want)
    assert {r.get("af", -1) for r in back["records"]["sidtab"].values()} == {-1, 0x80}
    plain = regs(songio.loads(DM_AF), 120)
    assert not np.array_equal(plain, want)
