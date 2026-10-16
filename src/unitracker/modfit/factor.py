"""Generic accumulator factorisation: step, hit, respond and gate from a cell's guarded cases.

An effect on ``x`` is ``x' = respond(v0, v)`` when ``hit(v)``, else ``v = v0 + step``, under
``gate``: brackets reading ``x`` are the hit, values under it the respond, the remaining change
the step and the other conditions the gate. The rebuilt accumulator is proved equal with z3.
"""

import dataclasses

import z3

from unitracker.modfit.fit import Fitter, model_text, src
from unitracker.modfit.flow import Writes
from unitracker.modfit.invariant import Slicer, compared
from unitracker.modfit.form import CONDS, atoms, lin, mk, show

V, V0, S = "v", "v0", "s"


@dataclasses.dataclass
class Factor:
    """One gate group of a factorised effect: its lambdas as source text, or why none holds."""

    # pylint: disable=too-many-instance-attributes

    gate: tuple
    step: object = None
    hit: str = "-"
    respond: str = "-"
    kinds: tuple = ()
    status: str = "proved"
    texts: dict = dataclasses.field(default_factory=dict)
    evolving: set = dataclasses.field(default_factory=set)

    @property
    def proved(self) -> bool:
        """True when the rebuilt accumulator is proved equal to the original."""
        return self.status == "proved"


def brackets(t, inside: bool = False):
    """Every ``ite`` test and Iverson bracket in ``t``, outermost first; in tests if ``inside``."""
    if not isinstance(t, tuple):
        return
    if t[0] in ("ite", "iv"):
        yield t[1]
    if t[0] == "lin":
        for a, _ in t[2]:
            yield from brackets(a, inside)
    elif inside or t[0] not in CONDS:
        for x in t[1:]:
            yield from brackets(x, inside)


def walk_terms(t):
    """Every tuple node of ``t``."""
    if isinstance(t, tuple):
        yield t
        for x in (a for a, _ in t[2]) if t[0] == "lin" else t[1:]:
            yield from walk_terms(x)


def direct(t) -> set:
    """The cells ``t`` reads other than as a table or record index."""
    if isinstance(t, str):
        return {t}
    if not isinstance(t, tuple) or t[0] in ("idx", "rec", "fld"):
        return set()
    return (
        set().union(*(direct(a) for a, _ in t[2]))
        if t[0] == "lin"
        else set().union(*map(direct, t[1:]))
    )


def flipped(t, cell: str) -> bool:
    """True when ``t`` is ``cell`` with constant bits inverted: ``cell ^ c`` or ``c - cell``."""
    if isinstance(t, tuple) and t[0] == "xor":
        return cell in t[1:] and any(isinstance(x, int) for x in t[1:])
    if isinstance(t, tuple) and t[0] == "and" and isinstance(t[2], int):
        return flipped(t[1], cell)
    c, coeffs = lin(t)
    return coeffs == {cell: -1} and c != 0


class Factorer:
    """Factorisation of guarded cases into the generic accumulator, over one ``Merge`` form."""

    def __init__(self, form):
        self.form = form
        self.fitter = Fitter(form.pr, form)
        form.procs = dict(form.procs)
        self.xs, self.chosen = set(), set()
        self.writes = Writes(form.pr)
        self.slicer = Slicer(form.pr, self.writes, form.procs)
        for name in (V, V0, S):
            if name in form.width:
                raise ValueError(f"cell {name!r} shadows a lambda argument")

    def parts(self, cell: str) -> set:
        """``cell`` and, for a fused pair, its two cells."""
        return {cell, *self.form.pair.get(cell, (None, None))[:2]} - {None}

    def paths(self, actions, cell: str, extra=()) -> list:
        """The paths of ``actions`` cut to what can affect ``cell`` and ``extra``, pairs fused."""
        parts = self.parts(cell) | set(extra)
        sliced, _ = self.slicer.seq(actions, parts, parts)
        paths = self.form.paths(sliced)
        return self.form.fused(paths) if cell in self.form.pair else paths

    def unit(self, actions, cell: str, assume=()) -> list:
        """``factor`` of ``cell`` over an action list cut to it, every cell it writes split on.

        A second cut keeps the written cells the gates and steps read, so a respond's mode flip
        is seen.
        """
        moved = self.writes.of(actions)
        out = self.factor(self.paths(actions, cell), cell, moved, assume)
        read = set().union(*(direct(g) for x in out for g in (*x.gate, x.step or 0)))
        extra = (read & moved) - self.parts(cell)
        return self.factor(self.paths(actions, cell, extra), cell, moved, assume) if extra else out

    def split(self, t, watched: set, conds) -> list:
        """``[(conds, value)]`` of ``t`` split at each bracket reading ``watched``."""
        f = self.form
        c = next((b for b in self.splits(t) if atoms(b) & watched or b in self.chosen), None)
        if c is None:
            return [(conds, t)]
        out = []
        for g in (c, f.negate(c)):
            if f.feasible((*conds, g)):
                sub = f.under(t, g)
                if c in brackets(sub) or f.negate(c) in brackets(sub):
                    return [(conds, t)]
                out += self.split(sub, watched, (*conds, g))
        return out

    def simplify(self, t, conds):
        """``t`` with every test ``conds`` decide replaced by its value."""
        f, lits = self.form, {}
        tests = [u for u in walk_terms(t) if u[0] in CONDS]
        for c in dict.fromkeys([*brackets(t, True), *tests]):
            if f.implies(conds, c):
                lits[c] = True
            elif f.implies(conds, f.negate(c)):
                lits[c] = False
        return f.subst(t, lits) if lits else t

    def splits(self, t):
        """The brackets of ``t``; ``chosen`` holds the ``ite`` tests whose branches read x."""
        self.chosen = {
            u[1] for u in walk_terms(t) if u[0] == "ite" and (atoms(u[2]) | atoms(u[3])) & self.xs
        }
        return brackets(t)

    def leaves(self, paths, cell: str, moved: set) -> dict:
        """``{gate: [(hit conds, value, final state)]}``: the cases of ``cell`` by gate."""
        xs, groups = self.parts(cell), {}
        self.xs = xs
        for conds, final in paths:
            ordered = [c for c in conds if not atoms(c) & xs] + [c for c in conds if atoms(c) & xs]
            conds = ()
            for c in ordered:
                c = self.simplify(c, conds)
                conds += () if c is True else (c,)
            if False in conds:
                continue
            value = self.simplify(final.get(cell, cell), conds)
            for cs, value in self.split(value, xs | moved, conds):
                gate = frozenset(c for c in cs if not atoms(c) & xs)
                dyn = tuple(c for c in cs if atoms(c) & xs)
                groups.setdefault(gate, []).append((dyn, value, final))
        return groups

    def delta(self, cell: str, value):
        """``d`` when ``value`` is ``cell + d`` wrapped to its width, ``d`` signed, free of it."""
        f = self.form
        w = f.width.get(cell)
        inner = value[1] if w and isinstance(value, tuple) and value[0] == "and" else value
        if w and inner is not value and value[2] != (1 << w) - 1:
            return None
        if inner == cell:
            return 0
        if lin(inner)[1].get(cell) != 1:
            return None
        d = f.sub(inner, cell)
        if atoms(d) & self.parts(cell):
            return None
        if w and f.rng(f.modular(d, 1 << w))[0] >= 1 << (w - 1):
            return f.sub(f.modular(d, 1 << w), 1 << w)
        return d

    def factor(self, paths, cell: str, moved, assume=()) -> list:
        """``[Factor]`` per gate group of ``cell``'s changed cases; ``moved`` cells are split on.

        Groups whose gates differ in one test of bound cells, with the same hit and respond, are
        one group whose step selects on that test.
        """
        moved = set(moved) - self.parts(cell)
        groups = {
            gate: cases
            for gate, cases in self.leaves(paths, cell, moved).items()
            if any(v != cell for _, v, _ in cases)
        }
        out = {
            g: self.group(paths, cell, tuple(sorted(g, key=show)), c, assume)
            for g, c in groups.items()
        }
        merged = True
        while merged:
            merged = False
            for a, b in (
                (a, b)
                for a in out
                for b in out
                if show(sorted(a, key=show)) < show(sorted(b, key=show))
            ):
                joined = self.merge(paths, cell, a, b, out, assume)
                if joined is not None:
                    del out[a], out[b]
                    out[a & b] = joined
                    merged = True
                    break
        return list(out.values())

    def merge(self, paths, cell, a, b, out, assume):
        """The group of gates ``a`` and ``b`` joined, when they differ only in a bound test."""
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        fa, fb = out[a], out[b]
        shape = (fa.proved, fa.hit, fa.respond, fa.kinds)
        if shape != (fb.proved, fb.hit, fb.respond, fb.kinds) or shape[:2] != (True, "-"):
            return None
        if len(a - b) != 1 or len(b - a) != 1:
            return None
        (c,), (d,) = a - b, b - a
        if self.form.relation(c, d) != "complement" or direct(c) & fa.evolving:
            return None
        step = self.form.ite(c, fa.step, fb.step)
        joined = dataclasses.replace(fa, gate=tuple(sorted(a & b, key=show)), step=step)
        joined.texts = {**fa.texts, "step": src(step)}
        joined.status = self.prove(paths, cell, joined.gate, assume, joined.texts)
        return joined

    def choose(self, cell: str, cases) -> tuple:
        """``(step, stepped value)``: the change shared by most cases, nonzero before zero."""
        steps = [d for _, v, _ in cases if (d := self.delta(cell, v)) is not None]
        rank = {s: (-steps.count(s), s == 0, show(s)) for s in steps}
        step = min(rank, key=rank.get) if steps else 0
        return step, self.form.wrap(cell, self.form.add(cell, step))

    def group(self, paths, cell, gate, cases, assume) -> Factor:
        """The factorisation of one gate group."""
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        xs = self.parts(cell)
        step, v = self.choose(cell, cases)
        hits = [(dyn, u, fin) for dyn, u, fin in cases if u != v]
        out = Factor(gate, step)
        out.evolving = {c for _, u, fin in cases if u == v for c, t in fin.items() if t != c} - xs
        hit = self.form.junction("lor", [self.form.junction("land", list(d)) for d, _, _ in hits])
        reads = any(atoms(u) & xs for _, u, _ in hits)
        if reads and len(hits) == len(cases) or any(not d for d, _, _ in hits) and len(cases) > 1:
            out.status = "step reads x"
        elif direct(hit) - xs & out.evolving:
            out.status = "hit reads non-bound state"
        elif any(direct(u) & out.evolving for _, u, _ in hits):
            out.status = "respond outside (v0, v, bounds)"
        else:
            out.texts = self.texts(cell, step, gate, hits)
            out.hit, out.respond, out.kinds = (out.texts[k] for k in ("hit", "respond", "kinds"))
            out.status = self.prove(paths, cell, gate, assume, out.texts)
        return out

    def views(self, cell: str, step, t) -> str:
        """The shorter source of ``t`` over ``v0`` and ``v`` or over ``v``, the step named ``s``."""
        f = self.form
        f.width[V] = f.width[V0] = f.width.get(cell)
        mixed = f.subst(f.subst(t, {cell: V0}), {f.wrap(V0, f.add(V0, step)): V})
        over_v = f.subst(t, {cell: f.wrap(V, f.sub(V, step))})
        del f.width[V], f.width[V0]
        texts = [src(mixed), src(over_v)]
        if not isinstance(step, int):
            texts = [x.replace(src(step), S) for x in texts]
        return min(texts, key=len)

    def prune(self, gate, dyn) -> tuple:
        """``dyn`` without the conditions the gate and the others imply."""
        keep = list(dyn)
        for c in dyn:
            rest = [x for x in keep if x is not c]
            if self.form.implies((*gate, *rest), c):
                keep = rest
        return tuple(keep)

    def texts(self, cell, step, gate, hits) -> dict:
        """Source text of the step, hit and respond lambdas, and each respond's kind."""
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        f = self.form
        pieces = [(self.prune(gate, dyn), u, fin) for dyn, u, fin in hits]
        hit = f.junction("lor", [f.junction("land", list(dyn)) for dyn, _, _ in pieces])
        respond = self.views(cell, step, pieces[-1][1]) if pieces else "-"
        for dyn, u, _ in reversed(pieces[:-1]):
            cond = self.views(cell, step, f.junction("land", list(dyn)))
            respond = f"({self.views(cell, step, u)} if {cond} else {respond})"
        return {
            "step": src(step),
            "hit": "-" if hit is False else "1" if hit is True else self.views(cell, step, hit),
            "respond": respond,
            "kinds": tuple(dict.fromkeys(self.kind(cell, step, *p) for p in pieces)),
        }

    def kind(self, cell, step, dyn, u, final) -> str:
        """``hold``, ``clamp``, ``bounce``, ``reload``, ``reflect`` or ``reads v`` of a respond.

        A clamp is a bound the hit compares against, at either strictness.
        """
        # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-return-statements
        f, xs = self.form, self.parts(cell)
        if u == cell:
            return "hold"
        if not atoms(u) & xs:
            bounds = {b for c in dyn if (b := self.bound(c, cell)) is not None}
            if bounds & {f.add(u, k) for k in (-1, 0, 1)}:
                return "clamp"
            if any(flipped(t, c) for c, t in final.items() if c not in xs):
                return "bounce"
            return "reload"
        if u == f.wrap(cell, f.sub(cell, step)):
            return "reflect"
        return "reads v"

    def bound(self, cond, cell: str):
        """``B`` when ``cond`` compares one unit term over ``cell`` (or signed) with ``B``."""
        f = self.form
        cond = cond[1] if cond[0] == "not" else cond
        w = f.width.get(cell)
        if w and cond[0] in ("lt", "eq"):
            c, coeffs = lin(cond[1])
            sign = f.iv(f.ge(cell, 1 << (w - 1)))
            if coeffs.get(cell) and coeffs.get(sign) == -coeffs[cell] << w:
                del coeffs[sign]
                cond = (cond[0], mk(c, coeffs), 0)
        return compared(f, cond, cell)

    def prove(self, paths, cell, gate, assume, texts) -> str:
        """``proved``, or the witness where the accumulator rebuilt from ``texts`` differs."""
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        f = self.form
        step = f.expr(texts["step"], {})
        v = f.wrap(cell, f.add(cell, step))
        env = {V0: cell, V: v, S: step}
        hit = f.truth(f.expr(texts["hit"], {}, env)) if texts["hit"] != "-" else False
        respond = f.wrap(cell, f.expr(texts["respond"], {}, env)) if hit is not False else v
        cpaths = [
            ((g,), {cell: value})
            for g, value in ((hit, respond), (f.negate(hit), v))
            if g is not False and f.feasible((*gate, g) if g is not True else gate)
        ]
        result, model = self.fitter.check_paths(paths, (*gate, *assume), cell, cpaths)
        if result == z3.unsat:
            return "proved"
        return f"witness {model_text(model)}" if model is not None else "undecided"


def fused_moved(form, moved) -> set:
    """``moved`` with each pair whose two cells both move written as the pair."""
    out = set(moved)
    for name, (lo, hi, _, _) in form.pair.items():
        if {lo, hi} <= out:
            out = (out - {lo, hi}) | {name}
    return out


def keyword_factors(comp, code: int, when) -> list:
    """``[(cell, Factor)]`` of a keyword's composed per-tick effect, each moved cell."""
    eff = comp.compose(code, when, "plain", fits=False)
    fz = factorer(comp.form)
    out = []
    for cell in sorted(fused_moved(comp.form, eff["moved"])):
        paths = comp.tick_paths(cell, eff["chosen"], eff["conds"], eff["begin"])
        out += [(cell, x) for x in fz.factor(paths, cell, eff["moved"])]
    return out


FACTORERS = {}


def factorer(form) -> Factorer:
    """The factorer of a form, made once."""
    if id(form) not in FACTORERS:
        FACTORERS[id(form)] = (form, Factorer(form))
    return FACTORERS[id(form)][1]
