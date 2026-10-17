"""Lift-time conversion: record and event sites, clones, phrase variants and scaled units."""

import random

import numpy as np
import pytest
import sympy

from unitracker import songio
from unitracker.modfit import convert
from unitracker.modfit import grammar as G
from unitracker.modulation import Occurrence
from unitracker.vm import SongPlayer

V, PHI = G.V, G.PHI


def gt_ir(phrase: str, ins: str = "{sr: 240, ctrl: [{ctrl: 65, note: +0}, end], gt: 2, fw: 9}"):
    """A one-voice goattracker2 song read from v1.5 text, the other voices silent."""
    return songio.loads(f"""
unitracker: 1.5
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
""")


def regs(ir, ticks):
    return SongPlayer(ir).render_grid(ticks).astype(int)


# ---- scaled units -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "e",
    [
        sympy.Mod(V + 3, 256),
        sympy.Mod(V - 7 * G.ge(PHI, 4) + 2 * PHI, 256),
        G.ge(V, 100) * 5 + G.lt(V, 30) * sympy.Mod(V + 1, 256),
        sympy.Abs(V - 128) + sympy.Min(V, 200) - sympy.Max(V, 50) + G.eq(V, 7),
        sympy.floor((V + 9) / 4) + 3 * sympy.Mod(V, 16),
    ],
)
def test_scaled_reads_v_as_the_scaled_cell(e):
    f, g = G.compiled(e), G.compiled(G.scaled(e, 8))
    rng = random.Random(3)
    for _ in range(300):
        c, phi = rng.randrange(0, 256), rng.randrange(0, 40)
        assert g(8 * c, phi, 0) == 8 * f(c, phi, 0)


def test_scaled_text_and_forms_it_refuses():
    assert G.text(G.scaled(sympy.Mod(V + 3, 256), 8)) == "(v + 24) % 2048"
    assert G.text(G.scaled(4 * PHI + 128, 8)) == "32*φ + 1024"
    assert G.scaled(V * PHI, 1) == V * PHI
    with pytest.raises(ValueError, match="not linear"):
        G.scaled(V * V, 8)


def test_a_filter_sweep_is_written_in_cutoff_units():
    ins = "{sr: 240, ctrl: [{ctrl: 65, note: +0}, end], gt: 2, fw: 9, filter: [{cut: 1024, "
    ir = gt_ir("[C4.i1*4, D4*4]", ins + "resfilt: 193, mode: 16}, end]}")
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


def test_the_player_floors_a_scaled_expression_to_the_cell():
    ins = "{sr: 240, ctrl: [{ctrl: 65, note: +0}, end], gt: 2, fw: 9, filter: [{cut: "
    ir = gt_ir("[C4.i1*8]", ins + "'1024 + 12*min(φ, 6)', resfilt: 193, mode: 16}, end]}")
    cut = regs(ir, 40)[:, 22]
    run = [int(x) for x in cut[np.argmax(cut > 0) :][:8]]
    assert run == [(1024 + 12 * min(phi, 6)) // 8 for phi in range(8)]


def test_a_unit_with_no_scaled_form_is_refused():
    derived = type("D", (), {"order": type("O", (), {"kind": "time"})()})()
    assert convert.checked(derived, sympy.Integer(1), V + PHI, 8) == "v + 8*φ"
    with pytest.raises(convert.ConversionError, match="no scale 8 form"):
        convert.checked(derived, sympy.Integer(1), V * V + PHI, 8)
    with pytest.raises(convert.ConversionError, match="not one per firing"):
        convert.checked(derived, sympy.Integer(2), V + PHI, 8)


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


def test_an_event_command_becomes_a_freq_expression_and_keeps_its_legato():
    ir = gt_ir("[C4.i1*4, D4*4]")
    ir["phrases"]["p1"][1]["commands"] = [[17, 0], [3, 1]]
    ir["records"]["speedtab"] = {1: {"l": 0, "r": 64}}
    want = regs(ir, 60)
    out = convert.convert(ir, cache=None, workers=1)
    (variant,) = [p["phrase"] for p in out["tracks"][0]["plays"]]
    assert out["phrases"][variant][1]["commands"] == [[17, 0], [16, 1]]
    assert "min(F(N), (v + 64) % 65536)" in out["exprs"][1]
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
