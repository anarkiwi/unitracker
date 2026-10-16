"""Arithmetize playroutine operators: branches become Iverson-bracket coefficients.

Each written cell becomes ``x + sum_i [G_i] * (u_i - x)`` over the operator's guarded cases,
expanded to a multilinear polynomial in the brackets (``[c]**2 = [c]``, ``[not c] = 1 - [c]``,
infeasible bracket products dropped), which is independent of the order of the tests.
"""

import argparse
import itertools
import sys
from pathlib import Path

import sympy
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from abstract import ROOT, resolve  # noqa: E402  pylint: disable=wrong-import-position
from opform import Form, sexpr, show  # noqa: E402  pylint: disable=wrong-import-position


class Arith:
    """Polynomial form of one playroutine's operators."""

    def __init__(self, pr: dict):
        self.form = Form(pr)
        self.conds = {}
        self.reps = []

    def bracket(self, c):
        """``[c]`` with one canonical polarity per condition pair."""
        f = self.form
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
            other = f.negate(c)
            if show(other) < show(c):
                return 1 - self.bracket(other)
        for rep, sym in self.reps:
            kind = f.relation(c, rep)
            if kind == "same":
                return sym
            if kind == "complement":
                return 1 - sym
        name = "[" + show(sexpr(c)) + "]"
        self.conds[name] = c
        sym = sympy.Symbol(name)
        self.reps.append((c, sym))
        return sym

    def term(self, t):
        """Sympy expression of an opform term."""
        # pylint: disable=too-many-return-statements
        if isinstance(t, bool):
            return sympy.Integer(int(t))
        if isinstance(t, int):
            return sympy.Integer(t)
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
            return sympy.Function(f"wrap{t[2].bit_length()}")(self.term(t[1]))
        if head == "shr" and isinstance(t[2], int):
            return sympy.Function(f"shr{t[2]}")(self.term(t[1]))
        if head in ("idx", "rec"):
            return sympy.Function(f"{t[1]}")(self.term(t[2]))
        if head == "fld":
            return sympy.Function(f".{t[1]}")(self.term(t[2]))
        return sympy.Function(head)(*(self.term(x) for x in t[1:]))

    def reduce(self, expr):
        """Idempotent brackets, infeasible bracket products dropped, implied brackets absorbed."""
        expr = sympy.expand(expr)
        brackets = {s for s in expr.free_symbols if s.name in self.conds}
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
                if not any(o is not c and self.form.implies((o,), c) for o in conds if o != c)
            ]
            out.append(sympy.Mul(*(f for f in factors if f not in brackets), *keep))
        return sympy.Add(*out)

    def operator(self, actions) -> dict:
        """``{cell: polynomial}`` for the cells the operator writes."""
        f = self.form
        cases = []
        for conds, final in f.paths(actions):
            upd = {k: v for k, v in final.items() if v != k and k not in f.scratch}
            cases.append((conds, f.fuse(conds, upd) if upd else upd))
        cells = sorted(set(itertools.chain.from_iterable(u for _, u in cases)))
        out = {}
        for cell in cells:
            x = sympy.Symbol(cell)
            total = x
            for conds, upd in cases:
                if cell in upd:
                    guard = sympy.Mul(*(self.bracket(c) for c in conds))
                    total += guard * (self.term(upd[cell]) - x)
            width = 16 if ":" in cell else f.width.get(cell)
            out[cell] = self.polarity(self.modulo(self.reduce(total), width), width)
        return out

    def polarity(self, expr, width):
        """Flip each bracket to ``[not c]`` where that gives fewer monomials (in name order)."""
        for b in sorted((s for s in expr.free_symbols if s.name in self.conds), key=str):
            c = self.form.negate(self.conds[b.name])
            name = "[" + show(sexpr(c)) + "]"
            self.conds[name] = c
            flipped = self.modulo(self.reduce(expr.subs(b, 1 - sympy.Symbol(name))), width)
            if len(sympy.Add.make_args(flipped)) < len(sympy.Add.make_args(expr)):
                expr = flipped
        return expr

    def modulo(self, expr, width):
        """``expr`` over the integers modulo ``2**width``: same-width wraps dropped, coefficients reduced."""
        if not width:
            return expr
        wrap = sympy.Function(f"wrap{width}")
        expr = sympy.expand(expr.replace(lambda e: e.func == wrap, lambda e: e.args[0]))
        m = 1 << width
        sym = lambda v: (v + m // 2) % m - m // 2  # noqa: E731
        return sympy.Add(
            *(sym(c) * mono for mono, c in expr.as_coefficients_dict().items() if sym(c))
        )

    def pretty(self, expr) -> str:
        """One line per bracket monomial with its factored coefficient."""
        groups = {}
        for mono in sympy.Add.make_args(sympy.expand(expr)):
            fs = sympy.Mul.make_args(mono)
            key = tuple(sorted((f for f in fs if f.is_Symbol and f.name in self.conds), key=str))
            rest = sympy.Mul(*(f for f in fs if f not in key))
            groups[key] = groups.get(key, 0) + rest
        lines = []
        for key in sorted(groups, key=lambda k: (len(k), str(k))):
            coeff = sympy.factor_terms(groups[key])
            if coeff != 0:
                lines.append(("".join(str(k) for k in key) or "1") + " * (" + str(coeff) + ")")
        return "\n      + ".join(lines)


def main():
    """Print the polynomial form of each named operator."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "units", nargs="+", help="PLAYROUTINE:dotted.path, e.g. sidwizard:procs.tick_pw"
    )
    args = ap.parse_args()
    for unit in args.units:
        key, path = unit.split(":")
        with open(ROOT / f"{key}.yaml", encoding="utf-8") as fh:
            pr = yaml.safe_load(fh)
        arith = Arith(pr)
        print(f"== {unit}")
        for cell, expr in arith.operator(resolve(pr, path)).items():
            print(f"  {cell}' = {arith.pretty(expr)}")


if __name__ == "__main__":
    main()
