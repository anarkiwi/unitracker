"""Start values a modulator inherits from the one before it, as inputs of one expression.

A modulator whose expression differs between starts only through cells another modulator left
is derived once with those cells symbolic: they become inputs. A cell the firing counts from its
inherited value is a phase instead: each start is the longest start's run from an offset ``φ0``.
"""

import copy
import dataclasses

import sympy

from unitracker.modfit import grammar as G
from unitracker.modfit.phase import Prover, derive
from unitracker.modfit.replay import G_value


@dataclasses.dataclass
class Inherited:
    """How a modulator's inherited start cells enter its one expression."""

    kind: str
    cells: list
    update: tuple | None = None
    proof: str = ""
    reason: str = ""

    @property
    def text(self) -> str:
        """``a; f`` of the expression."""
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
                    raise ValueError(f"{c} counts from its inherited value: a phase, not an input")
        return out

    m.step = step
    return m


def inherit(an, mod) -> Inherited:
    """The inherited cells of a modulator with one expression per start, as inputs or a phase."""
    machine = an.machines[mod.unit["name"]]
    starts = [dict(r.start) for r in mod.live]
    varying = sorted({c for s in starts for c in s if len({str(x.get(c)) for x in starts}) > 1})
    sym = tuple((c, c if c in varying else v) for c, v in mod.live[0].start)
    v0 = mod.live[0].start_value if len({str(r.start_value) for r in mod.live}) == 1 else None
    extra = {c: c for c in varying}
    extra.update(
        {p: p for p, (lo, hi, *_) in machine.form.pair.items() if {lo, hi} <= set(varying)}
    )
    d = derive(guarded(machine, set(varying)), sym, v0, an.freq, extra)
    if d.error or not d.exact:
        return phase_offset(mod, machine, an, varying, d.error or "not proved with inputs")
    a, f = d.update
    cells = sorted(s.name for s in a.free_symbols | f.free_symbols if s.name in extra)
    prover = Prover(machine, an.freq)
    bad = [s for s in starts if not prover.equal(_bound(f, cells, s), _update(mod, s)[1])]
    if bad:
        return Inherited("none", cells, reason=f"the expression differs from the start {bad[0]}")
    proof = (f"proved for every value of {', '.join(cells)} (z3 over the cells' widths); equal to "
             f"each of the {len(starts)} starts' expressions")  # fmt: skip
    return Inherited("input", cells, (a, f), proof)


def _bound(e, cells: list, start: dict):
    """``e`` with input cells at a start's values."""
    return e.xreplace({sympy.Symbol(c, integer=True): G_value(start, c) for c in cells})


def _update(mod, start: dict) -> tuple:
    """``(a, f)`` of the live start with these cells."""
    return next(r for r in mod.live if dict(r.start) == start).derived.update


def phase_offset(mod, machine, an, cells: list, why: str) -> Inherited:
    """Each start as the longest start's run entered at ``φ0``, the run's position of its state."""
    # pylint: disable=too-many-locals
    base = max(mod.live, key=lambda r: (r.derived.states, str(r.start)))
    nodes = machine.explore(base.start).nodes
    a0, f0 = base.derived.update
    prover = Prover(machine, an.freq)
    offsets = {}
    for r in mod.live:
        if r.start not in nodes:
            why += f"; the start {dict(r.start)} is not on the run from {dict(base.start)}"
            return Inherited("none", cells, reason=why)
        k = nodes.index(r.start)
        if not prover.equal(f0.xreplace({G.PHI: G.PHI + k}), r.derived.update[1]):
            why += f"; the run from φ0 = {k} differs from the start {dict(r.start)}"
            return Inherited("none", cells, reason=why)
        offsets[tuple(dict(r.start)[c] for c in cells)] = k
    totals = {key[0] + k for key, k in offsets.items()} if len(cells) == 1 else set()
    if len(totals) != 1:
        return Inherited(
            "none", cells, reason=f"{why}; φ0 is no sum with one inherited cell: {offsets}"
        )
    shift = totals.pop() - sympy.Symbol(cells[0], integer=True)
    update = (a0, f0.xreplace({G.PHI: G.PHI + shift}))
    first = ", ".join(f"{c} = {dict(base.start)[c]}" for c in cells)
    proof = (f"{why}; φ0 = {G.text(shift)}: each start equals the run from {first} entered at φ0 "
             "(z3, every φ)")  # fmt: skip
    return Inherited("phase", cells, update, proof)
