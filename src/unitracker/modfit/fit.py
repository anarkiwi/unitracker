"""One-tick fits: candidate ``set``/``accumulate`` actions for a cell, each proved with z3.

A candidate is accepted under a dispatch guard only when, for every pair of original and
candidate paths, the solver finds the cell's final values identical.
"""

import z3

from unitracker.modfit.form import Form, atoms, lin

BINARY = {"and": "&", "or": "|", "xor": "^", "shr": ">>", "mul": "*"}


def pair_src(name: str) -> str:
    """Source of a cell, or of a fused pair ``hi:lo`` (``hi:lo:k`` when ``lo`` is masked)."""
    hi, lo, k = (name.split(":") + [None, None])[:3]
    if k:
        return f"(({hi} << {k}) | ({lo} & {(1 << int(k)) - 1}))"
    return f"(({hi} << 8) | {lo})" if lo else name


def src(t) -> str:
    """Python expression source of a form term."""
    # pylint: disable=too-many-return-statements
    if isinstance(t, bool):
        return "1" if t else "0"
    if isinstance(t, int):
        return str(t)
    if isinstance(t, str):
        return pair_src(t)
    head = t[0]
    if head == "lin":
        parts = [str(t[1])] if t[1] else []
        parts += [src(a) if k == 1 else f"{k} * {src(a)}" for a, k in t[2]]
        return "(" + " + ".join(parts) + ")"
    if head == "iv":
        return f"(1 if {src(t[1])} else 0)"
    if head == "ite":
        return f"({src(t[2])} if {src(t[1])} else {src(t[3])})"
    if head in BINARY:
        return f"({src(t[1])} {BINARY[head]} {src(t[2])})"
    if head in ("idx", "rec"):
        return f"{t[1]}[{src(t[2])}]"
    if head == "fld":
        return f"{src(t[2])}.{t[1]}"
    if head in ("lt", "eq"):
        return f"({src(t[1])} {'<' if head == 'lt' else '=='} 0)"
    if head == "not":
        return f"(not {src(t[1])})"
    if head in ("land", "lor"):
        return "(" + (" and " if head == "land" else " or ").join(src(x) for x in t[1:]) + ")"
    raise ValueError(f"no source for {t!r}")


class Fitter:
    """Proved accumulator fits for one playroutine."""

    def __init__(self, pr: dict):
        self.form = Form(pr)

    @staticmethod
    def cases(paths, cell: str) -> list:
        """``[(dispatch guard, dynamic guard, value)]`` of ``cell`` over symbolic ``paths``."""
        out = []
        for conds, final in paths:
            if final.get(cell, cell) != cell:
                dyn = tuple(c for c in conds if cell in atoms(c))
                out.append((tuple(c for c in conds if c not in dyn), dyn, final[cell]))
        return out

    def ingredients(self, cell: str, cases: list) -> tuple:
        """``(steps, bounds, reload values)`` the cases expose for ``cell``."""
        f = self.form
        mask = (1 << f.width[cell]) - 1 if f.width.get(cell) else None
        steps, bounds, values = set(), set(), set()
        for _, dyn, value in cases:
            wrapped = isinstance(value, tuple) and value[0] == "and" and value[2] == mask
            inner = value[1] if wrapped else value
            if lin(inner)[1].get(cell) == 1:
                steps.add(f.sub(inner, cell))
            elif cell not in atoms(value):
                values.add(value)
            bounds.update(b for b in (self.threshold(c, cell) for c in dyn) if b is not None)
        return steps, bounds | values, values

    def threshold(self, cond, cell: str):
        """``B`` when ``cond`` compares ``cell`` (unit coefficient) against ``B``, else None."""
        if cond[0] not in ("lt", "eq"):
            return None
        c, coeffs = lin(cond[1])
        k = coeffs.pop(cell, 0)
        if k not in (1, -1):
            return None
        f = self.form
        return f.scale(-k, f.add(c, *(f.scale(v, a) for a, v in coeffs.items())))

    def candidates(self, cell: str, cases: list) -> list:
        """``set``/``accumulate`` actions built from the cases, shortest first."""
        steps, bounds, values = self.ingredients(cell, cases)
        out = [{"set": {cell: src(v)}} for v in values]
        for s in map(src, steps):
            out.append({"accumulate": cell, "step": s})
            for edge in (src(self.form.add(b, shift)) for b in bounds for shift in (0, 1, -1)):
                out.append({"accumulate": cell, "step": s, "toward": edge})
                for key in ("max", "min"):
                    out.append({"accumulate": cell, "step": s, key: edge})
                    out += [
                        {
                            "accumulate": cell,
                            "step": s,
                            key: edge,
                            "clamp": False,
                            "on_bound": [{"set": {cell: src(v)}}],
                        }
                        for v in values
                    ]
        if self.form.width.get(cell):
            out += [{**a, "signed": True} for a in out if a.keys() & {"min", "max", "toward"}]
        return sorted(out, key=lambda a: len(str(a)))

    def check(self, paths, guard, cell: str, candidate) -> tuple:
        """``(z3 result, model)`` of the first path pair where ``cell`` can differ under ``guard``.

        ``(unsat, None)`` means ``candidate`` agrees with the original ``paths``.
        """
        for cconds, cfinal in self.form.paths([candidate]):
            for conds, final in paths:
                smt = self.form.solver()
                smt.solver.add(
                    *(smt.cond(c) for c in (*guard, *conds, *cconds)),
                    smt.term(final.get(cell, cell)) != smt.term(cfinal.get(cell, cell)),
                )
                result = smt.solver.check()
                if result != z3.unsat:
                    return result, smt.solver.model() if result == z3.sat else None
        return z3.unsat, None

    def paths(self, actions, cell: str) -> list:
        """The symbolic paths of ``actions``, pairs fused when ``cell`` is a pair."""
        paths = self.form.paths(actions)
        return self.form.fused(paths) if cell in self.form.pair else paths

    def agrees(self, actions, guard, cell: str, candidate) -> bool:
        """True when ``candidate`` provably gives ``cell`` the original value under ``guard``."""
        return self.check(self.paths(actions, cell), guard, cell, candidate)[0] == z3.unsat

    def witness(self, actions, guard, cell: str, candidate) -> z3.ModelRef | None:
        """A model under ``guard`` where ``candidate`` and ``actions`` disagree on ``cell``."""
        return self.check(self.paths(actions, cell), guard, cell, candidate)[1]

    def fit(self, actions, cell: str, assume=()) -> list:
        """``[(dispatch guard, proved accumulator or None)]`` per dispatch group of ``cell``."""
        return self.fit_paths(self.paths(actions, cell), cell, assume)

    def fit_paths(self, paths, cell: str, assume=()) -> list:
        """``fit`` over symbolic ``paths``, each proof also assuming the conditions ``assume``."""
        groups = {}
        for case in self.cases(paths, cell):
            groups.setdefault(case[0], []).append(case)
        out = []
        for guard, cases in groups.items():
            cands = (
                c
                for c in self.candidates(cell, cases)
                if self.check(paths, (*guard, *assume), cell, c)[0] == z3.unsat
            )
            out.append((guard, next(cands, None)))
        return out

    def rejection(self, actions, guard, cell: str, assume=()) -> tuple:
        """``(shortest candidate, witness model)`` under ``guard``, or ``(None, None)``."""
        paths = self.paths(actions, cell)
        cands = self.candidates(cell, [c for c in self.cases(paths, cell) if c[0] == guard])
        if not cands:
            return None, None
        return cands[0], self.check(paths, (*guard, *assume), cell, cands[0])[1]


def model_text(model: z3.ModelRef) -> str:
    """``name=value`` of a model's constants, signed."""
    return ", ".join(
        f"{d}={model[d].as_signed_long()}"
        for d in sorted(model.decls(), key=str)
        if d.arity() == 0 and z3.is_bv_sort(d.range())
    )
