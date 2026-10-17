"""A song played with its modulated cells driven by derived phase and value expressions.

The playroutine's units start, fire and open occurrences as in the player
(:mod:`unitracker.modulation`); ``derive`` gives each occurrence its derived expressions.
"""

from tqdm import tqdm

from unitracker.modfit import grammar as G
from unitracker.modulation import Modulation, joined
from unitracker.vm import SongPlayer

G_value = joined  # pylint: disable=invalid-name


class Replay(Modulation):
    """One song's player with every unit's occurrences tracked and optionally overridden.

    ``derive(unit, occurrence)`` returns the occurrence's derived expressions (or None);
    ``override(occurrence)`` says whether its values are written into the player.
    """

    def __init__(self, song: dict, derive, override=lambda occ: False):
        super().__init__(SongPlayer(song, watch=True), derive, override)

    def run(self, ticks: int, desc: str = "replay", position: int = 0):
        """The register grid of ``ticks`` ticks."""
        grid = []
        for _ in tqdm(range(ticks), desc=desc, unit="tick", leave=False, position=position):
            self.player.play_frame()
            grid.append(list(self.player.regs))
        return grid


class Stepper:
    """Compiled ``(v, φ) -> (v', φ')`` of derived expressions over a player's tables."""

    def __init__(self, phase, value, tables: dict, freq: dict, offset: int):
        self.funcs = {}
        if "word" in freq:
            word = tables[freq["word"]]
            self.funcs["F"] = lambda k: _at(word, k + offset)
        else:
            hi, lo = tables[freq["hi"]], tables[freq["lo"]]
            self.funcs["F"] = lambda k: (_at(hi, k + offset) << 8) | _at(lo, k + offset)
        for name, data in tables.items():
            self.funcs.setdefault(name, lambda k, data=data: _at(data, k))
        self.f = G.compiled(value)
        self.a = G.compiled(phase)
        self.note = 0

    def step(self, v: int, phi: int) -> tuple:
        """``(f(v, φ), φ + a(v, φ))``."""
        return (
            self.f(v, phi, self.note, **self.kw(self.f)),
            phi + self.a(v, phi, self.note, **self.kw(self.a)),
        )

    def kw(self, fn) -> dict:
        """The table callables ``fn`` takes."""
        names = fn.__code__.co_varnames[3 : fn.__code__.co_argcount]
        return {n: self.funcs[n] for n in names}


def _at(table, i):
    return table[i] if 0 <= i < len(table) else 0
