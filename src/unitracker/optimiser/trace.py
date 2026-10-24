"""Each modulator slot's firings over a document's play (the lossy mode's evidence)."""

import numpy as np
from unitracker_player import compiler
from unitracker_player.code import Routine
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
MASKS = {"freq": compiler.FREQ_MASK, "pw": compiler.PW_MASK, "cut": compiler.CUT_MASK}


class _Tracer(Player):
    """A player that logs each slot's firing, its code call's ``(call, cell)`` by index."""

    def __init__(self, song, routines: dict):
        self.routines, self.log, self.keys = routines, [], {}
        super().__init__(song)

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def _chain(self, t: int, mods: list, value: int, mask: int, cell: str) -> int | None:
        if not any(mods):
            return None
        c = CELLS.index(cell)
        for slot, mod in enumerate(mods):
            if mod is None:
                continue
            if self._marked(mod[2], "reset " + mod[3], t):
                mod[1] = 0
            if not self._marked(mod[2], "hold " + mod[3], t):
                fn, notes = mod[0], self._notes(mod[2])
                if type(fn) is Routine:  # pylint: disable=unidiomatic-typecheck
                    state = self.cut_state if cell == "cut" else self.voices[mod[2]].state[cell]
                    out = fn(value, mod[1], *notes, state) & mask
                    key = self.routines.get(fn)
                    key = -1 if key is None else self.keys.setdefault(key, len(self.keys))
                else:
                    out, key = fn(value, mod[1], *notes) & mask, -1
                owner = 0 if cell == "cut" else mod[2]
                self.log.append((t, c, owner, mod[2], slot, key, mod[1], value, out, *notes))
                value = out
                mod[1] += 1
        return value


class Trace:  # pylint: disable=too-few-public-methods
    """A play's register grid ``frames``, every slot firing in order ``chain`` (:data:`CHAIN`:
    ``owner`` the voice of the cell, 0 for the cutoff, ``voice`` the one that bound the slot,
    ``key`` the index in ``keys`` of its code call's ``(call, cell)``, -1 for an expression)
    and each code call's firings ``firings`` by ``(call, cell)`` (:data:`FIRING`)."""

    def __init__(self, frames: np.ndarray, chain: np.ndarray, keys: list):
        self.frames, self.chain, self.keys = frames, chain, keys
        self.firings = {}
        for k, key in enumerate(keys):
            rec = chain[chain["key"] == k]
            out = np.zeros(len(rec), dtype=FIRING)
            for name in FIRING.names:
                out[name] = rec[name]
            self.firings[key] = out


def record(text: str, ticks: int | None) -> Trace:
    """The :class:`Trace` of the document ``text`` over ``ticks`` (to its end where None)."""
    routines = {}

    class _Compiler(compiler._Compiler):  # pylint: disable=protected-access
        def call(self, value, cell):
            fn = super().call(value, cell)
            if type(fn) is Routine:  # pylint: disable=unidiomatic-typecheck
                routines[fn] = (value, cell)
            return fn

    song = compiler.compile_document(read(text), _Compiler)
    player = _Tracer(song, routines)
    frames = []
    while ticks is None or len(frames) < ticks:
        regs = player.step()
        if regs is None:
            break
        frames.append(regs)
    frames = np.array(frames, dtype=np.uint8).reshape(-1, compiler.REGISTERS)
    chain = np.array(player.log, dtype=CHAIN)
    return Trace(frames, chain, sorted(player.keys, key=player.keys.get))


def trace(text: str, ticks: int | None) -> tuple:
    """``(frames, firings)``: the document's register grid over ``ticks`` (to its end where
    None) and ``{(call, cell): FIRING array}`` of every code template call that fires."""
    out = record(text, ticks)
    return out.frames, out.firings
