"""Inductive invariants of a chip unit's hidden cells: where its recurrence over ``v`` is sound.

``G``: guard comparisons over the hidden cells every action list of the player keeps. ``I``: the
strongest conjunction of guard comparisons and domain bounds true after a start's first firing
from any ``G`` state and kept by every firing (Houdini). Under ``G ∧ I`` the recurrence holds.
"""

import ast
import dataclasses

import z3

from unitracker.modfit import grammar as G
from unitracker.modfit.flow import NESTED, cells_of, names, parse
from unitracker.modfit.invariant import inductive
from unitracker.player.modulation import VALUE


@dataclasses.dataclass
class Proof:
    """The invariants of one start and the outcome of the recurrence under them."""

    glob: tuple = ()
    unit: tuple = ()
    ok: bool = True
    why: str = ""


def sources(procs: dict, actions) -> list:
    """Every expression source of ``actions``, through the ``procs`` they call."""
    out, seen, todo = [], set(), [list(actions or [])]
    while todo:
        for a in todo.pop():
            out += [str(a[k]) for k in ("if", "step", "min", "max", "toward") if k in a]
            out += [str(v) for k in ("set", "with") for v in (a.get(k) or {}).values()]
            todo += [a[k] for k in (*NESTED, "for_voices") if a.get(k)]
            if "call" in a and a["call"] not in seen and a["call"] in procs:
                seen.add(a["call"])
                todo.append(procs[a["call"]])
    return out


def comparisons(texts) -> list:
    """Each comparison in ``texts`` and its negation, as sources, in first-seen order."""
    out = {}
    for text in texts:
        for n in ast.walk(parse(text)):
            if isinstance(n, ast.Compare):
                src = ast.unparse(n)
                out.setdefault(src, None)
                out.setdefault(f"not ({src})", None)
    return list(out)


class Checker:
    """z3 checks of one machine's real firings from its graph's control states."""

    def __init__(self, machine, graph):
        self.m, self.f = machine, machine.form
        self.hidden = machine.hidden
        rows = [machine.row(graph, i) for i in range(len(graph.nodes))]
        self.nodes = [{c: v for c, v in st.items() if not c.startswith("_")} for st, _, _ in rows]
        self.rows = [(n, count) for n, (_, _, count) in zip(graph.lengths, rows)]
        self.paths = [
            self.f.paths(machine.real, known, st) for st, (_, known, _) in zip(self.nodes, rows)
        ]
        self.entered = {j for row in graph.cases for _, _, j in row}

    def value(self, state: dict):
        """The chip value term over ``state``."""
        return self.f.wrap(VALUE, self.f.expr(self.m.chip["value"], state))

    def query(self, pre, conds, claim) -> str | None:
        """None when ``pre`` and ``conds`` imply ``claim(smt)``, else a witness."""
        smt = self.f.solver()
        for name, (lo, hi, k, part) in self.f.pair.items():
            if {lo, hi} <= set(self.hidden):
                smt.solver.add(smt.term(name) == smt.term(hi) * (1 << k) + smt.term(part))
        smt.solver.add(*(smt.cond(c) for c in (*pre, *conds)))
        smt.solver.add(z3.Not(claim(smt)))
        if smt.solver.check() == z3.unsat:
            return None
        model = smt.solver.model()
        return ", ".join(
            f"{h}={model.eval(smt.term(h), model_completion=True).as_long()}" for h in self.hidden
        )

    def holds(self, state: dict, texts) -> list:
        """Condition terms of ``texts`` over ``state``, those that fold to True dropped."""
        return [c for t in texts if (c := self.f.truth(self.f.expr(t, state))) is not True]


def candidates(machine, domain) -> tuple:
    """``(global, unit)`` sources: guard comparisons over the hidden cells only, then those
    also reading the start's cells and the chip value's domain bounds."""
    pr, hidden = machine.form.pr, set(machine.hidden)
    cells = set(cells_of(pr)) | {VALUE}
    found = comparisons(sources(machine.form.procs, machine.real) + [machine.chip["value"]])
    glob = [t for t in found if names(t) & cells and names(t) & cells <= hidden]
    own = [t for t in found if names(t) & hidden]
    lo, hi, r, m = domain
    x = f"({machine.chip['value']})"
    own += [f"{x} >= {lo}", f"{x} <= {hi}"] + ([f"{x} % {m} == {r}"] if m > 1 else [])
    return glob, own


def houdini(chk: Checker, glob: list, own: list) -> list:
    """The strongest conjunction of ``own`` true after a first firing from any ``G`` state
    and kept by every firing from a state satisfying it and ``G``."""

    def kept(pre_texts, keep, node) -> list:
        state = chk.nodes[node]
        pre = chk.holds(state, pre_texts)
        out = []
        for t in keep:
            ok = True
            for conds, final in chk.paths[node]:
                c = chk.f.truth(chk.f.expr(t, {**state, **final}))
                if c is False or (
                    c is not True and chk.query(pre, conds, lambda smt, c=c: smt.cond(c))
                ):
                    ok = False
                    break
            if ok:
                out.append(t)
        return out

    keep = kept(glob, own, 0)
    while True:
        nxt = keep
        for i in range(len(chk.nodes)):
            nxt = kept(glob + (nxt if i in chk.entered else []), nxt, i)
        if nxt == keep:
            return keep
        keep = nxt


def recurrence(chk: Checker, inv: list, cls: list, at: dict, value, prover) -> str | None:
    # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    """None when every firing from a state the graph enters and ``inv`` holds in gives
    ``value`` of its chip value at its position, else the first counterexample; the start's
    first firing is the start constant's. A row's firings are one claim over its count ``j``
    at ``φ = φ0 + j``."""
    for i, state in enumerate(chk.nodes):
        if cls[i] not in at or i not in chk.entered:
            continue
        phi, (n, count) = at[cls[i]], chk.rows[i]
        pre, x = chk.holds(state, inv), chk.value(state)
        for conds, final in chk.paths[i]:
            y = chk.value({**state, **final})

            def claim(smt, x=x, y=y, phi=phi, n=n, count=count):
                # pylint: disable=too-many-arguments,too-many-positional-arguments
                p = prover.phase(smt, phi, n)
                if count is not None:
                    smt.solver.add(smt.term(count) == p - phi)
                env = prover.env(smt, p)
                return G.to_z3(value, {**env, "v": smt.term(x)}) == smt.term(y)

            why = chk.query(pre, conds, claim)
            if why is not None:
                return f"state {i} at φ = {phi}: {why}"
    return None


def prove(machine, graph, domain, cls: list, at: dict, value, prover) -> Proof:
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    """The invariants of a chip unit's start and the recurrence ``value`` proved under them."""
    chk = Checker(machine, graph)
    glob, own = candidates(machine, domain)
    glob = [t for t in glob if inductive(machine.form.pr, t)]
    unit = houdini(chk, glob, own)
    why = recurrence(chk, glob + unit, cls, at, value, prover)
    return Proof(tuple(glob), tuple(unit), why is None, why or "")
