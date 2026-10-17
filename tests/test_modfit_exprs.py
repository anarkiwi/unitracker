"""Modulators as phase and value expressions: grammar, derivation, proofs and replay."""

import random
import re
import types

import numpy as np
import pytest
import sympy
import z3

from unitracker.modfit import grammar as G
from unitracker.modfit.cli import main
from unitracker.modfit.exprs import (
    Analysis,
    Names,
    analyse,
    derive_task,
    document,
    driven_task,
    dump,
    keys_task,
    load,
    native_grid,
    observe_task,
)
from unitracker.modfit.exprs_report import (
    expression_document,
    failures,
    leftovers,
    modulator_lines,
)
from unitracker.modfit.inherit import guarded, inherit
from unitracker.modfit.form import Form
from unitracker.modfit.orbit import deltas
from unitracker.modfit.phase import Machine, derive, unproved_functions
from unitracker.modfit.replay import Replay
from unitracker.songio import library

PHI, V, N = G.PHI, G.V, G.N
GT_FREQ, DM_FREQ = {"word": "ftab"}, {"hi": "note_hi", "lo": "note_lo"}
TICKN = [{"run": {"record": "tickn", "index": "command", "field": "do"}}]


def from_text(text: str):
    """A Python function of ``(v, phi, N)`` parsed from grammar text, brackets innermost first."""
    src = text.replace("φ", "phi")
    inner = re.compile(r"\[([^\[\]]*)\]")
    while inner.search(src):
        src = inner.sub(lambda m: f"(1 if {m.group(1)} else 0)", src)
    return eval(f"lambda v, phi, N: {src}")  # pylint: disable=eval-used


def equal(a, b, phi_from=0, bounds=((0, 1 << 16),)) -> bool:
    """True when two expressions over ``v`` and ``φ`` agree everywhere (z3)."""
    env = {"v": z3.BitVec("v", G.BITS), "φ": z3.BitVec("φ", G.BITS), "N": z3.BitVec("N", G.BITS)}
    s = z3.Solver()
    s.add(
        env["φ"] >= phi_from, env["φ"] < 1 << 20, env["v"] >= bounds[0][0], env["v"] < bounds[0][1]
    )
    s.add(G.to_z3(a, env) != G.to_z3(b, env))
    return s.check() == z3.unsat


@pytest.fixture(name="mw", scope="module")
def mw_fixture(metal_warrior):
    return load(metal_warrior)


@pytest.fixture(name="csillag_song", scope="module")
def csillag_fixture(csillag):
    return load(csillag)


# ---- grammar ----------------------------------------------------------------------------


def test_brackets_have_one_canonical_form():
    assert G.lt(PHI, 64) == 1 - G.ge(PHI, 64)
    assert G.ge(2 * PHI, 5) == G.ge(PHI, 3)
    assert G.eq(2 * PHI, 5) == 0
    assert G.Iv(sympy.Ne(PHI, 3)) == 1 - G.eq(PHI, 3)
    assert G.text(G.ge(V, G.freq(N) + 1)) == "[v > F(N)]"
    assert G.text(G.polar(G.lt(PHI, 64) * 5 + 2)) == "5*[φ < 64] + 2"


def test_text_parses_back_to_the_same_values():
    exprs = [
        -sympy.Mod(V + 5, 65536) + 4088 * G.ge(V, 4096),
        sympy.Mod(V - 32, 65536) * G.lt(PHI, 3) + G.shr(V, 8),
        16 * sympy.Abs(sympy.Mod(PHI - 32, 128) - 64) + 512,
        sympy.Min(PHI, 64) - sympy.Max(0, sympy.Min(PHI - 65, 64)) + G.eq(V, 0),
        -sympy.floor((V - 7) / 4) + G.BitAnd(V, 6),
    ]
    rng = random.Random(1)
    for e in exprs:
        parsed, compiled = from_text(G.text(e)), G.compiled(e)
        for _ in range(200):
            v, phi = rng.randrange(-70000, 70000), rng.randrange(0, 1000)
            assert parsed(v, phi, 0) == compiled(v, phi, 0)


def test_z3_division_and_modulo_are_floored():
    env = {"v": z3.BitVecVal(-5, G.BITS)}
    for e, want in ((sympy.Mod(V, 3), 1), (sympy.floor(V / 2), -3), (sympy.Abs(V), 5)):
        assert z3.simplify(G.to_z3(e, env)).as_signed_long() == want == G.compiled(e)(-5, 0, 0)


def test_display_is_shorter_and_equivalent():
    u = sympy.Mod(V - 513, 65536)
    e = G.canon(
        G.eq(PHI, 0) * 16384
        + G.eq(PHI, 1) * sympy.Mod(V - 1, 65536)
        + G.ge(PHI, 2) * (G.ge(u, 32768) * (512 + sympy.Mod(V - 1, 256)) + (1 - G.ge(u, 32768)) * u)
    )
    shown = G.polar(e)
    assert G.rank(shown) < G.rank(e)
    assert equal(e, shown)
    assert G.text(shown).startswith("16384*[φ == 0]")


def test_nested_brackets_split_over_the_domain():
    signed = V - 65536 * G.ge(V, 32768)
    e = G.canon(G.eq(signed, 0) * 7 + G.ge(signed, 1) * V)
    shown = G.polar(e, (("v", 0, 65535),))
    assert "[v >= 32768]]" not in G.text(shown) and equal(e, shown)


def test_shortest_rejects_fractions():
    assert not G.valid(sympy.Rational(1, 2) * PHI)
    assert G.shortest([sympy.Rational(1, 2) * PHI, PHI + PHI]) == 2 * PHI


# ---- derivation and proof ---------------------------------------------------------------


def test_metal_warrior_pulse_ping_pong_closed_form(mw):
    machine = Machine(mw.ir, {"cell": "pulse", "fire": ["pulse_exec"]})
    d = derive(machine, machine.start({"pptr": 1, "ptime": 0}), None, GT_FREQ)
    assert d.exact and d.order.kind == "time" and (d.order.prefix, d.order.period) == (1, 128)
    assert G.text(d.closed) == "16*abs((φ - 32) % 128 - 64) + 512"
    asked = 1024 + 16 * (32 - sympy.Abs(sympy.Mod(PHI + 32, 128) - 64))
    assert equal(d.closed, asked)
    assert d.update[0] == 1


def test_goattracker_slide_and_toneporta():
    song = {"playroutine": library("goattracker2"), "records": {"speedtab": {1: {"l": 0, "r": 32}}}}
    machine = Machine(song, {"cell": "freq", "fire": TICKN})
    base = {"vibtime": 0, "vibdelay": 0, "lastnote": 40, "note": "N"}
    slide = derive(machine, machine.start({**base, "command": 1, "cmddata": 1}), None, GT_FREQ)
    assert slide.canonical == "0; (v + 32) % 65536" and slide.exact
    down = derive(machine, machine.start({**base, "command": 2, "cmddata": 1}), None, GT_FREQ)
    assert down.canonical == "0; (v - 32) % 65536"
    porta = derive(machine, machine.start({**base, "command": 3, "cmddata": 1}), None, GT_FREQ)
    assert porta.exact and G.freq(N) in porta.value.atoms(G.FREQ)
    target = 1000
    toward = G.compiled(porta.value, ("v", "phi", "N"))

    def ftab(k):
        return target if k == 7 else 0

    assert [toward(v, 0, 7, F=ftab) for v in (900, 990, 1000, 1010, 1100)] == [
        932,
        1000,
        1000,
        1000,
        1068,
    ]


def test_goattracker_vibrato_is_periodic_in_elapsed_firings():
    song = {"playroutine": library("goattracker2"), "records": {"speedtab": {1: {"l": 4, "r": 32}}}}
    machine = Machine(song, {"cell": "freq", "fire": TICKN})
    d = derive(machine, machine.start({"command": 4, "cmddata": 1, "vibtime": 0, "vibdelay": 0,
                                       "lastnote": 40, "note": "N"}), None, GT_FREQ)  # fmt: skip
    assert d.exact and (d.order.period, d.phase) == (12, 1)
    f = G.compiled(d.value)
    v, phi, trace = 20000, 0, []
    for _ in range(24):
        v, phi = f(v, phi, 0), phi + 1
        trace.append(v - 20000)
    assert trace[:12] == [32, 64, 96, 64, 32, 0, -32, -64, -96, -64, -32, 0] == trace[12:]


def test_defmon_pulse_bounce_counts_turns():
    song = {"playroutine": library("defmon"), "records": {}}
    machine = Machine(song, {"cell": "pw", "fire": ["pulse"]})
    d = derive(machine, machine.start({"ps": 0x85, "fc": 0}), None, DM_FREQ)
    assert d.exact and d.order.kind == "turn" and d.order.period == 2
    assert V in d.phase.free_symbols and PHI in d.value.free_symbols
    pr = library("defmon")
    want = deltas(Form(pr), pr["procs"]["pulse"], {"pw": 4000, "ps": 0x85, "fc": 0}, "pw", 2000)
    f, a = G.compiled(d.value), G.compiled(d.phase)
    v, phi, got = 4000, 0, []
    for _ in range(2000):
        v, phi = f(v, phi, 0), phi + a(v, phi, 0)
        got.append(v)
    assert np.array_equal(np.diff([4000] + got), want) and phi > 2


def test_csillag_pulse_program_is_one_modulator(csillag_song):
    machine = Machine(csillag_song.ir, {"cell": "pwhi:pwlo", "fire": ["tick_pw"]})
    d = derive(
        machine,
        machine.start({"ppos": 1, "pwcnt": 0, "ci": 1}),
        None,
        {"hi": "freqhi", "lo": "freqlo"},
    )
    assert d.exact and G.text(d.closed) == "2*min(64, φ) - 8*min(64, max(0, φ - 65)) + 2048"
    assert equal(
        d.closed, 2048 + 2 * sympy.Min(PHI, 64) - 8 * sympy.Max(0, sympy.Min(PHI - 65, 64))
    )


def test_named_tables_equal_the_functions_they_replace(csillag_song):
    spec = Analysis(csillag_song).spec
    assert not unproved_functions(csillag_song.ir, spec)
    wrong = {**spec, "functions": {"fmlo": "W[fyc(a, d)] & 0x7F"}}
    assert unproved_functions(csillag_song.ir, wrong)


def test_inherited_acid_direction_gives_two_expressions(crosseyed):
    song = load(crosseyed)
    machine = Machine(song.ir, {"cell": "acc", "fire": ["layer_b", "acid"]})
    base = {"rowB": 10, "cntB": 0, "astep": 0}
    one = derive(machine, machine.start({**base, "sub": 1}), None, DM_FREQ)
    zero = derive(machine, machine.start({**base, "sub": 0}), None, DM_FREQ)
    assert one.exact and zero.exact and one.canonical != zero.canonical


def test_unknown_cells_are_reported():
    song = {"playroutine": library("defmon"), "records": {}}
    machine = Machine(song, {"cell": "pw", "fire": ["pulse"]})
    with pytest.raises(ValueError, match="no start value"):
        machine.start({"ps": 0x85})


# ---- replay and report ------------------------------------------------------------------


def test_driven_replay_matches_and_a_wrong_expression_does_not(mw):
    an = Analysis(mw)
    ticks = 700
    native = native_grid(mw, ticks)
    good = Replay(mw.ir, an.units, an.derive, override=lambda occ: True, note=an.note)
    assert np.array_equal(np.asarray(good.run(ticks), dtype=np.uint8), native)
    assert good.occurrences and not [o for o in good.occurrences if o.mismatch or o.crossed]

    def wrong(unit, occ):
        res = an.derive(unit, occ)
        if res is None or unit["name"] != "pulse":
            return res
        broken = type(res)(res.derived, res.start, res.v0, res.stepper)
        broken.step = lambda v, phi, n: ((v + 1) % 4096, phi + 1)
        return broken

    bad = Replay(mw.ir, an.units, wrong, override=lambda occ: True, note=an.note)
    grid = np.asarray(bad.run(ticks), dtype=np.uint8)
    assert not np.array_equal(grid, native)
    assert any(o.mismatch for o in bad.occurrences if o.unit == "pulse")


def test_document_roundtrip_and_names(mw):
    assert dump(document(mw.text)) == mw.text
    names = Names(mw)
    assert names.command(1, 1) == "!mod:@m1" and names.instrument(4) == "i4"
    assert names.origin("!mod:@m2") == "mod m2: {reg: freq, step: 32, to: note}"
    assert ("filter rows carrying cut", 2) in leftovers(mw)


def test_analyse_and_expression_document(mw):
    rep = analyse(mw.path, 900)
    assert rep.grid_equal and not rep.unproved_functions
    labels = {m.label: m for m in rep.modulators}
    assert labels["i1 pulse"].canonical == "1; 16*abs((φ - 32) % 128 - 64) + 512"
    assert labels["!mod:@m1"].canonical.endswith("% 65536")
    text, defs = expression_document(rep)
    assert "x:" in text and "pulse: x" in text and "!mod:@x" in text and "m1:" not in text
    assert all("v" in d for d in defs.values())


def test_cli_writes_the_report(tmp_path, crosseyed):
    out = tmp_path / "exprs.txt"
    main(["--out", str(out), "exprs", str(crosseyed), "--ticks", "300", "--before", str(tmp_path),
          "--after", str(tmp_path), "--workers", "2"])  # fmt: skip
    text = out.read_text()
    assert "## Crosseyed (defmon), 300 ticks" in text and "## Tokens" in text
    assert "t2 pitch" in text and "(v - 139) % 65536" in text


def test_csillag_slide_reads_the_speed_table(csillag_song):
    rep = analyse(csillag_song.path, 700)
    labels = {m.label: m for m in rep.modulators}
    assert rep.grid_equal
    assert labels["!mod:@m1"].canonical == "0; (v + W[N + 25]) % 65536"
    assert labels["i2 pulse"].canonical == "1; 2*min(64, φ) - 8*min(64, max(0, φ - 65)) + 2048"
    assert "i2 vibrato" in rep.quiet


# ---- inherited start values -------------------------------------------------------------


def test_a_counted_start_cell_is_refused_as_an_input(mw):
    machine = Machine(mw.ir, {"cell": "pulse", "fire": ["pulse_exec"]})
    start = machine.start({"pptr": 1, "ptime": "ptime"})
    with pytest.raises(ValueError, match="ptime counts"):
        guarded(machine, {"ptime"}).explore(start)
    d = derive(guarded(machine, {"ptime"}), start, None, GT_FREQ, {"ptime": "ptime"})
    assert "counts" in d.error


def _driven(path, ticks, inputs):
    """``(grid, occurrences)`` of a tune with the given inherited expressions driven."""
    occs = observe_task(str(path), ticks, None)
    keys = {k for k, _ in keys_task(str(path), occs, None) if k is not None}
    found = {k: derive_task(str(path), k, None) for k in keys}
    live = {k: v for k, v in found.items() if not v[0].identity}
    return driven_task(str(path), ticks, live, set(inputs), 0, None, inputs)


def test_acid_direction_is_an_input_and_a_wrong_input_is_caught(crosseyed):
    rep = analyse(crosseyed, workers=4)
    acid = next(m for m in rep.modulators if m.label == "t6 acid")
    assert acid.inherited.kind == "input" and acid.inherited.cells == ["sub"]
    assert "sub" in acid.canonical and acid.proved and failures([rep]) == ["  none"]
    text = "\n".join(modulator_lines(Names(rep.song), acid))
    assert "inherited (inputs sub): proved for every value of sub" in text
    assert "reads: φ, sub (a cell read when it starts)" in text
    a, f = acid.canonical_exprs
    sub = sympy.Symbol("sub", integer=True)
    key = (acid.unit["name"], acid.label)
    native = native_grid(load(crosseyed), rep.ticks)
    grid, occs = _driven(crosseyed, rep.ticks, {key: (a, f)})
    assert np.array_equal(grid, native)
    assert not [o for o in occs if o.unit == key[0] and o.mismatch]
    _, occs = _driven(crosseyed, rep.ticks, {key: (a, f.xreplace({sub: 1 - sub}))})
    assert [o for o in occs if o.unit == key[0] and o.mismatch]


def test_a_counted_inherited_cell_is_a_phase_offset(mw):
    machine = Machine(mw.ir, {"cell": "pulse", "fire": ["pulse_exec"]})
    nodes = machine.explore(machine.start({"pptr": 1, "ptime": 0})).nodes
    same = [n for n in nodes[2:] if dict(n)["pptr"] == dict(nodes[2])["pptr"]][:3]
    unit = {"name": "pulse", "cell": "pulse"}
    live = [
        types.SimpleNamespace(start=s, derived=derive(machine, s, None, GT_FREQ), start_value=None)
        for s in same
    ]
    an = types.SimpleNamespace(machines={"pulse": machine}, freq=GT_FREQ)
    got = inherit(an, types.SimpleNamespace(unit=unit, live=live))
    assert got.kind == "phase" and got.cells == ["ptime"] and "ptime counts" in got.proof
    assert f"φ0 = -ptime + {dict(same[0])['ptime']}" in got.proof
    f = G.compiled(got.update[1], ("v", "phi", "N", "ptime"))
    for s, r in zip(same, live):
        want = G.compiled(r.derived.update[1])
        assert [f(0, phi, 0, dict(s)["ptime"]) for phi in range(40)] == [
            want(0, phi, 0) for phi in range(40)
        ]
    other = machine.start({"pptr": 3, "ptime": 5})
    off = live[:1] + [types.SimpleNamespace(start=other, derived=derive(machine, other, None, GT_FREQ),
                                            start_value=None)]  # fmt: skip
    lost = inherit(an, types.SimpleNamespace(unit=unit, live=off))
    assert lost.kind == "none" and lost.update is None and lost.reason
