"""Bounce orbits: a cell stepping one way per mode until a hit reloads it and flips the mode.

For every ``m = q & ~k`` the flip ``q ^ k`` keeps, z3 proves over the guarded cases that a mode
steps ``v`` by ``s`` while ``v + s`` stays within a threshold ``B`` and reloads and flips past it;
a phase from ``x`` then lasts ``|B - x| // |s| + 1`` ticks, giving the cycle in closed form.
"""

import dataclasses
import itertools

import z3
from tqdm import tqdm

from unitracker.modfit import playroutine, resolve
from unitracker.modfit.compose import Merge
from unitracker.modfit.factor import flipped
from unitracker.modfit.fit import model_text, src
from unitracker.modfit.form import BITS, atoms, lin
from unitracker.modfit.timer import spans


@dataclasses.dataclass
class Phase:
    """One mode's proved phase over the parameter values ``ms``."""

    # pylint: disable=too-many-instance-attributes

    bit: int
    ms: list
    step: object
    sign: int
    bound: int
    reload: int
    wraps: bool


def iv_values(t) -> set:
    """The values of a linear term with each Iverson bracket at 0 or 1, when all are integers."""
    c, coeffs = lin(t)
    out = {c}
    for a, k in coeffs.items():
        if not (isinstance(a, tuple) and a[0] == "iv"):
            return set()
        out |= {x + k for x in out}
    return out


def phase_value(sign: int, bound: int, x: int, d: int) -> tuple:
    """``(ticks, turning value)`` of a phase from ``x`` with step magnitude ``d``."""
    gap = bound - x if sign > 0 else x - bound
    return gap // d + 1, bound - sign * (gap % d)


def phase_text(sign: int, bound: int, x, d: str) -> tuple:
    """``(ticks, turning value)`` sources of a phase from ``x`` with step magnitude ``d``."""
    if isinstance(x, int):
        gap = str(bound - x if sign > 0 else x - bound)
    else:
        gap = f"({bound} - {x})" if sign > 0 else f"({x} - {bound})" if bound else x
    if sign > 0:
        last = f"{bound} - {gap} % {d}"
    else:
        last = f"{bound} + {gap} % {d}" if bound else f"{gap} % {d}"
    return f"{gap}//{d} + 1", last


class Bounce:
    """The bounce orbit of ``cell`` under one action list of a library playroutine."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, name: str, path: str, cell: str):
        self.name, self.path, self.cell = name, path, cell
        self.form = f = Merge(playroutine(name))
        self.cases = f.paths(resolve(f.pr, path))
        flips = {
            (c, next(x for x in t[1:] if isinstance(x, int)))
            for _, final in self.cases
            for c, t in final.items()
            if c != cell and isinstance(t, tuple) and t[0] == "xor" and flipped(t, c)
        }
        if len(flips) != 1:
            raise ValueError(f"{name}:{path} has {len(flips)} flipped mode cells")
        ((self.mode, self.flip),) = flips
        self.top = (1 << f.width[cell]) - 1
        self.kept = ((1 << f.width[self.mode]) - 1) & ~self.flip
        self.param = "m"
        while self.param in f.width:
            self.param += "_"
        tr = f.translation
        self.var = {c: z3.BitVec(c, BITS) for c in (cell, self.mode)}
        self.m = z3.BitVec(self.param, BITS)
        self.z3cases, read = [], set()
        for conds, final in self.cases:
            parts = [tr.cond(c) for c in conds]
            parts += [tr.term(final.get(c, c)) for c in (cell, self.mode)]
            self.z3cases.append([e for e, _ in parts])
            read.update(a for _, got in parts for a in got)
        self.facts = [x for a in read - {cell, self.mode} for x in tr.bounds(a)]

    # proofs ----------------------------------------------------------------
    def mode_of(self, bit: int):
        """The mode cell for parameter ``m`` with the flip bits set when ``bit``."""
        return self.m | self.flip if bit else self.m

    def moves(self, v, q, ev, eq):
        """z3: every case taken at ``(v, q)`` leaves the cell ``ev`` and the mode ``eq``."""
        sub = [(self.var[self.cell], v), (self.var[self.mode], q)]
        return z3.And(
            [
                z3.Implies(
                    z3.And([z3.substitute(c, *sub) for c in case[:-2]]),
                    z3.And(
                        z3.substitute(case[-2], *sub) == ev, z3.substitute(case[-1], *sub) == eq
                    ),
                )
                for case in self.z3cases
            ]
        )

    def valid(self, ms: list, *claim) -> str | None:
        """None when ``claim[-1]`` follows from ``claim[:-1]`` for all ``m`` in ``ms``, else why."""
        solver = z3.Solver()
        solver.add(*self.facts, z3.Or([self.m == m for m in ms]), *claim[:-1], z3.Not(claim[-1]))
        result = solver.check()
        if result == z3.unsat:
            return None
        return model_text(solver.model()) if result == z3.sat else "undecided"

    def term(self, t):
        """The z3 term of a form term over ``m`` and the inputs."""
        return z3.substitute(
            self.form.translation.term(t)[0], (z3.BitVec(self.param, BITS), self.m)
        )

    def steps(self, bit: int) -> set:
        """The non-hit steps of mode ``bit``, over ``m`` and the inputs."""
        f, cell = self.form, self.cell
        f.width[self.param] = self.kept.bit_length()
        mode = f.add(self.param, self.flip) if bit else self.param
        out = set()
        for _, final in self.cases:
            value = final.get(cell, cell)
            inner = value[1] if isinstance(value, tuple) and value[0] == "and" else value
            if final.get(self.mode, self.mode) == self.mode and lin(inner)[1].get(cell) == 1:
                d = f.modular(f.subst(f.sub(inner, cell), {self.mode: mode}), self.top + 1)
                out.add(f.sub(d, self.top + 1) if f.rng(d)[0] > self.top // 2 else d)
        del f.width[self.param]
        return out - {0}

    def classify(self, bit: int, m: int):
        """``(sign, step)`` of mode ``bit`` at ``m``, ``(0, 0)`` when the cell holds, else None.

        The step is one some value takes, of one sign for every input.
        """
        q, v = self.mode_of(bit), self.var[self.cell]
        if self.valid([m], v >= 0, v <= self.top, self.moves(v, q, v, q)) is None:
            return 0, 0
        for step in sorted(self.steps(bit), key=src):
            s = self.term(step)
            taken = z3.Solver()
            taken.add(*self.facts, self.m == m, v >= 0, v <= self.top)
            taken.add(self.moves(v, q, (v + s) & self.top, q))
            if taken.check() != z3.sat:
                continue
            for sign in (1, -1):
                if self.valid([m], sign * s > 0) is None:
                    return sign, step
        return None

    def phase(self, bit: int, ms: list, sign: int, step) -> Phase | None:
        """The phase of mode ``bit`` over ``ms`` at the first threshold and reload that prove."""
        v, q, s = self.var[self.cell], self.mode_of(bit), self.term(step)
        cell = (v >= 0, v <= self.top)
        for bound in self.thresholds():
            edge, inside = (
                (v + s <= bound, v <= bound) if sign > 0 else (v + s >= bound, v >= bound)
            )
            if self.valid(ms, *cell, edge, self.moves(v, q, v + s, q)):
                continue
            for reload in self.reloads():
                hit = self.moves(v, q, reload, self.mode_of(1 - bit))
                if self.valid(ms, *cell, z3.Not(edge), inside, hit) is None:
                    wraps = self.moves(v, q, (v + s) & self.top, q)
                    wraps = self.valid(ms, *cell, z3.Not(inside), wraps) is None
                    return Phase(bit, ms, step, sign, bound, reload, wraps)
        return None

    def thresholds(self) -> list:
        """The constants hit tests compare the stepped cell with, and the width's edges."""
        out = {0, self.top}
        for conds, _ in self.cases:
            for c in conds:
                c = c[1] if c[0] == "not" else c
                if c[0] != "lt":
                    continue
                const, coeffs = lin(c[1])
                own = [a for a, k in coeffs.items() if k in (1, -1) and a[0] != "iv"]
                if len(own) == 1 and self.cell in atoms(own[0]):
                    k = coeffs.pop(own[0])
                    rest = self.form.scale(-k, self.form.add(const, *(
                        self.form.scale(x, a) for a, x in coeffs.items())))  # fmt: skip
                    out |= iv_values(rest)
        return sorted(out)

    def reloads(self) -> list:
        """The constants a case that flips the mode gives the cell."""
        return sorted(
            {
                final[self.cell]
                for _, final in self.cases
                if isinstance(final.get(self.cell), int)
                and final.get(self.mode, self.mode) != self.mode
            }
        )

    # orbit -------------------------------------------------------------------
    def phases(self) -> tuple:
        """``(phases, [(bit, constant ms)], [(bit, unproved ms)])`` over every ``m``."""
        values = range(self.kept + 1)
        phases, const, bad = [], [], []
        for bit in (1, 0):
            kinds = {}
            for m in tqdm(values, desc=f"{self.name} bounce {self.mode_text(bit)}", leave=False):
                kinds.setdefault(self.classify(bit, m), []).append(m)
            for kind, ms in kinds.items():
                found = self.phase(bit, ms, *kind) if kind and kind[0] else None
                if found is not None:
                    phases.append(found)
                else:
                    (const if kind == (0, 0) else bad).append((bit, ms))
        return phases, const, bad

    def mode_text(self, bit: int) -> str:
        """``q & k = k`` or ``q & k = 0``."""
        return f"{self.mode} & {self.flip} = {self.flip if bit else 0}"

    def magnitude(self, p: Phase) -> str:
        """Source of ``|s|``."""
        return src(p.step if p.sign > 0 else self.form.neg(p.step))

    def lines(self) -> list:
        """Report lines: each phase, constant modes, then each cycle's closed forms."""
        phases, const, bad = self.phases()
        lines = [
            f"== {self.name}:{self.path} {self.cell}, mode {self.mode} ^ {self.flip}, "
            f"{self.param} = {self.mode} & {self.kept}"
        ]
        for p in phases:
            lines += self.phase_lines(p)
        lines += [
            f"   {self.mode_text(bit)}, {self.param} in {spans(ms)}: "
            + ("constant (proved)" if (bit, ms) in const else "no bounce phase")
            for bit, ms in const + bad
        ]
        return lines + self.cycle_lines(phases)

    def phase_lines(self, p: Phase) -> list:
        """A phase's step, threshold and reload, and its ticks from a start ``x``."""
        d, top = self.magnitude(p), self.top
        ticks, last = phase_text(p.sign, p.bound, "x", d)
        dom = f"0..{p.bound}" if p.sign > 0 else f"{p.bound}..{top}"
        out = [
            f"   {self.mode_text(p.bit)}, {self.param} in {spans(p.ms)}: step "
            f"{'+' if p.sign > 0 else '-'}{d} while v {'<=' if p.sign > 0 else '>='} {p.bound}, "
            f"else {self.cell} := {p.reload} and {self.mode} ^= {self.flip} (proved)",
            f"      from x in {dom}: {ticks} ticks, turning at {last}",
        ]
        far = (p.bound + 1, top) if p.sign > 0 else (0, p.bound - 1)
        if p.wraps and far[0] <= far[1]:
            gap = f"({top} - x)//{d} + 1" if p.sign > 0 else f"x//{d} + 1"
            out.append(f"      from x in {far[0]}..{far[1]}: wraps at {top + 1} after {gap} ticks")
        return out

    def cycle_lines(self, phases: list) -> list:
        """The cycle of each pair of opposite phases sharing ``m`` values: bounds and period."""
        out = []
        for up in (p for p in phases if p.sign > 0):
            for down in (p for p in phases if p.sign < 0 and p.bit != up.bit):
                ms = sorted(set(up.ms) & set(down.ms))
                if not ms or not (0 <= down.reload <= up.bound and down.bound <= up.reload):
                    continue
                su, sd = self.magnitude(up), self.magnitude(down)
                nu, lu = phase_text(1, up.bound, down.reload, su)
                nd, ld = phase_text(-1, down.bound, up.reload, sd)
                out += [
                    f"   cycle {self.param} in {spans(ms)}: period {nu} + {nd}",
                    f"      bounds [min({down.reload}, {ld}), max({up.reload}, {lu})]; first "
                    f"step +{su} when {self.mode_text(up.bit)}, else -{sd}",
                ]
                out += self.input_lines(up, down, ms)
        return out

    def input_lines(self, up: Phase, down: Phase, ms: list) -> list:
        """The inputs the steps read, and the cycle stepped over them."""
        inputs = sorted({a for p in (up, down) for a in atoms(p.step)} & set(self.form.width))
        out = [
            f"      {a} in {int(self.form.rng(a)[0])}..{int(self.form.rng(a)[1])} is read per"
            " tick: held constant the forms are exact; varying, a phase lasts between its forms"
            " at the ends"
            for a in inputs
        ]
        return out + ["      " + self.stepped(up, down, ms, inputs)]

    def stepped(self, up: Phase, down: Phase, ms: list, inputs: list) -> str:
        """The cycle stepped through the guarded cases for every ``m`` and input value.

        From the up phase's start, the state must first recur after the period, within the bounds.
        """
        # pylint: disable=too-many-locals
        f = self.form
        actions = resolve(f.pr, self.path)
        ranges = [range(int(f.rng(a)[0]), int(f.rng(a)[1]) + 1) for a in inputs]
        grid = list(itertools.product(*ranges))
        for m in tqdm(ms, desc=f"{self.name} bounce cycles", leave=False):
            for point in grid:
                env = {self.param: m, **dict(zip(inputs, point))}
                su, sd = (f.expr(src(p.step), {}, env) * p.sign for p in (up, down))
                nu, lu = phase_value(1, up.bound, down.reload, su)
                nd, ld = phase_value(-1, down.bound, up.reload, sd)
                lo, hi = min(down.reload, ld), max(up.reload, lu)
                mode = m | self.flip if up.bit else m
                state = {self.cell: down.reload, self.mode: mode, **dict(zip(inputs, point))}
                seen = []
                for _ in range(nu + nd):
                    seen.append((state[self.cell], state[self.mode]))
                    ((_, state),) = f.paths(actions, (), state)
                key = (state[self.cell], state[self.mode])
                cells = [v for v, _ in seen]
                if key != seen[0] or key in seen[1:] or (min(cells), max(cells)) != (lo, hi):
                    return f"stepping {env} differs from the forms"
        where = ", ".join(f"{a} in {r.start}..{r.stop - 1}" for a, r in zip(inputs, ranges))
        return f"stepped for every {self.param} in {spans(ms)}, {where}: period and bounds equal"


def bounce_lines(units) -> list:
    """Report lines of each ``(playroutine, path, cell)`` bounce unit."""
    return [x for u in units for x in Bounce(*u).lines()]
