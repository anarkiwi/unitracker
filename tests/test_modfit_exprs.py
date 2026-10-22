"""Modulators as phase and value expressions: grammar, derivation, proofs, replay and report."""

import functools
import random
import re
import types
from pathlib import Path

import numpy as np
import pytest
import sympy
import z3
from conftest import gt_tune

from unitracker.playroutine.library import library
from unitracker.lift import goattracker
from unitracker.analysis import exprs as exprs_report
from unitracker.analysis.cli import main
from unitracker.cache import Pool, cached
from unitracker.modfit import chipinv
from unitracker.modfit import grammar as G
from unitracker.modfit.exprs import Analysis, Result
from unitracker.modfit.form import Form
from unitracker.modfit.inherit import DELAY, guarded, inherit
from unitracker.modfit.orbit import deltas
from unitracker.modfit import phase as P
from unitracker.modfit.phase import Machine, derive, unproved_functions
from unitracker.modfit.replay import Replay
from unitracker.player.modulation import declared
from unitracker.player.vm import SongPlayer

PHI, V, N = G.PHI, G.V, G.N
GT_FREQ, DM_FREQ = {"word": "ftab"}, {"hi": "note_hi", "lo": "note_lo"}
SW_FREQ = {"hi": "freqhi", "lo": "freqlo"}
TICKN = [{"run": {"record": "tickn", "index": "command", "field": "do"}}]
# A pulse program that sets the width, then ping-pongs: 32 steps up, 64 down, 32 up, repeat.
GT_PULSE = {
    1: {"l": 0x84, "r": 0, "next": 2},
    2: {"l": 32, "r": 16, "next": 3},
    3: {"l": 64, "r": 0xF0, "next": 4},
    4: {"l": 32, "r": 16, "next": 2},
}
# A pulse program that sets the width, ramps 64 steps up by 2, then 64 down by 8 and stops.
SW_PULSE = {
    1: {"a": 0x88, "b": 0, "k": 0, "next": 2},
    2: {"a": 64, "b": 2, "k": 0, "next": 3},
    3: {"a": 64, "b": 0xF8, "k": 0, "next": 0},
}

# A pulse program that sets the width, then rises 37 steps by 51 and falls 37 by 37, repeat:
# each cycle drifts by 518, wrapping the 12-bit width.
GT_DRIFT = {
    1: {"l": 0x85, "r": 0, "next": 2},
    2: {"l": 37, "r": 51, "next": 3},
    3: {"l": 37, "r": 0xDB, "next": 2},
}


def from_text(text: str, tables: dict | None = None):
    """A Python function of ``(v, phi, N)`` parsed from grammar text, brackets innermost first;
    ``tables`` are the functions its table reads ``T[k]`` call."""
    src = text.replace("φ", "phi")
    if tables:
        src = re.sub(r"\b([A-Z])\[([^\[\]]*)\]", r"\1(\2)", src)
    inner = re.compile(r"\[([^\[\]]*)\]")
    while inner.search(src):
        src = inner.sub(lambda m: f"(1 if {m.group(1)} else 0)", src)
    env = {**globals(), **(tables or {})}
    return eval(f"lambda v, phi, N: {src}", env)  # pylint: disable=eval-used


def equal(a, b, phi_from=0, bounds=((0, 1 << 16),)) -> bool:
    """True when two expressions over ``v`` and ``φ`` agree everywhere (z3)."""
    s = z3.Solver()
    phi = z3.BitVec("φ", G.BITS)
    env = {"v": z3.BitVec("v", G.BITS), "φ": G.Phase(s.add, phi), "N": z3.BitVec("N", G.BITS)}
    s.add(phi >= phi_from, phi < 1 << 20, env["v"] >= bounds[0][0], env["v"] < bounds[0][1])
    s.add(G.to_z3(a, env) != G.to_z3(b, env))
    return s.check() == z3.unsat


def machine(ref: str, unit, **records) -> Machine:
    """A machine over playroutine ``ref`` and ``records``: ``unit`` a declared unit's name (with
    the playroutine's named tables) or a ``{cell, fire}`` modulator."""
    song = {"playroutine": library(ref), "records": records}
    if isinstance(unit, dict):
        return Machine(song, unit)
    spec = declared(song["playroutine"])
    return Machine(song, next(u for u in spec["units"] if u["name"] == unit), spec)


# ---- grammar ----------------------------------------------------------------------------


def test_brackets_have_one_canonical_form():
    assert G.lt(PHI, 64) == 1 - G.ge(PHI, 64)
    assert G.ge(2 * PHI, 5) == G.ge(PHI, 3)
    assert G.eq(2 * PHI, 5) == 0
    assert G.Iv(sympy.Ne(PHI, 3)) == 1 - G.eq(PHI, 3)
    assert G.text(G.ge(V, G.freq(N) + 1)) == "(v > F(N))"
    assert G.text(G.polar(G.lt(PHI, 64) * 5 + 2)) == "5*(φ < 64) + 2"
    assert not G.valid(sympy.Rational(1, 2) * PHI)
    assert G.shortest([sympy.Rational(1, 2) * PHI, PHI + PHI]) == 2 * PHI


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


# A filter program: set 117, set 102, 30 steps of -3, then +3 and -1 in turn.
GT_TURN = {
    1: {"l": 0x90, "r": 0xA1, "cut": 117, "next": 2},
    2: {"l": 0, "r": 102, "next": 3},
    3: {"l": 30, "r": 0xFD, "next": 4},
    4: {"l": 1, "r": 3, "next": 5},
    5: {"l": 1, "r": 0xFF, "next": 4},
}
TURN_CYCLE = "((-(φ % 2) + 2*(φ // 2) - 17) % 256)"
TURN_MISREAD = "((-φ % 2 - 2*(φ // 2) - 17) % 256)"


def test_a_text_that_does_not_read_back_is_refused(monkeypatch):
    e = G.parse(f"8*(φ >= 32)*{TURN_CYCLE}")
    assert G.parse(G.text(e)) == e
    G.text.cache_clear()
    monkeypatch.setattr(G, "_text", lambda _: f"8*(φ >= 32)*{TURN_MISREAD}")
    with pytest.raises(ValueError, match="does not read back"):
        G.text(e)
    assert not G.valid(e)
    with pytest.raises(ValueError, match="not in the grammar"):
        G.parse("v << 2")


def test_terms_are_equivalent_within_their_bounds_and_modulo_a_wrap():
    v, shifted = G.parse("abs(v)"), G.parse("v + 256")
    assert G.equivalent(v, G.parse("v"), (("v", 0, 100),))
    assert not G.equivalent(v, G.parse("v"))
    assert not G.equivalent(shifted, G.parse("v")) and G.equivalent(shifted, G.parse("v"), wrap=8)


def test_a_closed_form_is_proved_as_its_text_reads():
    m = machine("goattracker2", "filter", filttab=GT_TURN)
    start = m.start({"fptr": 1, "ftime": 0})
    d = derive(m, start, None, GT_FREQ)
    text = G.text(d.closed)
    assert d.exact and TURN_CYCLE in text
    domain = tuple(dict(start)[c] for c in P.DOMAIN)
    prover = P.Prover(m, GT_FREQ, None, domain, m.atom(dict(start)))
    assert prover.closed(G.parse(text), d.value, None) is None
    misread = G.parse(text.replace(TURN_CYCLE, TURN_MISREAD))
    assert prover.closed(misread, d.value, None) is not None


def test_z3_division_and_modulo_are_floored():
    env = {"v": z3.BitVecVal(-5, G.BITS)}
    cases = (
        (sympy.Mod(V, 3), 1),
        (sympy.Mod(V, 4), 3),
        (sympy.floor(V / 2), -3),
        (sympy.floor(V / 3), -2),
        (sympy.Abs(V), 5),
    )
    for e, want in cases:
        assert z3.simplify(G.to_z3(e, env)).as_signed_long() == want == G.compiled(e)(-5, 0, 0)


def test_a_phase_floors_by_its_cycle_atoms():
    rng = random.Random(2)
    for _ in range(40):
        k, c, shift = rng.choice((1, 3, 74, 113)), rng.randrange(-300, 300), rng.randrange(3)
        s = z3.Solver()
        base = z3.BitVec("φ", G.BITS)
        env = {"φ": G.Phase(s.add, base) + shift}
        e = sympy.floor((PHI + c) / k) * 1000 + sympy.Mod(PHI + c, k)
        term = G.to_z3(e, env)
        assert not any(x.decl().kind() == z3.Z3_OP_BSREM for x in _subterms(term))
        phi = rng.randrange(0, 1000)
        s.add(base == phi)
        assert s.check() == z3.sat
        got = s.model().eval(term).as_signed_long()
        assert got == ((phi + shift + c) // k) * 1000 + (phi + shift + c) % k


def _subterms(t):
    yield t
    for a in t.children():
        yield from _subterms(a)


_U = sympy.Mod(V - 513, 65536)
_SIGNED = V - 65536 * G.ge(V, 32768)


@pytest.mark.parametrize(
    "e,domain,check",
    [
        (
            G.eq(PHI, 0) * 16384
            + G.eq(PHI, 1) * sympy.Mod(V - 1, 65536)
            + G.ge(PHI, 2)
            * (G.ge(_U, 32768) * (512 + sympy.Mod(V - 1, 256)) + (1 - G.ge(_U, 32768)) * _U),
            None,
            lambda shown, e: G.rank(shown) < G.rank(e)
            and G.text(shown).startswith("16384*(φ == 0)"),
        ),
        (
            G.eq(_SIGNED, 0) * 7 + G.ge(_SIGNED, 1) * V,
            (("v", 0, 65535),),
            lambda shown, e: "(v >= 32768))" not in G.text(shown),
        ),
    ],
)
def test_the_display_form_is_shorter_and_equivalent(e, domain, check):
    e = G.canon(e)
    shown = G.polar(e) if domain is None else G.polar(e, domain)
    assert check(shown, e) and equal(e, shown)


@functools.lru_cache(maxsize=None)
def _enumerated(e, context=(), domain=()):
    """``display`` by every combination of its four forms per bracket, each cofactor shown
    again under every choice of the brackets before it."""
    # pylint: disable=protected-access
    e = G._case(e, context, domain)
    flat = G.denest(e, domain) if not context else e
    forms = [_enumerated(flat, context, domain)] if flat != e else []
    brackets = sorted(e.atoms(G.Iv), key=sympy.default_sort_key)
    if not brackets:
        return G.shortest([*forms, e, sympy.factor_terms(e)])
    b = brackets[0]
    no, yes = context + ((b, 0),), context + ((b, 1),)
    rest = G.products(e.subs(b, 0), context, domain)
    full = G.products(e.subs(b, 1), context, domain)
    whole = G._case(full, yes, domain)
    y = G._case(rest, no, domain)
    forms += [
        e,
        G._plus(
            G._times(b, G._case(whole - rest, yes, domain)), _enumerated(rest, context, domain)
        ),
        G._plus(
            G._times(G._complement(b), G._case(y - full, no, domain)),
            _enumerated(full, context, domain),
        ),
    ]
    if y != 0:
        forms.append(
            G._plus(
                G._times(b, _enumerated(whole, yes, domain)),
                G._times(G._complement(b), _enumerated(y, no, domain)),
            )
        )
    return G.shortest(forms)


def _set_rows(n: int) -> dict:
    """A goattracker2 pulse program of ``n`` set rows, then a loop 32 up by 16, 32 down by 16."""
    rows = {k: {"l": 0x80 + k % 8, "r": 37 * k % 256, "next": k + 1} for k in range(1, n + 1)}
    return {
        **rows,
        n + 1: {"l": 32, "r": 16, "next": n + 2},
        n + 2: {"l": 32, "r": 0xF0, "next": n + 1},
    }


def _value(table: dict):
    """``(value term, domain)`` of a goattracker2 pulse program's start at its first row."""
    m = machine("goattracker2", {"cell": "pulse", "fire": ["pulse_exec"]}, pulsetab=table)
    start = m.start({"pptr": 1, "ptime": 0})
    _, raw, edges, lengths = P.minimal(m.explore(start), P.Lift(m.form, m.cell, GT_FREQ))
    lo, hi, r, k = m.domain(start)
    values = [G.shortest([x, G.on_domain(x, r, k, lo, hi)]) for x in raw]
    order = P.order(edges, lengths, raw)
    return G.canon(G.shortest(P.value_forms(values, order, m.mask))), (("v", lo, hi),)


@pytest.mark.parametrize(
    "e,domain",
    [
        (_value(_set_rows(3))[0], _value(_set_rows(3))[1]),
        (G.canon(G.ge(PHI, 1) * sympy.Mod(V - 16, 4096) - 1024 * G.ge(PHI, 1) + 1024), ()),
        (G.canon(G.ge(PHI, 1) * (V - 1024) + 1024), (("v", 0, 4095),)),
        (G.canon(G.eq(_SIGNED, 0) * 7 + G.ge(_SIGNED, 1) * V), (("v", 0, 65535),)),
        (
            G.canon(
                G.eq(PHI, 0) * 16384
                + G.eq(PHI, 1) * sympy.Mod(V - 1, 65536)
                + G.ge(PHI, 2) * (G.ge(_U, 32768) * sympy.Mod(V - 1, 256) + G.lt(_U, 32768) * _U)
            ),
            (),
        ),
    ],
)
def test_the_display_form_is_the_enumerated_shortest(e, domain):
    assert G.text(G.polar(e, domain)) == G.text(_enumerated(e, (), domain))


def test_a_remainder_under_its_guard_folds_to_a_constant():
    r = sympy.Mod(V, 32768)
    e = G.canon((r + 65535) * G.ge(r, 1) + (sympy.Mod(V - 1, 256) + 512) * G.lt(r, 1))
    shown = G.polar(e, (("v", 0, 131071),))
    assert G.text(shown) == "-64768*(v % 32768 < 1) + v % 32768 + 65535"
    assert equal(shown, e, bounds=((0, 131072),))


def test_a_form_with_fewer_brackets_ranks_first_at_equal_tokens():
    two = "16*(φ >= 4)*(((φ - 5) % 65 + 68*((φ - 5) // 65) + 25) % 256) + 2048*(φ < 4)"
    one = "(16*(((φ - 5) % 65 + 68*((φ - 5) // 65) + 25) % 256) - 2048)*(φ >= 4) + 2048"
    cycle = G.parse("((φ - 5) % 65 + 68*((φ - 5) // 65) + 25) % 256")
    a, b = G.parse(one), 16 * G.ge(PHI, 4) * cycle + 2048 * G.IvLt(PHI - 4)
    assert [G.text(a), G.text(b)] == [one, two] and G.tokens(one) == G.tokens(two)
    assert len(one) > len(two) and G.rank(a) < G.rank(b) and equal(a, b)


_FN = G.freq(N)


@pytest.mark.parametrize(
    "e,context,domain,want",
    [
        (sympy.Mod(PHI + 3, 8), ((G.ge(PHI, 5), 0),), (), PHI + 3),
        (sympy.floor(PHI / 4), ((G.ge(PHI, 4), 1), (G.ge(PHI, 8), 0)), (), 1),
        (sympy.Abs(PHI - 10), ((G.ge(PHI, 5), 0),), (), 10 - PHI),
        (sympy.Abs(PHI - 3), ((G.ge(PHI, 5), 1),), (), PHI - 3),
        (sympy.Min(30, sympy.Max(0, PHI - 1)), ((G.ge(PHI, 32), 1),), (), 30),
        (sympy.Max(V, 40), ((G.ge(sympy.Mod(V + 3, 65536), 50), 1),), (("v", 0, 100),), V),
        (G.eq(PHI, 7) * V, ((G.ge(PHI, 5), 0),), (), 0),
        (G.ge(PHI, 2) * V, ((G.ge(PHI, 5), 1),), (), V),
        (sympy.Min(PHI, 1), ((G.eq(PHI, 0), 0),), (), 1),
        (V * G.eq(_FN, sympy.Mod(V + _FN, 65536)), ((G.eq(_FN, sympy.Mod(V + _FN, 65536)), 1),),
         (("v", 0, 65535),), 0),
        (sympy.Mod(V - 1, 100), ((G.ge(sympy.Mod(V, 32768), 1), 0),), (), sympy.Mod(V - 1, 100)),
        (sympy.Mod(V - 1, 256), ((G.ge(sympy.Mod(V, 32768), 1), 0),), (), 255),
        (sympy.Mod(V, 4), ((G.ge(V, 5), 1),), (), sympy.Mod(V, 4)),
    ],
)  # fmt: skip
def test_a_case_is_folded_over_the_interval_its_facts_bound(e, context, domain, want):
    assert G._folded(e, context, domain) == want  # pylint: disable=protected-access


def test_a_long_prefix_of_set_rows_displays_in_calls_linear_in_its_brackets(monkeypatch):
    e, domain = _value(_set_rows(20))
    brackets = len(e.atoms(G.Iv))
    inner, calls = G.display.__wrapped__, []

    @functools.lru_cache(maxsize=None)
    def counted(*args):
        calls.append(args)
        return inner(*args)

    monkeypatch.setattr(G, "display", counted)
    shown = G.polar(e, domain)
    assert brackets == 21 and len(calls) <= 3 * brackets
    assert G.text(shown).count("(φ >= ") + G.text(shown).count("(φ < ") <= brackets
    assert equal(shown, e, bounds=((domain[0][1], domain[0][2] + 1),))


# ---- derivation and proof ---------------------------------------------------------------


@pytest.mark.parametrize(
    "ref,unit,records,start,freq,text,asked,order",
    [
        (
            "goattracker2",
            {"cell": "pulse", "fire": ["pulse_exec"]},
            {"pulsetab": GT_PULSE},
            {"pptr": 1, "ptime": 0},
            GT_FREQ,
            "16*abs((φ - 32) % 128 - 64) + 512",
            1024 + 16 * (32 - sympy.Abs(sympy.Mod(PHI + 32, 128) - 64)),
            (1, 128),
        ),
        (
            "sidwizard",
            {"cell": "pwhi:pwlo", "fire": ["tick_pw"]},
            {"pwtab": SW_PULSE},
            {"ppos": 1, "pwcnt": 0, "ci": 1},
            SW_FREQ,
            "2*min(64, φ) - 8*min(64, max(0, φ - 65)) + 2048",
            2048 + 2 * sympy.Min(PHI, 64) - 8 * sympy.Max(0, sympy.Min(PHI - 65, 64)),
            None,
        ),
        (
            "goattracker2",
            {"cell": "pulse", "fire": ["pulse_exec"]},
            {"pulsetab": GT_DRIFT},
            {"pptr": 1, "ptime": 0},
            GT_FREQ,
            "(-44*abs(φ % 74 - 37) + 7*(φ % 74) + 518*(φ // 74) - 1188) % 4096",
            sympy.Mod(
                1280
                + 518 * sympy.floor(PHI / 74)
                + 51 * sympy.Min(sympy.Mod(PHI, 74), 37)
                - 37 * sympy.Max(0, sympy.Mod(PHI, 74) - 37),
                4096,
            ),
            (1, 74),
        ),
    ],
)
def test_a_table_program_is_one_closed_form(ref, unit, records, start, freq, text, asked, order):
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    m = machine(ref, unit, **records)
    d = derive(m, m.start(start), None, freq)
    assert d.exact and d.order.kind == "time" and G.text(d.closed) == text
    assert equal(d.closed, asked) and d.update[0] == 1
    assert order in (None, (d.order.prefix, d.order.period))


def _checked(fn):
    """``(fn(), z3 solver checks it ran)`` with the display caches cold."""
    for fn_ in vars(G).values():
        if hasattr(fn_, "cache_clear"):
            fn_.cache_clear()
    check, count = z3.Solver.check, [0]

    def counted(solver, *args):
        count[0] += 1
        return check(solver, *args)

    z3.Solver.check = counted
    try:
        return fn(), count[0]
    finally:
        z3.Solver.check = check


@pytest.mark.parametrize(
    "ref,unit,table,start,freq,rows,texts",
    [
        (
            "goattracker2",
            {"cell": "pulse", "fire": ["pulse_exec"]},
            "pulsetab",
            {"pptr": 1, "ptime": 0},
            GT_FREQ,
            lambda k: {
                **GT_PULSE,
                2: {**GT_PULSE[2], "l": k},
                3: {**GT_PULSE[3], "l": 2 * k},
                4: {**GT_PULSE[4], "l": k},
            },
            {
                4: "16*abs((φ - 4) % 16 - 8) + 960",
                32: "16*abs((φ - 32) % 128 - 64) + 512",
                60: "16*abs((φ - 60) % 240 - 120) + 64",
            },
        ),
        (
            "sidwizard",
            "pulse",
            "pwtab",
            {**{"ppos": 1, "pwcnt": 0, "ci": 1, "pwkt": 0, "oct": 0}, "note": 0},
            SW_FREQ,
            lambda k: {**SW_PULSE, 2: {**SW_PULSE[2], "a": k}, 3: {**SW_PULSE[3], "a": k}},
            {
                4: "2*min(4, φ) - 8*min(4, max(0, φ - 5)) + 2048",
                64: "2*min(64, φ) - 8*min(64, max(0, φ - 65)) + 2048",
                120: "2*min(120, φ) - 8*min(120, max(0, φ - 121)) + 2048",
            },
        ),
    ],
)
def test_a_row_is_one_state_whatever_its_length(ref, unit, table, start, freq, rows, texts):
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    got = {}
    for k, text in texts.items():
        m = machine(ref, unit, **{table: rows(k)})
        d, checks = _checked(lambda m=m: derive(m, m.start(start), None, freq))
        assert d.exact and G.text(d.closed) == text
        got[k] = (d.states, checks)
    assert len(set(got.values())) == 1, got


def test_a_row_counts_only_inside_its_cells_width():
    m = machine("sidwizard", "pulse", pwtab=SW_PULSE)
    assert m.count("pwcnt", 254, 1) == "_pwcnt254+1" and m.form.ranges["_pwcnt254+1"] == (0, 0)
    assert m.count("pwcnt", 255, 1) is None and m.count("pwcnt", 0, -1) is None


def test_a_turn_order_moves_at_each_firing_of_a_row():
    g = G.ge(V, 7)
    values = [V + 1, V + 2, V + 3]
    rows = P.order(
        [{0: g, 1: 1 - g}, {2: sympy.Integer(1)}, {2: sympy.Integer(1)}], [1, 3, 2], values
    )
    assert (rows.kind, rows.prefix, rows.period, rows.at) == ("turn", 4, 1, {0: 0, 1: 1, 2: 4})
    assert rows.runs == [(0, 1), (1, 3), (2, 1)] and rows.advance == [1 - g, 1, 1]
    stop = P.order([{0: g, 1: 1 - g}, {1: sympy.Integer(1)}], [1, 1], values)
    assert (stop.prefix, stop.period, stop.advance) == (1, 1, [1 - g, 0])
    assert P.order([{1: g, 2: 1 - g}, {1: g}, {2: g}], [1, 1, 1], values) is None


def _expanded(sizes: list, tokens: list, loop: int, length: int) -> list:
    """The first ``length`` tokens of the sequence repeating ``tokens[i]`` ``sizes[i]`` times,
    the entries from ``loop`` on forever."""
    head = [t for t, n in zip(tokens[:loop], sizes[:loop]) for _ in range(n)]
    cyc = [t for t, n in zip(tokens[loop:], sizes[loop:]) for _ in range(n)]
    return (head + cyc * length)[:length]


def test_the_shortest_lasso_of_runs_is_that_of_their_positions():
    rng = random.Random(3)
    for _ in range(300):
        m = rng.randrange(1, 7)
        tokens = [rng.choice("abc") for _ in range(m)]
        sizes = [rng.randrange(1, 5) for _ in range(m)]
        loop = rng.randrange(m)
        prefix, period = P.shortest_lasso(tokens, sizes, loop)
        total = sum(sizes)
        seq = _expanded(sizes, tokens, loop, 4 * total)
        want_q = next(
            q
            for q in range(1, total + 1)
            if all(seq[i] == seq[i + q] for i in range(total, 2 * total))
        )
        want_p = next(
            p
            for p in range(total + 1)
            if all(seq[i] == seq[i + want_q] for i in range(p, 2 * total))
        )
        assert (prefix, period) == (want_p, want_q), (tokens, sizes, loop)


def test_a_run_of_positions_synthesises_as_its_positions_one_by_one():
    rng = random.Random(4)
    mask = 4095
    for _ in range(20):
        m = rng.randrange(1, 6)
        values = [
            rng.choice(
                [
                    sympy.Integer(rng.randrange(0, 4096)),
                    sympy.Mod(V + rng.choice((-8, 0, 2, 16)), 4096),
                ]
            )
            for _ in range(m)
        ]
        sizes = [rng.randrange(1, 6) for _ in range(m)]
        prefix = sum(sizes[: rng.randrange(m)])
        runs = list(enumerate(sizes))
        period = sum(sizes) - prefix
        whole = P.Order("time", runs, prefix, period, [sympy.Integer(1)] * m, {})
        flat_values = [values[c] for c, n in runs for _ in range(n)]
        flat = P.Order(
            "time",
            [(i, 1) for i in range(len(flat_values))],
            prefix,
            period,
            [sympy.Integer(1)] * len(flat_values),
            {},
        )
        for v0 in (None, sympy.Integer(100), G.freq(N)):
            assert P.closed_forms(values, whole, mask, v0) == P.closed_forms(
                flat_values, flat, mask, v0
            )
        assert P.value_forms(values, whole, mask) == P.value_forms(flat_values, flat, mask)


def test_goattracker_slide_and_toneporta():
    m = machine("goattracker2", {"cell": "freq", "fire": TICKN}, speedtab={1: {"l": 0, "r": 32}})
    base = {"vibtime": 0, "vibdelay": 0, "lastnote": 40, "note": "N"}
    slide = derive(m, m.start({**base, "command": 1, "cmddata": 1}), None, GT_FREQ)
    assert slide.canonical == "0; (v + 32) % 65536" and slide.exact
    down = derive(m, m.start({**base, "command": 2, "cmddata": 1}), None, GT_FREQ)
    assert down.canonical == "0; (v - 32) % 65536"
    porta = derive(m, m.start({**base, "command": 3, "cmddata": 1}), None, GT_FREQ)
    assert porta.exact and G.freq(N) in porta.value.atoms(G.FREQ)
    toward = G.compiled(porta.value, ("v", "phi", "N"))

    def ftab(k):
        return 1000 if k == 7 else 0

    got = [toward(v, 0, 7, F=ftab) for v in (900, 990, 1000, 1010, 1100)]
    assert got == [932, 1000, 1000, 1000, 1068]


def test_goattracker_vibrato_is_periodic_in_elapsed_firings():
    m = machine("goattracker2", {"cell": "freq", "fire": TICKN}, speedtab={1: {"l": 4, "r": 32}})
    start = {"command": 4, "cmddata": 1, "vibtime": 0, "vibdelay": 0, "lastnote": 40, "note": "N"}
    d = derive(m, m.start(start), None, GT_FREQ)
    assert d.exact and (d.order.period, d.phase) == (12, 1)
    f = G.compiled(d.value)
    v, phi, trace = 20000, 0, []
    for _ in range(24):
        v, phi = f(v, phi, 0), phi + 1
        trace.append(v - 20000)
    assert trace[:12] == [32, 64, 96, 64, 32, 0, -32, -64, -96, -64, -32, 0] == trace[12:]


def test_defmon_pulse_bounce_counts_turns():
    m = machine("defmon", {"cell": "pw", "fire": ["pulse"]})
    d = derive(m, m.start({"ps": 0x85, "fc": 0}), None, DM_FREQ)
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
    with pytest.raises(ValueError, match="no start value"):
        m.start({"ps": 0x85})


SW_START = {"ppos": 1, "pwcnt": 0, "ci": 1, "pwkt": 0, "oct": 0}


def test_a_keyboard_tracked_pulse_row_is_a_term_of_the_note():
    m = machine("sidwizard", "pulse", pwtab={1: {"a": 0x88, "b": 0, "k": 5, "next": 0}})
    start = {**SW_START, "note": ("lin", 1, (("N", 1),))}
    d = derive(m, m.start(start), None, SW_FREQ)
    assert d.exact and {"E[N + 5]", "E[N + 6]"} <= set(re.findall(r"E\[[^]]*\]", G.text(d.closed)))
    hi = library("sidwizard")["tables"]["freqhi"]
    track = from_text(G.text(d.closed), {"E": lambda i: hi[i - 11] if i >= 11 else 0})
    steps = {n: hi[n + 6 - 11] - hi[n + 5 - 11] for n in range(6, 90)}
    assert {0, 1, 3} <= set(steps.values())
    for n, step in steps.items():
        assert track(0, 0, n) == 2048 + 256 * (step if step else -1)


def test_a_paired_chip_value_must_be_its_pair_plus_an_offset():
    song = {"playroutine": library("sidwizard"), "records": {"pwtab": SW_PULSE}}
    spec = declared(song["playroutine"])
    unit = next(u for u in spec["units"] if u["name"] == "pulse")
    m = Machine(song, {**unit, "chip": {"width": 12, "value": "(pwhi << 8) ^ pwlo"}}, spec)
    d = derive(m, m.start({**SW_START, "note": 0}), None, SW_FREQ)
    assert d.error == "the chip value is not its pair plus an offset"


def test_named_tables_equal_the_functions_they_replace():
    song = {"playroutine": library("sidwizard"), "records": {}}
    spec = declared(song["playroutine"])
    assert not unproved_functions(song, spec)
    assert unproved_functions(song, {**spec, "functions": {"fmlo": "W[fyc(a, d)] & 0x7F"}})


def test_the_acid_direction_gives_two_expressions():
    m = machine(
        "defmon",
        {"cell": "acc", "fire": ["layer_b", "acid_step"]},
        sidtab={1: {"dl": 1, "next": 2}, 2: {"dl": 1, "next": 1}},
    )
    base = {"rowB": 1, "cntB": 0, "astep": 0, "cp": 0}
    one = derive(m, m.start({**base, "sub": 1}), None, DM_FREQ)
    zero = derive(m, m.start({**base, "sub": 0}), None, DM_FREQ)
    assert one.exact and zero.exact and one.canonical != zero.canonical


def test_comparisons_are_found_with_their_negations():
    got = chipinv.comparisons(["a + (b > 3)", "x if y == 0 else z"])
    assert got == ["b > 3", "not (b > 3)", "y == 0", "not (y == 0)"]


# ---- inherited start values -------------------------------------------------------------


def test_a_counted_start_cell_is_refused_as_an_input():
    m = machine("goattracker2", {"cell": "pulse", "fire": ["pulse_exec"]}, pulsetab=GT_PULSE)
    start = m.start({"pptr": 1, "ptime": "ptime"})
    with pytest.raises(ValueError, match="ptime counts"):
        guarded(m, {"ptime"}).explore(start)
    d = derive(guarded(m, {"ptime"}), start, None, GT_FREQ, {"ptime": "ptime"})
    assert "counts" in d.error


def test_a_start_off_the_longest_run_is_no_phase_offset():
    m = machine("goattracker2", {"cell": "pulse", "fire": ["pulse_exec"]}, pulsetab=GT_PULSE)
    starts = [m.start({"pptr": 2, "ptime": 7}), m.start({"pptr": 3, "ptime": 5})]
    live = [
        types.SimpleNamespace(start=s, derived=derive(m, s, None, GT_FREQ), start_value=None)
        for s in starts
    ]
    an = types.SimpleNamespace(machines={"pulse": m}, freq=GT_FREQ)
    lost = inherit(an, {"name": "pulse", "cell": "pulse"}, live)
    assert lost.kind == "none" and lost.update is None and lost.reason


def test_a_carried_cell_the_firing_only_reads_is_an_input():
    m = machine(
        "defmon",
        {"cell": "acc", "fire": ["layer_b", "acid_step"]},
        sidtab={1: {"dl": 1, "next": 2}, 2: {"dl": 1, "next": 1}},
    )
    live = []
    for astep in (0x0A00, 0x0200):
        s = m.start({"rowB": 1, "cntB": 0, "astep": astep, "cp": 0, "sub": 0})
        live.append(
            types.SimpleNamespace(start=s, derived=derive(m, s, None, DM_FREQ), start_value=None)
        )
    got = inherit(types.SimpleNamespace(machines={"acid": m}, freq=DM_FREQ), {"name": "acid"}, live)
    assert got.kind == "input" and got.cells == ["astep"] and got.proof.startswith("proved")
    general = G.compiled(got.update[1], ("v", "phi", "N", "astep"))
    for r in live:
        astep, own = dict(r.start)["astep"], G.compiled(r.derived.update[1])
        for v in range(0, 65536, 997):
            assert general(v, 0, 0, astep) == own(v, 0, 0)
        assert any(general(v, 0, 0, astep + 1) != own(v, 0, 0) for v in range(0, 65536, 997))


W_LINEAR = {"W": lambda k: 7 * k + 1}


@pytest.mark.parametrize("known", [True, False])
def test_a_carried_delay_counter_is_a_gate(known):
    m = machine("sidwizard", "pitch")
    vib = {"ci": 1, "svib": 0x10, "vfc": 2, "vper": 8, "fml": ("idx", "W", "N"), "fmh": 0}
    live = []
    for k in (-1, 3, 15):
        v0 = sympy.Integer(0) if known or k != 3 else None
        s = m.start({**vib, "vdc": k})
        live.append(
            types.SimpleNamespace(start=s, derived=derive(m, s, v0, SW_FREQ), start_value=v0)
        )
    got = inherit(
        types.SimpleNamespace(machines={"pitch": m}, freq=SW_FREQ), {"name": "pitch"}, live
    )
    assert got.kind == "gate" and got.cells == ["vdc"]
    assert got.proof.startswith("d = vdc - (-1)") and DELAY in got.update[1].free_symbols
    points = [(v, phi) for v in (0, 1000, 40000) for phi in range(64)]
    for r in live:
        d = dict(r.start)["vdc"] + 1
        f, wrong = (G.compiled(got.update[1].xreplace({DELAY: k})) for k in (d, d + 1))
        want = G.compiled(
            r.derived.value if V in got.update[1].free_symbols else r.derived.update[1]
        )
        values = [want(v, phi, 40, **W_LINEAR) for v, phi in points]
        assert [f(v, phi, 40, **W_LINEAR) for v, phi in points] == values
        assert [wrong(v, phi, 40, **W_LINEAR) for v, phi in points] != values


# ---- replay and report ------------------------------------------------------------------


@pytest.fixture(name="gt_song")
def gt_song_fixture():
    """A synthetic GoatTracker 2 tune lifted with its native modulations, and its native player:
    a ping-pong pulse program and a constant one, a slide up and a toneporta down."""
    pulse = [(0x84, 0), (0x20, 0x10), (0x40, 0xF0), (0x20, 0x10), (0xFF, 2), (0x88, 0), (0xFF, 0)]
    ins = {"gateoff_timer": 2, "sustain_release": 0xF0}
    rest = [(None, 0, 0, 0)]
    rows = [(24, 1, 1, 1)] + rest * 5 + [(26, 2, 0, 0)] + rest * 4 + [(22, 0, 3, 1)] + rest * 4
    dec, player = gt_tune(
        [(0x41, 0), (0xFF, 0), (0x21, 0), (0xFF, 0)],
        instruments=[
            {**ins, "wave_ptr": 1, "pulse_ptr": 1},
            {**ins, "wave_ptr": 3, "pulse_ptr": 6},
        ],
        rows=rows,
        speed=[(0, 32)],
        pulse=pulse,
    )
    return goattracker.GoatTrackerLift(dec, expressions=False).run(), player


def test_the_pool_maps_in_order_and_the_cache_reads_an_entry_written_while_it_waited(
    tmp_path, monkeypatch
):
    pool = Pool(2, 3)
    try:
        assert pool.workers == 2
        assert pool.map(divmod, [(7, 2), (9, 4), (1, 1)], "divmod") == [(3, 1), (2, 1), (1, 0)]
    finally:
        pool.close()
    seen = iter([False, True])
    monkeypatch.setattr(Path, "exists", lambda self: next(seen))
    got = cached(tmp_path / "entry.pkl", lambda: pytest.fail("computed"), lambda p: p.name, None)
    assert got == "entry.pkl"


def test_a_tune_loads_with_its_native_modulations_and_plays_its_native_player(metal_warrior):
    song = exprs_report.load(metal_warrior)
    assert song.player == "goattracker2"
    assert not song.ir.get("exprs")
    assert SongPlayer(song.ir).mismatch(exprs_report.native_grid(song, 600)) is None


def test_driven_replay_matches_and_a_wrong_expression_does_not(gt_song):
    ir, player = gt_song
    an, ticks = Analysis(ir), 300
    native = np.asarray(player.render_grid(ticks), dtype=np.uint8)
    good = Replay(ir, an.derive, override=lambda occ: True)
    assert np.array_equal(np.asarray(good.run(ticks), dtype=np.uint8), native)
    assert good.occurrences and not [o for o in good.occurrences if o.mismatch or o.crossed]

    def wrong(unit, occ):
        res = an.derive(unit, occ)
        if res is None or unit["name"] != "pulse":
            return res
        broken = Result(res.derived, res.start, res.v0, res.stepper)
        broken.step = lambda v, phi, n: ((v + 1) % 4096, phi + 1)
        return broken

    bad = Replay(ir, wrong, override=lambda occ: True)
    assert not np.array_equal(np.asarray(bad.run(ticks), dtype=np.uint8), native)
    assert any(o.mismatch for o in bad.occurrences if o.unit == "pulse")


def test_cli_writes_the_report(gt_song, tmp_path, monkeypatch):
    ir, player = gt_song
    grid = np.asarray(player.render_grid(exprs_report.TICKS["goattracker2"]), dtype=np.uint8)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(exprs_report, "load", lambda p: exprs_report.Song(Path(p), ir, b""))
    monkeypatch.setattr(exprs_report, "native_grid", lambda song, ticks: grid[:ticks])
    texts = []
    for workers in ("2", "1"):
        out = tmp_path / f"exprs-{workers}.txt"
        main(["--out", str(out), "exprs", "synthetic.sid", "--workers", workers])
        texts.append(out.read_text())
    line = "its generic document on unitracker-player over 7680 ticks: (verified|mismatch): "
    assert all(re.search(line, t) for t in texts)
    text = texts[0]
    assert "## synthetic (goattracker2)" in text and "## Tokens" in text
    assert "closed form (start value any): v = 16*abs((φ - 32) % 128 - 64) + 512" in text
    assert re.search(r"written \(\d+ occurrences\): v \+ 32  at p\w+~1\[0\]\n", text)
    assert re.search(r"written \(\d+ occurrences\): 2048  at pulsetab \d+ x\n", text)
    assert re.search(r"## Tokens\n\n  synthetic: (\d+)\n  total: \1\n", text)


def test_a_fired_record_function_runs_at_the_index_of_its_call():
    song = {"playroutine": library("goattracker2"), "records": {}}
    spec = declared(song["playroutine"])
    unit = next(u for u in spec["units"] if u["name"] == "wave")
    assert unit["fire"] == [{"record": "wavecmd", "field": "do", "index": [1, 2, 3, 4]}]
    run = {"run": {"record": "wavecmd", "index": "wc", "field": "do"}}
    wave, ran = Machine(song, unit, spec), Machine(song, {**unit, "fire": [run]}, spec)
    assert (wave.actions, wave.control, wave.real) == (ran.actions, ran.control, ran.real)


@pytest.mark.parametrize(
    "e,width,written",
    [
        (sympy.Mod(G.V + 32, 65536), 16, "v + 32"),
        (sympy.Mod(G.V + 65504, 65536), 16, "v - 32"),
        (sympy.Mod(G.V + 32, 65536), 17, "(v + 32) % 65536"),
        (sympy.Mod(G.V + 32, 65536), None, "(v + 32) % 65536"),
        (G.ge(G.PHI, 2) * sympy.Mod(G.V + 65000, 65536), 12, "(v - 536)*(φ >= 2)"),
        (sympy.Min(sympy.Mod(G.V - 3, 4096), 7) + 1, 12, "min(7, (v - 3) % 4096) + 1"),
    ],
)
def test_a_wrap_the_unit_repeats_is_not_written(e, width, written):
    assert G.text(G.unwrapped(e, width)) == written
