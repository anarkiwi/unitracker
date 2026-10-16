"""Arithmetic form of playroutine operators: branches become Iverson-bracket coefficients.

Each written cell becomes ``x + sum_i [G_i] * (u_i - x)`` over its guarded cases, a
multilinear polynomial in the brackets that is independent of the order of the tests.
"""

import itertools

import sympy

from unitracker.modfit.form import Form, sexpr, show


def apply(name: str, *args):
    """The undefined sympy function ``name`` applied to ``args``."""
    return sympy.Function(name)(*args)  # pylint: disable=not-callable


class Arith:
    """Polynomial form of one playroutine's operators."""

    def __init__(self, pr: dict):
        self.form = Form(pr)
        self.conds = {}
        self.reps = []

    def symbol(self, c) -> sympy.Symbol:
        """The bracket symbol named after condition ``c``."""
        name = "[" + show(sexpr(c)) + "]"
        self.conds[name] = c
        return sympy.Symbol(name)

    def bracket(self, c):
        """``[c]`` with one atom per class of equivalent or complementary conditions."""
        # pylint: disable=too-many-return-statements
        if isinstance(c, bool):
            return sympy.Integer(int(c))
        head = c[0]
        if head == "land":
            return sympy.Mul(*(self.bracket(x) for x in c[1:]))
        if head == "lor":
            return 1 - sympy.Mul(*(1 - self.bracket(x) for x in c[1:]))
        if head == "not":
            return 1 - self.bracket(c[1])
        if head == "lt":
            other = self.form.negate(c)
            if show(other) < show(c):
                return 1 - self.bracket(other)
        for rep, sym in self.reps:
            kind = self.form.relation(c, rep)
            if kind == "same":
                return sym
            if kind == "complement":
                return 1 - sym
        sym = self.symbol(c)
        self.reps.append((c, sym))
        return sym

    def term(self, t):
        """Sympy expression of a form term."""
        # pylint: disable=too-many-return-statements
        if isinstance(t, (bool, int)):
            return sympy.Integer(int(t))
        if isinstance(t, str):
            return sympy.Symbol(t)
        head = t[0]
        if head == "lin":
            return t[1] + sympy.Add(*(k * self.term(a) for a, k in t[2]))
        if head == "iv":
            return self.bracket(t[1])
        if head == "ite":
            a, b = self.term(t[2]), self.term(t[3])
            return b + self.bracket(t[1]) * (a - b)
        if head == "and" and isinstance(t[2], int) and t[2] & (t[2] + 1) == 0:
            return apply(f"wrap{t[2].bit_length()}", self.term(t[1]))
        if head == "shr" and isinstance(t[2], int):
            return apply(f"shr{t[2]}", self.term(t[1]))
        if head in ("idx", "rec"):
            return apply(t[1], self.term(t[2]))
        if head == "fld":
            return apply(f".{t[1]}", self.term(t[2]))
        return apply(head, *(self.term(x) for x in t[1:]))

    def is_bracket(self, s) -> bool:
        """True for a bracket symbol."""
        return s.is_Symbol and s.name in self.conds

    def reduce(self, expr):
        """Idempotent brackets, infeasible bracket products dropped, implied brackets absorbed."""
        expr = sympy.expand(expr)
        brackets = {s for s in expr.free_symbols if self.is_bracket(s)}
        expr = sympy.expand(
            expr.replace(lambda e: e.is_Pow and e.base in brackets, lambda e: e.base)
        )
        out = []
        for mono in sympy.Add.make_args(expr):
            factors = sympy.Mul.make_args(mono)
            bs = [b for b in factors if b in brackets]
            conds = [self.conds[b.name] for b in bs]
            if bs and not self.form.feasible(conds):
                continue
            keep = [
                b
                for b, c in zip(bs, conds)
                if not any(o != c and self.form.implies((o,), c) for o in conds)
            ]
            out.append(sympy.Mul(*(f for f in factors if f not in brackets), *keep))
        return sympy.Add(*out)

    def operator(self, actions) -> dict:
        """``{cell: polynomial}`` for the cells the operator writes."""
        cases = self.form.updates(actions)
        out = {}
        for cell in sorted(set(itertools.chain.from_iterable(u for _, u in cases))):
            x = sympy.Symbol(cell)
            total = x + sympy.Add(
                *(
                    sympy.Mul(*(self.bracket(c) for c in conds)) * (self.term(upd[cell]) - x)
                    for conds, upd in cases
                    if cell in upd
                )
            )
            width = 16 if ":" in cell else self.form.width.get(cell)
            out[cell] = self.polarity(self.modulo(self.reduce(total), width), width)
        return out

    def polarity(self, expr, width):
        """Flip each bracket to ``[not c]`` where that gives fewer monomials, in name order."""
        for b in sorted((s for s in expr.free_symbols if self.is_bracket(s)), key=str):
            flipped = self.symbol(self.form.negate(self.conds[b.name]))
            flipped = self.modulo(self.reduce(expr.subs(b, 1 - flipped)), width)
            if len(sympy.Add.make_args(flipped)) < len(sympy.Add.make_args(expr)):
                expr = flipped
        return expr

    @staticmethod
    def modulo(expr, width):
        """``expr`` modulo ``2**width``: same-width wraps dropped, coefficients reduced."""
        if not width:
            return expr
        wrap = sympy.Function(f"wrap{width}")
        expr = sympy.expand(expr.replace(lambda e: e.func == wrap, lambda e: e.args[0]))
        m = 1 << width
        reduced = (
            (mono, (c + m // 2) % m - m // 2) for mono, c in expr.as_coefficients_dict().items()
        )
        return sympy.Add(*(c * mono for mono, c in reduced if c))

    def pretty(self, expr) -> str:
        """One line per bracket monomial with its factored coefficient."""
        groups = {}
        for mono in sympy.Add.make_args(sympy.expand(expr)):
            fs = sympy.Mul.make_args(mono)
            key = tuple(sorted((f for f in fs if self.is_bracket(f)), key=str))
            groups[key] = groups.get(key, 0) + sympy.Mul(*(f for f in fs if f not in key))
        lines = [
            ("".join(map(str, key)) or "1") + " * (" + str(sympy.factor_terms(groups[key])) + ")"
            for key in sorted(groups, key=lambda k: (len(k), str(k)))
            if groups[key] != 0
        ]
        return "\n      + ".join(lines)
