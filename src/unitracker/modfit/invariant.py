"""Inductive invariants: comparisons a cell keeps after every action that writes it.

A candidate must hold at the initial values and after every action list assigning its cells,
from any state satisfying it. ``for_voices``, ``run`` and ``fetch`` havoc what they can write.
"""

import functools

import z3

from unitracker.playroutine.expr import with_builtins
from unitracker.modfit import playroutine, resolve
from unitracker.modfit.fit import Fitter, src
from unitracker.modfit.flow import NESTED, Writes, action_lists, cells_of, direct_writes, names
from unitracker.modfit.form import Form, atoms, lin
from unitracker.player.vm import PSEUDO


def bodies(actions):
    """Every ``for_voices`` body nested in an action list."""
    for a in actions:
        if "for_voices" in a:
            yield a["for_voices"]
            yield from bodies(a["for_voices"])
        for key in NESTED:
            yield from bodies(a.get(key) or [])


def units(pr: dict) -> dict:
    """Every action list and nested ``for_voices`` body, by path."""
    out = action_lists(pr)
    for path, actions in list(out.items()):
        out.update({f"{path}.for_voices.{i}": b for i, b in enumerate(bodies(actions))})
    return out


class Slicer:
    """Action lists cut to the actions that can affect a set of cells at their exits."""

    def __init__(self, pr: dict, writes: Writes, procs: dict):
        self.pr, self.writes, self.procs = pr, writes, procs
        self.cells = set(cells_of(pr)) | set(PSEUDO)
        self.functions = with_builtins(pr.get("functions"))
        self.memo = {}

    def reads(self, text) -> set:
        """Cells an expression source reads, through the functions it calls."""
        found, out = names(text), set()
        while found:
            n = found.pop()
            if n in self.functions:
                found |= names(self.functions[n]["expr"]) - out
            out.add(n)
        return out & self.cells

    def seq(self, actions, need: set, exit_need: set, tail: bool = False) -> tuple:
        """``(kept actions, cells needed before)`` given those needed after and at a return.

        ``tail`` is true when kept actions of an enclosing list follow this one.
        """
        out = []
        for a in reversed(actions or []):
            kept, need = self.action(a, need, exit_need, bool(out) or tail)
            out = kept + out
        return out, need

    def assigns(self, sets: dict, need: set) -> tuple:
        """``(kept sets, cells needed before)`` of sequential assignments."""
        kept = {}
        for cell, value in reversed(sets.items()):
            if cell in need:
                kept = {cell: value, **kept}
                need = (need - {cell}) | self.reads(value)
        return kept, need

    def call(self, a: dict, need: set) -> tuple:
        """``seq`` of a call: the proc cut to ``need`` under a new name."""
        key = (a["call"], frozenset(need))
        if key not in self.memo:
            body, before = self.seq(self.pr["procs"][a["call"]], need, need)
            self.memo[key] = (f"{a['call']}#{len(self.memo)}", body, before)
            self.procs[self.memo[key][0]] = body
        name, body, before = self.memo[key]
        kept, before = self.assigns(a.get("with") or {}, before)
        return ([{"call": name, "with": kept}] if body or kept else []), before

    def action(self, a: dict, need: set, exit_need: set, rest: bool) -> tuple:
        """``seq`` of one action, ``rest`` true when later actions are kept."""
        # pylint: disable=too-many-return-statements
        if "return" in a:
            return [a], set(exit_need)
        if "set" in a:
            kept, need = self.assigns(a["set"], need)
            return ([{"set": kept}] if kept else []), need
        if "if" in a:
            (t, nt), (e, ne) = (self.seq(a.get(k), need, exit_need, rest) for k in ("then", "else"))
            if not rest and not any("return" not in x for x in t + e):
                return [], need
            return [{"if": a["if"], "then": t, "else": e}], nt | ne | self.reads(a["if"])
        if "call" in a:
            return self.call(a, need)
        if "accumulate" in a:
            ob, nob = self.seq(a.get("on_bound"), need, exit_need, rest)
            cell = a["accumulate"]
            if cell not in need and not ob:
                return [], need
            reads = {cell}.union(
                *(self.reads(a[k]) for k in ("step", "min", "max", "toward") if k in a)
            )
            return [{**a, "on_bound": ob}], (need - {cell}) | nob | reads
        written = self.writes.of(a)
        return ([a], need - written) if written & need else ([], need)


class Modular(Form):
    """A form whose opaque actions havoc their writes under an invariant source ``inv``."""

    def __init__(self, pr: dict, inv: str):
        super().__init__(pr)
        self.inv, self.writes = inv, Writes(pr)
        self.atoms = names(inv) & set(cells_of(pr))
        self.procs = dict(self.procs)
        self.slicer = Slicer(pr, self.writes, self.procs)
        self.obligations, self.fresh = [], 0

    def holds(self, state: dict):
        """The invariant over ``state``."""
        return self.truth(self.expr(self.inv, state))

    def opaque(self, a: dict, rest, conds, state) -> list:
        """Havoc what ``a`` can write; the invariant is owed before and assumed after."""
        if not a.keys() & {"for_voices", "run", "fetch"}:
            return super().opaque(a, rest, conds, state)
        cells, state = self.writes.of(a), dict(state)
        guarded = bool(cells & self.atoms)
        if guarded:
            self.obligations.append((conds, self.holds(state)))
        for c in sorted(cells):
            self.fresh += 1
            state[c] = f"{c}'{self.fresh}"
            self.width[state[c]] = self.width.get(c)
        after = self.holds(state) if guarded else True
        if after is False:
            return []
        return self.paths(rest, conds if after is True else (*conds, after), state)

    def initial(self) -> bool:
        """True when the invariant holds at the cells' initial values."""
        spec = {**(self.pr.get("global") or {}), **(self.pr.get("voice") or {})}
        start = {c: int((spec[c] or {}).get("init", 0)) for c in self.atoms}
        return self.implies((), self.holds(start))

    def broken(self) -> str | None:
        """The first action list writing the invariant's cells that can break it, or None."""
        start = self.holds({})
        writers = [(p, a) for p, a in units(self.pr).items() if direct_writes(a) & self.atoms]
        for path, actions in sorted(writers, key=lambda pa: len(str(pa[1]))):
            self.obligations = []
            sliced, _ = self.slicer.seq(actions, self.atoms, self.atoms)
            ends = [(c, self.holds(s)) for c, s in self.paths(sliced, (start,), {})]
            if not all(self.implies(c, h) for c, h in ends + self.obligations):
                return path
        return None


def inductive(pr: dict, inv: str) -> bool:
    """True when ``inv`` holds initially and every action list writing its cells keeps it."""
    m = Modular(pr, inv)
    return m.initial() and m.broken() is None


def compared(form: Form, cond, cell: str):
    """``B`` when ``cond`` compares one unit term over ``cell`` against ``B``, else None."""
    if cond[0] not in ("lt", "eq"):
        return None
    c, coeffs = lin(cond[1])
    own = {a: k for a, k in coeffs.items() if cell in atoms(a)}
    if len(own) != 1 or set(own.values()) - {1, -1}:
        return None
    (_, k), rest = own.popitem(), {a: v for a, v in coeffs.items() if cell not in atoms(a)}
    return form.scale(-k, form.add(c, *(form.scale(v, a) for a, v in rest.items())))


def candidates(fitter: Fitter, cell: str, cases: list) -> list:
    """Comparisons of ``cell`` with its cases' bound terms, reload values, 0 and its maximum.

    One source per distinct condition, the shortest first.
    """
    f = fitter.form
    _, bounds, _ = fitter.ingredients(cell, cases)
    bounds |= {b for _, dyn, _ in cases for c in dyn if (b := compared(f, c, cell)) is not None}
    top = {(1 << f.width[cell]) - 1} if f.width.get(cell) else set()
    texts = sorted(
        (
            f"{cell} {op} {src(t)}"
            for t in bounds | {0} | top
            if cell not in atoms(t)
            for op in ("<=", "<", ">=", ">")
        ),
        key=lambda s: (len(s), s),
    )
    out = {}
    for text in texts:
        cond = f.truth(f.expr(text, {}))
        if not isinstance(cond, bool):
            out.setdefault(cond, text)
    return list(out.values())


@functools.lru_cache(maxsize=None)
def invariants(name: str, path: str, cell: str) -> tuple:
    """The inductive candidates of a library modulation's ``cell``, none implied by another."""
    fitter = Fitter(playroutine(name))
    cases = fitter.cases(fitter.paths(resolve(fitter.form.pr, path), cell), cell)
    found = [i for i in candidates(fitter, cell, cases) if inductive(fitter.form.pr, i)]
    f = fitter.form
    conds = {i: f.truth(f.expr(i, {})) for i in found}
    return tuple(
        i
        for i in found
        if not any(
            j != i
            and f.implies((conds[j],), conds[i])
            and not (f.implies((conds[i],), conds[j]) and found.index(j) > found.index(i))
            for j in found
        )
    )


def fit_under(fitter: Fitter, actions, cell: str, guard, invs) -> tuple:
    """``(invariant, proved accumulator)`` for a dispatch ``guard``, or ``(conjunction, None)``.

    Candidates come from every case of the cell; each invariant is tried alone, then all.
    """
    paths = fitter.paths(actions, cell)
    cands = fitter.candidates(cell, fitter.cases(paths, cell))
    f = fitter.form
    tries = [*invs, " and ".join(invs)] if len(invs) > 1 else list(invs)
    for inv in tries:
        assume = (*guard, f.truth(f.expr(inv, {})))
        for cand in cands:
            if fitter.check(paths, assume, cell, cand)[0] == z3.unsat:
                return inv, cand
    return " and ".join(invs), None
