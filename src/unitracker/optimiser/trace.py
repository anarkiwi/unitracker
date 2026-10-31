"""Each modulator slot's firings over a document's play (the evidence of the lossy mode and of
the ``prelude`` pass)."""

import numpy as np
from unitracker_player import compiler
from unitracker_player.code import Routine
from unitracker_player.compiler import REGISTERS, compile_document
from unitracker_player.document import read
from unitracker_player.player import Player

FIRING = np.dtype(
    [
        ("t", "i4"),
        ("voice", "i1"),
        ("phi", "i4"),
        ("v", "i4"),
        ("out", "i4"),
        ("N", "i2"),
        ("n", "i2"),
    ]
)
CHAIN = np.dtype(
    [
        ("t", "i4"),
        ("cell", "i1"),
        ("owner", "i1"),
        ("voice", "i1"),
        ("slot", "i1"),
        ("key", "i4"),
        ("phi", "i4"),
        ("v", "i4"),
        ("out", "i4"),
        ("N", "i2"),
        ("n", "i2"),
    ]
)
CELLS = ("freq", "pw", "cut")


class _Log:  # pylint: disable=too-few-public-methods
    """The observer of a play that logs each slot's firing, its call's ``(call, cell)`` by
    index where ``routines`` holds the call's modulator."""

    def __init__(self, routines: dict):
        self.routines, self.rows, self.keys, self.player = routines, [], {}, None

    def __call__(self, kind: str, *args):
        if kind != "fire" or args[0] not in CELLS:
            return
        cell, owner, slot, by, fn, *rest = args
        key = self.routines.get(fn)
        key = -1 if key is None else self.keys.setdefault(key, len(self.keys))
        self.rows.append((self.player.tick, CELLS.index(cell), owner, by, slot, key, *rest))


class Trace:  # pylint: disable=too-few-public-methods
    """A play's register grid ``frames``, every slot firing in order ``chain`` (:data:`CHAIN`:
    ``owner`` the voice of the cell, 0 for the cutoff, ``voice`` the one that bound the slot,
    ``key`` the index in ``keys`` of its call's ``(call, cell)``, -1 for one not recorded)
    and each recorded call's firings ``firings`` by ``(call, cell)`` (:data:`FIRING`)."""

    def __init__(self, frames: np.ndarray, chain: np.ndarray, keys: list):
        self.frames, self.chain, self.keys = frames, chain, keys
        self.firings = {}
        for k, key in enumerate(keys):
            rec = chain[chain["key"] == k]
            out = np.zeros(len(rec), dtype=FIRING)
            for name in FIRING.names:
                out[name] = rec[name]
            self.firings[key] = out


def record(text: str, ticks: int | None, every: bool = False) -> Trace:
    """The :class:`Trace` of the document ``text`` over ``ticks`` (to its end where None), its
    ``keys`` and ``firings`` those of code calls, of every template call where ``every``."""
    routines = {}

    class _Compiler(compiler._Compiler):  # pylint: disable=protected-access
        def call(self, value, cell):
            fn = super().call(value, cell)
            if every or type(fn) is Routine:  # pylint: disable=unidiomatic-typecheck
                routines[fn] = (value, cell)
            return fn

    log = _Log(routines)
    player = log.player = Player(compile_document(read(text), _Compiler), log)
    frames = []
    while ticks is None or len(frames) < ticks:
        regs = player.step()
        if regs is None:
            break
        frames.append(regs)
    frames = np.array(frames, dtype=np.uint8).reshape(-1, REGISTERS)
    chain = np.array(log.rows, dtype=CHAIN)
    return Trace(frames, chain, sorted(log.keys, key=log.keys.get))


def trace(text: str, ticks: int | None) -> tuple:
    """``(frames, firings)``: the document's register grid over ``ticks`` (to its end where
    None) and ``{(call, cell): FIRING array}`` of every code template call that fires."""
    out = record(text, ticks)
    return out.frames, out.firings
