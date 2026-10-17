"""Modulators as phase and value expressions: grammar, derivation, proofs and replay."""

import random
import re
import types

import numpy as np
import pytest
import sympy
import z3

from unitracker.modfit import grammar as G
from unitracker import songio
from unitracker.modfit.cli import main
from unitracker.modfit import convert
from unitracker.modfit.convert import analyse, groups
from unitracker.modfit.exprs import Analysis, Result
from unitracker.modfit.exprs_report import load, native_grid
from unitracker.modfit.inherit import DELAY, guarded, inherit
from unitracker.modfit.form import Form
from unitracker.modfit.orbit import deltas
from unitracker.modfit import chipinv
from unitracker.modfit.phase import Machine, Prover, derive, unproved_functions
from unitracker.modfit.replay import Replay
from unitracker.songio import library
from unitracker.vm import SongPlayer

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
    spec = Analysis(csillag_song.ir).spec
    assert not unproved_functions(csillag_song.ir, spec)
    wrong = {**spec, "functions": {"fmlo": "W[fyc(a, d)] & 0x7F"}}
    assert unproved_functions(csillag_song.ir, wrong)


def test_inherited_acid_direction_gives_two_expressions(crosseyed):
    song = load(crosseyed)
    machine = Machine(song.ir, {"cell": "acc", "fire": ["layer_b", "acid_step"]})
    base = {"rowB": 10, "cntB": 0, "astep": 0}
    one = derive(machine, machine.start({**base, "sub": 1}), None, DM_FREQ)
    zero = derive(machine, machine.start({**base, "sub": 0}), None, DM_FREQ)
    assert one.exact and zero.exact and one.canonical != zero.canonical


def test_unknown_cells_are_reported():
    song = {"playroutine": library("defmon"), "records": {}}
    machine = Machine(song, {"cell": "pw", "fire": ["pulse"]})
    with pytest.raises(ValueError, match="no start value"):
        machine.start({"ps": 0x85})


# ---- replay, conversion and report -----------------------------------------------------


def test_driven_replay_matches_and_a_wrong_expression_does_not(mw):
    an = Analysis(mw.ir)
    ticks = 700
    native = native_grid(mw, ticks)
    good = Replay(mw.ir, an.derive, override=lambda occ: True)
    assert np.array_equal(np.asarray(good.run(ticks), dtype=np.uint8), native)
    assert good.occurrences and not [o for o in good.occurrences if o.mismatch or o.crossed]

    def wrong(unit, occ):
        res = an.derive(unit, occ)
        if res is None or unit["name"] != "pulse":
            return res
        broken = Result(res.derived, res.start, res.v0, res.stepper)
        broken.step = lambda v, phi, n: ((v + 1) % 4096, phi + 1)
        return broken

    bad = Replay(mw.ir, wrong, override=lambda occ: True)
    grid = np.asarray(bad.run(ticks), dtype=np.uint8)
    assert not np.array_equal(grid, native)
    assert any(o.mismatch for o in bad.occurrences if o.unit == "pulse")


@pytest.fixture(name="mw_conv", scope="module")
def mw_conv_fixture(mw):
    return analyse(mw.ir, workers=4)


def test_conversion_texts_and_the_written_song(mw, mw_conv):
    texts = {t for t in mw_conv.texts if t is not None}
    assert "16*abs((φ - 32) % 128 - 64) + 512" in texts and "(v + 32) % 65536" in texts
    written = set(mw_conv.out["exprs"].values())
    assert written <= texts and {"1024", "1920"} == texts - written
    text = songio.dumps(mw_conv.out)
    assert "pw: 16*abs((φ - 32) % 128 - 64) + 512" in text and "!freq:@x" in text
    ticks = 3840
    grid = SongPlayer(songio.loads(text)).render_grid(ticks)
    assert np.array_equal(grid, native_grid(mw, ticks))


def test_cli_writes_the_report(tmp_path, metal_warrior):
    out = tmp_path / "exprs.txt"
    main(["--out", str(out), "exprs", str(metal_warrior), "--before", str(tmp_path),
          "--after", str(tmp_path), "--workers", "4"])  # fmt: skip
    text = out.read_text()
    assert "## Metal_Warrior_Title_Remix (goattracker2)" in text and "## Tokens" in text
    assert "read back from its v1.6 text, 7680 ticks are equal to" in text
    assert "written (" in text and "(v + 32) % 65536" in text
    assert "Metal_Warrior_Title_Remix: - / - / " in text


# ---- inherited start values -------------------------------------------------------------


def test_a_counted_start_cell_is_refused_as_an_input(mw):
    machine = Machine(mw.ir, {"cell": "pulse", "fire": ["pulse_exec"]})
    start = machine.start({"pptr": 1, "ptime": "ptime"})
    with pytest.raises(ValueError, match="ptime counts"):
        guarded(machine, {"ptime"}).explore(start)
    d = derive(guarded(machine, {"ptime"}), start, None, GT_FREQ, {"ptime": "ptime"})
    assert "counts" in d.error


def general_forms(conv) -> list:
    """``(inherited, live results)`` of each unit and label written with several expressions."""
    an, occs, out = conv.analysis, conv.obs["occurrences"], []
    for key, members in groups(occs).items():
        if len({conv.texts[i] for i in members} - {None}) > 1:
            starts = list(dict.fromkeys(occs[i].key for i in members))
            res = [
                Result(conv.found[k][0], k[1], k[2], start_value=conv.found[k][1]) for k in starts
            ]
            live = [r for r in res if not r.derived.identity]
            out.append((inherit(an, an.unit(key[0]), live), live))
    return out


def test_acid_carries_its_hidden_carry_and_clamp_in_as_a_start_constant(crosseyed):
    conv = analyse(load(crosseyed).ir, workers=4)
    an, seen = conv.analysis, set()
    floor = an.ir["playroutine"]["constants"]["floor"]
    for occ in conv.obs["occurrences"]:
        d = conv.found[occ.key][0] if occ.unit == "acid B" else None
        if d is None or d.identity:
            continue
        acc, cp = occ.start["acc"], occ.start["cp"]
        cf = ((acc & 0xFFFF) >> 8) + cp + (acc >> 16)
        rising = convert.carried(an, occ, d.phase, d.value)
        hidden = 8 * (max(cf, floor) - cf) + 8 * (acc >> 16)
        assert rising in (0, hidden)
        seen.add((rising, hidden))
    assert {(0, 0), (8, 8), (16, 16)} <= seen


RISE = "v + (-8*(v // 8) - 64)*[v >= 944] + 80"


def test_acid_rises_are_proved_under_an_invariant_and_falls_are_not(crosseyed):
    # pylint: disable=too-many-locals
    conv = analyse(load(crosseyed).ir, workers=4)
    acid = {k: d for k, (d, _) in conv.found.items() if k[0] == "acid B" and not d.identity}
    proved = {G.text(d.value) for d in acid.values() if d.invariant.ok}
    assert proved == {RISE}
    held = "not (acc & 65535 > 32767)"
    assert all(d.invariant.glob == (held,) for d in acid.values())
    for d in acid.values():
        if not d.invariant.ok:
            acc = int(re.search(r"acc=(\d+)", d.invariant.why).group(1))
            assert acc >> 16 or acc & 0xFF
    key = next(k for k, d in acid.items() if G.text(d.value) == RISE)
    machine = conv.analysis.machines["acid B"]
    graph = machine.explore(key[1])
    chk = chipinv.Checker(machine, graph)
    inv = [*acid[key].invariant.glob, *acid[key].invariant.unit]
    prover = Prover(machine, DM_FREQ)
    cls, seq = [0] * len(graph.nodes), [0]
    rise = acid[key].value
    assert chipinv.recurrence(chk, inv, cls, seq, rise, prover) is None
    assert chipinv.recurrence(chk, inv, cls, seq, rise + 8, prover)
    assert chipinv.recurrence(chk, [], cls, seq, rise, prover)


def test_comparisons_are_found_with_their_negations():
    got = chipinv.comparisons(["a + (b > 3)", "x if y == 0 else z"])
    assert got == ["b > 3", "not (b > 3)", "y == 0", "not (y == 0)"]


def test_a_carried_delay_counter_is_a_gate(csillag_song):
    conv = analyse(csillag_song.ir, workers=4)
    got, live = next((r, live) for r, live in general_forms(conv) if r.kind == "gate")
    (cell,) = got.cells
    low = min(dict(r.start)[cell] for r in live)
    assert got.proof.startswith(f"d = {cell} - ({low})") and DELAY in got.update[1].free_symbols
    points = [(v, phi) for v in (0, 1000, 40000) for phi in range(64)]
    for r in live:
        d = dict(r.start)[cell] - low
        f, wrong = (G.compiled(got.update[1].xreplace({DELAY: k})) for k in (d, d + 1))
        recurrence = V in got.update[1].free_symbols
        want = G.compiled(r.derived.value if recurrence else r.derived.update[1])
        table = {"W": lambda k: 7 * k + 1}
        values = [want(v, phi, 40, **table) for v, phi in points]
        assert [f(v, phi, 40, **table) for v, phi in points] == values
        assert [wrong(v, phi, 40, **table) for v, phi in points] != values


def test_a_start_off_the_longest_run_is_no_phase_offset(mw):
    machine = Machine(mw.ir, {"cell": "pulse", "fire": ["pulse_exec"]})
    starts = [machine.start({"pptr": 2, "ptime": 7}), machine.start({"pptr": 3, "ptime": 5})]
    live = [
        types.SimpleNamespace(start=s, derived=derive(machine, s, None, GT_FREQ), start_value=None)
        for s in starts
    ]
    an = types.SimpleNamespace(machines={"pulse": machine}, freq=GT_FREQ)
    lost = inherit(an, {"name": "pulse", "cell": "pulse"}, live)
    assert lost.kind == "none" and lost.update is None and lost.reason
