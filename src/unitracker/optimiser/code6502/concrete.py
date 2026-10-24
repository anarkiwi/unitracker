"""Concrete evaluation of output terms, and of a routine on unitracker-player's machine.

:func:`evaluate` runs z3 terms on Python integers (z3's own substitution is far slower), and
:func:`player` runs the same inputs through :class:`~unitracker_player.code.Routine`.
"""

import random

import z3
from unitracker_player.code import Machine

from unitracker.optimiser.code6502.semantics import CELL, SPAN, SYMBOLS, Shape


class OutsideModel(ValueError):
    """A read past a table's ``T.hi+255``, where the machine reads whatever follows."""


def _mask(width: int) -> int:
    return (1 << width) - 1


def _signed(value: int, width: int) -> int:
    return value - (1 << width) if value >> (width - 1) else value


def _ops():
    k = z3
    return {
        k.Z3_OP_BADD: lambda w, a, *r: sum(r, a) & _mask(w),
        k.Z3_OP_BSUB: lambda w, a, b: (a - b) & _mask(w),
        k.Z3_OP_BMUL: lambda w, a, b: (a * b) & _mask(w),
        k.Z3_OP_BAND: lambda w, a, *r: _fold(a, r, lambda x, y: x & y),
        k.Z3_OP_BOR: lambda w, a, *r: _fold(a, r, lambda x, y: x | y),
        k.Z3_OP_BXOR: lambda w, a, *r: _fold(a, r, lambda x, y: x ^ y),
        k.Z3_OP_BNOT: lambda w, a: ~a & _mask(w),
        k.Z3_OP_BNEG: lambda w, a: -a & _mask(w),
        k.Z3_OP_BSHL: lambda w, a, b: (a << b) & _mask(w) if b < w else 0,
        k.Z3_OP_BLSHR: lambda w, a, b: a >> b,
        k.Z3_OP_BASHR: lambda w, a, b: (_signed(a, w) >> min(b, w)) & _mask(w),
        k.Z3_OP_ULEQ: lambda w, a, b: a <= b,
        k.Z3_OP_ULT: lambda w, a, b: a < b,
        k.Z3_OP_UGEQ: lambda w, a, b: a >= b,
        k.Z3_OP_UGT: lambda w, a, b: a > b,
        k.Z3_OP_EQ: lambda w, a, b: a == b,
        k.Z3_OP_DISTINCT: lambda w, a, b: a != b,
        k.Z3_OP_NOT: lambda w, a: not a,
        k.Z3_OP_AND: lambda w, *r: all(r),
        k.Z3_OP_OR: lambda w, *r: any(r),
        k.Z3_OP_XOR: lambda w, a, b: a != b,
        k.Z3_OP_TRUE: lambda w: True,
        k.Z3_OP_FALSE: lambda w: False,
    }


def _fold(a, rest, fn):
    for b in rest:
        a = fn(a, b)
    return a


_OPS = _ops()
_SIGNED = {z3.Z3_OP_SLEQ: "<=", z3.Z3_OP_SLT: "<", z3.Z3_OP_SGEQ: ">=", z3.Z3_OP_SGT: ">"}


def evaluate(terms: dict, env: dict) -> dict:
    """``{name: int}`` of z3 ``terms`` ``{name: term}`` on ``env``: bitvector inputs by name
    as ints, arrays by name as sequences; OutsideModel at an index past one's end."""
    memo = {}

    def ev(t):  # pylint: disable=too-many-branches
        key = t.get_id()
        if key in memo:
            return memo[key]
        kind = t.decl().kind()
        if z3.is_bv_value(t):
            out = t.as_long()
        elif kind == z3.Z3_OP_UNINTERPRETED:
            out = env[t.decl().name()]
        elif kind == z3.Z3_OP_ITE:
            out = ev(t.arg(1)) if ev(t.arg(0)) else ev(t.arg(2))
        elif kind == z3.Z3_OP_EXTRACT:
            hi, lo = t.params()
            out = (ev(t.arg(0)) >> lo) & _mask(hi - lo + 1)
        elif kind == z3.Z3_OP_CONCAT:
            out = 0
            for a in t.children():
                out = out << a.size() | ev(a)
        elif kind == z3.Z3_OP_ZERO_EXT:
            out = ev(t.arg(0))
        elif kind == z3.Z3_OP_SIGN_EXT:
            out = _signed(ev(t.arg(0)), t.arg(0).size()) & _mask(t.size())
        elif kind == z3.Z3_OP_SELECT:
            table, index = env[t.arg(0).decl().name()], ev(t.arg(1))
            if index >= len(table):
                raise OutsideModel(f"{t.arg(0)} at {index}")
            out = table[index]
        elif kind in _SIGNED:
            w = t.arg(0).size()
            a, b = (_signed(ev(x), w) for x in t.children())
            out = {"<=": a <= b, "<": a < b, ">=": a >= b, ">": a > b}[_SIGNED[kind]]
        else:
            w = t.size() if z3.is_bv(t) else 0
            out = _OPS[kind](w, *(ev(a) for a in t.children()))
        memo[key] = out
        return out

    return {name: ev(term) for name, term in terms.items()}


def sample(shape: Shape, rng: random.Random) -> dict:
    """A random environment of ``shape``'s inputs: parameters 16-bit, each table 512 bytes
    (256 16-bit entries' low bytes, then their high bytes)."""
    env = {n: rng.getrandbits(8 * size) for n, size in SYMBOLS + shape.state}
    env.update((f"#{p}", rng.getrandbits(16)) for p in shape.params)
    for table in shape.tables:
        env[table] = [rng.getrandbits(8) for _ in range(2 * SPAN)]
    return env


def edges(shape: Shape, rng: random.Random) -> dict:
    """A random environment biased to edge values (0, 1, $7f, $80, $ff and neighbours)."""
    env = sample(shape, rng)
    picks = (0, 1, 2, 0x7F, 0x80, 0xFE, 0xFF)
    for name in env:
        if isinstance(env[name], int) and rng.random() < 0.5:
            width = (
                16 if name[0] == "#" or name in ("v", "phi") else 8 * dict(shape.state).get(name, 1)
            )
            value = rng.choice(picks) | (rng.choice(picks) << 8 if width > 8 else 0)
            env[name] = value & _mask(width)
    return env


class Player:  # pylint: disable=too-few-public-methods
    """A template on unitracker-player's machine: called with an environment (its parameters
    and tables bound per call), its outputs."""

    def __init__(self, statements, shape: Shape):
        self.statements, self.shape = statements, shape

    def __call__(self, env: dict) -> dict:
        shape = self.shape
        tables = {
            t: [env[t][k] | env[t][SPAN + k] << 8 for k in range(SPAN)]
            for t in shape.tables
            if t != "F"
        }
        machine = Machine(tables, {CELL: shape.state})
        if "F" in shape.tables:
            machine.symbols["F.lo"] = machine.top
            machine.symbols["F.hi"] = machine.top + SPAN
            at = machine.top
            data = bytes(env["F"])
            machine.memory[at : at + 2 * SPAN] = machine.image[at : at + 2 * SPAN] = data
            machine.top += 2 * SPAN
        bag = {p: _signed(env[f"#{p}"], 16) for p in shape.params}
        routine = machine.compile("r", self.statements, bag, CELL)
        state = bytearray()
        for name, size in shape.state:
            state += env[name].to_bytes(size, "little")
        v = routine(env["v"], env["phi"], env["nn"], env["ne"], state)
        out, at = {"v": v}, 0
        for name, size in shape.state:
            out[name] = int.from_bytes(state[at : at + size], "little")
            at += size
        return out
