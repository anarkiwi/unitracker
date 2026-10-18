"""The modulator expression grammar: canonical sympy terms, their text, z3 and Python forms.

Integers, ``+ - * // % & | ^ << >>``, ``abs``, ``min``, ``max``, comparisons ``(a < b)`` as 0 or 1,
``v``, ``φ``, ``N``, ``F(k)`` (frequency of format note k), player tables ``name[k]`` and other
modulators by name. Shifts and all-ones masks fold into ``*``, ``//`` and ``%``.
"""

import functools

import sympy
import z3

V = sympy.Symbol("v", integer=True)
PHI = sympy.Symbol("φ", integer=True, nonnegative=True)
N = sympy.Symbol("N", integer=True, nonnegative=True)
FREQ = sympy.Function("F")
BITS = 48


def freq(k):
    """``F(k)``."""
    return FREQ(k)  # pylint: disable=not-callable


def _normal(rel):
    """``(kind, e, negated)``: ``rel`` as ``[e >= 0]``/``[e == 0]``, or a constant 0/1."""
    e = sympy.expand(rel.lhs - rel.rhs)
    if isinstance(rel, (sympy.Le, sympy.Lt)):
        e = -e
    if isinstance(rel, (sympy.Gt, sympy.Lt)):
        e -= 1
    is_eq = isinstance(rel, sympy.Eq)
    const, rest = e.as_coeff_Add()
    if rest == 0:
        return None, int(const == 0 if is_eq else const >= 0), False
    terms = sympy.Add.make_args(rest)
    coeffs = [t.as_coeff_Mul()[0] for t in terms]
    if not all(c.is_Integer for c in coeffs) or not const.is_Integer:
        return ("eq" if is_eq else "ge"), e, False
    g = abs(functools.reduce(sympy.igcd, [int(c) for c in coeffs]))
    lead = sorted(terms, key=sympy.default_sort_key)[0].as_coeff_Mul()[0]
    if is_eq:
        if const % g:
            return None, 0, False
        return "eq", sympy.expand(e / g * (1 if lead > 0 else -1)), False
    e = sympy.floor(const / g) + sympy.expand(rest / g)
    if lead < 0:
        return "ge", sympy.expand(-e - 1), True
    return "ge", e, False


class Iv(sympy.Function):
    """The Iverson bracket of an integer relation, kept as ``[e >= 0]`` or ``[e == 0]``.

    ``e`` has coprime integer coefficients and, for ``>=``, a positive leading coefficient; a
    bracket with a negative one is ``1 - [-e - 1 >= 0]``.
    """

    is_integer = True
    is_nonnegative = True

    @classmethod
    def eval(cls, rel):  # pylint: disable=arguments-differ
        if rel in (sympy.true, sympy.false):
            return sympy.Integer(int(bool(rel)))
        if isinstance(rel, sympy.Ne):
            return 1 - cls(sympy.Eq(rel.lhs, rel.rhs, evaluate=False))
        if not isinstance(rel, (sympy.Ge, sympy.Gt, sympy.Le, sympy.Lt, sympy.Eq)):
            return None
        kind, e, negated = _normal(rel)
        if kind is None:
            return sympy.Integer(e)
        target = (sympy.Eq if kind == "eq" else sympy.Ge)(e, 0, evaluate=False)
        if not negated and target == rel:
            return None
        return 1 - cls(target) if negated else cls(target)


class IvLt(sympy.Function):
    """``[e < 0]``, the display form of ``1 - [e >= 0]``."""

    is_integer = True
    is_nonnegative = True


class IvNe(sympy.Function):
    """``[e != 0]``, the display form of ``1 - [e == 0]``."""

    is_integer = True
    is_nonnegative = True


class BitAnd(sympy.Function):
    """``a & b`` for a mask that is not all ones."""

    is_integer = True


class BitOr(sympy.Function):
    """``a | b``."""

    is_integer = True


class BitXor(sympy.Function):
    """``a ^ b``."""

    is_integer = True


def table(name: str):
    """The sympy function of a named player table."""
    return sympy.Function(name)


def ge(a, b):
    """``[a >= b]``."""
    return Iv(sympy.Ge(a, b, evaluate=False))


def lt(a, b):
    """``[a < b]``."""
    return Iv(sympy.Lt(a, b, evaluate=False))


def eq(a, b):
    """``[a == b]``."""
    return Iv(sympy.Eq(a, b, evaluate=False))


def band(a, m):
    """``a & m``: ``a % 2**k`` for an all-ones mask."""
    if isinstance(m, int) and m >= 0 and m & (m + 1) == 0:
        return sympy.Mod(a, m + 1)
    return BitAnd(a, m)


def shr(a, k: int):
    """``a >> k``."""
    return sympy.floor(a / sympy.Integer(1 << k))


def _complement(b):
    """``1 - b`` as one bracket."""
    rel = b.args[0]
    return IvLt(rel.lhs) if isinstance(rel, sympy.Ge) else IvNe(rel.lhs)


def _z3_free(e, env: dict, facts: list):
    """``to_z3`` with free symbols and functions as fresh bit-vectors and uninterpreted ones.

    A nonnegative symbol adds its fact to ``facts``; ``φ`` is a :class:`Phase`.
    """
    for sym in e.free_symbols:
        if sym.name not in env:
            x = z3.BitVec(sym.name, BITS)
            env[sym.name] = Phase(lambda *cs: facts.extend(cs), x) if sym == PHI else x
            if sym.is_nonnegative:
                facts.append(x >= 0)
    for f in e.atoms(sympy.core.function.AppliedUndef):
        name = type(f).__name__
        if name not in env:
            sort = [z3.BitVecSort(BITS)] * (len(f.args) + 1)
            env[name] = z3.Function(f"u_{name}", *sort)
    return to_z3(e, env)


@functools.lru_cache(maxsize=None)
def forced(context: tuple, b, domain: tuple = ()) -> int | None:
    """The value bracket ``b`` must take where every ``(bracket, value)`` of ``context`` holds.

    ``domain`` bounds symbols: ``(name, lo, hi)``.
    """
    env, facts = {}, []
    zb = _z3_free(b, env, facts)
    known = [_z3_free(a, env, facts) == value for a, value in context]
    known += [z3.And(env[n] >= lo, env[n] <= hi) for n, lo, hi in domain if n in env]
    for out in (0, 1):
        solver = z3.Solver()
        solver.add(*facts, *known, zb != out)
        if solver.check() == z3.unsat:
            return out
    return None


def denest(e, domain: tuple = ()):
    """``e`` with each bracket over another bracket ``g`` split as ``[g]*b1 + (1 - [g])*b0``."""
    for b in sorted(e.atoms(Iv), key=sympy.default_sort_key):
        inner = sorted(b.args[0].atoms(Iv), key=sympy.default_sort_key)
        if inner:
            g = inner[0]
            split = g * b.subs(g, 1) + (1 - g) * b.subs(g, 0)
            return denest(products(e.subs(b, split), (), domain), domain)
    return e


def products(e, context: tuple = (), domain: tuple = ()):
    """``e`` where ``context`` holds, each term's implied brackets dropped, contradictions 0."""
    return canon(sympy.Add(*(_term(t, context, domain) for t in sympy.Add.make_args(canon(e)))))


def _term(term, context: tuple, domain: tuple):
    """One product with its implied brackets dropped and equalities substituted, or 0."""
    factors = list(sympy.Mul.make_args(term))
    bs = [f for f in factors if isinstance(f, Iv)]
    keep = []
    for i, b in enumerate(bs):
        known = forced(context + tuple((k, 1) for k in keep + bs[i + 1 :]), b, domain)
        if known == 0:
            return sympy.Integer(0)
        if known is None:
            keep.append(b)
    rest = sympy.Mul(*(f for f in factors if not isinstance(f, Iv)))
    for b in keep:
        rest = _equal(rest, b.args[0])
    return rest * sympy.Mul(*keep)


def _equal(e, rel):
    """``e`` with a symbol of unit coefficient replaced by what an equality makes it."""
    if not isinstance(rel, sympy.Eq):
        return e
    for sym in sorted(rel.lhs.free_symbols, key=sympy.default_sort_key):
        k = rel.lhs.coeff(sym)
        if k in (1, -1) and sym not in (rel.lhs - k * sym).free_symbols:
            return e.subs(sym, sympy.expand(-(rel.lhs - k * sym) / k))
    return e


@functools.lru_cache(maxsize=None)
def display(e, context: tuple = (), domain: tuple = ()):
    """The shortest form of ``e`` where ``context`` holds, split on its brackets in sort order.

    ``e = b*X + Y`` is shown as itself, as ``b*X' + Y'``, ``(1 - b)*(-X)' + (X + Y)'`` or
    ``b*(X + Y)' + (1 - b)*Y'``, the primed parts shown the same way; a deterministic function
    of ``e`` and ``domain``.
    """
    e = products(e, context, domain)
    if not context:
        flat = denest(e, domain)
        if flat != e:
            return shortest([display(flat, context, domain), _split(e, context, domain)])
    return _split(e, context, domain)


def _split(e, context: tuple, domain: tuple):
    """``display`` of ``e`` without un-nesting its brackets first.

    The parts are shown recursively: ``Y`` and ``X + Y`` over the context, ``X + Y`` where
    ``b`` holds and ``Y`` where it does not; differences of them are kept canonical.
    """
    brackets = sorted(e.atoms(Iv), key=sympy.default_sort_key)
    if not brackets:
        return shortest([e, sympy.factor_terms(e)])
    b = brackets[0]
    no, yes = context + ((b, 0),), context + ((b, 1),)
    rest, full = products(e.subs(b, 0), context, domain), products(e.subs(b, 1), context, domain)
    whole, y = products(full, yes, domain), products(rest, no, domain)
    forms = [
        e,
        _plus(_times(b, canon(whole - rest)), display(rest, context, domain)),
        _plus(_times(_complement(b), canon(y - full)), display(full, context, domain)),
    ]
    if y != 0:
        forms.append(
            _plus(
                _times(b, display(whole, yes, domain)),
                _times(_complement(b), display(y, no, domain)),
            )
        )
    return shortest(forms)


def _times(a, b):
    """``a*b`` unevaluated, with unit factors dropped."""
    if a == 0 or b == 0:
        return sympy.Integer(0)
    if a == 1 or b == 1:
        return b if a == 1 else a
    return sympy.Mul(a, b, evaluate=False)


def _plus(a, b):
    """``a + b`` unevaluated, with zero terms dropped."""
    if a == 0 or b == 0:
        return b if a == 0 else a
    return sympy.Add(a, b, evaluate=False)


def polar(e, domain: tuple = ()):
    """The display form of ``e`` over ``domain``."""
    return display(canon(e), (), tuple(domain))


def canon(e):
    """``e`` expanded with idempotent brackets."""
    e = sympy.expand(e)
    brackets = e.atoms(Iv)
    return sympy.expand(e.replace(lambda x: x.is_Pow and x.base in brackets, lambda x: x.base))


def on_domain(e, r: int, m: int, lo: int = 0, hi: int | None = None):
    """``e`` for ``v ≡ r (mod m)`` in ``[lo, hi]``, its floors of ``a·v + c`` (``m·a`` an
    integer) written linearly where the result keeps integer coefficients: in a bracket (scaled
    to integers), under a multiple of ``m`` or a remainder; and a remainder of ``a·v + c`` that
    stays in ``[0, divisor)`` dropped."""
    if V not in e.free_symbols:
        return e

    def exact(x):
        y = sympy.expand(x.args[0])
        if not (y.free_symbols <= {V} and y.is_polynomial(V)) or sympy.degree(y, V) != 1:
            return None
        a, c = y.coeff(V, 1), y.coeff(V, 0)
        return a * (V - r) + sympy.floor(a * r + c) if (a * m).is_integer else None

    def linear(x):
        x = x.replace(lambda z: isinstance(z, sympy.floor) and exact(z) is not None, exact)
        return sympy.expand(x)

    def integral(x) -> bool:
        return all(t.as_coeff_Mul()[0].is_Integer for t in sympy.Add.make_args(sympy.expand(x)))

    def scaled(x):
        y = linear(x)
        return y if integral(y) else x

    def bracket(z):
        rel = z.args[0]
        y = linear(rel.lhs - rel.rhs)
        dens = [t.as_coeff_Mul()[0].q for t in sympy.Add.make_args(y)]
        y = sympy.expand(y * functools.reduce(sympy.ilcm, dens, 1))
        return (eq if isinstance(rel, sympy.Eq) else ge)(y, 0) if integral(y) else z

    def product(x):
        c, rest = x.as_coeff_Mul()
        if not (c.is_Integer and c > 0) or not rest.has(sympy.floor, sympy.Mod):
            return x
        if isinstance(rest, sympy.Mod) and rest.args[1].is_Integer:
            return sympy.Mod(scaled(c * rest.args[0]), c * rest.args[1])
        return scaled(x)

    def dropped(x):
        arg, k = x.args
        if hi is None or not k.is_Integer or not (arg.free_symbols <= {V} and arg.is_polynomial(V)):
            return x
        if sympy.degree(arg, V) > 1:
            return x
        ends = [arg.subs(V, lo), arg.subs(V, hi)]
        return arg if 0 <= min(ends) and max(ends) < k else x

    e = e.replace(lambda x: isinstance(x, Iv), bracket)
    e = e.replace(lambda x: x.is_Mul, product)

    def remainder(x):
        return dropped(sympy.Mod(scaled(x.args[0]), x.args[1])) if x.args[1].is_Integer else x

    e = e.replace(lambda x: isinstance(x, sympy.Mod), remainder)
    return sympy.expand(e)


# ---- text ---------------------------------------------------------------------------

ADD, MUL, ATOM = 1, 2, 3


def _floordiv(e):
    """``(a, k)`` when ``e`` is ``floor(a / k)``, else None."""
    if not isinstance(e, sympy.floor):
        return None
    num, den = sympy.fraction(sympy.together(e.args[0]))
    return (sympy.expand(num), den) if den != 1 else None


def _prec(e) -> int:
    if e.is_Add:
        return ADD
    if e.is_Mul or isinstance(e, sympy.Mod) or _floordiv(e) or (e.is_Integer and e < 0):
        return MUL
    return ATOM


def _wrap(e, prec: int) -> str:
    s = text(e)
    return f"({s})" if _prec(e) < prec else s


def _product(e) -> str:
    coeff, rest = e.as_coeff_Mul()
    factors = sorted(sympy.Mul.make_args(rest), key=sympy.default_sort_key)
    parts = [_wrap(f, ATOM if isinstance(f, sympy.Mod) or _floordiv(f) else MUL) for f in factors]
    if coeff == -1:
        return "-" + "*".join(
            parts if len(parts) > 1 or _prec(factors[0]) > MUL else [f"({parts[0]})"]
        )
    if coeff != 1:
        parts.insert(0, text(coeff))
    return "*".join(parts)


def _sum(e) -> str:
    terms = sorted(sympy.Add.make_args(e), key=lambda t: (t.is_Number, sympy.default_sort_key(t)))
    out = []
    for i, t in enumerate(terms):
        neg = t.could_extract_minus_sign()
        body = text(-t if neg else t)
        if i == 0 and neg and (isinstance(-t, sympy.Mod) or _floordiv(-t)):
            body = f"({body})"
        out.append(("-" if neg else "") + body if i == 0 else (" - " if neg else " + ") + body)
    return "".join(out)


def _compare(e, op: str) -> str:
    """``(e op 0)`` as ``(P op Q + k)`` with positive terms left; ``> Q`` for ``>= Q + 1``."""
    const, rest = e.as_coeff_Add()
    terms = sympy.Add.make_args(rest)
    pos = sympy.Add(*(t for t in terms if not t.could_extract_minus_sign()))
    neg = -sympy.Add(*(t for t in terms if t.could_extract_minus_sign()))
    if pos == 0:
        pos, neg, const = neg, pos, -const
        op = {">=": "<=", "<": ">", "==": "==", "!=": "!="}[op]
    if const == -1 and op in (">=", "<=") and neg != 0:
        op, const = op[0], 0
    if const == 1 and op in (">=", "<=") and neg != 0:
        const = 0
        op = {">=": ">", "<=": "<"}[op]
    right = sympy.expand(neg - const)
    return f"({text(pos)} {op} {text(right)})"


def _nearest(e, size: int):
    """``e`` with its constant term the nearest to 0 that is alike modulo ``size``."""
    const, rest = e.as_coeff_Add()
    if not const.is_Integer or rest == 0:
        return e
    near = int(const) % size
    return rest + (near - size if 2 * near >= size else near)


def _ring(e, size: int):
    """``e`` without each ``% m`` (``size`` dividing ``m``) reached through sums and products
    alone, which leave ``e`` modulo ``size`` alike; each sum it opens with its constant nearest
    to 0."""
    if isinstance(e, sympy.Mod) and e.args[1].is_Integer and int(e.args[1]) % size == 0:
        return _nearest(_ring(e.args[0], size), size)
    if e.is_Add or e.is_Mul:
        return e.func(*(_ring(a, size) for a in e.args))
    if e.is_Pow and e.exp.is_Integer and e.exp > 0:
        return sympy.Pow(_ring(e.base, size), e.exp)
    return e


def unwrapped(e, width: int | None):
    """``e`` without the ``% m`` its unit's wrap to ``width`` bits repeats (``2**width``
    divides ``m``) through its sums and products (see :func:`_ring`)."""
    if width is None:
        return e
    out = _ring(e, 1 << width)
    return e if out == e else _nearest(out, 1 << width)


@functools.lru_cache(maxsize=None)
def text(e) -> str:
    """The grammar text of an expression."""
    # pylint: disable=too-many-return-statements,too-many-branches
    e = sympy.sympify(e)
    if e.is_Integer:
        return str(int(e))
    if e.is_Symbol:
        return e.name
    if e.is_Add:
        return _sum(e)
    if e.is_Mul:
        return _product(e)
    if e.is_Pow and e.exp.is_Integer and e.exp > 0:
        return "*".join([_wrap(e.base, ATOM)] * int(e.exp))
    if isinstance(e, sympy.Mod):
        a, m = e.args
        const, rest = a.as_coeff_Add()
        if m.is_Integer and const.is_Integer and rest != 0 and 2 * const > m:
            a = sympy.Add(rest, const - m, evaluate=False)
        return f"{_wrap(a, ATOM)} % {_wrap(m, ATOM)}"
    fd = _floordiv(e)
    if fd:
        return f"{_wrap(fd[0], ATOM)} // {_wrap(fd[1], ATOM)}"
    if isinstance(e, sympy.Abs):
        return f"abs({text(e.args[0])})"
    if isinstance(e, (sympy.Min, sympy.Max)):
        args = sorted(e.args, key=sympy.default_sort_key)
        return f"{'min' if isinstance(e, sympy.Min) else 'max'}({', '.join(map(text, args))})"
    if isinstance(e, Iv):
        rel = e.args[0]
        return _compare(rel.lhs, "==" if isinstance(rel, sympy.Eq) else ">=")
    if isinstance(e, IvLt):
        return _compare(e.args[0], "<")
    if isinstance(e, IvNe):
        return _compare(e.args[0], "!=")
    ops = {BitAnd: " & ", BitOr: " | ", BitXor: " ^ "}
    if type(e) in ops:
        return "(" + ops[type(e)].join(_wrap(a, ATOM) for a in e.args) + ")"
    if isinstance(e, sympy.core.function.AppliedUndef):
        name = type(e).__name__
        if name == "F":
            return f"F({', '.join(map(text, e.args))})"
        return f"{name}[{text(e.args[0])}]"
    raise ValueError(f"no grammar text for {e!r}")


@functools.lru_cache(maxsize=1)
def _encoding():
    import tiktoken  # pylint: disable=import-outside-toplevel

    return tiktoken.get_encoding("cl100k_base")


def tokens(s: str) -> int:
    """Tokens of ``s`` with ``tools/token_report.py``'s tokenizer."""
    return len(_encoding().encode(s))


@functools.lru_cache(maxsize=None)
def rank(e) -> tuple:
    """The order of equivalent forms: fewest tokens, then characters, then text."""
    s = text(e)
    return tokens(s), len(s), s


def valid(e) -> bool:
    """True when ``e`` is written in the grammar (no fractions outside ``//``)."""
    try:
        text(e)
    except ValueError:
        return False
    return True


def shortest(forms):
    """The first form by ``rank`` of those written in the grammar."""
    return min((e for e in forms if valid(e)), key=rank)


# ---- evaluation ---------------------------------------------------------------------


def pysrc(e) -> str:
    """Python source of an expression over ``v``, ``phi``, ``N`` and function callables."""
    # pylint: disable=too-many-return-statements,too-many-branches
    e = sympy.sympify(e)
    if e.is_Integer:
        return f"({int(e)})"
    if e.is_Symbol:
        return {"φ": "phi"}.get(e.name, e.name)
    if e.is_Add or e.is_Mul:
        return "(" + (" + " if e.is_Add else " * ").join(pysrc(a) for a in e.args) + ")"
    if e.is_Pow:
        return f"({pysrc(e.base)} ** {int(e.exp)})"
    if isinstance(e, sympy.Mod):
        return f"({pysrc(e.args[0])} % {pysrc(e.args[1])})"
    fd = _floordiv(e)
    if fd:
        return f"({pysrc(fd[0])} // {pysrc(fd[1])})"
    if isinstance(e, sympy.Abs):
        return f"abs({pysrc(e.args[0])})"
    if isinstance(e, (sympy.Min, sympy.Max)):
        return f"{'min' if isinstance(e, sympy.Min) else 'max'}({', '.join(map(pysrc, e.args))})"
    if isinstance(e, Iv):
        rel = e.args[0]
        return f"(1 if {pysrc(rel.lhs)} {'==' if isinstance(rel, sympy.Eq) else '>='} 0 else 0)"
    if isinstance(e, IvLt):
        return f"(1 if {pysrc(e.args[0])} < 0 else 0)"
    if isinstance(e, IvNe):
        return f"(1 if {pysrc(e.args[0])} != 0 else 0)"
    ops = {BitAnd: "&", BitOr: "|", BitXor: "^"}
    if type(e) in ops:
        return f"({pysrc(e.args[0])} {ops[type(e)]} {pysrc(e.args[1])})"
    if isinstance(e, sympy.core.function.AppliedUndef):
        return f"{type(e).__name__}({', '.join(map(pysrc, e.args))})"
    raise ValueError(f"no Python source for {e!r}")


def compiled(e, names=("v", "phi", "N")):
    """A Python function of ``names`` (then any function callables by keyword) for ``e``."""
    funcs = sorted(
        {type(f).__name__ for f in sympy.sympify(e).atoms(sympy.core.function.AppliedUndef)}
    )
    args = ", ".join([*names, *(f"{f}=None" for f in funcs)])
    return eval(f"lambda {args}: {pysrc(e)}")  # pylint: disable=eval-used


def _fmod(a, k):
    """Floor modulo of bit-vectors for a positive divisor ``k``."""
    r = z3.SRem(a, k)
    return z3.If(r < 0, r + k, r)


class Phase:
    """``φ`` as the bit-vector ``base + shift`` with, per constant divisor ``k``, its floor
    quotient and remainder as atoms tied linearly to it: ``base = k·q + r``, ``0 <= r < k``.

    ``(φ + c) // k`` and ``(φ + c) % k`` are then ``q`` and ``r`` with at most one carry, so a
    proof over phase cycles needs no division.
    """

    def __init__(self, add, base, shift: int = 0, cycles: dict | None = None):
        self.add, self.base, self.shift = add, base, shift
        self.cycles = {} if cycles is None else cycles

    @property
    def term(self):
        """``φ`` as a bit-vector."""
        return self.base + self.shift if self.shift else self.base

    def __add__(self, k: int) -> "Phase":
        return Phase(self.add, self.base, self.shift + k, self.cycles)

    def divmod(self, k: int, c: int) -> tuple:
        """``((φ + c) // k, (φ + c) % k)``."""
        if k not in self.cycles:
            q, r = z3.BitVec(f"φ//{k}", BITS), z3.BitVec(f"φ%{k}", BITS)
            bound = (1 << (BITS - 2)) // k
            self.add(self.base == k * q + r, r >= 0, r < k, q >= -bound, q <= bound)
            self.cycles[k] = q, r
        q, r = self.cycles[k]
        e, d = divmod(self.shift + c, k)
        if d == 0:
            return q + e, r
        carry = r >= k - d
        bit = z3.If(carry, z3.BitVecVal(1, BITS), z3.BitVecVal(0, BITS))
        return q + e + bit, r + d - k * bit


def _cycle(a, k, env: dict):
    """``(a // k, a % k)`` from the phase's cycle atoms when ``a`` is ``φ`` plus a constant and
    ``k`` a positive constant, else None."""
    phase = env.get(PHI.name)
    if not isinstance(phase, Phase) or not (k.is_Integer and k > 0):
        return None
    c = sympy.expand(a - PHI)
    return phase.divmod(int(k), int(c)) if c.is_Integer else None


def to_z3(e, env: dict):
    """The 48-bit z3 bit-vector of ``e``; ``env`` maps symbol and function names.

    ``%`` and ``//`` by a positive constant have floor semantics, as Python's.
    """
    # pylint: disable=too-many-return-statements,too-many-branches
    e = sympy.sympify(e)
    if e.is_Integer:
        return z3.BitVecVal(int(e), BITS)
    if e.is_Symbol:
        x = env[e.name]
        return x.term if isinstance(x, Phase) else x
    if e.is_Add:
        return z3.Sum([to_z3(a, env) for a in e.args])
    if e.is_Mul or e.is_Pow:
        parts = list(e.args) if e.is_Mul else [e.base] * int(e.exp)
        out = to_z3(parts[0], env)
        for a in parts[1:]:
            out = out * to_z3(a, env)
        return out
    if isinstance(e, sympy.Mod):
        cyc = _cycle(*e.args, env)
        return cyc[1] if cyc else _fmod(to_z3(e.args[0], env), to_z3(e.args[1], env))
    fd = _floordiv(e)
    if fd:
        cyc = _cycle(*fd, env)
        if cyc:
            return cyc[0]
        a, k = to_z3(fd[0], env), to_z3(fd[1], env)
        return (a - _fmod(a, k)) / k
    if isinstance(e, sympy.Abs):
        a = to_z3(e.args[0], env)
        return z3.If(a < 0, -a, a)
    if isinstance(e, (sympy.Min, sympy.Max)):
        args = [to_z3(a, env) for a in e.args]
        out = args[0]
        for a in args[1:]:
            out = z3.If((a < out) if isinstance(e, sympy.Min) else (a > out), a, out)
        return out
    if isinstance(e, Iv):
        rel = e.args[0]
        lhs = to_z3(rel.lhs, env)
        cond = lhs == 0 if isinstance(rel, sympy.Eq) else lhs >= 0
        return z3.If(cond, z3.BitVecVal(1, BITS), z3.BitVecVal(0, BITS))
    if isinstance(e, IvLt):
        return z3.If(to_z3(e.args[0], env) < 0, z3.BitVecVal(1, BITS), z3.BitVecVal(0, BITS))
    if isinstance(e, IvNe):
        return z3.If(to_z3(e.args[0], env) != 0, z3.BitVecVal(1, BITS), z3.BitVecVal(0, BITS))
    ops = {BitAnd: lambda a, b: a & b, BitOr: lambda a, b: a | b, BitXor: lambda a, b: a ^ b}
    if type(e) in ops:
        return ops[type(e)](to_z3(e.args[0], env), to_z3(e.args[1], env))
    if isinstance(e, sympy.core.function.AppliedUndef):
        return env[type(e).__name__](*(to_z3(a, env) for a in e.args))
    raise ValueError(f"no z3 form for {e!r}")
