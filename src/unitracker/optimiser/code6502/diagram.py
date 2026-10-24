"""A canonical decision diagram of a routine's outputs: the atomic predicates of every
if-then-else become tests, ordered to minimise size (ties by the atoms' printed order), with
infeasible arms pruned by z3 and identical subtrees shared; the leaves are straight-line terms.

A node is ``("leaf", ((name, term), ...))`` (the outputs it changes), ``("test", atom, then,
else)`` or ``("seq", name, first, rest)`` (``first`` writes output ``name``, its leaves
continuing to ``rest``).
"""

import functools
import time

import numpy as np
import z3

from unitracker.optimiser.code6502.terms import COMMUTATIVE, WORD, dag, substitute, to_z3
from unitracker.optimiser.code6502.vector import Bank

TEST_COST = 4
JOIN_COST = 3
INFINITE = float("inf")
BUDGET = 20000
SECONDS = 60
BINDS = 6
CONNECTIVES = ("Not", "PAnd", "POr")


class DiagramError(ValueError):
    """Outputs no diagram computes."""


def canon(term):
    """``term`` with commutative operands in printed order and ``Bit p`` as ``Ite p 1 0``."""
    if not isinstance(term, tuple):
        return term
    args = tuple(canon(a) for a in term[1:])
    if term[0] in COMMUTATIVE:
        args = tuple(sorted(args, key=repr))
    if term[0] == "Bit":
        return ("Ite", args[0], ("BC", 1), ("BC", 0))
    return (term[0],) + args


def atoms(term, out=None) -> set:
    """The atomic predicates the if-then-else conditions of ``term`` test."""
    out = set() if out is None else out
    if not isinstance(term, tuple):
        return out
    if term[0] in ("Ite", "WIte"):
        _predicates(term[1], out)
    for a in term[1:]:
        atoms(a, out)
    return out


def _predicates(p, out):
    if p[0] in CONNECTIVES:
        for a in p[1:]:
            _predicates(a, out)
    elif p[0] not in ("PT", "PF"):
        out.add(p)


def simp(term, known: dict):  # pylint: disable=too-many-return-statements
    """``term`` with each atom of ``known`` ``{atom: bool}`` replaced and the connectives and
    if-then-elses that leaves constant folded."""
    if not isinstance(term, tuple):
        return term
    if term in known:
        return ("PT",) if known[term] else ("PF",)
    op = term[0]
    if op in ("Ite", "WIte"):
        c = simp(term[1], known)
        if c[0] in ("PT", "PF"):
            return simp(term[2] if c[0] == "PT" else term[3], known)
        a, b = simp(term[2], known), simp(term[3], known)
        return a if a == b else (op, c, a, b)
    args = tuple(simp(a, known) for a in term[1:])
    if op == "Not" and args[0][0] in ("PT", "PF"):
        return ("PF",) if args[0][0] == "PT" else ("PT",)
    if op in ("PAnd", "POr"):
        absorb, unit = ("PF", "PT") if op == "PAnd" else ("PT", "PF")
        if any(a[0] == absorb for a in args):
            return (absorb,)
        rest = [a for a in args if a[0] != unit]
        return rest[0] if len(rest) == 1 else (unit,) if not rest else (op,) + tuple(rest)
    return (op,) + args


class Builder:  # pylint: disable=too-many-instance-attributes
    """Builds the diagram of ``outputs`` ``{name: term}`` of a routine of ``shape``, priced
    by ``costs``, ``(leaf cost, test cost)`` (bytes; by default term and test counts).

    After a ``seq`` writes output ``o``, its input is gone: the rest may test what ``o`` holds
    then, an atom over ``o'`` (bound to the term written), but no leaf may read ``o``.
    """

    def __init__(self, outputs: dict, shape, costs=None):
        self.inputs = shape.inputs()
        self.leaf_cost, self.test_cost = costs or (
            lambda terms, gone: dag(t for _, t in terms) + 1,
            lambda atom, gone: TEST_COST,
        )
        self.bank, self.memos = Bank(shape), {}
        self.outputs = tuple((k, canon(t)) for k, t in outputs.items() if t != self.identity(k))
        self.feasible = functools.lru_cache(maxsize=None)(self._feasible)
        self.best = functools.lru_cache(maxsize=None)(self._best)
        self.decide = functools.lru_cache(maxsize=None)(self._decide)
        self.envs, self.solvers, self.states, self.own, self.regions = {}, {}, 0, {}, {}
        self.deadline = time.monotonic() + SECONDS

    def values(self, term, gone: tuple):
        """``term`` over the bank of samples, each gone output's prime bound."""
        if gone not in self.memos:
            self.memos[gone] = {
                _prime(self.identity(name)): self.values(written, gone[:k])
                for k, (name, written) in enumerate(gone)
            }
        return self.bank.evaluate(term, self.memos[gone])

    def holds(self, known: frozenset, gone: tuple):
        """The samples where each of ``known`` holds."""
        mask = np.ones(self.bank.count, dtype=bool)
        for atom, value in known:
            truth = self.values(atom, gone)
            mask &= truth if value else ~truth
        return mask

    def env(self, gone: tuple):
        """``(inputs, memo)`` with each gone output's ``o'`` bound to its written term."""
        if gone not in self.envs:
            inputs = dict(self.inputs)
            for name, term in gone:
                inputs[f"{name}'"] = to_z3(term, inputs, {})
            self.envs[gone] = (inputs, {})
        return self.envs[gone]

    def z3(self, term, gone: tuple):
        """The z3 term of ``term`` with the gone outputs' primes bound."""
        inputs, memo = self.env(gone)
        return to_z3(term, inputs, memo)

    def solver(self, gone: tuple):
        """``(solver, literals, cores)`` of ``gone``: an incremental solver where literal
        ``literals[(atom, value)]`` assumes ``atom`` is ``value``, and the unsat cores found."""
        if gone not in self.solvers:
            self.solvers[gone] = (z3.Solver(), {}, [])
        return self.solvers[gone]

    def assume(self, known: frozenset, gone: tuple) -> list:
        """The literals assuming ``known``."""
        solver, literals, _ = self.solver(gone)
        out = []
        for atom, value in sorted(known, key=repr):
            if (atom, value) not in literals:
                flag = z3.Bool(f"a{len(literals)}")
                solver.add(flag == self.z3(atom, gone))
                literals[(atom, True)], literals[(atom, False)] = flag, z3.Not(flag)
            out.append(literals[(atom, value)])
        return out

    def _feasible(self, known: frozenset, gone: tuple) -> bool:
        solver, literals, cores = self.solver(gone)
        if any(core <= known for core in cores):
            return False
        if self.holds(known, gone).any():
            return True
        assumptions = self.assume(known, gone)
        if solver.check(*assumptions) != z3.unsat:
            return True
        found = {str(c) for c in solver.unsat_core()}
        cores.append(frozenset(p for p in known if str(literals[p]) in found))
        return False

    def _decide(self, term, known: frozenset, gone: tuple):
        """``term`` with each if-then-else ``known`` decides taken, arms ``known`` makes
        equal merged (z3, each condition assumed down its arm)."""
        if not isinstance(term, tuple):
            return term
        op = term[0]
        if op in ("Ite", "WIte"):
            c = term[1]
            arms = [known | {(c, v)} for v in (True, False)]
            ok = [self.feasible(a, gone) for a in arms]
            if not ok[1]:
                return self.decide(term[2], known, gone)
            if not ok[0]:
                return self.decide(term[3], known, gone)
            a = self.decide(term[2], arms[0], gone)
            b = self.decide(term[3], arms[1], gone)
            return a if a == b else (op, c, a, b)
        return (op,) + tuple(self.decide(a, known, gone) for a in term[1:])

    def _best(self, known: frozenset, terms: tuple, gone: tuple):  # pylint: disable=R0912
        """``(cost, leaves, node)`` of the cheapest diagram of ``terms`` ``((name, term),
        ...)`` under ``known`` ``{(atom, bool)}`` with outputs ``gone`` written: a test of an
        atom, or one output's diagram then the rest's (``("seq", name, first, rest)``)."""
        # pylint: disable=too-many-locals
        terms = tuple(
            (k, t)
            for k, t in ((k, self.decide(simp(t, dict(known)), known, gone)) for k, t in terms)
            if t != self.identity(k)
        )
        lost = [self.identity(o) for o, _ in gone]
        live = set()
        for _, t in terms:
            atoms(t, live)
        if not live:
            if any(_has(t, x) for _, t in terms for x in lost):
                return INFINITE, 1, None
            node = ("leaf", terms)
            self.own[node] = self.leaf_cost(terms, tuple(o for o, _ in gone))
            return self.own[node], 1, node
        candidates = set()
        for atom in live:
            primed = atom
            for o, _ in gone:
                primed = substitute(primed, {self.identity(o): _prime(self.identity(o))})
            candidates.add(primed)
        decided = {a for a, _ in known}
        candidates = [
            a
            for a in sorted(candidates, key=lambda a: (-_spread(a, terms), repr(a)))
            if a not in decided and not any(_has(a, x) for x in lost)
        ]
        for atom in candidates:
            arms = [known | {(atom, v)} for v in (True, False)]
            ok = [a for a in arms if self.feasible(a, gone)]
            if len(ok) == 1:
                return self.best(ok[0], terms, gone)
            either = self.either(known, atom, terms, gone)
            if either is not None:
                return self.best(known, either, gone)
        self.states += 1
        if time.monotonic() > self.deadline + 2 * SECONDS:
            raise DiagramError(f"no diagram within {3 * SECONDS} s")
        if self.exhausted():
            candidates = candidates[:1]
        options = [(INFINITE, 1, None)]
        for atom in candidates:
            arms = [known | {(atom, v)} for v in (True, False)]
            (ct, lt, then), (ce, le, other) = (self.best(a, terms, gone) for a in arms)
            if INFINITE in (ct, ce):
                continue
            node = ("test", atom, then, other)
            self.own[node] = self.test_cost(atom, tuple(o for o, _ in gone))
            options.append((self.shared(node), lt + le, node))
        if (
            not self.exhausted()
            and "phi" not in {o for o, _ in gone}
            and not any(_has(t, ("WV", "phi")) for _, t in terms)
        ):
            for shared in _shared(terms)[:BINDS]:
                primed = ("WV", "phi'")
                rest = tuple((k, substitute(t, {shared: primed})) for k, t in terms)
                options.append(self.seq(known, "phi", (("phi", shared),), rest, gone, shared))
        sets = {k: atoms(t) for k, t in terms}
        for k, (name, term) in enumerate(terms):
            rest = terms[:k] + terms[k + 1 :]
            if not rest or (self.exhausted() and options[-1][0] < INFINITE):
                continue
            if all(sets[name] == sets[other] for other, _ in rest) and not gone:
                continue
            options.append(self.seq(known, name, ((name, term),), rest, gone, term))
        return min(options, key=lambda o: o[0])

    def exhausted(self) -> bool:
        """Whether the search is past its state budget or deadline, so goes greedy."""
        return self.states > BUDGET or time.monotonic() > self.deadline

    def seq(self, known, name, first, rest, gone, written):
        """The option writing output ``name`` by diagram ``first`` and then ``rest``."""
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        cf, lf, head = self.best(known, first, gone)
        cr, lr, tail = self.best(known, rest, gone + ((name, written),))
        if INFINITE in (cf, cr):
            return INFINITE, 1, None
        node = ("seq", name, head, tail)
        self.own[node] = (JOIN_COST - 1) * lf - JOIN_COST
        return self.shared(node), lr, node

    def shared(self, node) -> float:
        """The cost of a diagram as its code shares it: each distinct node of a region once,
        a ``seq``'s parts each a region of their own."""
        if node in self.regions:
            return self.regions[node]
        seen, total, todo = set(), 0, [node]
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            seen.add(n)
            total += self.own[n]
            if n[0] == "test":
                todo += [n[2], n[3]]
            elif n[0] == "seq":
                total += self.shared(n[2]) + self.shared(n[3])
        self.regions[node] = total
        return total

    def either(self, known: frozenset, atom, terms: tuple, gone: tuple):
        """``terms`` with ``atom`` fixed one way where that holds the other way too (a test
        that need not be made), the false way first; else None."""
        if atom not in {a for _, t in terms for a in atoms(t)}:
            return None
        for value in (False, True):
            fixed = tuple((k, simp(t, {atom: value})) for k, t in terms)
            where = self.holds(known | {(atom, not value)}, gone)
            if any(
                (where & (self.values(t, gone) != self.values(f, gone))).any()
                for (_, t), (_, f) in zip(terms, fixed)
            ):
                continue
            solver = self.solver(gone)[0]
            assumptions = self.assume(known | {(atom, not value)}, gone)
            solver.push()
            solver.add(
                z3.Or(
                    [self.z3(t, gone) != self.z3(f, gone) for (_, t), (_, f) in zip(terms, fixed)]
                )
            )
            proved = solver.check(*assumptions) == z3.unsat
            solver.pop()
            if proved:
                return fixed
        return None

    def identity(self, name: str):
        """The term of output ``name`` unchanged."""
        return ("WV" if self.inputs[name].size() == 16 else "BV", name)

    def build(self):
        """The cheapest diagram; DiagramError where none reads only what it may."""
        cost, _, node = self.best(frozenset(), self.outputs, ())
        if cost == INFINITE:
            raise DiagramError("no diagram")
        return node


def _shared(terms) -> list:
    """The compound word subterms (no table index, no shift count) ``terms`` compute more
    than once, largest first."""
    counts = {}

    def walk(t):
        if not isinstance(t, tuple):
            return
        if t[0] in ("WAdd", "WSub", "WShr", "WShl"):
            counts[t] = counts.get(t, 0) + 1
        if t[0] not in ("Tab", "TabB"):
            for a in t[1:]:
                walk(a)

    for _, t in terms:
        walk(t)
    found = [t for t, n in counts.items() if n > 1]
    return sorted(found, key=lambda t: (-dag([t]), repr(t)))


def _spread(atom, terms) -> int:
    """How many of ``terms`` test ``atom``."""
    return sum(atom in atoms(t) for _, t in terms)


def _prime(var):
    return (var[0], f"{var[1]}'")


def _has(term, sub) -> bool:
    if term == sub:
        return True
    return isinstance(term, tuple) and any(_has(a, sub) for a in term[1:])


def nodes(node, out=None) -> dict:
    """``{node: predecessors}`` of a diagram, shared subtrees counted once; a ``seq`` node's
    parts are diagrams of their own."""
    out = {} if out is None else out
    out[node] = out.get(node, 0) + 1
    if out[node] == 1 and node[0] == "test":
        nodes(node[2], out)
        nodes(node[3], out)
    return out


def flatten(node, identity) -> dict:  # pylint: disable=too-many-locals
    """``{name: term}`` a diagram computes for each output it changes, ``identity(name)``
    the unchanged term of output ``name`` (a seq's primes replaced by what it wrote)."""
    kind = node[0]
    if kind == "leaf":
        return dict(node[1])
    if kind == "seq":
        _, name, first, rest = node
        written = flatten(first, identity)[name]
        primed = _prime(identity(name))
        out = {k: substitute(t, {primed: written}) for k, t in flatten(rest, identity).items()}
        out[name] = written
        return out
    _, atom, then, other = node
    a, b = flatten(then, identity), flatten(other, identity)
    out = {}
    for k in sorted(set(a) | set(b)):
        x, y = a.get(k, identity(k)), b.get(k, identity(k))
        out[k] = x if x == y else ("WIte" if x[0] in WORD else "Ite", atom, x, y)
    return out
