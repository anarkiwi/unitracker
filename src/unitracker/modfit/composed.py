"""Register reach through a playroutine's composed ``init`` and ``tick``, held to the bit ranges
its inductive invariants prove."""

import ast
import functools

from unitracker.modfit.flow import ALL, Flow, Val, flow_of, run_lists, source, spread
from unitracker.modfit.invariant import inductive
from unitracker.player.vm import PSEUDO, acc_mode


class Composed(Flow):
    """Register reach through the composed ``init`` and ``tick``, by value only.

    Calls are inlined with their ``with`` bindings and every cell is resolved along its path, so
    a parameter or scratch cell carries only what each call site gives it; tests steer nothing.
    With ``plain``, ``&`` of two variables, or ``|``/``^`` over bits both can set, passes neither.
    """

    steer = False

    def __init__(self, pr: dict, plain: bool = False):
        # pylint: disable=super-init-not-called
        self.base(pr)
        self.plain, self.steps, self.ranges = plain, {}, {}
        self.timing = flow_of(pr).timing
        self.ends = {k: self.join(self.seq(pr.get(k), {}, None)) for k in ("init", "tick")}
        self.edges = {}
        for key, env in self.ends.items():
            for cell, v in env.items():
                for n, b in v.rel.items():
                    if key == "tick" or n not in self.width:
                        mine = self.edges.setdefault(n, {}).setdefault(cell, {})
                        for i, m in b.items():
                            mine[i] = mine.get(i, 0) | m
        self.reach = self.registers()

    def binop(self, n: ast.BinOp, a: Val, b: Val) -> Val:
        """``Flow.binop``; with ``plain``, a mask of one variable by another is dropped."""
        if self.plain and a.rel and b.rel and isinstance(n.op, (ast.BitAnd, ast.BitOr, ast.BitXor)):
            if isinstance(n.op, ast.BitAnd) or (
                a.support() & b.support() and self.live(a) & self.live(b)
            ):
                return Val()
        return Flow.binop(self, n, a, b)

    def live(self, v: Val) -> int:
        """The bits ``v`` can set, each source cell held to its proved bit range."""
        out = v.const
        for n, b in v.rel.items():
            keep = self.bit_range(n)
            for i, m in b.items():
                out |= m if keep >> i & 1 else 0
        return out

    def bit_range(self, cell: str) -> int:
        """The mask of bits ``[lo, hi)`` that inductive invariants leave ``cell`` able to set."""
        w = self.width.get(cell)
        if not w:
            return ALL
        if cell not in self.ranges:
            hi = next(
                k for k in range(1, w + 1) if k == w or inductive(self.pr, f"{cell} < {1 << k}")
            )
            lo = next(
                (
                    j
                    for j in range(hi - 1, 0, -1)
                    if inductive(self.pr, f"({cell} & {(1 << j) - 1}) == 0")
                ),
                0,
            )
            self.ranges[cell] = ((1 << hi) - 1) & ~((1 << lo) - 1)
        return self.ranges[cell]

    def row(self, rec, attr: str) -> Val:
        """A template's ``row.attr``: the field, selected by the run's index."""
        return self.select(rec[0], rec[1], self.field(rec[0], attr))

    def select(self, rtype: str, index: Val, out: Val) -> Val:
        """``Flow.select``; a program pointer into a chained type selects control, not a value."""
        rt = self.rtypes.get(rtype)
        return out if rt is not None and rt.chain else Flow.select(self, rtype, index, out)

    def join(self, envs: list) -> dict:
        """The union of the environments; a cell missing from one holds its start value there."""
        envs = [e for e in envs if e is not None]
        if len(envs) == 1:
            return envs[0]
        out = {}
        for c in set().union(*envs):
            vals = [e.get(c) for e in envs]
            if any(v is not vals[0] for v in vals):
                start = source(c, self.width.get(c))
                out[c] = functools.reduce(Val.__or__, [start if v is None else v for v in vals])
            else:
                out[c] = vals[0]
        return out

    def put(self, env: dict, cell: str, v: Val) -> None:
        """``cell := v`` wrapped to its width."""
        w = self.width.get(cell)
        env[cell] = v.map(lambda m: m & ((1 << w) - 1)) if w else v

    def seq(self, actions, env: dict, rec) -> list:
        """The environments at fall-through and at each ``return`` of ``actions``."""
        # pylint: disable=too-many-branches
        ends = []
        for a in actions or []:
            if "return" in a:
                return ends + [env]
            env = dict(env)
            for key in ("set", "with"):
                for cell, text in (a.get(key) or {}).items():
                    self.put(env, cell, self.expr(text, env, rec))
            if "if" in a:
                outs = [self.seq(a.get(k), env, rec) for k in ("then", "else")]
                ends += [e for o in outs for e in o[:-1]]
                falls = [o[-1] for o in outs if o[-1] is not None]
                if not falls:
                    return ends + [None]
                env = self.join(falls)
            elif "call" in a:
                env = self.join(self.seq(self.pr["procs"][a["call"]], env, rec))
            elif "for_voices" in a:
                env = self.join(self.seq(a["for_voices"], env, rec))
            elif "accumulate" in a:
                env = self.accumulate(a, env, rec)
            elif "run" in a:
                env = self.join([env] + self.run(a["run"], env, rec))
            elif "fetch" in a:
                env.update({p: source(p, self.width.get(p)) for p in PSEUDO})
        return ends + [env]

    def run(self, run: dict, env: dict, rec) -> list:
        """The environments after each action list a ``run`` can execute."""
        if "template" in run:
            row = (run["record"], self.expr(run["index"], env, rec))
            body = self.rtypes[run["record"]].templates[run["template"]]
            return [self.join(self.seq(body, env, row))]
        return [self.join(self.seq(body, env, rec)) for body in run_lists(self.pr, run)]

    def accumulate(self, a: dict, env: dict, rec) -> dict:
        """``cell ± step``, the bound on a clamp; ``on_bound`` joined in."""
        cell = a["accumulate"]
        step = self.expr(a.get("step", 0), env, rec)
        self.steps[cell] = self.steps.get(cell, Val()) | step
        v = (env.get(cell, source(cell, self.width.get(cell))) | step).map(spread)
        if acc_mode(a, self.width.get(cell) is not None)[0] == 1:
            for k in ("min", "max", "toward"):
                if k in a:
                    v = v | self.expr(a[k], env, rec)
        out = dict(env)
        self.put(out, cell, v)
        return self.join([out, *self.seq(a.get("on_bound"), out, rec)])
