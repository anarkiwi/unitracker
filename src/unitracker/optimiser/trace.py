"""Each code modulator call's firings over a document's play (the lossy mode's evidence)."""

from collections import defaultdict

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
MASKS = {"freq": compiler.FREQ_MASK, "pw": compiler.PW_MASK, "cut": compiler.CUT_MASK}


class _Tracer(Player):
    """A player that records each firing of a code call's routine, by ``(call, cell)``."""

    def __init__(self, song, routines: dict):
        self.routines, self.firings = routines, defaultdict(list)
        super().__init__(song)

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def _chain(self, t: int, mods: list, value: int, mask: int, cell: str) -> int | None:
        if not any(mods):
            return None
        for mod in mods:
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
                    if key is not None:
                        self.firings[key].append((t, mod[2], mod[1], value, out, *notes))
                    value = out
                else:
                    value = fn(value, mod[1], *notes) & mask
                mod[1] += 1
        return value


def trace(text: str, ticks: int | None) -> tuple:
    """``(frames, firings)``: the document's register grid over ``ticks`` (to its end where
    None) and ``{(call, cell): FIRING array}`` of every code template call that fires."""
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
    firings = {k: np.array(v, dtype=FIRING) for k, v in player.firings.items()}
    return np.array(frames, dtype=np.uint8).reshape(-1, compiler.REGISTERS), firings
