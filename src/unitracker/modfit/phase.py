"""A modulator as a phase update ``φ' = φ + a`` and a value update ``v' = f(v, φ)``.

A firing runs the player's action list on concrete control state with the value symbolic;
the reachable control states form a graph whose order is the phase: elapsed firings when every
state has one successor, turns when a state stays until a test of ``v`` advances it.
"""

import dataclasses
import functools
import itertools
import typing

import sympy
import z3

from unitracker.modfit import grammar as G
from unitracker.modfit.flow import Writes
from unitracker.modfit.form import INF, Form, Programs, atoms, lin, mk, record_data, show
from unitracker.modfit.invariant import Slicer
from unitracker.codec import Evaluator

PHI_BOUND = 1 << 20


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
    out = []
    for a in actions:
        run = a.get("run") or {}
        rows = (pr.get("records") or {}).get(run.get("record"))
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


class Lift:
    """Form terms of one player as grammar expressions: the cell is ``v``, ``N`` the note."""

    def __init__(self, form: Form, cell: str, freq: dict, extra: dict | None = None):
        self.form, self.cell, self.freq = form, cell, freq
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
    """Reachable control states: per state its guarded cases ``(conds, value, next state)``."""

    nodes: list
    cases: list
    error: str = ""


class Machine:  # pylint: disable=too-many-instance-attributes
    """One unit's per-firing action list, sliced to the modulated cell, stepped over control."""

    def __init__(self, song: dict, unit: dict, spec: dict | None = None):
        self.form = form = player_form(song, spec)
        self.unit = unit
        self.cell = unit["cell"]
        self.parts = set(form.pair[self.cell][:2]) if self.cell in form.pair else {self.cell}
        actions = dispatch(
            form.pr, [a if isinstance(a, dict) else {"call": a} for a in unit["fire"]]
        )
        slicer = Slicer(form.pr, Writes(form.pr), form.procs)
        written = Writes(form.pr).of(actions)
        need = set(self.parts)
        while True:
            self.actions, before = slicer.seq(actions, need, need)
            grown = need | (before & written)
            if grown == need:
                break
            need = grown
        self.reads = before - self.parts
        self.control = sorted((need & written) - self.parts)
        self.mask = (1 << form.width[self.cell]) - 1
        self.memo = {}

    def step(self, state: tuple) -> list:
        """``[(conds, value, next control)]`` of one firing from control ``state``."""
        if state not in self.memo:
            self.memo[state] = self._step(dict(state))
        return self.memo[state]

    def _step(self, state: dict) -> list:
        f, out = self.form, []
        for conds, final in f.paths(self.actions, (), state):
            fused = f.fuse(conds, final) if self.cell in f.pair else final
            nxt = tuple((c, final.get(c, state.get(c, c))) for c in self.control)
            bad = [c for c, val in nxt if self.parts & atoms(val)]
            if bad:
                raise ValueError(f"control {', '.join(bad)} reads the value")
            out.append((conds, fused.get(self.cell, self.cell), nxt))
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
        return tuple(sorted(keep.items(), key=lambda x: x[0]))

    def explore(self, start: tuple) -> Graph:
        """Every control state reachable from ``start``, in discovery order."""
        index, nodes, cases, todo = {start: 0}, [start], [], [start]
        params = {c: v for c, v in start if c not in self.control}
        while todo:
            node = todo.pop(0)
            row = []
            for conds, value, nxt in self.step(node):
                full = tuple(sorted({**params, **dict(nxt)}.items(), key=lambda x: x[0]))
                if full not in index:
                    index[full] = len(nodes)
                    nodes.append(full)
                    todo.append(full)
                row.append((conds, value, index[full]))
            cases.append(row)
        return Graph(nodes, cases)


def minimal(graph: Graph, lift: Lift) -> tuple:  # pylint: disable=too-many-locals
    """``(class per state, value per class, {class: guard} per class)``: the coarsest bisimulation.

    States are equivalent when their value updates and their guarded successor classes agree.
    """
    values = [lift.cases([(c, v) for c, v, _ in row]) for row in graph.cases]
    guards = [_guards(row, lift) for row in graph.cases]
    label = [G.text(v) for v in values]
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
    return cls, [values[r] for r in rep], edges


def _guards(row, lift: Lift) -> dict:
    """Successor state to the bracket of the paths leading to it."""
    out = {}
    for conds, _, j in row:
        out[j] = out.get(j, 0) + sympy.Mul(*(lift.cond(c) for c in conds))
    return out


@dataclasses.dataclass
class Order:
    """The phase: ``kind`` time or turn, the state at each position, the prefix and the cycle."""

    kind: str
    seq: list
    prefix: int
    period: int
    advance: list


def order(edges: list) -> Order | None:
    """The phase order of a minimal graph from class 0, or None when no single phase fits.

    Time: every class has one successor. Turns: a class stays or moves to one other class.
    """
    time = all(len(e) == 1 for e in edges)
    seq, seen, adv = [], {}, []
    c = 0
    while c not in seen:
        seen[c] = len(seq)
        seq.append(c)
        out = {k: g for k, g in edges[c].items() if time or k != c}
        if len(out) > 1:
            return None
        if not out:
            adv.append(sympy.Integer(0))
            return Order("turn", seq, len(seq) - 1, 1, adv)
        ((c, g),) = out.items()
        adv.append(sympy.Integer(1) if time else g)
    start = seen[c]
    return Order("time" if time else "turn", seq, start, len(seq) - start, adv)


# ---- synthesis ------------------------------------------------------------------------


def absorb(e) -> sympy.Expr:
    """``e`` with each term's brackets taken as 1 inside its other factors."""
    out = []
    for term in sympy.Add.make_args(G.canon(e)):
        bs = [f for f in sympy.Mul.make_args(term) if isinstance(f, G.Iv)]
        rest = sympy.Mul(*(f for f in sympy.Mul.make_args(term) if not isinstance(f, G.Iv)))
        out.append(sympy.Mul(*bs) * rest.subs({b: 1 for b in bs}) if bs else term)
    return G.canon(sympy.Add(*out))


def steps(items: list, x) -> sympy.Expr:
    """``items[x]`` for ``x`` in ``[0, len)``: the first item plus a bracket per change."""
    out = items[0]
    for b in range(1, len(items)):
        if items[b] != items[b - 1]:
            out += (items[b] - items[b - 1]) * G.ge(x, b)
    return out


def boundaries(items: list) -> list:
    """Rotations of a cycle that start it at a change, or ``[0]`` for a constant cycle."""
    n = len(items)
    return [k for k in range(n) if items[k] != items[k - 1]] or [0]


def by_phase(items: list, o: Order) -> list:
    """Candidate expressions of ``items[position(φ)]``: one per rotation of the cycle."""
    pre, cyc = items[: o.prefix], items[o.prefix :]
    out = []
    for k in boundaries(cyc):
        rot = cyc[k:] + cyc[:k]
        x = sympy.Mod(G.PHI - o.prefix - k, o.period) if o.period > 1 else 0
        tail = steps(rot, x) if o.period > 1 else rot[0]
        if o.prefix:
            out.append(steps(pre + [tail], G.PHI))
        else:
            out.append(tail)
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
    items = [values[c] for c in o.seq]
    out = by_phase(items, o)
    ops = [additive(v, mask) for v in items]
    if all(ops):
        sets = [sympy.Integer(int(k == "set")) for k, _ in ops]
        cs = [x if k == "set" else sympy.Integer(0) for k, x in ops]
        ds = [x if k == "add" else sympy.Integer(0) for k, x in ops]
        for s, c, d in zip(by_phase(sets, o), by_phase(cs, o), by_phase(ds, o)):
            out.append(G.canon(s * c + (1 - s) * sympy.Mod(G.V + d, mask + 1)))
    return [absorb(e) for e in out]


def closed_forms(values: list, o: Order, mask: int, v0) -> list:
    """Candidate ``g(φ)``, the value after the firing at ``φ``, when no value reads ``v``."""
    if o.kind != "time":
        return []
    ops = [additive(values[c], mask) for c in o.seq]
    if not all(ops):
        return []
    trace, y = [], v0
    for phi in range(o.prefix + 2 * o.period):
        k, x = ops[phi if phi < len(ops) else o.prefix + (phi - o.prefix) % o.period]
        y = x if k == "set" else (None if y is None else sympy.expand(y + x))
        trace.append(y)
    if any(t is None for t in trace):
        return []
    cyc = ops[o.prefix :]
    drift = sympy.expand(sum((x for k, x in cyc if k == "add"), sympy.Integer(0)))
    if any(k == "set" for k, _ in cyc) or drift == 0:
        drift = sympy.Integer(0)
    out = []
    for k in boundaries([x for _, x in cyc]) if o.period > 1 else [0]:
        out += _windows(trace, o, k, drift)
    return [G.canon(e) for e in out]


def _ramp(seq: list, x, window: bool = False) -> sympy.Expr:
    """``seq[x]`` as its first value plus clamped ramps over runs of equal steps.

    With ``window``, ``0 <= x < len(seq)``: the first run needs no lower clamp, the last no upper.
    """
    out, i = seq[0], 1
    while i < len(seq):
        d = sympy.expand(seq[i] - seq[i - 1])
        j = i
        while j + 1 < len(seq) and sympy.expand(seq[j + 1] - seq[j] - d) == 0:
            j += 1
        if d != 0:
            ramp = x - i + 1 if window and i == 1 else sympy.Max(x - i + 1, 0)
            out += d * (ramp if window and j == len(seq) - 1 else sympy.Min(ramp, j - i + 1))
        i = j + 1
    return out


def _windows(trace: list, o: Order, k: int, drift) -> list:
    """Closed forms with a window starting at the value before the cycle's change at ``k``.

    A window ramp plus drift; the window form alone is kept when it also gives the prefix.
    """
    p, n = o.prefix, o.period
    start = p + (k - 1) % n
    if n == 1 and drift == 0:
        forms = [_ramp(trace[: p + 1], G.PHI)]
    else:
        t = G.PHI - start
        tail = _ramp(trace[start : start + n], sympy.Mod(t, n), True) + drift * sympy.floor(t / n)
        forms = [_gated(trace, tail, start) or _join(trace[:start], tail, start)]
    out = []
    for e in forms:
        out += [e, sympy.expand(e.rewrite(sympy.Abs))]
    return out


def _gated(trace: list, tail, start: int):
    """``tail`` from the first ``φ`` it fits on, the constant before it; None if none fits."""
    fits = start
    while fits > 0 and sympy.expand(tail.subs(G.PHI, fits - 1) - trace[fits - 1]) == 0:
        fits -= 1
    if any(sympy.expand(trace[i] - trace[0]) != 0 for i in range(fits)):
        return None
    return tail if fits == 0 else trace[0] + G.ge(G.PHI, fits) * (tail - trace[0])


def _join(head: list, tail, start: int):
    """``head[φ]`` before ``start``, ``tail`` from it."""
    return _ramp(head, G.PHI) * G.lt(G.PHI, start) + tail * G.ge(G.PHI, start)


# ---- proof ----------------------------------------------------------------------------


class Prover:
    """z3 obligations of expressions against the player's paths of one unit."""

    def __init__(self, machine: Machine, freq: dict, extra: dict | None = None):
        self.machine, self.freq, self.extra = machine, freq, dict(extra or {})
        self.form = machine.form
        self.offset = int(self.form.pr.get("note_offset", 0))

    def env(self, smt, phi) -> dict:
        """Symbols and functions of the grammar over one solver's atoms."""
        f = self.form
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
        return env

    def node(self, graph: Graph, cls: list, i: int, phi: int, value, adv) -> str | None:
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        """None when every path of state ``i`` gives ``value`` and ``adv`` at ``φ``, else why.

        ``adv`` is 1 on the paths that leave the state's class, else 0.
        """
        for conds, val, j in graph.cases[i]:
            smt = self.form.solver()
            env = self.env(smt, z3.BitVecVal(phi, G.BITS))
            smt.solver.add(*(smt.cond(c) for c in conds))
            moved = int(cls[j] != cls[i])
            smt.solver.add(
                z3.Or(G.to_z3(value, env) != smt.term(val), G.to_z3(adv, env) != moved)
                if adv is not None
                else G.to_z3(value, env) != smt.term(val)
            )
            if smt.solver.check() != z3.unsat:
                return f"at φ = {phi}: {witness(smt.solver)}"
        return None

    def periodic(self, e, start: int, period: int) -> str | None:
        """None when ``e(φ + period) == e(φ)`` for every ``φ >= start``."""
        smt = self.form.solver()
        phi = z3.BitVec("φ", G.BITS)
        smt.solver.add(phi >= start, phi < PHI_BOUND)
        env = self.env(smt, phi)
        later = G.to_z3(e, {**env, "φ": phi + period})
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
        step = G.to_z3(f, {**env, "v": G.to_z3(g, env), "φ": phi + 1})
        smt.solver.add(
            z3.Or(G.to_z3(g, start) != G.to_z3(f, start), G.to_z3(g, {**env, "φ": phi + 1}) != step)
        )
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
class Derived:
    """The expressions of one start, with their proof status."""

    phase: typing.Any = None
    value: typing.Any = None
    closed: typing.Any = None
    order: Order | None = None
    states: int = 0
    proof: list = dataclasses.field(default_factory=list)
    error: str = ""

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
    lift = Lift(machine.form, machine.cell, freq, extra)
    try:
        graph = machine.explore(start)
    except ValueError as exc:
        return Derived(error=str(exc))
    cls, values, edges = minimal(graph, lift)
    if lift.unknown:
        return Derived(states=len(graph.nodes), error=f"reads {', '.join(sorted(lift.unknown))}")
    o = order(edges)
    if o is None:
        return Derived(
            states=len(graph.nodes), error="more than one phase: a state advances on two tests"
        )
    out = Derived(order=o, states=len(graph.nodes))
    prover = Prover(machine, freq, extra)
    domain = (("v", 0, machine.mask), ("N", 0, (1 << machine.form.width["N"]) - 1))
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
        if cls[i] in o.seq
        and (why := prover.node(graph, cls, i, o.seq.index(cls[i]), out.value,
                                out.phase if o.kind == "turn" else None))
    ]  # fmt: skip
    out.proof.append(
        (f"{len(graph.nodes)} states", not bad, f"state {bad[0][0]} {bad[0][1]}" if bad else "")
    )
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
