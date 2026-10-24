"""A code template optimised: semantics, saturation, diagram, code, proof and measures."""

import random
from dataclasses import dataclass, field

import z3
from deity_informant import lift
from unitracker_grammar.code import Op, printed, read

from unitracker.optimiser.code6502.codegen import Codegen
from unitracker.optimiser.code6502.concrete import OutsideModel, Player, edges, evaluate, sample
from unitracker.optimiser.code6502.diagram import Builder
from unitracker.optimiser.code6502.semantics import SPAN, Assembly, semantics
from unitracker.optimiser.code6502.simplify import baseline, saturate
from unitracker.optimiser.code6502.superopt import Leaves
from unitracker.optimiser.code6502.terms import dag, from_z3

LOOP_TRIPS = 255


@dataclass
class Measure:
    """A routine's instructions, bytes and worst-case cycles (a counted loop taken as
    :data:`LOOP_TRIPS` trips, a taken branch +1, an indexed read off a page start +1)."""

    instructions: int
    size: int
    cycles: int

    @classmethod
    def of(cls, statements, shape) -> "Measure":
        """The measure of checked ``statements`` of ``shape``."""
        code = Assembly.of(statements, shape)
        ops, pc = {}, code.start
        while pc < code.end:
            ops[pc] = lift(code.memory, pc)
            pc += ops[pc]["len"]
        pcs = sorted(ops)
        cost = {}
        for pc in pcs:
            rec = ops[pc]
            extra = (
                1 if rec["pen"] and rec["pen"][0] in ("ax", "ay") and rec["pen"][1] % SPAN else 0
            )
            cost[pc] = rec["cyc"] + extra
        memo, stack = {}, set()

        def longest(pc):
            if pc in memo:
                return memo[pc]
            stack.add(pc)
            ctrl, here = ops[pc]["ctrl"], cost[pc]
            if ctrl[0] == "rts":
                out = here
            elif ctrl[0] == "jmp":
                out = here + longest(ctrl[1])
            else:
                fall = pc + ops[pc]["len"] if ctrl[0] != "br" else ctrl[4]
                out = here + longest(fall)
                if ctrl[0] == "br" and ctrl[3] in stack:
                    body = sum(cost[p] for p in pcs if ctrl[3] <= p <= pc) + 1
                    out += body * (LOOP_TRIPS - 1)
                elif ctrl[0] == "br":
                    out = max(out, here + 1 + longest(ctrl[3]))
            stack.discard(pc)
            memo[pc] = out
            return out

        return cls(len(ops), code.end - code.start, longest(code.start))


@dataclass
class Result:  # pylint: disable=too-many-instance-attributes
    """One routine optimised."""

    name: str
    before: str
    after: str
    shape: object
    measures: tuple
    proved: bool
    differential: tuple
    sizes: dict = field(default_factory=dict)
    loops: list = field(default_factory=list)
    diagram: object = None
    semantics: object = None
    searches: list = field(default_factory=list)


def z3_dag(terms) -> int:
    """The number of distinct compound subterms of z3 ``terms``."""
    seen, todo = set(), list(terms)
    while todo:
        t = todo.pop()
        if t.num_args() and t.get_id() not in seen:
            seen.add(t.get_id())
            todo.extend(t.children())
    return len(seen)


def prove(left: dict, right: dict) -> bool:
    """Whether z3 proves the output terms ``left`` and ``right`` equal for all inputs."""
    solver = z3.Solver()
    solver.add(z3.Or([left[k] != right[k] for k in left]))
    return solver.check() == z3.unsat


def differential(before, after, sem, cases: int, seed: int = 0) -> tuple:
    """``(agreed, tried)``: of ``cases`` random inputs (half biased to edge values) inside the
    model of semantics ``sem`` (no read past a table), those where the player runs ``before``
    and ``after`` the same, and how many there were."""
    rng = random.Random(seed)
    old, new = Player(before, sem.shape), Player(after, sem.shape)
    envs = [(edges if k % 2 else sample)(sem.shape, rng) for k in range(cases)]
    inside = [env for env in envs if _inside(sem, env)]
    return sum(old(env) == new(env) for env in inside), len(inside)


def _inside(sem, env) -> bool:
    try:
        evaluate(sem.outputs, env)
    except OutsideModel:
        return False
    return True


def check(statements, sem, cases: int, seed: int = 0) -> int:
    """The cases where the symbolic terms ``sem`` and the player agree on ``statements``,
    skipping those reading past a table; raises AssertionError on a difference."""
    rng = random.Random(seed)
    player = Player(statements, sem.shape)
    agreed = 0
    for k in range(cases):
        env = (edges if k % 2 else sample)(sem.shape, rng)
        try:
            want = evaluate(sem.outputs, env)
        except OutsideModel:
            continue
        got = player(env)
        if want != got:
            raise AssertionError(f"semantics differ on {env}: {want} != {got}")
        agreed += 1
    return agreed


def optimise(  # pylint: disable=too-many-locals
    name: str, text: str, state: dict, cases: int = 1000, superopt=None
) -> Result:
    """``text``, a template of a cell declaring ``state`` ``{name: size}``, optimised; each
    final leaf superoptimised where ``superopt`` (:class:`~.superopt.Leaves` options) is given."""
    statements = read(text)
    sem = semantics(statements, state)
    memo = {}
    terms = {k: from_z3(t, memo) for k, t in sem.outputs.items()}
    saturated = saturate(terms)
    wide = {
        s.operand.value
        for s in statements
        if isinstance(s, Op) and getattr(s.operand, "byte", "") == ">"
    }
    codegen = Codegen(sem.shape, wide)
    diagram = Builder(saturated, sem.shape, (codegen.cost, codegen.test_cost)).build()
    leaves = Leaves(sem.shape, wide, **superopt) if superopt is not None else None
    after = printed(Codegen(sem.shape, wide, leaves).diagram(diagram))
    new = read(after)
    proved = prove(sem.outputs, semantics(new, state).outputs)
    agreed = differential(statements, new, sem, cases)
    sizes = {
        "lifted": z3_dag(sem.outputs.values()),
        "z3": z3_dag(baseline(sem.outputs).values()),
        "egglog": dag(saturated.values()),
    }
    return Result(
        name,
        text,
        after,
        sem.shape,
        (Measure.of(statements, sem.shape), Measure.of(new, sem.shape)),
        proved,
        agreed,
        sizes,
        sem.loops,
        diagram,
        sem,
        leaves.searches if leaves is not None else [],
    )
