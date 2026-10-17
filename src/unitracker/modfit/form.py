"""Symbolic evaluator and normaliser of the playroutine action language.

Sequential sets wrap to the cell width, expressions are unbounded integers and ``accumulate``
expands exactly as ``vm._acc``; terms normalise to linear forms, brackets and intervals.
"""

import ast

import z3

from unitracker.modfit.flow import flow_of, parse
from unitracker.vm import acc_mode

INF = float("inf")
BITS = 48
CONDS = {"lt", "eq", "not", "land", "lor"}


def is_cond(t) -> bool:
    """True for a boolean term."""
    return isinstance(t, bool) or (isinstance(t, tuple) and t[0] in CONDS)


def is_ones(m) -> bool:
    """True for a non-negative all-ones mask ``2**k - 1``."""
    return isinstance(m, int) and m >= 0 and m & (m + 1) == 0


SHOWN = {}


def show(t) -> str:
    """S-expression text of a term, memoised per tuple object."""
    if isinstance(t, bool):
        return "true" if t else "false"
    if not isinstance(t, tuple):
        return str(t)
    hit = SHOWN.get(id(t))
    if hit is None or hit[0] is not t:
        hit = SHOWN[id(t)] = (t, "(" + " ".join(show(x) for x in t) + ")")
    return hit[1]


def sexpr(t):
    """Readable s-expression tuple of a term, linear forms as sums."""
    if isinstance(t, bool):
        return "true" if t else "false"
    if not isinstance(t, tuple):
        return t
    if t[0] == "lin":
        terms = [
            sexpr(a) if k == 1 else ("neg", sexpr(a)) if k == -1 else ("mul", k, sexpr(a))
            for a, k in t[2]
        ]
        return ("add", *terms, *([t[1]] if t[1] else []))
    return tuple(sexpr(x) for x in t)


def lin(t) -> tuple:
    """``(const, {atom: coeff})`` of an integer term."""
    if isinstance(t, int):
        return t, {}
    if isinstance(t, tuple) and t[0] == "lin":
        return t[1], dict(t[2])
    return 0, {t: 1}


def mk(const, coeffs: dict):
    """The canonical integer term of a linear form."""
    items = tuple(sorted(((a, k) for a, k in coeffs.items() if k), key=lambda x: show(x[0])))
    if not items:
        return const
    if const == 0 and len(items) == 1 and items[0][1] == 1:
        return items[0][0]
    return ("lin", const, items)


def accumulate_into(out: dict, k: int, t) -> int:
    """Add ``k * t``'s atoms into ``out``; return ``k`` times its constant."""
    c, inner = lin(t)
    for x, v in inner.items():
        out[x] = out.get(x, 0) + k * v
    return k * c


def atoms(t) -> set:
    """Every cell name that occurs in ``t``."""
    if isinstance(t, str):
        return {t}
    if isinstance(t, tuple):
        return set().union(*(atoms(x) for x in t))
    return set()


NOATOMS = ()


def union(*groups) -> tuple:
    """The atoms of ``groups`` in first-seen order."""
    return tuple(dict.fromkeys(a for g in groups for a in g))


class Translation:
    """Cached z3 terms over 48-bit two's-complement bit-vectors, with the atoms they read."""

    OPS = {
        "and": lambda a, b: a & b,
        "or": lambda a, b: a | b,
        "xor": lambda a, b: a ^ b,
        "mul": lambda a, b: a * b,
        "shr": lambda a, b: a >> b,
    }

    def __init__(self, rng):
        self.rng = rng
        self.terms, self.conds, self.facts = {}, {}, {}

    def term(self, t) -> tuple:
        """``(bit-vector, atoms)`` of an integer term."""
        if isinstance(t, (bool, int)):
            return z3.BitVecVal(int(t), BITS), NOATOMS
        if t not in self.terms:
            self.terms[t] = self.build(t)
        return self.terms[t]

    def build(self, t) -> tuple:
        """Uncached ``term``."""
        # pylint: disable=too-many-return-statements
        if isinstance(t, str):
            return z3.BitVec(t, BITS), (t,)
        head = t[0]
        if head == "lin":
            parts = [(k, self.term(a)) for a, k in t[2]]
            expr = z3.Sum([z3.BitVecVal(t[1], BITS)] + [k * e for k, (e, _) in parts])
            return expr, union(*(a for _, (_, a) in parts))
        if head == "iv":
            c, atoms_read = self.cond(t[1])
            return z3.If(c, z3.BitVecVal(1, BITS), z3.BitVecVal(0, BITS)), atoms_read
        if head == "ite":
            (c, ac), (a, aa), (b, ab) = self.cond(t[1]), self.term(t[2]), self.term(t[3])
            return z3.If(c, a, b), union(ac, aa, ab)
        if head in self.OPS:
            (a, aa), (b, ab) = self.term(t[1]), self.term(t[2])
            return self.OPS[head](a, b), union(aa, ab)
        if head in ("idx", "rec", "fld"):
            fn = z3.Function(f"{head}_{t[1]}", z3.BitVecSort(BITS), z3.BitVecSort(BITS))
            arg, atoms_read = self.term(t[2])
            return fn(arg), union(atoms_read, (t,))
        return z3.BitVec(show(t), BITS), (t,)

    def cond(self, c) -> tuple:
        """``(boolean, atoms)`` of a condition term."""
        if isinstance(c, bool):
            return z3.BoolVal(c), NOATOMS
        if c not in self.conds:
            head = c[0]
            if head in ("lt", "eq"):
                e, atoms_read = self.term(c[1])
                self.conds[c] = (e < 0 if head == "lt" else e == 0), atoms_read
            elif head == "not":
                e, atoms_read = self.cond(c[1])
                self.conds[c] = z3.Not(e), atoms_read
            else:
                parts = [self.cond(x) for x in c[1:]]
                junction = z3.And if head == "land" else z3.Or
                self.conds[c] = junction([e for e, _ in parts]), union(*(a for _, a in parts))
        return self.conds[c]

    def bounds(self, atom) -> list:
        """The interval facts of an atom."""
        if atom not in self.facts:
            value, (lo, hi) = self.terms[atom][0], self.rng(atom)
            self.facts[atom] = [value >= int(lo)] if lo > -INF else []
            if hi < INF:
                self.facts[atom].append(value <= int(hi))
        return self.facts[atom]


class Smt:
    """A z3 solver over a shared translation, each atom read constrained to its interval."""

    def __init__(self, translation: Translation):
        self.translation = translation
        self.solver = z3.Solver()
        self.bounded = set()

    def bound(self, pair: tuple):
        """The expression of ``(expr, atoms)``, adding the atoms' bounds once."""
        expr, atoms_read = pair
        for a in atoms_read:
            if a not in self.bounded:
                self.solver.add(*self.translation.bounds(a))
                self.bounded.add(a)
        return expr

    def term(self, t):
        """The bit-vector of an integer term."""
        return self.bound(self.translation.term(t))

    def cond(self, c):
        """The z3 boolean of a condition term."""
        return self.bound(self.translation.cond(c))

    def sat(self, *conds) -> bool:
        """True unless ``conds`` are unsatisfiable together with the atom bounds."""
        self.solver.add(*(self.cond(c) for c in conds))
        return self.solver.check() != z3.unsat


class Algebra:
    """Normalising constructors of integer and condition terms over known value ranges."""

    # pylint: disable=too-many-public-methods

    def __init__(self, width: dict, tables: dict, byte_fields: set, pairs: set):
        self.width, self.tables, self.byte_fields, self.pairs = width, tables, byte_fields, pairs
        self.pair = {}
        for lo, hi, k in sorted(pairs):
            name = f"{hi}:{lo}" if k == width[lo] == 8 else f"{hi}:{lo}:{k}"
            self.width[name] = k + width[hi]
            part = lo if width[lo] <= k else ("and", lo, (1 << k) - 1)
            self.pair[name] = (lo, hi, k, part)

    # ranges -----------------------------------------------------------------
    def rng(self, t) -> tuple:
        """A sound interval for an integer term."""
        # pylint: disable=too-many-return-statements,too-many-branches
        if isinstance(t, (bool, int)):
            return int(t), int(t)
        if isinstance(t, str):
            w = self.width.get(t)
            return (0, (1 << w) - 1) if w else (-INF, INF)
        head = t[0]
        if head == "lin":
            lo = hi = t[1]
            for a, k in t[2]:
                alo, ahi = self.rng(a)
                lo, hi = lo + min(k * alo, k * ahi), hi + max(k * alo, k * ahi)
            return lo, hi
        if head == "iv":
            return 0, 1
        if head == "and" and isinstance(t[2], int) and t[2] >= 0:
            return 0, t[2]
        if head == "shr":
            lo, hi = self.rng(t[1])
            if not isinstance(t[2], int):
                return (0, hi) if lo >= 0 else (-INF, INF)
            d = 1 << t[2]
            return lo // d if lo > -INF else lo, hi // d if hi < INF else hi
        if head == "idx" and t[1] in self.tables:
            return self.tables[t[1]]
        if head == "fld" and (
            t[2] == "row"
            or (t[2][1] if isinstance(t[2], tuple) else "row", t[1]) in self.byte_fields
        ):
            return 0, 255
        if head == "ite":
            (a, b), (c, d) = self.rng(t[2]), self.rng(t[3])
            return min(a, c), max(b, d)
        if head in ("or", "xor"):
            (a, b), (c, d) = self.rng(t[1]), self.rng(t[2])
            if min(a, c) >= 0 and max(b, d) < INF:
                return 0, (1 << int(max(b, d)).bit_length()) - 1
        return -INF, INF

    # integer constructors ---------------------------------------------------
    def add(self, *terms):
        """Sum of integer terms."""
        const, coeffs = 0, {}
        for t in terms:
            const += accumulate_into(coeffs, 1, self.num(t))
        return self.pair_names(mk(const, coeffs))

    def scale(self, k: int, t):
        """``k * t``."""
        c, coeffs = lin(self.num(t))
        return mk(k * c, {a: k * v for a, v in coeffs.items()})

    def neg(self, t):
        """``-t``."""
        return self.scale(-1, t)

    def sub(self, a, b):
        """``a - b``."""
        return self.add(a, self.neg(b))

    def num(self, t):
        """An integer term; booleans become Iverson brackets."""
        return self.iv(t) if is_cond(t) else t

    def pair_names(self, t):
        """Rewrite ``c*lo + c*2**k*hi`` over a cell pair as ``c`` times the pair."""
        const, coeffs = lin(t)
        for name, (_, hi, k, part) in self.pair.items():
            if part in coeffs and coeffs.get(hi) == coeffs[part] << k:
                c = coeffs.pop(part)
                coeffs.pop(hi)
                coeffs[name] = coeffs.get(name, 0) + c
        return mk(const, coeffs)

    def mul(self, a, b):
        """Product; linear when one side is constant."""
        a, b = self.num(a), self.num(b)
        if isinstance(a, int):
            return self.scale(a, b)
        if isinstance(b, int):
            return self.scale(b, a)
        return ("mul", *sorted((a, b), key=show))

    def shl(self, a, k):
        """``a << k``, exact as a product for a constant shift."""
        return self.scale(1 << k, a) if isinstance(k, int) else ("shl", a, k)

    def shr(self, a, k):
        """``a >> k``; exact division when every coefficient is a multiple of ``2**k``."""
        a = self.num(a)
        if not isinstance(k, int):
            return ("shr", a, k)
        c, coeffs = lin(a)
        d = 1 << k
        if c % d == 0 and all(v % d == 0 for v in coeffs.values()):
            return mk(c // d, {x: v // d for x, v in coeffs.items()})
        lo, hi = self.rng(a)
        if 0 <= lo and hi < d:
            return 0
        return ("shr", a, k)

    def band(self, a, m):
        """``a & m``; a mask covering ``a``'s range is dropped."""
        a, m = self.num(a), self.num(m)
        if isinstance(a, int) and isinstance(m, int):
            return a & m
        if is_ones(m):
            a = self.modular(a, m + 1)
            if isinstance(a, int):
                return a & m
            lo, hi = self.rng(a)
            if 0 <= lo and hi <= m:
                return a
        return ("and", *sorted((a, m), key=lambda x: (isinstance(x, int), show(x))))

    def modular(self, t, modulus: int):
        """``t`` modulo ``modulus``: symmetric coefficients, inner wraps and splits dropped."""
        const, coeffs = lin(t)
        out = {}
        for a, k in coeffs.items():
            if isinstance(a, tuple) and a[0] == "and" and is_ones(a[2]):
                if (k * (a[2] + 1)) % modulus == 0:
                    const += accumulate_into(out, k, a[1])
                    continue
            out[a] = out.get(a, 0) + k
        for a, k in list(out.items()):
            if not (isinstance(a, tuple) and a[0] == "shr" and a[2] == 8):
                continue
            low = ("and", a[1], 255)
            if out.get(low) is not None and 256 * out[low] == k:
                del out[a]
                const += accumulate_into(out, out.pop(low), a[1])
        half = modulus // 2
        return self.pair_names(
            mk(
                const % modulus,
                {a: (k + half) % modulus - half for a, k in out.items() if k % modulus},
            )
        )

    def bor(self, a, b):
        """``a | b``; an add when one side is a non-negative multiple of the other's span."""
        a, b = self.num(a), self.num(b)
        for x, y in ((a, b), (b, a)):
            lo, hi = self.rng(x)
            c, coeffs = lin(y)
            if lo >= 0 and hi < INF and self.rng(y)[0] >= 0:
                span = 1 << int(hi).bit_length()
                if c % span == 0 and all(v % span == 0 for v in coeffs.values()):
                    return self.add(x, y)
        if isinstance(a, int) and isinstance(b, int):
            return a | b
        return ("or", *sorted((a, b), key=show))

    def bxor(self, a, m):
        """``a ^ m``; an all-ones mask over ``a``'s range is ``m - a``."""
        a, m = self.num(a), self.num(m)
        if isinstance(a, int) and isinstance(m, int):
            return a ^ m
        for x, y in ((a, m), (m, a)):
            if is_ones(y):
                lo, hi = self.rng(x)
                if 0 <= lo and hi <= y:
                    return self.add(y, self.neg(x))
        return ("xor", *sorted((a, m), key=show))

    def wrap(self, cell, t):
        """``t`` stored in ``cell``."""
        w = self.width.get(cell)
        return self.num(t) if w is None else self.band(t, (1 << w) - 1)

    # conditions -------------------------------------------------------------
    def truth(self, t):
        """A term as a condition."""
        if is_cond(t):
            return t
        if isinstance(t, tuple) and t[0] == "iv":
            return t[1]
        if (
            isinstance(t, tuple)
            and t[0] == "and"
            and isinstance(t[2], int)
            and t[2] & (t[2] - 1) == 0
        ):
            lo, hi = self.rng(t[1])
            if 0 <= lo and hi < 2 * t[2]:
                return self.ge(t[1], t[2])
        return self.negate(self.eq(t, 0))

    @staticmethod
    def iv(c):
        """The Iverson bracket of a condition."""
        return int(c) if isinstance(c, bool) else ("iv", c)

    def ltz(self, e):
        """``e < 0`` over integers."""
        lo, hi = self.rng(e)
        if hi < 0:
            return True
        if lo >= 0:
            return False
        return ("lt", e, 0)

    def lt(self, a, b):
        """``a < b``."""
        return self.ltz(self.add(a, self.neg(b)))

    def le(self, a, b):
        """``a <= b``."""
        return self.ltz(self.add(a, self.neg(b), -1))

    def gt(self, a, b):
        """``a > b``."""
        return self.ltz(self.add(b, self.neg(a)))

    def ge(self, a, b):
        """``a >= b``."""
        return self.ltz(self.add(b, self.neg(a), -1))

    def eq(self, a, b):
        """``a == b`` with a canonical sign."""
        e = self.add(a, self.neg(b))
        if isinstance(e, int):
            return e == 0
        lo, hi = self.rng(e)
        if lo > 0 or hi < 0:
            return False
        if isinstance(e, tuple) and e[0] == "lin" and e[2][0][1] < 0:
            e = self.neg(e)
        return ("eq", e, 0)

    def ne(self, a, b):
        """``a != b``."""
        return self.negate(self.eq(a, b))

    def negate(self, c):
        """``not c``; a strict integer comparison negates to another."""
        if isinstance(c, bool):
            return not c
        if c[0] == "not":
            return c[1]
        if c[0] == "lt":
            return self.ltz(self.add(self.neg(c[1]), -1))
        if c[0] in ("land", "lor"):
            return self.junction(
                "lor" if c[0] == "land" else "land", [self.negate(x) for x in c[1:]]
            )
        return ("not", c)

    def junction(self, head, items):
        """Flattened, decided, sorted ``land``/``lor``."""
        unit = head == "land"
        out = set()
        for x in items:
            x = self.truth(x)
            if x is (not unit):
                return not unit
            if x is unit:
                continue
            out.update(x[1:] if isinstance(x, tuple) and x[0] == head else [x])
        if not out:
            return unit
        return next(iter(out)) if len(out) == 1 else (head, *sorted(out, key=show))

    def ite(self, c, a, b):
        """``a if c else b`` with a canonical condition."""
        # pylint: disable=too-many-return-statements
        c = self.truth(c)
        if isinstance(c, bool):
            return a if c else b
        if a == b:
            return a
        if isinstance(a, tuple) and isinstance(b, tuple) and a[0] == b[0] == "and" and a[2] == b[2]:
            return self.band(self.ite(c, a[1], b[1]), a[2])
        if is_cond(a) and is_cond(b):
            return self.junction(
                "lor", [self.junction("land", [c, a]), self.junction("land", [self.negate(c), b])]
            )
        a, b = self.num(a), self.num(b)
        d = self.add(a, self.neg(b))
        if isinstance(d, int):
            return self.add(b, self.scale(d, self.iv(c)))
        (ca, la), (cb, lb) = lin(a), lin(b)
        common = {x: k for x, k in la.items() if lb.get(x) == k}
        if common:
            rest_a = mk(ca, {x: k for x, k in la.items() if x not in common})
            rest_b = mk(cb, {x: k for x, k in lb.items() if x not in common})
            return self.add(mk(0, common), self.ite(c, rest_a, rest_b))
        nc = self.negate(c)
        if show(nc) < show(c):
            c, a, b = nc, b, a
        return ("ite", c, a, b)


class Form(Algebra):
    """Symbolic evaluator of one playroutine's actions, optionally over record data."""

    # pylint: disable=too-many-instance-attributes,too-many-public-methods

    def __init__(self, pr: dict, data: dict | None = None):
        cells = {**(pr.get("global") or {}), **(pr.get("voice") or {})}
        width = {
            k: (v or {}).get("width") for k, v in cells.items() if isinstance(v, (dict, type(None)))
        }
        byte_fields = set()
        for name, rt in (pr.get("record_types") or {}).items():
            fill = defaults(rt)
            byte_fields |= {(name, f) for f, d in fill.items() if isinstance(d, int) and d >= 0}
        self.table_data = pr.get("tables") or {}
        super().__init__(
            width,
            {k: (min(v), max(v)) for k, v in self.table_data.items() if v},
            byte_fields,
            flow_of(pr).pairs(),
        )
        self.pr, self.data = pr, data or {}
        self.scratch = {k for k, w in width.items() if w is None}
        self.consts = dict(pr.get("constants") or {})
        self.functions = pr.get("functions") or {}
        self.procs = pr.get("procs") or {}
        self.translation = Translation(self.rng)
        self.feasibility = {}

    # expressions ------------------------------------------------------------
    BINOPS = {
        ast.Add: "add",
        ast.Sub: "sub",
        ast.Mult: "mul",
        ast.LShift: "shl",
        ast.RShift: "shr",
        ast.BitAnd: "band",
        ast.BitOr: "bor",
        ast.BitXor: "bxor",
    }
    COMPARES = {ast.Lt: "lt", ast.LtE: "le", ast.Gt: "gt", ast.GtE: "ge", ast.Eq: "eq"}

    def expr(self, src, state, env=None):
        """Term of an expression source."""
        if isinstance(src, (bool, int)):
            return int(src)
        return self.node(parse(str(src)), state, env or {})

    def node(self, n, state, env):
        """Term of a Python AST node."""
        handler = self.NODES.get(type(n))
        if handler is None:
            raise ValueError(f"unsupported expression {ast.dump(n)}")
        return handler(self, n, state, env)

    def _name(self, n, state, env):
        """A bound argument, a constant or a cell's current term."""
        if n.id in env:
            return env[n.id]
        if n.id in self.consts:
            return int(self.consts[n.id])
        return state.get(n.id, n.id)

    def _binop(self, n, state, env):
        """A binary operator."""
        a, b = self.node(n.left, state, env), self.node(n.right, state, env)
        op = self.BINOPS.get(type(n.op))
        return (
            getattr(self, op)(a, b)
            if op
            else (type(n.op).__name__.lower(), self.num(a), self.num(b))
        )

    def _unary(self, n, state, env):
        """``not``, ``-`` or ``~``."""
        v = self.node(n.operand, state, env)
        if isinstance(n.op, ast.Not):
            return self.negate(self.truth(v))
        return self.neg(v) if isinstance(n.op, ast.USub) else ("inv", self.num(v))

    def _boolop(self, n, state, env):
        """``and``/``or``."""
        head = "land" if isinstance(n.op, ast.And) else "lor"
        return self.junction(head, [self.node(v, state, env) for v in n.values])

    def _compare(self, n, state, env):
        """A comparison chain."""
        terms, left = [], self.node(n.left, state, env)
        for op, right in zip(n.ops, n.comparators):
            right = self.node(right, state, env)
            terms.append(getattr(self, self.COMPARES.get(type(op), "ne"))(left, right))
            left = right
        return self.junction("land", terms)

    def _ifexp(self, n, state, env):
        """``a if c else b``."""
        ev = [self.node(x, state, env) for x in (n.test, n.body, n.orelse)]
        return self.ite(*ev)

    def _call(self, n, state, env):
        """A playroutine function, expanded."""
        fn = self.functions[n.func.id]
        args = (self.node(a, state, env) for a in n.args)
        return self.expr(fn["expr"], state, dict(zip(fn.get("args") or [], args)))

    NODES = {
        ast.Constant: lambda self, n, state, env: int(n.value),
        ast.Name: _name,
        ast.BinOp: _binop,
        ast.UnaryOp: _unary,
        ast.BoolOp: _boolop,
        ast.Compare: _compare,
        ast.IfExp: _ifexp,
        ast.Subscript: lambda self, n, state, env: self.subscript(
            n.value.id, self.num(self.node(n.slice, state, env))
        ),
        ast.Attribute: lambda self, n, state, env: self.field(
            self.node(n.value, state, env), n.attr
        ),
        ast.Call: _call,
    }

    def subscript(self, name: str, index):
        """``name[index]``: a folded table constant, a table entry or a record."""
        data = self.table_data.get(name)
        if data is not None and isinstance(index, int) and 0 <= index < len(data):
            return int(data[index])
        return ("idx", name, index) if name in self.tables else ("rec", name, index)

    def field(self, rec, attr: str):
        """``rec.attr``, concrete when the record data holds it."""
        if rec == "row":
            row = self.data.get("row")
        elif isinstance(rec, tuple) and rec[0] == "rec":
            row = self.data.get(rec[1], {}).get(rec[2])
        else:
            row = None
        if row is not None and attr in row:
            return int(row[attr])
        return ("fld", attr, rec)

    # solving ----------------------------------------------------------------
    def solver(self) -> Smt:
        """A fresh bit-vector translation; values stay far inside ``BITS``."""
        return Smt(self.translation)

    def feasible(self, conds) -> bool:
        """False when ``conds`` are contradictory; memoised per condition set."""
        key = frozenset(conds)
        if key not in self.feasibility:
            self.feasibility[key] = not key or self.solver().sat(*conds)
        return self.feasibility[key]

    def implies(self, conds, c) -> bool:
        """True when ``conds`` imply ``c``."""
        c = self.truth(c)
        return c is True or (c is not False and not self.feasible((*conds, self.negate(c))))

    def relation(self, a, b) -> str | None:
        """``"same"`` or ``"complement"`` when conditions ``a`` and ``b`` always agree or differ."""
        for kind, test in (("same", lambda x, y: x != y), ("complement", lambda x, y: x == y)):
            smt = self.solver()
            smt.solver.add(test(smt.cond(a), smt.cond(b)))
            if smt.solver.check() == z3.unsat:
                return kind
        return None

    # statements -------------------------------------------------------------
    def paths(self, actions, conds=(), state=None) -> list:
        """``[(conds, state)]``: the feasible guarded cases of ``actions`` over start values."""
        state = dict(state or {})
        actions = list(actions or [])
        for i, a in enumerate(actions):
            rest = actions[i + 1 :]
            if "return" in a:
                return [(conds, state)]
            if "if" in a:
                c = self.truth(self.expr(a["if"], state))
                bodies = (list(a.get("then") or []) + rest, list(a.get("else") or []) + rest)
                return self.branch(conds, c, bodies, state)
            if "accumulate" in a:
                return self.accumulate(a, rest, conds, state)
            if "call" in a:
                self.assign(a.get("with") or {}, state)
                self.enter(a["call"])
                return [
                    p
                    for cs, st in self.paths(self.procs[a["call"]], conds, state)
                    for p in self.paths(rest, cs, st)
                ]
            if "set" not in a:
                return self.opaque(a, rest, conds, state)
            self.assign(a["set"], state)
        return [(conds, state)]

    def enter(self, proc: str) -> None:
        """Called before a proc's actions are evaluated."""

    def opaque(self, a: dict, rest, conds, state) -> list:
        """Paths through an action this evaluator does not model."""
        raise ValueError(f"unsupported action {a!r}")

    def assign(self, sets: dict, state: dict) -> None:
        """Run sequential sets on ``state``, each wrapped to its cell."""
        for k, v in sets.items():
            state[k] = self.wrap(k, self.expr(v, state))

    def branch(self, conds, c, bodies, state) -> list:
        """Paths through ``if c`` with ``bodies`` the then and else continuations."""
        out = []
        for guard, body in zip((c, self.negate(c)), bodies):
            if guard is False:
                continue
            cs = conds if guard is True else (*conds, guard)
            if guard is True or self.feasible(cs):
                out += self.paths(body, cs, state)
        return out

    def accumulate(self, a: dict, rest, conds, state) -> list:
        """The VM's bounded accumulator as guarded cases; ``on_bound`` runs on a hit."""
        out = []
        for guard, value, hit in self.acc_cases(a, state):
            cs = tuple(g for g in (*conds, *guard) if g is not True)
            if False in cs or not self.feasible(cs):
                continue
            body = list(a.get("on_bound") or []) if hit else []
            out += self.paths(body + rest, cs, {**state, a["accumulate"]: value})
        return out

    def acc_cases(self, a: dict, state) -> list:
        """``[(guard, value, hit)]`` of one ``_acc`` step."""
        cell = a["accumulate"]
        v0, step = state.get(cell, cell), self.expr(a.get("step", 0), state)
        lo, hi = (self.expr(a[k], state) if k in a else None for k in ("min", "max"))
        mode, signed = acc_mode(a, self.width.get(cell) is not None)
        view = self.signed(cell) if signed else lambda x: x
        if "toward" not in a:
            return self.bounds(cell, v0, step, lo, hi, mode, view)
        t = self.expr(a["toward"], state)
        moving, below = self.ne(view(v0), view(t)), self.lt(view(v0), view(t))
        return [
            ((self.negate(moving),), v0, False),
            *(
                ((moving, side, *g), v, h)
                for side, args in (
                    (below, (step, lo, t)),
                    (self.negate(below), (self.neg(step), t, hi)),
                )
                for g, v, h in self.bounds(cell, v0, *args, mode, view)
            ),
        ]

    def signed(self, cell):
        """The two's complement view ``x - 2**w * [x >= 2**(w-1)]`` at ``cell``'s width."""
        w = self.width[cell]
        return lambda x: self.sub(x, self.scale(1 << w, self.iv(self.ge(x, 1 << (w - 1)))))

    def bounds(self, cell, v0, s, lo, hi, mode, view) -> list:
        """``[(guard, value, hit)]`` of ``v0 + s`` tested against the bounds, as ``_acc``."""
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        value = self.wrap(cell, self.add(v0, s))
        test = view(value)
        hhit = False if hi is None else self.gt(test, view(hi))
        lhit = False if lo is None else self.lt(test, view(lo))
        nh = self.negate(hhit)
        return [
            (
                guard,
                self.wrap(cell, bound if mode == 1 else self.sub(v0, s)) if mode else value,
                True,
            )
            for bound, guard in ((hi, (hhit,)), (lo, (nh, lhit)))
            if bound is not None
        ] + [((nh, self.negate(lhit)), value, False)]

    # byte pairs -------------------------------------------------------------
    def unwrap(self, conds, t, modulus: int) -> tuple:
        """``(t mod modulus, fired)`` with carry identities applied to a fixpoint.

        ``k*(E & m)`` is ``k*E`` when ``k*(m+1)`` vanishes, ``k*(E >> s)`` is
        ``(k >> s)*(E - (E & (2**s - 1)))``, and a wrap the guard pins to one period is removed.
        """
        fired, changed = False, True
        while changed:
            const, coeffs = lin(self.modular(t, modulus))
            out, changed = {}, False
            for a, k in coeffs.items():
                rep, k, carry = self.carry(conds, a, k, modulus)
                if rep is None:
                    out[a] = out.get(a, 0) + k
                    continue
                changed, fired = True, fired or carry
                const += accumulate_into(out, k, rep)
            t = mk(const, out)
        return self.modular(t, modulus), fired

    def carry(self, conds, a, k: int, modulus: int) -> tuple:
        """``(replacement, coefficient, is a carry)`` for one atom of ``unwrap``."""
        if isinstance(a, tuple) and a[0] == "and" and is_ones(a[2]):
            span = a[2] + 1
            if (k * span) % modulus == 0:
                return a[1], k, False
            for shift in (0, -span, span):
                pin = self.junction("land", [self.ge(a[1], -shift), self.lt(a[1], span - shift)])
                if self.implies(conds, pin):
                    return self.add(a[1], shift), k, True
        elif (
            isinstance(a, tuple)
            and a[0] == "shr"
            and isinstance(a[2], int)
            and k % (1 << a[2]) == 0
        ):
            return self.sub(a[1], self.band(a[1], (1 << a[2]) - 1)), k >> a[2], True
        return None, k, False

    def fuse(self, conds, upd: dict) -> dict:
        """Write each pair whose joint update is ``P + D`` (``D`` free of its parts) as one."""
        for name, (lo, hi, k, part) in self.pair.items():
            if lo not in upd and hi not in upd:
                continue
            low = upd.get(lo, lo) if part == lo else self.band(upd.get(lo, lo), (1 << k) - 1)
            joint = self.add(self.scale(1 << k, upd.get(hi, hi)), low, self.neg(name))
            d, _ = self.unwrap(conds, joint, 1 << self.width[name])
            if not {lo, hi} & atoms(d):
                upd = {c: v for c, v in upd.items() if c not in (lo, hi)}
                upd[name] = self.band(self.add(name, d), (1 << self.width[name]) - 1)
        return upd

    def fused(self, paths) -> list:
        """``paths`` with each final state's pairs fused."""
        return [(conds, self.fuse(conds, final)) for conds, final in paths]

    def updates(self, actions) -> list:
        """``[(conds, {cell: term})]`` per path, byte pairs fused, unchanged and scratch dropped."""
        out = []
        for conds, final in self.paths(actions):
            upd = {k: v for k, v in final.items() if v != k and k not in self.scratch}
            out.append((conds, self.fuse(conds, upd)))
        return out


class Rows(dict):
    """A record type's rows; a missing row or field reads as the field's default."""

    def __init__(self, rows: dict, fill: dict):
        super().__init__(rows)
        self.defaults = fill

    def get(self, key, default=None):
        """The row at ``key`` with its defaults filled in."""
        del default
        return {**self.defaults, **super().get(key, {})}


def defaults(rtype: dict) -> dict:
    """``{field: default}`` of a record type."""
    fields = rtype.get("fields") or []
    return dict(fields) if isinstance(fields, dict) else dict.fromkeys(fields, 0)


class Programs(Form):
    """A form over a song's records that also runs record templates with ``row`` bound."""

    def opaque(self, a: dict, rest, conds, state) -> list:
        """A template ``run`` on its row, then the rest; other actions are unsupported."""
        run = a.get("run") or {}
        template = (
            (self.pr.get("record_types") or {}).get(run.get("record"), {}).get(run.get("field"))
        )
        if not isinstance(template, list):
            return super().opaque(a, rest, conds, state)
        row = ("rec", run["record"], self.num(self.expr(run["index"], state)))
        out = []
        for cs, st in self.paths(template, conds, {**state, "row": row}):
            st = {**st, "row": state.get("row", "row")}
            out += self.paths(rest, cs, st)
        return out


def record_data(song: dict, rtypes: dict) -> dict:
    """``{record type: Rows}`` of a song, every record type present."""
    data = {
        name: Rows(rows, defaults(rtypes.get(name) or {}))
        for name, rows in (song.get("records") or {}).items()
    }
    for name, rtype in rtypes.items():
        data.setdefault(name, Rows({}, defaults(rtype)))
    return data
