"""Equivalence groups of routines: those of one shape that z3 proves equal under a renaming of
their state variables, parameters and tables, and whether their optimised texts then agree."""

import itertools
import random

import z3
from unitracker_grammar.code import Imm, Mem, Op, printed, read

from unitracker.optimiser.code6502.concrete import OutsideModel, evaluate, sample

TRIALS = 64


def signature(shape) -> tuple:
    """What a renaming must preserve: the state sizes, parameter and table counts."""
    return (tuple(sorted(s for _, s in shape.state)), len(shape.params), len(shape.tables))


def renamings(source, target):
    """Each ``{name: name}`` from ``source``'s shape's names to ``target``'s, sizes kept."""
    if signature(source) != signature(target):
        return
    state_options = [
        dict(zip((n for n, _ in source.state), perm))
        for perm in itertools.permutations([n for n, _ in target.state])
        if all(
            dict(target.state)[t] == dict(source.state)[s]
            for s, t in zip((n for n, _ in source.state), perm)
        )
    ]
    for state in state_options:
        for params in itertools.permutations(target.params):
            for tables in itertools.permutations(target.tables):
                out = dict(state)
                out.update((f"#{a}", f"#{b}") for a, b in zip(source.params, params))
                out.update(zip(source.tables, tables))
                yield out


def _renamed(sem, mapping: dict, target_inputs: dict) -> dict:
    inputs = sem.shape.inputs()
    pairs = [(inputs[a], target_inputs[b]) for a, b in mapping.items()]
    return {mapping.get(k, k): z3.substitute(t, *pairs) for k, t in sem.outputs.items()}


def equivalent(a, b, seed: int = 0):
    """A renaming of ``b``'s names to ``a``'s under which semantics ``a`` and ``b`` are
    equal (z3), else None; random inputs refute most candidates first."""
    target = a.shape.inputs()
    rng = random.Random(seed)
    envs = [sample(a.shape, rng) for _ in range(TRIALS)]
    for mapping in renamings(b.shape, a.shape):
        right = _renamed(b, mapping, target)
        if any(_differs(a.outputs, right, env) for env in envs):
            continue
        solver = z3.Solver()
        solver.add(z3.Or([a.outputs[k] != right[k] for k in a.outputs]))
        if solver.check() == z3.unsat:
            return mapping
    return None


def _differs(left, right, env) -> bool:
    try:
        return evaluate(left, env) != evaluate(right, env)
    except OutsideModel:
        return False


def rename(text: str, mapping: dict) -> str:
    """``text`` with its state symbols, parameters and tables renamed by ``mapping``."""
    out = []
    for s in read(text):
        if isinstance(s, Op) and isinstance(s.operand, Mem):
            symbol, *half = s.operand.symbol.split(".")
            symbol = ".".join([mapping.get(symbol, symbol)] + half)
            s = s._replace(operand=s.operand._replace(symbol=symbol))
        elif isinstance(s, Op) and isinstance(s.operand, Imm) and isinstance(s.operand.value, str):
            s = s._replace(operand=s.operand._replace(value=mapping[f"#{s.operand.value}"][1:]))
        out.append(s)
    return printed(out)


def groups(results) -> list:
    """``[(members, canonical)]``: each group of equivalent results (two or more), as
    ``(name, renaming)`` pairs, and whether their optimised texts agree once renamed."""
    out, placed = [], set()
    for i, a in enumerate(results):
        if a.name in placed:
            continue
        members = [(a.name, {})]
        texts = {a.after}
        for b in results[i + 1 :]:
            if b.name in placed:
                continue
            mapping = equivalent(a.semantics, b.semantics)
            if mapping is not None:
                members.append((b.name, mapping))
                texts.add(rename(b.after, mapping))
                placed.add(b.name)
        if len(members) > 1:
            out.append((members, len(texts) == 1))
    return out
