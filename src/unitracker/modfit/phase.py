"""A modulator as a phase update ``φ' = φ + a`` and a value update ``v' = f(v, φ)``.

A firing runs the player's action list on concrete control state with the value symbolic;
the reachable control states form a graph whose order is the phase: elapsed firings when every
state has one successor, turns when a state stays until a test of ``v`` advances it.
"""

# pylint: disable=too-many-lines
import ast
import bisect
import dataclasses
import functools
import itertools
import typing

import sympy
import z3

from unitracker.playroutine.codec import Evaluator
from unitracker.modfit import chipinv
from unitracker.modfit import grammar as G
from unitracker.modfit.flow import Writes, action_lists, walk
from unitracker.modfit.form import INF, Form, Programs, atoms, lin, mk, record_data, show
from unitracker.modfit.invariant import Slicer
from unitracker.player.modulation import VALUE, function, run_of

PHI_BOUND = 1 << 20
FIRE = "_fire"
DOMAIN = ("_lo", "_hi", "_r", "_m")
OFFSET = ("_off0", "_off1")


def zeroed(src: str, names: set) -> str:
    """Expression ``src`` with each of ``names`` read as 0: a paired chip value's offset."""

    class Zero(ast.NodeTransformer):
        """Each of ``names`` as 0."""

        def visit_Name(self, node):  # pylint: disable=invalid-name
            """A name of ``names`` as 0, another as it is."""
            return ast.Constant(0) if node.id in names else node

    return ast.unparse(Zero().visit(ast.parse(src, mode="eval")))


class Player(Programs):
    """A form over a song that also runs playroutine record lists at a concrete index."""

    def opaque(self, a: dict, rest, conds, state) -> list:
        """A ``run`` of a playroutine record's list at a concrete index, then the rest."""
        run = a.get("run") or {}
        rows = (self.pr.get("records") or {}).get(run.get("record"))
        index = self.num(self.expr(run.get("index", 0), state)) if rows is not None else None
        if not isinstance(index, int):
            return super().opaque(a, rest, conds, state)
        body = list((rows.get(index) or {}).get(run["field"]) or [])
        return self.paths(body + list(rest), conds, state)


def dispatch(pr: dict, actions: list) -> list:
    """``actions`` with each ``run`` of a playroutine record's lists as an ``if`` chain."""
    records = pr.get("records") or {}
    out = []
    for a in actions:
        run = a.get("run") or {}
        rows = records.get(run.get("record"))
        if rows is None:
            out.append(a)
            continue
        chain = []
        for k, row in sorted(rows.items(), reverse=True):
            body = dispatch(pr, list((row or {}).get(run["field"]) or []))
            chain = [{"if": f"({run['index']}) == {int(k)}", "then": body, "else": chain}]
        out += chain
    return out


@functools.lru_cache(maxsize=None)
def named_tables(entries: tuple, tables: tuple) -> dict:
    """Named tables, ``{name: [entry(i) for i < size]}`` over a player's tables."""
    ev = Evaluator(None, {k: list(v) for k, v in tables})
    return {name: [ev(entry, {"i": i}) for i in range(size)] for name, size, entry in entries}


def unproved_functions(song: dict, spec: dict) -> list:
    """The replaced functions of ``spec`` that differ from the player's on some argument byte."""
    pr = song["playroutine"]
    tables = {**(pr.get("tables") or {}), **(song.get("tables") or {})}
    new = player_form(song, spec).pr
    old_ev = Evaluator(pr.get("functions"), tables)
    new_ev = Evaluator(new.get("functions"), new.get("tables"))
    bad = []
    for name in spec.get("functions") or {}:
        args = pr["functions"][name].get("args") or []
        call = f"{name}({', '.join(args)})"
        for values in itertools.product(range(256), repeat=len(args)):
            env = dict(zip(args, values))
            if old_ev(call, env) != new_ev(call, env):
                bad.append(f"{call} at {env}")
                break
    return bad


def player_form(song: dict, spec: dict | None = None) -> Player:
    """The form of a song's playroutine over its records and tables, with the atom ``N``.

    ``spec`` adds named tables and replaces functions by forms over them.
    """
    spec = spec or {}
    pr = dict(song["playroutine"])
    pr["tables"] = {**(pr.get("tables") or {}), **(song.get("tables") or {})}
    entries = tuple((k, int(v["size"]), v["entry"]) for k, v in (spec.get("tables") or {}).items())
    if entries:
        key = tuple((k, tuple(v)) for k, v in sorted(pr["tables"].items()))
        pr["tables"] = {**pr["tables"], **named_tables(entries, key)}
    if spec.get("functions"):
        pr["functions"] = {
            k: ({**v, "expr": spec["functions"][k]} if k in spec["functions"] else v)
            for k, v in (pr.get("functions") or {}).items()
        }
    form = Player(pr, record_data(song, pr.get("record_types") or {}))
    form.width["N"] = 7
    form.procs = dict(form.procs)
    return form


class Lift:  # pylint: disable=too-many-instance-attributes
    """Form terms of one player as grammar expressions: the cell is ``v``, ``N`` the note."""

    def __init__(self, form: Form, cell: str, freq: dict, extra: dict | None = None, alias=None):
        self.form, self.cell, self.freq = form, cell, freq
        self.alias = dict(alias or {})
        self.offset = int(form.pr.get("note_offset", 0))
        self.extra = dict(extra or {})
        self.unknown = set()
        self.memo = {}

    def term(self, t):
        """The expression of an integer term, memoised."""
        key = ("term", t)
        if key not in self.memo:
            self.memo[key] = self._term(t)
        return self.memo[key]

    def _term(self, t):
        """Uncached ``term``."""
        # pylint: disable=too-many-return-statements,too-many-branches
        if isinstance(t, bool):
            return sympy.Integer(int(t))
        if isinstance(t, int):
            return sympy.Integer(t)
        if isinstance(t, str):
            if t == self.cell:
                return G.V
            if t in self.alias:
                return self.alias[t]
            if t == "N":
                return G.N
            if t in self.extra:
                return sympy.Symbol(self.extra[t], integer=True)
            self.unknown.add(t)
            return sympy.Symbol(t, integer=True)
        head = t[0]
        if head == "lin":
            return t[1] + sympy.Add(*(k * self.term(a) for a, k in t[2]))
        if head == "iv":
            return self.cond(t[1])
        if head == "ite":
            a, b = self.term(t[2]), self.term(t[3])
            return b + self.cond(t[1]) * (a - b)
        if head == "and":
            return (
                G.band(self.term(t[1]), t[2])
                if isinstance(t[2], int)
                else G.BitAnd(self.term(t[1]), self.term(t[2]))
            )
        if head == "or":
            return G.BitOr(self.term(t[1]), self.term(t[2]))
        if head == "xor":
            return G.BitXor(self.term(t[1]), self.term(t[2]))
        if head == "shr" and isinstance(t[2], int):
            return G.shr(self.term(t[1]), t[2])
        if head == "mul":
            return self.term(t[1]) * self.term(t[2])
        if head == "floordiv":
            return sympy.floor(self.term(t[1]) / self.term(t[2]))
        if head == "mod":
            return sympy.Mod(self.term(t[1]), self.term(t[2]))
        if head == "idx":
            return G.table(t[1])(self.term(t[2]))  # pylint: disable=not-callable
        self.unknown.add(show(t))
        return sympy.Symbol(show(t), integer=True)

    def cond(self, c):
        """The bracket of a condition term, memoised."""
        key = ("cond", c)
        if key not in self.memo:
            self.memo[key] = self._cond(c)
        return self.memo[key]

    def _cond(self, c):
        """Uncached ``cond``."""
        if isinstance(c, bool):
            return sympy.Integer(int(c))
        head = c[0]
        if head == "lt":
            return G.lt(self.term(c[1]), 0)
        if head == "eq":
            return G.eq(self.term(c[1]), 0)
        if head == "not":
            return 1 - self.cond(c[1])
        if head == "land":
            return sympy.Mul(*(self.cond(x) for x in c[1:]))
        if head == "lor":
            return 1 - sympy.Mul(*(1 - self.cond(x) for x in c[1:]))
        raise ValueError(f"no bracket for {c!r}")

    def fold(self, e):
        """``e`` with the player's frequency table read as ``F`` of a format note."""
        e = G.canon(e.replace(lambda x: x.is_Add, _bytes))
        if "word" in self.freq:
            word = G.table(self.freq["word"])
            return e.replace(lambda x: x.func == word, lambda x: G.freq(x.args[0] - self.offset))
        return G.canon(e.replace(lambda x: x.is_Add, self._fold_add))

    def _fold_add(self, e):
        """One sum with each ``k*256*hi[x] + k*lo[x]`` as ``k*F(x - offset)``."""
        hi, lo = G.table(self.freq["hi"]), G.table(self.freq["lo"])
        terms = list(sympy.Add.make_args(e))
        out, used = [], set()
        for i, t in enumerate(terms):
            his = sorted(t.atoms(hi), key=sympy.default_sort_key) if i not in used else []
            if len(his) != 1:
                if i not in used:
                    out.append(t)
                continue
            arg = his[0].args[0]
            k = sympy.expand(t / his[0] / 256)
            partner = next(
                (
                    j
                    for j, u in enumerate(terms)
                    if j not in used
                    and j != i
                    and sympy.expand(u - k * lo(arg)) == 0  # pylint: disable=not-callable
                ),
                None,
            )
            if partner is None or k.atoms(hi):
                out.append(t)
                continue
            used |= {i, partner}
            out.append(k * G.freq(arg - self.offset))
        return sympy.Add(*out)

    def cases(self, cases) -> sympy.Expr:
        """The value of disjoint guarded cases: the shortest of their partition forms."""
        leaves = self.leaves([(tuple(c), v) for c, v in cases])
        forms = [self.partition(leaves), self.partition(leaves, intervals=False)]
        return G.shortest([self.fold(e) for e in forms])

    def leaves(self, cases: list) -> list:
        """``[(tests, value)]``: cases split on their first test, joined where values agree.

        Two sides that a test of their difference chooses join as ``min``/``max``; a case
        without the test or its negation belongs to both sides.
        """
        values = {self.term(v) for _, v in cases}
        if len(values) == 1:
            return [((), values.pop())]
        c = next(conds[0] for conds, _ in cases if conds)
        nc = self.form.negate(c)
        yes = self.leaves([(tuple(x for x in k if x != c), v) for k, v in cases if nc not in k])
        no = self.leaves([(tuple(x for x in k if x != nc), v) for k, v in cases if c not in k])
        if len(yes) == len(no) == 1 and c[0] == "lt":
            a, b, e = yes[0][1], no[0][1], sympy.expand(self.term(c[1]))
            if sympy.expand(a - b - e) == 0:
                return [((), sympy.Min(a, b))]
            if sympy.expand(b - a - e) == 0:
                return [((), sympy.Max(a, b))]
            if a == b:
                return [((), a)]
        return [((c, *k), v) for k, v in yes] + [((nc, *k), v) for k, v in no]

    def partition(self, leaves: list, intervals: bool = True) -> sympy.Expr:
        """``Σ [region] * value``; with ``intervals`` a region's tests of one linear form meet."""
        out = 0
        for conds, value in leaves:
            out += (self.region(conds) if intervals else self.brackets(conds)) * value
        return G.canon(out)

    def brackets(self, conds) -> sympy.Expr:
        """The product of a region's brackets."""
        return sympy.Mul(*(self.cond(c) for c in conds))

    def region(self, conds) -> sympy.Expr:
        """A conjunction with the comparisons of each linear form met as one interval."""
        spans, rest = {}, []
        for c in conds:
            got = _interval(c)
            if got is None:
                rest.append(self.cond(c))
                continue
            base, lo, hi, hole = got
            span = spans.setdefault(base, [-INF, INF, set()])
            span[0], span[1] = max(span[0], lo), min(span[1], hi)
            span[2] |= hole
        out = sympy.Mul(*rest)
        for base, (lo, hi, holes) in spans.items():
            while lo in holes:
                lo += 1
            while hi in holes:
                hi -= 1
            if lo > hi:
                return sympy.Integer(0)
            x = self.term(base)
            if lo == hi:
                out *= G.eq(x, lo)
                continue
            out *= (G.ge(x, lo) if lo > -INF else 1) * (G.lt(x, hi + 1) if hi < INF else 1)
            out *= sympy.Mul(*(1 - G.eq(x, h) for h in sorted(holes) if lo < h < hi))
        return out


def _interval(c):
    """``(base, lo, hi, holes)``: a comparison of a linear form as an interval of its base."""
    neg = c[0] == "not"
    inner = c[1] if neg else c
    if inner[0] not in ("lt", "eq"):
        return None
    const, coeffs = lin(inner[1])
    if not coeffs:
        return None
    first = sorted(coeffs, key=show)[0]
    sign = 1 if coeffs[first] > 0 else -1
    base = mk(0, {a: k * sign for a, k in coeffs.items()})
    bound = -const * sign
    if inner[0] == "eq":
        return (base, -INF, INF, {bound}) if neg else (base, bound, bound, set())
    if neg:
        return None
    return (base, -INF, bound - 1, set()) if sign > 0 else (base, bound + 1, INF, set())


def _bytes(e):
    """One sum with each ``k*2**j*(x // 2**j) + k*(x % 2**j)`` as ``k*x``."""
    terms = list(sympy.Add.make_args(e))
    for i, t in enumerate(terms):
        for f in t.atoms(sympy.floor):
            num, den = sympy.fraction(sympy.together(f.args[0]))
            if not den.is_Integer or den & (den - 1):
                continue
            k = sympy.expand(t / f / den)
            low = sympy.expand(k * sympy.Mod(num, den))
            j = next(
                (j for j, u in enumerate(terms) if j != i and sympy.expand(u - low) == 0), None
            )
            if j is not None and not k.atoms(sympy.floor):
                rest = [u for n, u in enumerate(terms) if n not in (i, j)]
                return _bytes(sympy.Add(*rest, sympy.expand(k * num)))
    return e


@dataclasses.dataclass
class Graph:
    """Reachable control states: per state its guarded cases ``(conds, value, next state)``.

    A state is a row of ``lengths[i]`` firings; ``counters[i]`` is ``(cell, d)`` when the row
    counts ``cell`` by ``d`` from the state's value, each firing taking the same cases, else
    None for one firing. A row's cases name the state after its last firing.
    """

    nodes: list
    cases: list
    lengths: list
    counters: list
    error: str = ""


def fire_actions(pr: dict, item) -> list:
    """The actions of a ``fire`` entry: a proc's call, a ``run``, or a template or record function
    run at the index its one calling ``run`` gives."""
    ref = function(item)
    if "proc" in ref:
        return [{"call": ref["proc"]}]
    if "run" in item:
        return [item]
    run = run_of(ref)
    calls = {
        str(d["run"]["index"])
        for d in walk(list(action_lists(pr).values()))
        if "run" in d and run_of(function(d)) == run
    }
    if len(calls) != 1:
        raise ValueError(f"{run} is run at {len(calls)} indices, not one")
    return [{"run": {**run, "index": calls.pop()}}]


class Machine:  # pylint: disable=too-many-instance-attributes
    """One unit's per-firing action list, sliced to the modulated cell, stepped over control."""

    def __init__(self, song: dict, unit: dict, spec: dict | None = None):
        self.form = form = player_form(song, spec)
        self.unit = unit
        self.chip = unit.get("chip")
        self.paired = bool(self.chip) and unit["cell"] in form.pair
        actions = dispatch(form.pr, [a for f in unit["fire"] for a in fire_actions(form.pr, f)])
        if self.chip and not self.paired:
            self.cell = VALUE
            form.width[VALUE] = int(self.chip["width"])
            self.store = [{"set": dict(self.chip["store"])}]
            form.pr["procs"] = {**(form.pr.get("procs") or {}), FIRE: actions}
            form.procs[FIRE] = actions
            actions = [{"call": FIRE}, {"set": {VALUE: self.chip["value"]}}]
            self.parts = {VALUE, *(p for c in self.chip["store"] for p in self._parts(c))}
        else:
            self.cell = unit["cell"]
            self.store = []
            self.parts = self._parts(self.cell)
        self.offsets = set(OFFSET) if self.paired else set()
        if self.paired:
            off = zeroed(self.chip["value"], self.parts)
            actions = [{"set": {OFFSET[0]: off}}, *actions, {"set": {OFFSET[1]: off}}]
        slicer = Slicer(form.pr, Writes(form.pr), form.procs)
        full = self.store + actions
        written = Writes(form.pr).of(full) | self.parts
        need = {VALUE} if self.cell == VALUE else set(self.parts) | (set(OFFSET) & self.offsets)
        while True:
            self.actions, before = slicer.seq(full, need, need)
            grown = need | (before & written)
            if grown == need:
                break
            need = grown
        self.reads = before - self.parts
        self.control = sorted((need & written) - self.parts - set(OFFSET))
        self.hidden = sorted(self.parts - {VALUE}) if self.chip else []
        self.real = []
        if self.chip:
            fire = actions if self.paired else form.procs[FIRE]
            need = set(self.hidden) | set(self.control)
            while True:
                self.real, before = slicer.seq(fire, need, need)
                if need | (before & written) == need:
                    break
                need |= before & written
        self.mask = (1 << int(self.chip["width"] if self.chip else form.width[self.cell])) - 1
        self.memo, self.domains = {}, {}

    def check_offset(self, state: dict) -> None:
        """Raise unless the chip value at control ``state`` is the pair plus its offset, wrapped
        to the chip width."""
        f = self.form
        off = f.expr(zeroed(self.chip["value"], self.parts), state)
        lo, hi, k, _ = f.pair[self.cell]
        smt = f.solver()
        x = smt.term(f.expr(self.chip["value"], state))
        joined = smt.term(hi) * (1 << k) + (smt.term(lo) & ((1 << k) - 1))
        smt.solver.add(smt.term(self.cell) == joined, x != (joined + smt.term(off)) & self.mask)
        if smt.solver.check() != z3.unsat:
            raise ValueError("the chip value is not its pair plus an offset")

    def domain(self, start) -> tuple:
        """``(lo, hi, r, m)``: the chip values the cells give at control ``start`` lie in
        ``[lo, hi]`` and are ``r`` modulo ``m`` (a power of two); ``(0, mask, 0, 1)`` without
        a chip map. Memoised per start."""
        if not self.chip or self.paired:
            return 0, self.mask, 0, 1
        if start not in self.domains:
            self.domains[start] = self._domain(start)
        return self.domains[start]

    def _domain(self, start) -> tuple:
        state = {c: v for c, v in dict(start).items() if c not in DOMAIN}
        f = self.form
        smt = f.solver()
        x = smt.term(f.wrap(VALUE, f.expr(self.chip["value"], state)))

        def holds(c) -> bool:
            smt.solver.push()
            smt.solver.add(z3.Not(c))
            out = smt.solver.check() == z3.unsat
            smt.solver.pop()
            return out

        def least(sign) -> int:
            lo, hi = 0, self.mask
            while lo < hi:
                mid = (lo + hi) // 2
                if holds(x >= mid if sign > 0 else x <= self.mask - mid):
                    lo = mid + 1
                else:
                    hi = mid
            return lo - 1 if sign > 0 else self.mask - lo + 1

        lo, hi = least(1), least(-1)
        m = 1
        while m <= self.mask and holds(z3.URem(x - lo, 2 * m) == 0):
            m *= 2
        return lo, hi, lo % m, m

    def atom(self, state: dict):
        """``(name, lo, m)`` of the index atom ``t`` of a chip value ``lo + m·t`` in ``state``,
        ``t`` in ``[0, (hi - lo) / m]``; None without a chip map or for a pair."""
        if not self.chip or self.paired:
            return None
        lo, hi, _, m = (state[c] for c in DOMAIN)
        name = f"_t{(hi - lo) // m}"
        self.form.ranges[name] = (0, (hi - lo) // m)
        return name, lo, m

    def bounds(self, state: dict) -> tuple:
        """The conditions of the chip value's domain in ``state``."""
        if not self.paired:
            return ()
        lo, hi, r, m = (state[c] for c in DOMAIN)
        f, x = self.form, self.cell
        return tuple(
            c for c in (f.ge(x, lo), f.le(x, hi), f.eq(f.band(x, m - 1), r)) if c is not True
        )

    def _parts(self, cell: str) -> set:
        """The cells a unit cell names: a pair's parts, or the cell."""
        pair = self.form.pair
        return set(pair[cell][:2]) if cell in pair else {cell}

    def step(self, state: tuple) -> list:
        """``[(conds, value, next control)]`` of one firing from control ``state``."""
        if state not in self.memo:
            self.memo[state] = self._step(dict(state))
        return self.memo[state]

    def _step(self, state: dict) -> list:
        f, out = self.form, []
        known, index = self.bounds(state), self.atom(state)
        if self.paired:
            self.check_offset(state)
        if index is not None:
            state = {**state, VALUE: f.add(index[1], f.scale(index[2], index[0]))}
        for conds, final in f.paths(self.actions, known, state):
            conds = conds[len(known) :]
            fused = f.fuse(conds, final) if self.cell in f.pair else final
            nxt = tuple((c, final.get(c, state.get(c, c))) for c in self.control)
            bad = [c for c, val in nxt if (self.parts | {index and index[0]}) & atoms(val)]
            if bad:
                raise ValueError(f"control {', '.join(bad)} reads the value")
            value = fused.get(self.cell, self.cell)
            if self.paired:
                wrapped = (
                    isinstance(value, tuple) and value[0] == "and" and isinstance(value[2], int)
                )
                inner = value[1] if wrapped and (value[2] + 1) % (self.mask + 1) == 0 else value
                shift = f.sub(final.get(OFFSET[1], 0), final.get(OFFSET[0], 0))
                value = f.band(f.add(inner, shift), self.mask)
            out.append((conds, value, nxt))
        return out if self.cell not in f.pair else self.project(out)

    def project(self, cases: list) -> list:
        """A fused pair's cases with the tests of its parts dropped (carries the fusion absorbs).

        Raises when two cases left with the same tests differ, so the parts matter.
        """
        seen = {}
        for conds, value, nxt in cases:
            key = tuple(c for c in conds if not self.parts & atoms(c))
            if seen.setdefault(key, (value, nxt)) != (value, nxt):
                raise ValueError("the value reads its pair's parts beyond their sum")
        return [(k, v, n) for k, (v, n) in seen.items()]

    def start(self, values: dict) -> tuple:
        """The control state of a start: ``values`` for every cell the list reads or keeps."""
        missing = sorted(c for c in self.reads | set(self.control) if c not in values)
        if missing:
            raise ValueError(f"no start value for {', '.join(missing)}")
        keep = {c: values[c] for c in sorted(self.reads | set(self.control))}
        if self.chip:
            keep.update(zip(DOMAIN, self.domain(tuple(keep.items()))))
        return tuple(sorted(keep.items(), key=lambda x: x[0]))

    def explore(self, start: tuple) -> Graph:
        """Every control state reachable from ``start``, in discovery order; a state other than
        the start whose firings count one cell is the row of them (``counted``)."""
        graph = Graph([start], [], [], [])
        index, todo = {start: 0}, [start]
        params = {c: v for c, v in start if c not in self.control}
        while todo:
            node = todo.pop(0)
            got = self.step(node)
            run = self.counted(node, got) if node != start else None
            if run is not None:
                got = [(conds, value, _after(nxt, *run)) for conds, value, nxt in got]
            row = []
            for conds, value, nxt in got:
                full = tuple(sorted({**params, **dict(nxt)}.items(), key=lambda x: x[0]))
                if full not in index:
                    index[full] = len(graph.nodes)
                    graph.nodes.append(full)
                    todo.append(full)
                row.append((conds, value, index[full]))
            graph.cases.append(row)
            graph.lengths.append(run[2] if run else 1)
            graph.counters.append(run and run[:2])
        return graph

    def count(self, cell: str, k0: int, d: int) -> str | None:
        """The atom ``j`` of a row that counts ``cell`` by ``d`` from ``k0``: ``cell`` is
        ``k0 + d·j`` at its ``j``-th firing, over every ``j`` from which one more count stays in
        the cell's width (any ``j`` for a cell without one); None when there is none."""
        width = self.form.width.get(cell)
        last = INF
        if width:
            top = (1 << width) - 1
            last = (top - d - k0) // d if d > 0 else (k0 + d) // -d
        if last < 0:
            return None
        name = f"_{cell}{k0}{d:+d}"
        self.form.ranges[name] = (0, last)
        return name

    def counted(self, node: tuple, cases: list) -> tuple | None:
        """``(cell, d, n)`` when the firings at ``node`` and the ``n - 1`` after it are one row:
        each case keeps the other control and counts ``cell`` by ``d``, and with ``cell`` as
        ``k0 + d·j`` over an atom ``j`` the cases whose tests hold are the same for each ``j``
        below ``n``; else None.

        A test of ``j`` must be linear in it alone, and no value or other control may read it,
        so the count enters a row's claims only as its length.
        """
        state = dict(node)
        found = _counting(state, cases)
        atom = found and self.count(*found)
        if not atom:
            return None
        cell, k0, d = found
        try:
            sym = self.step(tuple(sorted({**state, cell: mk(k0, {atom: d})}.items())))
        except ValueError:
            return None
        tests = [_counts(conds, atom) for conds, _, _ in sym]
        if None in tests or any(
            atom in atoms(value) | atoms(tuple(v for c, v in nxt if c != cell))
            for _, value, nxt in sym
        ):
            return None
        here = _holding(tests, atom, 0)
        stay = {c: mk(k0 + d, {atom: d}) if c == cell else state[c] for c in self.control}
        if not here or any(dict(sym[i][2]) != stay for i in here):
            return None
        n = self.form.ranges[atom][1] + 1
        for j in sorted({j for t in tests for _, points in t for j in points}):
            if 0 < j < n and _holding(tests, atom, j) != here:
                n = j
                break
        return (cell, d, n) if 1 < n < INF else None

    def row(self, graph: Graph, i: int) -> tuple:
        """``(state, conds, j)`` of node ``i``: a row's counted cell as ``k0 + d·j`` with ``j``
        bounded to its firings; a firing's own state, ``()`` and None."""
        state = dict(graph.nodes[i])
        if graph.counters[i] is None:
            return state, (), None
        cell, d = graph.counters[i]
        atom = self.count(cell, state[cell], d)
        known = self.form.le(atom, graph.lengths[i] - 1)
        return (
            {**state, cell: mk(state[cell], {atom: d})},
            (() if known is True else (known,)),
            atom,
        )


def _after(nxt: tuple, cell: str, d: int, n: int) -> tuple:
    """The control after a row of ``n`` firings whose first gives ``nxt``."""
    return tuple((c, v + (n - 1) * d if c == cell else v) for c, v in nxt)


def _counting(state: dict, cases: list) -> tuple | None:
    """``(cell, k0, d)`` when every case moves one control cell alone, from ``k0`` to the same
    ``k0 + d``."""
    moved = {c for _, _, nxt in cases for c, v in nxt if v != state[c]}
    after = {dict(nxt)[c] for c in moved for _, _, nxt in cases}
    if len(moved) != 1 or len(after) != 1:
        return None
    (cell,), (k1,) = moved, after
    k0 = state[cell]
    return (cell, k0, k1 - k0) if isinstance(k0, int) and isinstance(k1, int) else None


def _counts(conds, atom: str) -> list | None:
    """``[(test, counts)]`` of the tests in ``conds`` that read ``atom``, each linear in it
    alone, with the counts around which it can change; None when one is not linear in it alone."""
    out = []
    for c in conds:
        if atom not in atoms(c):
            continue
        leaves = _leaves(c, atom)
        if leaves is None:
            return None
        out.append((c, {(-const) // k + e for const, k in leaves for e in (-1, 0, 1, 2)}))
    return out


def _leaves(c, atom: str) -> list | None:
    """``[(const, k)]`` of each comparison ``const + k·atom`` of condition ``c``, or None when a
    comparison reads anything else."""
    if c[0] in ("lt", "eq"):
        const, coeffs = lin(c[1])
        return [(const, coeffs[atom])] if set(coeffs) == {atom} else None
    parts = [_leaves(x, atom) for x in c[1:]]
    return None if None in parts else [leaf for p in parts for leaf in p]


def _truth(c, atom: str, k: int) -> bool:
    """Condition ``c`` over ``atom`` alone at ``atom = k``."""
    head = c[0]
    if head in ("lt", "eq"):
        const, coeffs = lin(c[1])
        x = const + coeffs[atom] * k
        return x < 0 if head == "lt" else x == 0
    if head == "not":
        return not _truth(c[1], atom, k)
    parts = (_truth(x, atom, k) for x in c[1:])
    return all(parts) if head == "land" else any(parts)


def _holding(tests: list, atom: str, k: int) -> frozenset:
    """The cases whose tests of ``atom`` all hold at ``atom = k``."""
    return frozenset(i for i, t in enumerate(tests) if all(_truth(c, atom, k) for c, _ in t))


def minimal(graph: Graph, lift: Lift) -> tuple:  # pylint: disable=too-many-locals
    """``(class per state, value per class, {class: guard} per class, firings per class)``: the
    coarsest bisimulation.

    States are equivalent when their value updates, their firings and their guarded successor
    classes agree.
    """
    values = [lift.cases([(c, v) for c, v, _ in row]) for row in graph.cases]
    guards = [_guards(row, lift) for row in graph.cases]
    label = [f"{G.text(v)} x{n}" for v, n in zip(values, graph.lengths)]
    while True:
        keys = []
        for i, gs in enumerate(guards):
            merged = {}
            for j, g in gs.items():
                merged[label[j]] = merged.get(label[j], 0) + g
            keys.append(
                (label[i], tuple(sorted((k, G.text(G.canon(g))) for k, g in merged.items())))
            )
        names = {k: str(n) for n, k in enumerate(dict.fromkeys(keys))}
        new = [names[k] for k in keys]
        stable = len(set(new)) == len(set(label))
        label = new
        if stable:
            break
    order_ = list(dict.fromkeys(label))
    cls = [order_.index(x) for x in label]
    rep = [cls.index(c) for c in range(len(order_))]
    edges = []
    for c in range(len(order_)):
        merged = {}
        for j, g in guards[rep[c]].items():
            merged[cls[j]] = merged.get(cls[j], 0) + g
        edges.append({k: G.canon(g) for k, g in merged.items()})
    return cls, [values[r] for r in rep], edges, [graph.lengths[r] for r in rep]


def _guards(row, lift: Lift) -> dict:
    """Successor state to the bracket of the paths leading to it."""
    out = {}
    for conds, _, j in row:
        out[j] = out.get(j, 0) + sympy.Mul(*(lift.cond(c) for c in conds))
    return out


@dataclasses.dataclass
class Order:
    """The phase: ``kind`` time or turn; ``runs`` of ``(class, positions)`` over the prefix and
    one cycle with each run's ``advance``; ``at`` each class's first position from the start."""

    kind: str
    runs: list
    prefix: int
    period: int
    advance: list
    at: dict


def order(edges: list, lengths: list, values: list) -> Order | None:
    """The phase order of a minimal graph from class 0, or None when no single phase fits.

    Time: every class has one successor. Turns: a class stays or moves to one other class; a
    class of several firings moves at each. A class holds its firings' positions; prefix and
    period are the shortest that give the positions' value and guard sequence.
    """
    time = all(len(e) == 1 for e in edges)
    path, at, tokens = [], {}, []
    c = 0
    while c not in at:
        at[c] = sum(n for _, n, _ in path)
        out = {k: g for k, g in edges[c].items() if time or k != c or lengths[c] > 1}
        if len(out) > 1:
            return None
        g = next(iter(out.values()), None)
        path.append((c, lengths[c], sympy.Integer(int(time)) if time or g is None else g))
        stay = edges[c].get(c) if len(out) < len(edges[c]) else None
        tokens.append(tuple("" if x is None else G.text(x) for x in (values[c], stay, g)))
        if g is None:
            break
        c = next(iter(out))
    loop = len(path) - 1 if g is None else [x for x, _, _ in path].index(c)
    prefix, period = shortest_lasso(tokens, [n for _, n, _ in path], loop)
    runs = _clipped(path, prefix + period)
    return Order(
        "time" if time else "turn",
        [(c, n) for c, n, _ in runs],
        prefix,
        period,
        [a for *_, a in runs],
        at,
    )


def _clipped(path: list, length: int) -> list:
    """The entries ``(class, positions, advance)`` of ``path`` over its first ``length``
    positions."""
    out, pos = [], 0
    for c, n, a in path:
        if pos < length:
            out.append((c, min(n, length - pos), a))
        pos += n
    return out


def shortest_lasso(tokens: list, sizes: list, loop: int) -> tuple:
    """``(prefix, period)`` in positions of the sequence that repeats ``tokens[i]`` ``sizes[i]``
    times, the entries from ``loop`` on forever: the shortest prefix and period that give it."""
    starts = list(itertools.accumulate(sizes, initial=0))
    cyc = []
    for t, n in zip(tokens[loop:], sizes[loop:]):
        cyc = cyc[:-1] + [(t, cyc[-1][1] + n)] if cyc and cyc[-1][0] == t else cyc + [(t, n)]
    if len(cyc) > 1 and cyc[0][0] == cyc[-1][0]:
        cyc = [(cyc[0][0], cyc[0][1] + cyc[-1][1])] + cyc[1:-1]
    period = 1
    if len(cyc) > 1:
        fail = [0] * len(cyc)
        for i in range(1, len(cyc)):
            k = fail[i - 1]
            while k and cyc[i] != cyc[k]:
                k = fail[k - 1]
            fail[i] = k + (cyc[i] == cyc[k])
        s = len(cyc) - fail[-1]
        period = sum(n for _, n in cyc[: s if len(cyc) % s == 0 else len(cyc)])
    prefix = starts[loop]
    while prefix:
        a = bisect.bisect_right(starts, prefix - 1) - 1
        b = bisect.bisect_right(starts, prefix - 1 + period) - 1
        if tokens[a] != tokens[b]:
            break
        prefix -= min(prefix - starts[a], prefix + period - starts[b])
    return prefix, period


# ---- synthesis ------------------------------------------------------------------------


def absorb(e) -> sympy.Expr:
    """``e`` with each term's brackets taken as 1 inside its other factors."""
    out = []
    for term in sympy.Add.make_args(G.canon(e)):
        bs = [f for f in sympy.Mul.make_args(term) if isinstance(f, G.Iv)]
        rest = sympy.Mul(*(f for f in sympy.Mul.make_args(term) if not isinstance(f, G.Iv)))
        out.append(sympy.Mul(*bs) * rest.subs({b: 1 for b in bs}) if bs else term)
    return G.canon(sympy.Add(*out))


def split(items: list, o: Order) -> tuple:
    """``(prefix, cycle)`` runs of ``(item, positions)``, one item per run of ``o``."""
    pre, cyc, pos = [], [], 0
    for item, (_, n) in zip(items, o.runs):
        a = min(n, max(0, o.prefix - pos))
        pre += [(item, a)] if a else []
        cyc += [(item, n - a)] if n > a else []
        pos += n
    return pre, cyc


def steps(runs: list, x) -> sympy.Expr:
    """The item at position ``x`` of runs ``(item, positions)``, ``x`` within them: the first
    item plus a bracket per change."""
    out = prev = runs[0][0]
    b = 0
    for item, n in runs:
        if item != prev:
            out += (item - prev) * G.ge(x, b)
        prev, b = item, b + n
    return out


def boundaries(runs: list) -> list:
    """Positions that start a cycle of runs at a change, or ``[0]`` for a constant cycle."""
    out, b = [], 0
    for i, (item, n) in enumerate(runs):
        if item != runs[i - 1][0]:
            out.append(b)
        b += n
    return out or [0]


def rotated(runs: list, k: int) -> list:
    """A cycle of runs started at position ``k``, a run's start."""
    i = list(itertools.accumulate((n for _, n in runs), initial=0)).index(k)
    return runs[i:] + runs[:i]


def by_phase(items: list, o: Order) -> list:
    """Candidate expressions of the item at ``φ``'s position, one item per run of ``o``: one
    per rotation of the cycle."""
    pre, cyc = split(items, o)
    out = []
    for k in boundaries(cyc):
        rot = rotated(cyc, k)
        if o.period > 1:
            tail = steps(rot, sympy.Mod(G.PHI - o.prefix - k, o.period))
        else:
            tail = rot[0][0]
        out.append(steps(pre + [(tail, 1)], G.PHI) if o.prefix else tail)
    return [G.canon(e) for e in out]


def signed(d, mask: int):
    """A constant step modulo ``mask + 1`` in ``(-(mask + 1)/2, (mask + 1)/2]``; others as given."""
    if not d.is_Integer:
        return d
    m = mask + 1
    return sympy.Integer((int(d) + m // 2 - 1) % m - m // 2 + 1)


def additive(value, mask: int):
    """``("add", d)`` for ``(v + d) % m``, ``("set", c)`` free of ``v``, else None."""
    if G.V not in value.free_symbols:
        return "set", value
    if isinstance(value, sympy.Mod) and value.args[1] == mask + 1:
        d = sympy.expand(value.args[0] - G.V)
        if G.V not in d.free_symbols:
            return "add", signed(d, mask)
    d = sympy.expand(value - G.V)
    if G.V not in d.free_symbols:
        return "add", d
    return None


def value_forms(values: list, o: Order, mask: int) -> list:
    """Candidate value updates: by position, and when every state adds or sets, the add form."""
    items = [values[c] for c, _ in o.runs]
    out = by_phase(items, o)
    ops = [additive(v, mask) for v in items]
    if all(ops):
        sets = [sympy.Integer(int(k == "set")) for k, _ in ops]
        cs = [x if k == "set" else sympy.Integer(0) for k, x in ops]
        ds = [x if k == "add" else sympy.Integer(0) for k, x in ops]
        for s, c, d in zip(by_phase(sets, o), by_phase(cs, o), by_phase(ds, o)):
            out.append(G.canon(s * c + (1 - s) * sympy.Mod(G.V + d, mask + 1)))
    return [absorb(e) for e in out]


class Trace:
    """Values after each firing as segments ``(first, step, positions)``: ``first`` then
    ``first + step·j``, a set's value as it is."""

    def __init__(self, segments: list):
        self.segments = segments
        self.starts = list(itertools.accumulate((n for _, _, n in segments), initial=0))

    def __len__(self) -> int:
        return self.starts[-1]

    def segment(self, i: int) -> int:
        """The segment holding position ``i``."""
        return bisect.bisect_right(self.starts, i) - 1

    def __getitem__(self, i: int):
        s = self.segment(i)
        y, d, _ = self.segments[s]
        j = i - self.starts[s]
        return y if j == 0 or d == 0 else sympy.expand(y + d * j)

    def cut(self, a: int, b: int) -> "Trace":
        """Positions ``[a, b)``."""
        out = []
        for s, (_, d, n) in enumerate(self.segments):
            lo, hi = max(a, self.starts[s]), min(b, self.starts[s] + n)
            if lo < hi:
                out.append((self[lo], d, hi - lo))
        return Trace(out)

    def diffs(self) -> list:
        """``[(d, i, j)]``: maximal runs of equal steps ``self[x] - self[x - 1]`` for ``x`` in
        ``[i, j]``."""
        out = []
        for s, (_, d, n) in enumerate(self.segments):
            b = self.starts[s]
            parts = [(sympy.expand(self[b] - self[b - 1]), b, b)] if b else []
            parts += [(sympy.expand(d), b + 1, b + n - 1)] if n > 1 else []
            for step, i, j in parts:
                if out and sympy.expand(step - out[-1][0]) == 0:
                    out[-1] = (out[-1][0], out[-1][1], j)
                else:
                    out.append((step, i, j))
        return out


def closed_forms(values: list, o: Order, mask: int, v0) -> list:
    """Candidate ``g(φ)``, the value after the firing at ``φ``, when no value reads ``v``."""
    if o.kind != "time":
        return []
    ops = [additive(values[c], mask) for c, _ in o.runs]
    if not all(ops):
        return []
    pre, cyc = split(ops, o)
    segments, y = [], v0
    for (k, x), n in pre + cyc + cyc:
        if k == "add" and y is None:
            return []
        segments.append((x, sympy.Integer(0), n) if k == "set" else (sympy.expand(y + x), x, n))
        y = Trace(segments[-1:])[n - 1]
    trace = Trace(segments)
    drift = sympy.expand(sum((n * x for (k, x), n in cyc if k == "add"), sympy.Integer(0)))
    if any(k == "set" for (k, _), _ in cyc) or drift == 0:
        drift = sympy.Integer(0)
    out = []
    for k in boundaries([(x, n) for (_, x), n in cyc]) if o.period > 1 else [0]:
        out += _windows(trace, o, k, drift)
    return [G.canon(e) for e in out]


def _ramp(seq: Trace, x, window: bool = False) -> sympy.Expr:
    """``seq[x]`` as its first value plus clamped ramps over runs of equal steps.

    With ``window``, ``0 <= x < len(seq)``: the first run needs no lower clamp, the last no upper.
    """
    out = seq[0]
    for d, i, j in seq.diffs():
        if d != 0:
            ramp = x - i + 1 if window and i == 1 else sympy.Max(x - i + 1, 0)
            out += d * (ramp if window and j == len(seq) - 1 else sympy.Min(ramp, j - i + 1))
    return out


def _windows(trace: Trace, o: Order, k: int, drift) -> list:
    """Closed forms with a window starting at the value before the cycle's change at ``k``.

    A window ramp plus drift; the window form alone is kept when it also gives the prefix.
    """
    p, n = o.prefix, o.period
    start = p + (k - 1) % n
    if n == 1 and drift == 0:
        forms = [_ramp(trace.cut(0, p + 1), G.PHI)]
    else:
        t = G.PHI - start
        window = trace.cut(start, start + n)
        tail = _ramp(window, sympy.Mod(t, n), True) + drift * sympy.floor(t / n)
        kinks = {0} | {b for _, i, j in window.diffs() for b in (i - 1, j)}
        forms = [_gated(trace, tail, start, (n, kinks)) or _join(trace.cut(0, start), tail, start)]
    out = []
    for e in forms:
        out += [e, sympy.expand(e.rewrite(sympy.Abs))]
    return out


def _gated(trace: Trace, tail, start: int, window: tuple):
    """``tail`` from the first ``φ`` it fits on, the constant before it; None if none fits.

    ``window`` is ``(n, kinks)``: ``tail`` is linear in ``φ`` between the kinks of
    ``(φ - start) % n``, as each trace segment is, so a stretch where both are linear fits when
    its two ends do, and a fit that stops inside it stops at its last position.
    """
    n, kinks = window

    def fits_at(x) -> bool:
        return sympy.expand(tail.subs(G.PHI, x) - trace[x]) == 0

    fits = start
    while fits > 0:
        r = fits - 1
        u = (r - start) % n
        a = max(trace.starts[trace.segment(r)], r - u + max((b for b in kinks if b < u), default=u))
        if not fits_at(r):
            break
        if a < r and not fits_at(a):
            fits = r
            break
        fits = a
    first = trace[0]
    for s in range(trace.segment(fits - 1) + 1 if fits else 0):
        lo, hi = trace.starts[s], min(fits, trace.starts[s + 1]) - 1
        if any(sympy.expand(trace[x] - first) != 0 for x in (lo, hi)):
            return None
    return tail if fits == 0 else first + G.ge(G.PHI, fits) * (tail - first)


def _join(head: Trace, tail, start: int):
    """``head[φ]`` before ``start``, ``tail`` from it."""
    return _ramp(head, G.PHI) * G.lt(G.PHI, start) + tail * G.ge(G.PHI, start)


# ---- proof ----------------------------------------------------------------------------


class Prover:
    """z3 obligations of expressions against the player's paths of one unit."""

    def __init__(self, machine: Machine, freq: dict, extra=None, domain=None, index=None):
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        self.machine, self.freq, self.extra = machine, freq, dict(extra or {})
        self.domain, self.index = domain, index
        self.form = machine.form
        self.offset = int(self.form.pr.get("note_offset", 0))

    def env(self, smt, phi) -> dict:
        """Symbols and functions of the grammar over one solver's atoms."""
        f = self.form
        if not z3.is_bv_value(phi):
            phi = G.Phase(smt.solver.add, phi)
        env = {"v": smt.term(self.machine.cell), "N": smt.term("N"), "φ": phi}
        env.update({name: smt.term(cell) for cell, name in self.extra.items()})

        def table(name):
            fn = z3.Function(f"idx_{name}", z3.BitVecSort(G.BITS), z3.BitVecSort(G.BITS))
            lo, hi = f.tables.get(name, (-INF, INF))

            def apply(x):
                y = fn(x)
                if lo > -INF:
                    smt.solver.add(y >= int(lo), y <= int(hi))
                return y

            return apply

        if "word" in self.freq:
            word = table(self.freq["word"])
            env["F"] = lambda x: word(x + self.offset)
        else:
            hi, lo = table(self.freq["hi"]), table(self.freq["lo"])
            env["F"] = lambda x: 256 * hi(x + self.offset) + lo(x + self.offset)
        for name in f.tables:
            env.setdefault(name, table(name))
        if self.index is not None:
            env["v"] = self.index[1] + self.index[2] * smt.term(self.index[0])
        elif self.domain is not None:
            lo, hi, r, m = self.domain
            v = env["v"]
            smt.solver.add(v >= lo, v <= hi, z3.URem(v, m) == r)
        return env

    def closure(self, graph: Graph) -> str | None:
        """None when every firing leaves a chip value in the domain."""
        lo, hi, r, m = self.domain
        for i, row in enumerate(graph.cases):
            for conds, val, _ in row:
                smt = self.form.solver()
                self.env(smt, z3.BitVecVal(0, G.BITS))
                y = smt.term(val)
                smt.solver.add(*(smt.cond(c) for c in conds))
                smt.solver.add(z3.Or(y < lo, y > hi, z3.URem(y, m) != r))
                if smt.solver.check() != z3.unsat:
                    return f"state {i}: {witness(smt.solver)}"
        return None

    def phase(self, smt, phi: int, n: int):
        """``φ`` at the ``n`` positions from ``phi``: a constant for one, else bounded to them."""
        if n == 1:
            return z3.BitVecVal(phi, G.BITS)
        x = z3.BitVec("φ", G.BITS)
        smt.solver.add(x >= phi, x < phi + n)
        return x

    def node(self, graph: Graph, cls: list, i: int, phi: int, value, adv) -> str | None:
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        """None when every path of state ``i`` gives ``value`` and ``adv`` at each of its
        firings' positions from ``φ``, else why.

        ``adv`` is 1 on the paths that leave the state's position, else 0: a row's firings
        each move on.
        """
        n = graph.lengths[i]
        for conds, val, j in graph.cases[i]:
            smt = self.form.solver()
            env = self.env(smt, self.phase(smt, phi, n))
            smt.solver.add(*(smt.cond(c) for c in conds))
            moved = int(n > 1 or cls[j] != cls[i])
            smt.solver.add(
                z3.Or(G.to_z3(value, env) != smt.term(val), G.to_z3(adv, env) != moved)
                if adv is not None
                else G.to_z3(value, env) != smt.term(val)
            )
            if smt.solver.check() != z3.unsat:
                return f"at φ = {phi}{f' + [0, {n})' if n > 1 else ''}: {witness(smt.solver)}"
        return None

    def periodic(self, e, start: int, period: int) -> str | None:
        """None when ``e(φ + period) == e(φ)`` for every ``φ >= start``."""
        smt = self.form.solver()
        phi = z3.BitVec("φ", G.BITS)
        smt.solver.add(phi >= start, phi < PHI_BOUND)
        env = self.env(smt, phi)
        later = G.to_z3(e, {**env, "φ": env["φ"] + period})
        smt.solver.add(later != G.to_z3(e, env))
        return None if smt.solver.check() == z3.unsat else witness(smt.solver)

    def closed(self, g, f, v0) -> str | None:
        """None when ``g(0) = f(v0, 0)`` and ``g(φ + 1) = f(g(φ), φ + 1)`` for every ``φ``."""
        smt = self.form.solver()
        phi = z3.BitVec("φ", G.BITS)
        smt.solver.add(phi >= 0, phi < PHI_BOUND)
        env = self.env(smt, phi)
        start = {**env, "φ": z3.BitVecVal(0, G.BITS)}
        if v0 is not None:
            start["v"] = G.to_z3(v0, env)
        nxt = {**env, "φ": env["φ"] + 1}
        step = G.to_z3(f, {**nxt, "v": G.to_z3(g, env)})
        smt.solver.add(z3.Or(G.to_z3(g, start) != G.to_z3(f, start), G.to_z3(g, nxt) != step))
        return None if smt.solver.check() == z3.unsat else witness(smt.solver)

    def in_range(self, g, mask: int) -> bool:
        """True when ``0 <= g(φ) <= mask`` for every ``φ``."""
        smt = self.form.solver()
        phi = z3.BitVec("φ", G.BITS)
        smt.solver.add(phi >= 0, phi < PHI_BOUND)
        y = G.to_z3(g, self.env(smt, phi))
        smt.solver.add(z3.Or(y < 0, y > mask))
        return smt.solver.check() == z3.unsat

    def equal(self, a, b, phi_from: int = 0) -> bool:
        """True when ``a`` and ``b`` agree for every ``v``, ``N`` and ``φ >= phi_from``."""
        smt = self.form.solver()
        phi = z3.BitVec("φ", G.BITS)
        smt.solver.add(phi >= phi_from, phi < PHI_BOUND)
        env = self.env(smt, phi)
        smt.solver.add(G.to_z3(a, env) != G.to_z3(b, env))
        return smt.solver.check() == z3.unsat


def witness(solver) -> str:
    """``name=value`` of a model's constants, signed."""
    if solver.check() != z3.sat:
        return "undecided"
    model = solver.model()
    return ", ".join(
        f"{d}={model[d].as_signed_long()}"
        for d in sorted(model.decls(), key=str)
        if d.arity() == 0 and z3.is_bv_sort(d.range())
    )


@dataclasses.dataclass
class Derived:  # pylint: disable=too-many-instance-attributes
    """The expressions of one start, with their proof status."""

    phase: typing.Any = None
    value: typing.Any = None
    closed: typing.Any = None
    order: Order | None = None
    states: int = 0
    proof: list = dataclasses.field(default_factory=list)
    error: str = ""
    invariant: typing.Any = None

    @property
    def exact(self) -> bool:
        """True when every obligation was proved."""
        return not self.error and all(ok for _, ok, _ in self.proof)

    @property
    def update(self) -> tuple:
        """``(a, f)``: the closed form with its phase when there is one, else the recurrence."""
        if self.closed is None:
            return self.phase, self.value
        if self.order.kind == "turn":
            return self.phase, self.closed
        return sympy.Integer(int(G.PHI in self.closed.free_symbols)), self.closed

    @property
    def identity(self) -> bool:
        """True when the value update leaves ``v`` as it is: no modulation."""
        return self.value is not None and G.canon(self.value) == G.V

    @property
    def canonical(self) -> str:
        """``a; f`` of ``update`` as text."""
        a, f = self.update
        return f"{G.text(a)}; {G.text(f)}"


def derive(machine: Machine, start: tuple, v0, freq: dict, extra: dict | None = None) -> Derived:
    # pylint: disable=too-many-locals
    """The phase and value expressions of ``machine`` from control ``start``, proved."""
    index = machine.atom(dict(start))
    alias = {index[0]: sympy.floor((G.V - index[1]) / index[2])} if index else None
    lift = Lift(machine.form, machine.cell, freq, extra, alias)
    try:
        graph = machine.explore(start)
    except ValueError as exc:
        return Derived(error=str(exc))
    cls, raw, edges, lengths = minimal(graph, lift)
    lo, hi, r, m = (dict(start)[c] for c in DOMAIN) if machine.chip else machine.domain(start)
    values = [G.shortest([x, G.on_domain(x, r, m, lo, hi)]) for x in raw]
    if lift.unknown:
        return Derived(states=len(graph.nodes), error=f"reads {', '.join(sorted(lift.unknown))}")
    o = order(edges, lengths, raw)
    if o is None:
        return Derived(
            states=len(graph.nodes), error="more than one phase: a state advances on two tests"
        )
    out = Derived(order=o, states=len(graph.nodes))
    prover = Prover(machine, freq, extra, (lo, hi, r, m), index)
    domain = (("v", lo, hi), ("N", 0, (1 << machine.form.width["N"]) - 1))
    forms = value_forms(values, o, machine.mask)
    out.value = G.polar(G.shortest(forms), domain)
    reads = G.PHI in out.value.free_symbols
    out.phase = (
        G.polar(G.shortest(by_phase(o.advance, o)), domain)
        if o.kind == "turn"
        else sympy.Integer(int(reads))
    )
    bad = [
        (i, why)
        for i in range(len(graph.nodes))
        if cls[i] in o.at
        and (why := prover.node(graph, cls, i, o.at[cls[i]], out.value,
                                out.phase if o.kind == "turn" else None))
    ]  # fmt: skip
    out.proof.append(
        (f"{len(graph.nodes)} states", not bad, f"state {bad[0][0]} {bad[0][1]}" if bad else "")
    )
    if machine.chip:
        out.invariant = chipinv.prove(machine, graph, (lo, hi, r, m), cls, o.at, out.value, prover)
        why = prover.closure(graph)
        out.proof.append(("every firing stays in the chip values", why is None, why or ""))
    if o.period > 1 or o.kind == "turn":
        for name, e in (("value", out.value), ("phase", out.phase)):
            why = prover.periodic(e, o.prefix, o.period)
            out.proof.append(
                (f"{name} periodic {o.period} from φ = {o.prefix}", why is None, why or "")
            )
    out.closed = closed_of(values, o, machine.mask, v0, out.value, prover)
    if out.closed is not None:
        out.closed = G.polar(out.closed, domain)
    if out.closed is not None:
        why = prover.closed(out.closed, out.value, v0)
        out.proof.append(("closed form", why is None, why or ""))
    return out


def closed_of(values, o: Order, mask: int, v0, f, prover: Prover):
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    """The shortest closed form proved against ``f``, wrapped only when it leaves the range.

    Candidates are proved in rank order, so the first proved is the shortest proved.
    """
    forms = [g if prover.in_range(g, mask) else sympy.Mod(g, mask + 1)
             for g in filter(G.valid, closed_forms(values, o, mask, v0))]  # fmt: skip
    for g in sorted(dict.fromkeys(f for f in forms if G.valid(f)), key=G.rank):
        if prover.closed(g, f, v0) is None:
            return g
    return None
