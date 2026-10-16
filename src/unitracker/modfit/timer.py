"""Timers: cells counting a constant per tick between reloads, resets and flips, and their orbits.

Each list writing a timer but ``init``, its calls and runs left to their own lists, gives cases
proved with z3 to be ``t ± c``, free of ``t`` (reload; reset when constant) or ``k - t`` (flip).
Orbits step those cases concretely; gate sequences are classified, their numbers fitted exactly.
"""

import dataclasses
import math

import z3

from tqdm import tqdm

from unitracker.modfit import playroutine, resolve, specs
from unitracker.modfit.compose import can_differ
from unitracker.modfit.factor import flipped
from unitracker.modfit.fit import src
from unitracker.modfit.flow import NESTED, Liveness, Writes, action_lists, cells_of, direct_writes
from unitracker.modfit.form import BITS, Form, atoms, lin, show
from unitracker.modfit.invariant import Slicer, compared
from unitracker.modfit.orbit import deltas, reflect_match, start


@dataclasses.dataclass
class Timer:
    """A timer cell: its count step, reload terms, constant resets, flips and firing tests."""

    # pylint: disable=too-many-instance-attributes

    cell: str
    step: int
    lists: list
    reloads: list
    resets: list
    flips: list
    tests: list
    selectors: list

    def text(self) -> str:
        """One line: the step, what reloads it and the tests that read it."""
        parts = [f"{self.cell}: {self.step:+d} per tick in {', '.join(self.lists)}"]
        if self.reloads:
            parts.append("reload " + ", ".join(map(src, self.reloads)))
        if self.resets:
            parts.append("reset " + ", ".join(map(str, self.resets)))
        if self.flips:
            parts.append("flip " + ", ".join(map(src, self.flips)))
        if self.selectors:
            parts.append("alternating on " + ", ".join(self.selectors))
        return "; ".join(parts)


class Timers:
    """Timer detection and orbits over one library playroutine."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, name: str):
        self.name = name
        self.pr = playroutine(name)
        self.form = Form(self.pr)
        self.writes = Writes(self.pr)
        self.slicer = Slicer(self.pr, self.writes, dict(self.form.procs))
        self.width = cells_of(self.pr)
        self.lists = action_lists(self.pr)
        self.fresh, self.memo = 0, {}

    def paths(self, path: str, cells: frozenset) -> list:
        """The guarded cases of the list at ``path`` on its own, cut to ``cells``."""
        if (path, cells) not in self.memo:
            local = self.local(self.lists[path])
            sliced = self.slicer.seq(local, set(cells), set(cells))[0]
            self.memo[path, cells] = self.form.paths(sliced)
        return self.memo[path, cells]

    def local(self, actions) -> list:
        """``actions`` with each call, run, ``for_voices`` and fetch a fresh value per write.

        A call's ``with`` bindings stay; what a callee does is its own list's cases.
        """
        out = []
        for a in actions or []:
            if a.keys() & {"call", "run", "for_voices", "fetch"}:
                self.fresh += 1
                out += [{"set": a["with"]}] if a.get("with") else []
                out.append({"set": {c: f"{c}__{self.fresh}" for c in sorted(self.writes.of(a))}})
            else:
                out.append({k: self.local(v) if k in NESTED else v for k, v in a.items()})
        return out

    def cases(self, cell: str) -> list:
        """``[(path, conds, final state)]`` over every action list assigning ``cell`` itself."""
        out = []
        for path, actions in self.lists.items():
            if path != "init" and cell in direct_writes(actions):
                out += [(path, cs, st) for cs, st in self.paths(path, frozenset({cell}))]
        return out

    def selectors(self, path: str, cell: str, value) -> list:
        """Cells the reload ``value`` reads that the same path flips: an alternating reload."""
        read = frozenset(a for a in atoms(value) if a in self.width) | {cell}
        out = []
        for _, final in self.paths(path, read):
            if final.get(cell, cell) == value:
                out += [m for m in read - {cell} if flipped(final.get(m, m), m)]
        return out

    def count(self, cell: str, conds, value):
        """``c`` when z3 proves ``value`` is ``cell + c`` (wrapped) under ``conds``, else None."""
        f = self.form
        w = self.width.get(cell)
        inner = value[1] if w and isinstance(value, tuple) and value[0] == "and" else value
        c, coeffs = lin(inner)
        if coeffs != {cell: 1} or not c:
            return None
        if w:
            c = (c + (1 << (w - 1))) % (1 << w) - (1 << (w - 1))
        return c if c and not can_differ(f, conds, value, f.wrap(cell, f.add(cell, c))) else None

    def timer(self, cell: str) -> Timer | None:
        """The timer of ``cell``, or None when some case is not a count, reload, reset or flip."""
        steps, reloads, resets, flips, lists, selectors = set(), [], [], [], set(), []
        for path, conds, final in self.cases(cell):
            value = final.get(cell, cell)
            if value == cell:
                continue
            c = self.count(cell, conds, value)
            if c is not None:
                steps.add(c)
                lists.add(path)
            elif any("__" in a for a in atoms(value) if isinstance(a, str)):
                continue
            elif cell not in atoms(value):
                (resets if isinstance(value, int) else reloads).append(value)
                selectors += self.selectors(path, cell, value)
            elif flipped(value, cell) or lin(value)[1].get(cell) == -1:
                flips.append(value)
            else:
                return None
        if len(steps) != 1:
            return None
        tests = []
        for path in sorted(lists):
            for conds, _ in self.paths(path, frozenset({cell})):
                for c in conds:
                    if cell in atoms(c) and c not in tests and self.form.negate(c) not in tests:
                        tests.append(c)
        return Timer(
            cell,
            steps.pop(),
            sorted(lists),
            list(dict.fromkeys(reloads)),
            sorted(set(resets)),
            list(dict.fromkeys(flips)),
            tests,
            sorted(set(selectors)),
        )

    def counted(self) -> set:
        """Cells some action list reads before it assigns them: the only possible timers."""
        lv = Liveness(self.pr)
        return {
            c
            for path, actions in self.lists.items()
            if path != "init"
            for c in lv.seq(actions, set())[0] & direct_writes(actions)
        } & set(self.width)

    def timers(self) -> dict:
        """``{cell: Timer}`` of every timer cell read by a test."""
        out = {}
        for cell in tqdm(
            sorted(self.counted()), desc=f"{self.name} timers", unit="cell", leave=False
        ):
            t = self.timer(cell)
            if t is not None and t.tests:
                out[cell] = t
        return out

    # orbits ---------------------------------------------------------------
    def domain(self, timer: Timer) -> range:
        """Start values: the cell's width, or the range of its reloads when it has none."""
        w = self.width.get(timer.cell)
        if w:
            return range(1 << w)
        bounds = [self.form.rng(r) for r in timer.reloads] + [(r, r) for r in timer.resets]
        lo, hi = min(b[0] for b in bounds), max(b[1] for b in bounds)
        return range(int(lo), int(hi) + 1)

    def orbit(self, timer: Timer, path: str) -> "Orbit":
        """The concrete successor graph of ``timer`` under the counting list at ``path``."""
        return Orbit(self, timer, path)


def leaves(e) -> list:
    """The uninterpreted constants and applications under a z3 expression."""
    out, todo, seen = [], [e], set()
    while todo:
        x = todo.pop()
        if x.get_id() in seen:
            continue
        seen.add(x.get_id())
        if z3.is_app(x) and x.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            out.append(x)
        else:
            todo += x.children()
    return out


def conjuncts(c) -> list:
    """The conjuncts of a condition, ``not`` of a disjunction included."""
    if isinstance(c, tuple) and c[0] == "land":
        return [y for x in c[1:] for y in conjuncts(x)]
    if isinstance(c, tuple) and c[0] == "not" and c[1][0] == "lor":
        return [("not", x) for x in c[1][1:]]
    return [c]


def bound_of(f: Form, timer: Timer):
    """The non-constant term, constant part dropped, the timer's tests compare it with, or None."""
    bounds = {
        b
        for c in timer.tests
        for x in conjuncts(c)
        if not isinstance(x, bool)
        and (b := compared(f, x[1] if x[0] == "not" else x, timer.cell)) is not None
    }
    bounds = {f.sub(b, lin(b)[0]) for b in bounds if not isinstance(b, int)}
    return min(bounds, key=lambda b: (len(show(b)), show(b))) if bounds else None


class Orbit:
    """A timer stepped through its counting list's own cases, memoised by value.

    A non-constant bound the counting tests compare the timer with is the parameter, and the
    run starts at the timer's reset; otherwise the start value is. Other atoms take a z3 model
    of a counting case, so a run stops at a firing case that reads any of them.
    """

    # pylint: disable=too-many-instance-attributes

    def __init__(self, owner: Timers, timer: Timer, path: str):
        f, t = owner.form, timer
        self.owner, self.timer, self.width = owner, t, owner.width.get(t.cell)
        smt = f.solver()
        cases = owner.paths(path, frozenset({t.cell}))
        self.bound = bound_of(f, t)
        free = {t.cell} | (set(atoms(self.bound)) if self.bound is not None else set())
        self.var, self.param = z3.BitVec("orbit_t", BITS), z3.BitVec("orbit_p", BITS)
        self.named = [(smt.term(t.cell), self.var)]
        if self.bound is not None:
            self.named.append((smt.term(self.bound), self.param))
        smt.sat(*next(cs for cs, st in cases if owner.count(t.cell, cs, st.get(t.cell, t.cell))))
        self.model, self.smt, self.succ, self.memo = smt.solver.model(), smt, {}, {}
        self.cases = []
        for conds, final in cases:
            value = final.get(t.cell, t.cell)
            kind = "count" if owner.count(t.cell, conds, value) else "fire"
            kind = "hold" if value == t.cell else kind
            stop = kind == "fire" and bool((set(atoms(conds)) | set(atoms(value))) - free)
            folded = [self.fold(smt.cond(c)) for c in conds]
            self.cases.append((folded, self.fold(smt.term(value)), kind, stop))

    def fold(self, e):
        """``e`` over the timer and parameter variables, every other atom at its model value."""
        e = z3.substitute(e, *self.named)
        keep = (self.var, self.param)
        fixed = [(x, self.model.eval(x, True)) for x in leaves(e) if x not in keep]
        return z3.simplify(z3.substitute(e, *fixed) if fixed else e)

    def domain(self) -> range:
        """Parameter values: the bound's range, else the timer's start values."""
        if self.bound is None:
            return self.owner.domain(self.timer)
        lo, hi = self.owner.form.rng(self.bound)
        return range(int(lo), int(hi) + 1)

    def start(self, p: int) -> int:
        """The timer's start value for parameter ``p``."""
        return (self.timer.resets or [0])[0] if self.bound is not None else p

    def eval(self, expr, value: int, p: int):
        """A folded ``expr`` with the timer at ``value`` and the bound at ``p``."""
        subs = [(self.var, z3.BitVecVal(value, BITS)), (self.param, z3.BitVecVal(p, BITS))]
        return z3.simplify(z3.substitute(expr, *subs))

    def step(self, value: int, p: int) -> tuple:
        """``(next value or None when the run stops, fired)``: one tick of the counting list."""
        key = (value, p if self.bound is not None else 0)
        if key not in self.succ:
            self.succ[key] = (None, False)
            for conds, term, kind, stop in self.cases:
                if all(z3.is_true(self.eval(c, value, p)) for c in conds):
                    nxt = self.eval(term, value, p).as_signed_long()
                    nxt = nxt & ((1 << self.width) - 1) if self.width else nxt
                    self.succ[key] = (None if stop else nxt, kind == "fire")
                    break
        return self.succ[key]

    def run(self, p: int) -> tuple:
        """``(values, cycle start or None when it stops, fired flags)`` from ``start(p)``."""
        seen, values, fired = {}, [], []
        value = self.start(p)
        while value is not None and value not in seen:
            seen[value] = len(values)
            values.append(value)
            value, fire = self.step(value, p)
            fired.append(fire)
        return values, None if value is None else seen[value], fired

    def duration(self, p: int):
        """Ticks until the timer first fires, or None when it holds."""
        _, _, fired = self.run(p)
        return next((i + 1 for i, f in enumerate(fired) if f), None)

    def truths(self, cond, p: int) -> tuple:
        """``(truth per tick, cycle start or None)`` of ``cond`` along the run."""
        values, loop, _ = self.run(p)
        if cond not in self.memo:
            self.memo[cond] = self.fold(self.smt.cond(cond))
        c = self.memo[cond]
        return [z3.is_true(self.eval(c, v, p)) for v in values], loop


def runs(bits) -> list:
    """``[(value, length)]`` of the runs of a sequence."""
    out = []
    for b in bits:
        if out and out[-1][0] == b:
            out[-1][1] += 1
        else:
            out.append([b, 1])
    return [tuple(r) for r in out]


def signature(bits, loop: int) -> tuple:
    """``(kind, numbers)`` of a gate sequence that repeats from ``loop``.

    ``constant``; ``delay d`` (false ``d`` ticks, then true) or ``duration n`` (true ``n``, then
    false); ``period p`` (one true and one false run per cycle, equal: a sign flip each half);
    ``drift`` (the cycle's run lengths, unequal or more than two).
    """
    if loop is None:
        loop = len(bits) - 1
    prefix, cycle = list(bits[:loop]), list(bits[loop:])
    if len(set(cycle)) == 1:
        if not prefix or set(prefix) == set(cycle):
            return "constant", (int(cycle[0]),)
        before = runs(prefix)
        if len(before) == 1:
            return ("delay" if cycle[0] else "duration"), (len(prefix),)
        return "drift", tuple(n for _, n in before)
    rs = runs(cycle)
    if len(rs) > 1 and rs[0][0] == rs[-1][0]:
        rs = [(rs[0][0], rs[0][1] + rs[-1][1])] + rs[1:-1]
    lengths = tuple(n for _, n in rs)
    if len(rs) == 2 and lengths[0] == lengths[1]:
        return "period", (len(cycle),)
    return "drift", lengths


def affine(xs, ys) -> tuple | None:
    """``(a, b, k)`` with ``y == (a*x + b) // k`` at every point, the least ``k`` first.

    For a fixed ``k`` the pairs of neighbouring points bound ``a``; each integer ``a`` in the
    bound fixes the interval of ``b`` every point allows. A strip of width one around a line
    has a slope of denominator at most the number of points, which bounds ``k``.
    """
    pts = sorted(zip(xs, ys))
    if len(pts) < 2:
        return None
    for k in range(1, len(pts) + 1):
        lo, hi = -math.inf, math.inf
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            dx, dy = x1 - x0, k * (y1 - y0)
            lo, hi = max(lo, math.ceil((dy - k + 1) / dx)), min(hi, math.floor((dy + k - 1) / dx))
        for a in range(int(lo), int(hi) + 1) if lo <= hi else ():
            b_lo = max(k * y - a * x for x, y in pts)
            b_hi = min(k * y - a * x + k - 1 for x, y in pts)
            if b_lo <= b_hi:
                return a, b_lo, k
    return None


def form_text(fit: tuple, var: str = "p") -> str:
    """Source of ``(a*p + b) // k``."""
    a, b, k = fit
    term = {0: "", 1: var, -1: f"-{var}"}.get(a, f"{a}*{var}")
    expr = f"{term} {'+' if b >= 0 else '-'} {abs(b)}" if term and b else term or str(b)
    return expr if k == 1 else f"({expr})//{k}"


def closed(points: dict) -> str:
    """Closed forms over maximal runs of the sorted points, the rest as a table.

    A run is reported as ``(a*p + b)//k`` only when it has more points than the form's three
    coefficients; ``p in lo..hi:`` names its domain when there is more than one piece.
    """
    xs, pieces, i = sorted(points), [], 0
    while i < len(xs):
        j = i + 1
        while j < len(xs) and affine(xs[i : j + 1], [points[x] for x in xs[i : j + 1]]):
            j += 1
        pieces.append(xs[i:j])
        i = j
    out = []
    for piece in pieces:
        fit = affine(piece, [points[x] for x in piece]) if len(piece) > 3 else None
        text = form_text(fit) if fit else ", ".join(f"{x}:{points[x]}" for x in piece)
        out.append(text if len(pieces) == 1 else f"[{spans(piece)}: {text}]")
    return " ".join(out)


def spans(values) -> str:
    """Sorted integers as ``a..b`` runs."""
    out, xs = [], sorted(values)
    for x in xs:
        if out and x == out[-1][1] + 1:
            out[-1][1] = x
        else:
            out.append([x, x])
    return ",".join(f"{a}..{b}" if a != b else str(a) for a, b in out)


def grouped(sigs: dict) -> list:
    """``[(kind, domain, closed forms)]`` of ``{p: (kind, numbers)}``, by kind and arity."""
    groups = {}
    for p, (kind, nums) in sigs.items():
        groups.setdefault((kind, len(nums)), {})[p] = nums
    out = []
    for (kind, arity), pts in sorted(groups.items(), key=lambda g: min(g[1])):
        forms = [closed({p: n[i] for p, n in pts.items()}) for i in range(arity)]
        out.append((kind, spans(pts), forms))
    return out


def timer_lines(name: str) -> list:
    """Report lines: each timer, then each counting list's tests along its orbits."""
    tm = Timers(name)
    lines = [f"== {name} timers"]
    for t in tm.timers().values():
        lines.append("   " + t.text())
        for path in t.lists:
            lines += orbit_lines(tm.orbit(t, path))
    return lines


def orbit_lines(orbit: "Orbit") -> list:
    """Durations to the first fire and each test's signature, by parameter."""
    dom = orbit.domain()
    what = f"bound {src(orbit.bound)} = p" if orbit.bound is not None else "start p"
    durations = {p: orbit.duration(p) for p in tqdm(dom, desc=orbit.timer.cell, leave=False)}
    fired = {p: d for p, d in durations.items() if d is not None}
    lines = (
        [f"      {what} in {spans(fired)}: fires after n = {closed(fired)} ticks"] if fired else []
    )
    held = [p for p, d in durations.items() if d is None]
    lines += [f"      {what} in {spans(held)}: holds"] if held else []
    for cond in orbit.timer.tests:
        sigs = {p: signature(*orbit.truths(cond, p)) for p in dom}
        for kind, where, forms in grouped(sigs):
            lines.append(f"      `{src(cond)}` p in {where}: {kind} {' '.join(forms)}")
    return lines


def repeating(steps) -> list:
    """The shortest repeating block of a step sequence's tail half, from a falling run."""
    tail = list(steps[len(steps) // 2 :])
    q = next(q for q in range(1, len(tail) + 1) if tail[q:] == tail[:-q] or q == len(tail))
    block = tail[:q]
    turn = next((i for i in range(q) if block[i] <= 0 < block[i - 1]), 0)
    return block[turn:] + block[:turn]


def vibrato_signatures(name: str, spec: dict, ticks: int) -> dict:
    """``{period: (kind, numbers)}`` of an orbit spec's per-tick steps.

    ``period`` with the reflect bounds when one reflect accumulator matches, ``constant`` with
    the step for a slide, else ``drift`` with the run lengths of the step signs per cycle.
    """
    pr = playroutine(name)
    actions, cell = resolve(pr, spec["path"]), spec["cell"]
    out = {}
    first, last = spec["periods"]
    for p in tqdm(range(first, last + 1), desc=f"{name} vibrato", unit="period", leave=False):
        state, data = start(spec, p)
        steps = deltas(Form(pr, data), actions, state, cell, ticks)
        match = reflect_match(pr, steps, cell, state)
        if match is not None and match[3] is not None:
            out[p] = ("period", (match[0], match[1]))
        elif len(set(steps.tolist())) == 1:
            out[p] = ("constant", (int(steps[0]),))
        else:
            out[p] = ("drift", tuple(n for _, n in runs(s > 0 for s in repeating(steps.tolist()))))
    return out


def vibrato_lines(ticks: int = 600) -> list:
    """Report lines of every orbit spec: its period groups with closed-form bounds."""
    lines = []
    for name, spec in specs()["orbits"].items():
        lines.append(f"== {name}:{spec['path']} {spec['cell']} by {spec['period']} = p")
        for kind, where, forms in grouped(vibrato_signatures(name, spec, ticks)):
            lines.append(f"   p in {where}: {kind} {' '.join(forms)}")
    return lines
