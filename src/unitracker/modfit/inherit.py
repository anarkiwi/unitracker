"""The general form of a modulator whose starts differ in values carried from earlier playback.

The lifter writes each start with its carried values as constants; this module proves one form
for every value of them. A carried cell the firing never counts is an input of one derivation
over symbolic cells; a carried counter the firing counts down is a delay gate ``[φ >= d]``.
"""

import copy
import dataclasses

import sympy
import z3

from unitracker.modfit import grammar as G
from unitracker.modfit.form import mk
from unitracker.modfit.phase import Prover, derive, witness
from unitracker.modfit.replay import G_value

DELAY = sympy.Symbol("d", integer=True)


@dataclasses.dataclass
class Inherited:
    """How a modulator's carried start cells enter its one general form."""

    kind: str
    cells: list
    update: tuple | None = None
    proof: str = ""
    reason: str = ""

    @property
    def text(self) -> str:
        """``a; f`` of the general form."""
        return f"{G.text(self.update[0])}; {G.text(self.update[1])}"


def _count(t, x) -> int:
    """Occurrences of cell ``x`` in a form term."""
    if t == x:
        return 1
    if not isinstance(t, tuple):
        return 0
    parts = [a for a, _ in t[2]] if t[0] == "lin" else list(t[1:])
    return sum(_count(a, x) for a in parts)


def guarded(machine, cells: set):
    """A copy of ``machine`` whose firing refuses to count a symbolic start cell."""
    m = copy.copy(machine)

    def step(state):
        out = machine.step(state)
        for _, _, nxt in out:
            for c, val in nxt:
                if c in cells and isinstance(val, tuple) and _count(val, c):
                    raise ValueError(f"{c} counts from its inherited value")
        return out

    m.step = step
    return m


def inherit(an, unit: dict, live: list) -> Inherited:
    """The general form of a unit's starts ``live`` (results with ``start`` and ``derived``):
    carried cells as inputs, or a carried counter as a delay gate."""
    # pylint: disable=too-many-locals
    machine = an.machines[unit["name"]]
    starts = [dict(r.start) for r in live]
    varying = sorted({c for s in starts for c in s if len({str(x.get(c)) for x in starts}) > 1})
    sym = tuple((c, c if c in varying else v) for c, v in live[0].start)
    v0 = live[0].start_value if len({str(r.start_value) for r in live}) == 1 else None
    extra = {c: c for c in varying}
    extra.update(
        {p: p for p, (lo, hi, *_) in machine.form.pair.items() if {lo, hi} <= set(varying)}
    )
    d = derive(guarded(machine, set(varying)), sym, v0, an.freq, extra)
    if d.error or not d.exact:
        return delay_gate(an, machine, live, varying, d.error or "not proved with inputs")
    a, f = d.update
    cells = sorted(s.name for s in a.free_symbols | f.free_symbols if s.name in extra)
    prover = Prover(machine, an.freq)
    bad = [
        r for r in live if not prover.equal(_bound(f, cells, dict(r.start)), r.derived.update[1])
    ]
    if bad:
        reason = f"the general form differs from the start {dict(bad[0].start)}"
        return Inherited("none", cells, reason=reason)
    proof = (f"proved for every value of {', '.join(cells)} (z3 over the cells' widths); equal to "
             f"each of the {len(starts)} starts' expressions")  # fmt: skip
    return Inherited("input", cells, (a, f), proof)


def _bound(e, cells: list, start: dict):
    """``e`` with input cells at a start's values."""
    return e.xreplace({sympy.Symbol(c, integer=True): G_value(start, c) for c in cells})


def counts_down(machine, start: dict, cell: str, low: int) -> str | None:
    """None when every firing from ``start`` with ``cell`` above ``low`` keeps the value and the
    other control and lowers ``cell`` by one (z3, every value of ``cell``); else a witness."""
    try:
        cases = machine.step(tuple(sorted({**start, cell: cell}.items())))
    except ValueError as exc:
        return str(exc)
    for conds, value, nxt in cases:
        smt = machine.form.solver()
        smt.solver.add(*(smt.cond(c) for c in conds), smt.term(cell) > low)
        moved = [smt.term(value) != smt.term(machine.cell)]
        for c, t in nxt:
            want = mk(-1, {cell: 1}) if c == cell else start[c]
            moved.append(smt.term(t) != smt.term(want))
        smt.solver.add(z3.Or(*moved))
        if smt.solver.check() != z3.unsat:
            return witness(smt.solver)
    return None


def delay_gate(an, machine, live: list, cells: list, why: str) -> Inherited:
    """One carried counter as a delay: ``[φ >= d]`` then the run of the start whose counter ran out.

    Proved for every ``d``: each firing above the base counter keeps the value and counts down
    one (z3), the base run is derived, and the form at each start's ``d`` solves its recurrence.
    """
    # pylint: disable=too-many-locals
    if len(cells) != 1:
        return Inherited("none", cells, reason=f"{why}; {len(cells)} carried cells")
    cell = cells[0]
    base = min(live, key=lambda r: dict(r.start)[cell])
    low = dict(base.start)[cell]
    bad = counts_down(machine, dict(base.start), cell, low)
    if bad is not None:
        return Inherited("none", cells, reason=f"{why}; {cell} does not count down: {bad}")
    closed = base.derived.closed is not None and all(
        r.start_value is not None and str(r.start_value) == str(base.start_value) for r in live
    )
    a, f = base.derived.update if closed else (base.derived.phase, base.derived.value)
    before = base.start_value if closed else G.V
    gated = G.ge(G.PHI, DELAY) * f.xreplace({G.PHI: G.PHI - DELAY}) + G.lt(G.PHI, DELAY) * before
    domain = (("v", 0, machine.mask), ("N", 0, (1 << machine.form.width["N"]) - 1))
    shown = G.polar(gated, domain)
    prover = GateProver(machine, an.freq)
    if not prover.equal(shown, gated):
        return Inherited("none", cells, reason=f"{why}; the gate's display form differs")
    for r in live:
        d = dict(r.start)[cell] - low
        g = shown.xreplace({DELAY: d})
        if closed:
            same = prover.closed(g, r.derived.value, r.start_value) is None
        else:
            same = prover.equal(g, r.derived.value)
        if not same:
            return Inherited("none", cells, reason=f"the gate differs from the start {d = }")
    phase = sympy.Integer(int(G.PHI in shown.free_symbols)) if a.is_Integer else a
    proof = (f"d = {cell} - ({low}); for every {cell} > {low} a firing keeps v and counts {cell} "
             f"down by one (z3); the start {cell} = {low} is derived; equal to each of the "
             f"{len(live)} starts' expressions")  # fmt: skip
    return Inherited("gate", cells, (phase, shown), proof)


class GateProver(Prover):
    """A prover whose expressions may read the delay ``d``, any value from 0."""

    def env(self, smt, phi) -> dict:
        env = super().env(smt, phi)
        env["d"] = z3.BitVec("d", G.BITS)
        smt.solver.add(env["d"] >= 0, env["d"] < 1 << 20)
        return env
