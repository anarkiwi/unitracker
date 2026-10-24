"""Terms evaluated with numpy over a bank of random inputs: a cheap refutation before z3."""

import random

import numpy as np

from unitracker.optimiser.code6502.concrete import edges, sample


class Bank:  # pylint: disable=too-few-public-methods
    """``count`` random environments of a shape (half biased to edge values), as arrays."""

    def __init__(self, shape, count: int = 512, seed: int = 0):
        rng = random.Random(seed)
        envs = [(edges if k % 2 else sample)(shape, rng) for k in range(count)]
        self.env = {n: np.array([e[n] for e in envs], dtype=np.int64) for n in envs[0]}
        self.count = count

    def evaluate(self, term, memo: dict):
        """``term``'s values over the bank: int64 arrays for bytes and words, bool arrays for
        predicates; a table read past an entry reads 0 (refutation only)."""
        # pylint: disable=too-many-return-statements,too-many-branches
        if term in memo:
            return memo[term]
        op, args = term[0], term[1:]

        def go(t):
            return self.evaluate(t, memo)

        n = self.count
        if op in ("BC", "WC"):
            out = np.full(n, args[0], dtype=np.int64)
        elif op in ("BV", "WV"):
            out = self.env[args[0]]
        elif op == "PT":
            out = np.ones(n, dtype=bool)
        elif op == "PF":
            out = np.zeros(n, dtype=bool)
        elif op in ("TabB", "Tab"):
            table, index = self.env[args[0]], go(args[1])
            out = _read(table, index)
            if op == "Tab":
                out = _read(table, index + 256) << 8 | out
        elif op in ("Shl", "Shr"):
            a = go(args[0])
            out = (a << args[1]) & 0xFF if op == "Shl" else a >> args[1]
        else:
            a = [go(x) for x in args]
            out = _OPS[op](*a)
        memo[term] = out
        return out


def _read(table, index):
    """``table[k, index[k]]`` for each sample ``k``; 0 past the table."""
    rows = np.arange(table.shape[0])
    return np.where(index < table.shape[1], table[rows, np.minimum(index, table.shape[1] - 1)], 0)


def _mask(width):
    return (1 << width) - 1


def _carry(width, wide_a, wide_b, c):
    return (wide_a + wide_b + c.astype(np.int64)) > _mask(width)


def _borrow(width, a, b, c):
    del width
    return b + (~c).astype(np.int64) <= a


_OPS = {
    "Lo": lambda w: w & 0xFF,
    "Hi": lambda w: w >> 8,
    "Add": lambda a, b: (a + b) & 0xFF,
    "Sub": lambda a, b: (a - b) & 0xFF,
    "And": lambda a, b: a & b,
    "Or": lambda a, b: a | b,
    "Xor": lambda a, b: a ^ b,
    "Ite": np.where,
    "WIte": np.where,
    "Bit": lambda p: p.astype(np.int64),
    "Cat": lambda h, l: h << 8 | l,
    "Zext": lambda b: b,
    "Sext": lambda b: np.where(b >= 128, b | 0xFF00, b),
    "WAdd": lambda a, b: (a + b) & 0xFFFF,
    "WSub": lambda a, b: (a - b) & 0xFFFF,
    "WAnd": lambda a, b: a & b,
    "WOr": lambda a, b: a | b,
    "WXor": lambda a, b: a ^ b,
    "WShl": lambda a, b: np.where(b < 16, (a << np.minimum(b, 15)) & 0xFFFF, 0),
    "WShr": lambda a, b: np.where(b < 16, a >> np.minimum(b, 15), 0),
    "Not": np.logical_not,
    "PAnd": np.logical_and,
    "POr": np.logical_or,
    "Eq": lambda a, b: a == b,
    "WEq": lambda a, b: a == b,
    "Ult": lambda a, b: a < b,
    "WUlt": lambda a, b: a < b,
    "Ule": lambda a, b: a <= b,
    "WUle": lambda a, b: a <= b,
    "Sgn": lambda a: a >= 128,
    "Odd": lambda a: (a & 1) == 1,
    "AdcC": lambda a, b, c: _carry(8, a, b, c),
    "WAdcC": lambda a, b, c: _carry(16, a, b, c),
    "SbcC": lambda a, b, c: _borrow(8, a, b, c),
    "WSbcC": lambda a, b, c: _borrow(16, a, b, c),
}
