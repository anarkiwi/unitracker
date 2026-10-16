"""Functional normal form of playroutine operators: one expression per written cell.

Actions are evaluated symbolically (sequential sets wrapped to cell width, branches and
returns merged into ``ite``, ``accumulate`` expanded as the VM's ``_acc``) over unbounded
integers, with linear arithmetic, interval facts and byte-pair fusion normalizing terms.
"""

import ast
import itertools

import z3

INF = float("inf")
BITS = 48
CONDS = {"lt", "eq", "not", "land", "lor", True, False}


def is_cond(t) -> bool:
    """True for a boolean term."""
    return isinstance(t, bool) or (isinstance(t, tuple) and t[0] in CONDS)


def show(t) -> str:
    """S-expression text of a term."""
    if isinstance(t, bool):
        return "true" if t else "false"
    if isinstance(t, tuple):
        return "(" + " ".join(show(x) for x in t) + ")"
    return str(t)


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


def size(t) -> int:
    """Node count of a term."""
    return 1 + sum(size(x) for x in t) if isinstance(t, tuple) else 1


def atoms_heads(t) -> set:
    """Every head symbol in ``t``."""
    if isinstance(t, tuple):
        return {t[0]}.union(*(atoms_heads(x) for x in t[1:]))
    return set()


def atoms(t) -> set:
    """Every cell name that occurs in ``t``."""
    if isinstance(t, str):
        return {t}
    if isinstance(t, tuple):
        return set().union(*(atoms(x) for x in t))
    return set()


def names(src) -> set:
    """Cell-like names read by an expression source."""
    if isinstance(src, (bool, int, float)) or src is None:
        return set()
    return {n.id for n in ast.walk(ast.parse(str(src), mode="eval")) if isinstance(n, ast.Name)}


def assignments(doc, scratch=frozenset(), env=None):
    """``(cell, names read)`` for every assignment; scratch cells resolve within their action list."""
    env = {} if env is None else env
    if isinstance(doc, list):
        local = dict(env)
        for x in doc:
            yield from assignments(x, scratch, local)
    elif isinstance(doc, dict):
        for key in ("set", "with"):
            for cell, src in (doc.get(key) or {}).items() if isinstance(doc.get(key), dict) else ():
                read = set().union(*(env.get(n, {n}) for n in names(src)))
                if cell in scratch:
                    env[cell] = read
                else:
                    yield cell, src, read
        for k, v in doc.items():
            if k not in ("set", "with") and isinstance(v, (dict, list)):
                yield from assignments(v, scratch, dict(env) if isinstance(v, dict) else env)


def joined(src) -> set:
    """``(lo, hi)`` for each ``(hi << 8) | lo`` (or ``+``) in an expression source."""
    out = set()
    if not isinstance(src, str):
        return out
    for n in ast.walk(ast.parse(src, mode="eval")):
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.BitOr, ast.Add)):
            for a, b in ((n.left, n.right), (n.right, n.left)):
                if (
                    isinstance(a, ast.BinOp)
                    and isinstance(a.op, ast.LShift)
                    and isinstance(a.left, ast.Name)
                    and isinstance(a.right, ast.Constant)
                    and a.right.value == 8
                    and isinstance(b, ast.Name)
                ):
                    out.add((b.id, a.left.id))
    return out


def byte_pairs(pr: dict) -> set:
    """``(lo, hi)`` cells the playroutine joins, or routes only into one SID register pair's halves."""
    cells = {**(pr.get("global") or {}), **(pr.get("voice") or {})}
    scratch = frozenset(k for k, v in cells.items() if isinstance(v, dict) and "width" not in v)
    edges, pairs = {}, set()
    for cell, src, read in assignments(pr, scratch):
        pairs |= joined(src)
        for n in read:
            edges.setdefault(n, set()).add(cell)
    voice = {
        cell: i
        for i, src in ((pr.get("ghost") or {}).get("voice") or {}).items()
        for cell in names(src)
    }
    reach = {}
    for start in edges:
        seen, todo = {start}, [start]
        while todo:
            for nxt in edges.get(todo.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        reach[start] = {voice[c] for c in seen if c in voice}
    for lo, hi in itertools.permutations(reach, 2):
        if len(reach[lo]) == len(reach[hi]) == 1:
            (rl,), (rh,) = reach[lo], reach[hi]
            if rl % 2 == 0 and rh == rl + 1 and rl < 4:
                pairs.add((lo, hi))
    return pairs


class Form:
    """Symbolic evaluator and normalizer for one playroutine."""

    def __init__(self, pr: dict, data: dict | None = None):
        self.pr = pr
        self.data = data or {}
        cells = {**(pr.get("global") or {}), **(pr.get("voice") or {})}
        self.width = {
            k: (v or {}).get("width") for k, v in cells.items() if isinstance(v, (dict, type(None)))
        }
        self.scratch = {k for k, w in self.width.items() if w is None}
        self.consts = dict(pr.get("constants") or {})
        self.tables = {k: (min(v), max(v)) for k, v in (pr.get("tables") or {}).items() if v}
        self.functions = pr.get("functions") or {}
        self.procs = pr.get("procs") or {}
        self.byte_fields = set()
        for name, rt in (pr.get("record_types") or {}).items():
            fields = rt.get("fields") or []
            defaults = fields if isinstance(fields, dict) else dict.fromkeys(fields, 0)
            self.byte_fields |= {
                (name, f) for f, d in defaults.items() if isinstance(d, int) and d >= 0
            }
        self.pairs = {
            (lo, hi) for lo, hi in byte_pairs(pr) if self.width.get(lo) == self.width.get(hi) == 8
        }
        self.records = {"row": None}

    # ranges -----------------------------------------------------------------
    def rng(self, t) -> tuple:
        """A sound interval for an integer term."""
        # pylint: disable=too-many-return-statements
        if isinstance(t, bool):
            return int(t), int(t)
        if isinstance(t, int):
            return t, t
        if isinstance(t, str):
            if ":" in t:
                return 0, 0xFFFF
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
            return lo // (1 << t[2]) if lo > -INF else lo, hi // (1 << t[2]) if hi < INF else hi
        if head == "idx" and t[1] in self.tables:
            return self.tables[t[1]]
        if (
            head == "fld"
            and (t[2][1] if isinstance(t[2], tuple) else "row", t[1]) in self.byte_fields
        ):
            return 0, 255
        if head == "fld" and t[2] == "row":
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
            c, k = lin(self.num(t))
            const += c
            for a, v in k.items():
                coeffs[a] = coeffs.get(a, 0) + v
        return self.pair_names(mk(const, coeffs))

    def scale(self, k: int, t):
        """``k * t``."""
        c, coeffs = lin(self.num(t))
        return mk(k * c, {a: k * v for a, v in coeffs.items()})

    def neg(self, t):
        """``-t``."""
        return self.scale(-1, t)

    def num(self, t):
        """An integer term; booleans become Iverson brackets."""
        return self.iv(t) if is_cond(t) else t

    def pair_names(self, t):
        """Rewrite ``c*lo + 256c*hi`` over an 8-bit cell pair as ``c * hi:lo``."""
        const, coeffs = lin(t)
        for lo, hi in self.pairs:
            if lo in coeffs and coeffs.get(hi) == 256 * coeffs[lo]:
                k = coeffs.pop(lo)
                coeffs.pop(hi)
                coeffs[f"{hi}:{lo}"] = coeffs.get(f"{hi}:{lo}", 0) + k
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
        if isinstance(m, int) and m >= 0 and m & (m + 1) == 0:
            a = self.modular(a, m + 1)
            if isinstance(a, int):
                return a & m
            lo, hi = self.rng(a)
            if 0 <= lo and hi <= m:
                return a
        return ("and", *sorted((a, m), key=lambda x: (isinstance(x, int), show(x))))

    def modular(self, t, modulus: int):
        """``t`` modulo ``modulus``: symmetric coefficients, inner same-modulus wraps dropped."""
        const, coeffs = lin(t)
        out = {}
        for a, k in coeffs.items():
            if (
                isinstance(a, tuple)
                and a[0] == "and"
                and isinstance(a[2], int)
                and a[2] & (a[2] + 1) == 0
                and (k * (a[2] + 1)) % modulus == 0
            ):
                c, inner = lin(a[1])
                const += k * c
                for x, v in inner.items():
                    out[x] = out.get(x, 0) + k * v
            else:
                out[a] = out.get(a, 0) + k
        for a, k in list(out.items()):
            low = (
                ("and", a[1], 255) if isinstance(a, tuple) and a[0] == "shr" and a[2] == 8 else None
            )
            if low is not None and out.get(low) is not None and 256 * out[low] == k:
                c, inner = lin(a[1])
                const += out.pop(low) * c
                v = out.pop(a) // 256
                for x, w in inner.items():
                    out[x] = out.get(x, 0) + v * w
        sym = lambda v: (v + modulus // 2) % modulus - modulus // 2  # noqa: E731
        return self.pair_names(
            mk(const % modulus, {a: sym(k) for a, k in out.items() if k % modulus})
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
            if isinstance(y, int) and y >= 0 and y & (y + 1) == 0:
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

    def iv(self, c):
        """The Iverson bracket of a condition."""
        if isinstance(c, bool):
            return int(c)
        return ("iv", c)

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
        return self.lt(b, a)

    def ge(self, a, b):
        """``a >= b``."""
        return self.le(b, a)

    def eq(self, a, b):
        """``a == b`` with a canonical sign."""
        e = self.add(a, self.neg(b))
        if isinstance(e, int):
            return e == 0
        lo, hi = self.rng(e)
        if lo > 0 or hi < 0:
            return False
        c, coeffs = lin(e)
        first = mk(c, coeffs)
        if isinstance(first, tuple) and first[0] == "lin" and first[2][0][1] < 0:
            e = self.neg(e)
        return ("eq", e, 0)

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
            shared = mk(0, common)
            rest_a = mk(ca, {x: k for x, k in la.items() if x not in common})
            rest_b = mk(cb, {x: k for x, k in lb.items() if x not in common})
            return self.add(shared, self.ite(c, rest_a, rest_b))
        nc = self.negate(c)
        if show(nc) < show(c):
            c, a, b = nc, b, a
        return ("ite", c, a, b)

    # expressions ------------------------------------------------------------
    def expr(self, src, state, env=None):
        """Term of an expression source."""
        if isinstance(src, (bool, int)):
            return int(src)
        return self.node(ast.parse(str(src), mode="eval").body, state, env or {})

    def node(self, n, state, env):
        """Term of a Python AST node."""
        # pylint: disable=too-many-return-statements,too-many-branches
        ev = lambda x: self.node(x, state, env)  # noqa: E731
        if isinstance(n, ast.Constant):
            return int(n.value)
        if isinstance(n, ast.Name):
            if n.id in env:
                return env[n.id]
            if n.id in self.consts:
                return int(self.consts[n.id])
            return state.get(n.id, n.id)
        if isinstance(n, ast.BinOp):
            a, b = ev(n.left), ev(n.right)
            op = type(n.op)
            if op is ast.Add:
                return self.add(a, b)
            if op is ast.Sub:
                return self.add(a, self.neg(b))
            if op is ast.Mult:
                return self.mul(a, b)
            if op is ast.LShift:
                return self.shl(a, b)
            if op is ast.RShift:
                return self.shr(a, b)
            if op is ast.BitAnd:
                return self.band(a, b)
            if op is ast.BitOr:
                return self.bor(a, b)
            if op is ast.BitXor:
                return self.bxor(a, b)
            return (type(n.op).__name__.lower(), self.num(a), self.num(b))
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            if isinstance(n.op, ast.Not):
                return self.negate(self.truth(v))
            if isinstance(n.op, ast.USub):
                return self.neg(v)
            return ("inv", self.num(v))
        if isinstance(n, ast.BoolOp):
            head = "land" if isinstance(n.op, ast.And) else "lor"
            return self.junction(head, [ev(v) for v in n.values])
        if isinstance(n, ast.Compare):
            fns = {
                ast.Lt: self.lt,
                ast.LtE: self.le,
                ast.Gt: self.gt,
                ast.GtE: self.ge,
                ast.Eq: self.eq,
            }
            terms, left = [], ev(n.left)
            for op, right in zip(n.ops, n.comparators):
                right = ev(right)
                fn = fns.get(type(op))
                terms.append(fn(left, right) if fn else self.negate(self.eq(left, right)))
                left = right
            return self.junction("land", terms)
        if isinstance(n, ast.IfExp):
            return self.ite(ev(n.test), ev(n.body), ev(n.orelse))
        if isinstance(n, ast.Subscript):
            name = n.value.id
            index = self.num(ev(n.slice))
            data = self.pr.get("tables", {}).get(name)
            if data is not None and isinstance(index, int) and 0 <= index < len(data):
                return int(data[index])
            return ("idx", name, index) if name in self.tables else ("rec", name, index)
        if isinstance(n, ast.Attribute):
            rec = ev(n.value)
            row = self.data.get(rec[1], {}).get(rec[2]) if isinstance(rec, tuple) and rec[0] == "rec" else None
            if rec == "row" and "row" in self.data:
                row = self.data["row"]
            if row is not None and n.attr in row:
                return int(row[n.attr])
            return ("fld", n.attr, rec)
        if isinstance(n, ast.Call):
            fn = self.functions[n.func.id]
            inner = dict(zip(fn.get("args") or [], (ev(a) for a in n.args)))
            return self.expr(fn["expr"], state, inner)
        raise ValueError(f"unsupported expression {ast.dump(n)}")

    # statements -------------------------------------------------------------
    def solver(self):
        """A fresh ``(solver, bv term, bv cond)`` triple over two's-complement bit-vectors.

        Values stay far inside ``BITS``, so bit-vector arithmetic, masks, shifts and signed
        comparisons agree with the playroutine's unbounded integers; atoms carry their ranges.
        """
        solver, memo = z3.Solver(), {}

        def bound(key, t, value):
            lo, hi = self.rng(t)
            if lo > -INF:
                solver.add(value >= int(lo))
            if hi < INF:
                solver.add(value <= int(hi))
            memo[key] = value
            return value

        def term(t):
            # pylint: disable=too-many-return-statements
            if isinstance(t, bool):
                return z3.BitVecVal(int(t), BITS)
            if isinstance(t, int):
                return z3.BitVecVal(t, BITS)
            key = show(t)
            if key in memo:
                return memo[key]
            if isinstance(t, str):
                return bound(key, t, z3.BitVec(t, BITS))
            head = t[0]
            if head == "lin":
                return z3.Sum([z3.BitVecVal(t[1], BITS)] + [k * term(a) for a, k in t[2]])
            if head == "iv":
                return z3.If(cond(t[1]), z3.BitVecVal(1, BITS), z3.BitVecVal(0, BITS))
            if head == "ite":
                return z3.If(cond(t[1]), term(t[2]), term(t[3]))
            ops = {
                "and": lambda a, b: a & b,
                "or": lambda a, b: a | b,
                "xor": lambda a, b: a ^ b,
                "mul": lambda a, b: a * b,
                "shr": lambda a, b: a >> b,
            }
            if head in ops:
                return ops[head](term(t[1]), term(t[2]))
            if head in ("idx", "rec"):
                fn = z3.Function(f"{head}_{t[1]}", z3.BitVecSort(BITS), z3.BitVecSort(BITS))
                return bound(key, t, fn(term(t[2])))
            if head == "fld":
                fn = z3.Function(f"fld_{t[1]}", z3.BitVecSort(BITS), z3.BitVecSort(BITS))
                return bound(key, t, fn(term(t[2])))
            return bound(key, t, z3.BitVec(key, BITS))

        def cond(c):
            if isinstance(c, bool):
                return z3.BoolVal(c)
            head = c[0]
            if head == "lt":
                return term(c[1]) < 0
            if head == "eq":
                return term(c[1]) == 0
            if head == "not":
                return z3.Not(cond(c[1]))
            return (z3.And if head == "land" else z3.Or)([cond(x) for x in c[1:]])

        return solver, term, cond

    def feasible(self, conds) -> bool:
        """False when ``conds`` are contradictory."""
        solver, _, cond = self.solver()
        solver.add(*(cond(c) for c in conds))
        return solver.check() != z3.unsat

    def relation(self, a, b) -> str | None:
        """``"same"`` or ``"complement"`` when conditions ``a`` and ``b`` always agree or always differ."""
        for kind, test in (("same", lambda x, y: x != y), ("complement", lambda x, y: x == y)):
            solver, _, cond = self.solver()
            solver.add(test(cond(a), cond(b)))
            if solver.check() == z3.unsat:
                return kind
        return None

    def branch(self, conds, c, then, other, rest, state):
        """Paths through ``if c`` with the remaining actions run on each side."""
        out = []
        for guard, body in ((c, then), (self.negate(c), other)):
            if guard is False:
                continue
            cs = conds if guard is True else (*conds, guard)
            if guard is True or self.feasible(cs):
                out += self.paths(list(body) + rest, cs, state)
        return out

    def paths(self, actions, conds=(), state=None) -> list:
        """``[(conds, state)]``: the guarded cases of ``actions`` over start values."""
        state = dict(state or {})
        actions = list(actions or [])
        for i, a in enumerate(actions):
            rest = actions[i + 1 :]
            if "return" in a:
                return [(conds, state)]
            if "if" in a:
                c = self.truth(self.expr(a["if"], state))
                return self.branch(conds, c, a.get("then") or [], a.get("else") or [], rest, state)
            if "set" in a:
                for k, v in a["set"].items():
                    state[k] = self.wrap(k, self.expr(v, state))
            elif "call" in a:
                for k, v in (a.get("with") or {}).items():
                    state[k] = self.wrap(k, self.expr(v, state))
                return [
                    p
                    for cs, st in self.paths(self.procs[a["call"]], conds, state)
                    for p in self.paths(rest, cs, st)
                ]
            elif "accumulate" in a:
                return self.accumulate(a, rest, conds, state)
            else:
                raise ValueError(f"unsupported action {a!r}")
        return [(conds, state)]

    def accumulate(self, a: dict, rest, conds, state) -> list:
        """The VM's bounded accumulator as guarded cases; ``on_bound`` runs on a hit."""
        cell = a["accumulate"]
        v0, step = state.get(cell, cell), self.expr(a.get("step", 0), state)
        lo, hi = (self.expr(a[k], state) if k in a else None for k in ("min", "max"))
        clamp = bool(a.get("clamp", True))
        cases = []
        if "toward" in a:
            t = self.expr(a["toward"], state)
            cases.append(((self.eq(v0, t),), v0, False))
            same = self.negate(self.eq(v0, t))
            for side, value, blo, bhi in (
                (self.lt(v0, t), self.wrap(cell, self.add(v0, step)), lo, t),
                (self.negate(self.lt(v0, t)), self.wrap(cell, self.add(v0, self.neg(step))), t, hi),
            ):
                cases += [
                    ((same, side, *g), v, h) for g, v, h in self.bounds(value, blo, bhi, clamp)
                ]
        else:
            cases = self.bounds(self.wrap(cell, self.add(v0, step)), lo, hi, clamp)
        out = []
        for guard, value, hit in cases:
            cs = tuple(g for g in (*conds, *guard) if g is not True)
            if False in cs or not self.feasible(cs):
                continue
            st = {**state, cell: value}
            body = list(a.get("on_bound") or []) if hit else []
            out += self.paths(body + rest, cs, st)
        return out

    def bounds(self, value, lo, hi, clamp) -> list:
        """``[(guard, value, hit)]`` of testing ``value`` against the bounds, as ``_acc``."""
        hhit = False if hi is None else self.gt(value, hi)
        lhit = False if lo is None else self.lt(value, lo)
        nh, nl = self.negate(hhit), self.negate(lhit)
        return [
            ((hhit,), hi if clamp else value, True),
            ((nh, lhit), lo if clamp else value, True),
            ((nh, nl), value, False),
        ]

    # byte pairs -------------------------------------------------------------
    def implies(self, conds, c) -> bool:
        """True when ``conds`` imply ``c``."""
        c = self.truth(c)
        return c is True or (c is not False and not self.feasible((*conds, self.negate(c))))

    def unwrap(self, conds, t, modulus: int) -> tuple:
        """``(t mod modulus, fired)`` with carry identities applied; ``fired`` if any applied.

        ``k*(E & m)`` is ``k*E`` when ``k*(m+1)`` vanishes, ``k*(E >> s)`` is
        ``(k >> s)*(E - (E & (2**s - 1)))``, and a wrap the guard pins to one period is removed.
        """
        fired = False
        for _ in range(8):
            const, coeffs = lin(self.modular(t, modulus))
            out, changed = {}, False
            for a, k in coeffs.items():
                rep, housekeeping = None, False
                if (
                    isinstance(a, tuple)
                    and a[0] == "and"
                    and isinstance(a[2], int)
                    and a[2] & (a[2] + 1) == 0
                ):
                    span = a[2] + 1
                    if (k * span) % modulus == 0:
                        rep, housekeeping = a[1], True
                    else:
                        for shift in (0, -span, span):
                            pin = self.junction(
                                "land", [self.ge(a[1], -shift), self.lt(a[1], span - shift)]
                            )
                            if self.implies(conds, pin):
                                rep = self.add(a[1], shift)
                                break
                elif (
                    isinstance(a, tuple)
                    and a[0] == "shr"
                    and isinstance(a[2], int)
                    and k % (1 << a[2]) == 0
                ):
                    rep = self.add(a[1], self.neg(self.band(a[1], (1 << a[2]) - 1)))
                    k >>= a[2]
                if rep is None:
                    out[a] = out.get(a, 0) + k
                    continue
                changed = True
                fired = fired or not housekeeping
                c, inner = lin(rep)
                const += k * c
                for x, v in inner.items():
                    out[x] = out.get(x, 0) + k * v
            t = mk(const, out)
            if not changed:
                break
        return self.modular(t, modulus), fired

    def fuse(self, conds, upd: dict) -> dict:
        """Write each byte pair whose joint update is ``P + D`` (``D`` free of its bytes) as one."""
        for lo, hi in sorted(self.pairs):
            if lo not in upd and hi not in upd:
                continue
            name = f"{hi}:{lo}"
            joint = self.add(self.scale(256, upd.get(hi, hi)), upd.get(lo, lo), self.neg(name))
            d, _ = self.unwrap(conds, joint, 1 << 16)
            if not {lo, hi} & atoms(d):
                upd = {k: v for k, v in upd.items() if k not in (lo, hi)}
                upd[name] = self.band(self.add(name, d), 0xFFFF)
        return upd

    def complement(self, a, b):
        """One case for ``(S & c, u1)`` and ``(S & not c, u2)`` when ``ite(c, u1, u2)`` folds."""
        (ga, ua), (gb, ub) = a, b
        if ua.keys() != ub.keys() or len(ga) != len(gb):
            return None
        da, db = [x for x in ga if x not in gb], [x for x in gb if x not in ga]
        if len(da) != 1 or len(db) != 1 or self.negate(da[0]) != db[0]:
            return None
        upd = {k: self.ite(da[0], ua[k], ub[k]) for k in ua}
        return tuple(x for x in ga if x != da[0]), upd

    def merged(self, cases: list) -> list:
        """Cases joined: equal updates by guard union, complementary guards by a folded ``ite``."""
        changed = True
        while changed:
            changed = False
            for a, b in itertools.combinations(range(len(cases)), 2):
                joined_case = self.complement(cases[a], cases[b])
                if joined_case is not None:
                    cases = [c for i, c in enumerate(cases) if i not in (a, b)] + [joined_case]
                    changed = True
                    break
        groups = {}
        for conds, upd in cases:
            key = tuple(sorted((k, show(v)) for k, v in upd.items()))
            groups.setdefault(key, (upd, []))[1].append(conds)
        out = []
        for upd, guards in groups.values():
            common = tuple(c for c in guards[0] if all(c in g for g in guards[1:]))
            rest = [self.junction("land", [c for c in g if c not in common]) for g in guards]
            union = self.junction("lor", rest)
            out.append((common if self.implies(common, union) else (*common, union), upd))
        return out

    def operator(self, actions) -> list:
        """``[(guard, {cell: term})]`` per feasible case that changes a non-scratch cell."""
        out = []
        for conds, final in self.paths(actions):
            upd = {k: v for k, v in final.items() if v != k and k not in self.scratch}
            if upd:
                out.append((conds, self.fuse(conds, upd)))
        return self.merged(out)


def sexpr(t):
    """Readable s-expression tuple of a term (linear forms as sums)."""
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
