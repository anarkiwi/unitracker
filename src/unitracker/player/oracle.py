"""unitracker-player, the generic player of the grammar's documents, with each voice's first
note: the onsets the project's mismatch criterion counts each voice from."""

import numpy as np
from tqdm import tqdm
from unitracker_player import Player, load

from unitracker.player.canonical import REG_COUNT


class _Onsets(Player):
    """The generic player, recording the tick of each voice's first note but the init row's."""

    def __init__(self, song):
        super().__init__(song)
        self.starts = [None] * song.voices
        self._init_events = {id(e) for e in song.init}

    def _event(self, t, v, event, *rest):  # pylint: disable=arguments-differ
        if self.starts[v] is None and isinstance(event.note, int):
            if id(event) not in self._init_events:
                self.starts[v] = t
        super()._event(t, v, event, *rest)


def play(text: str, ticks: int, progress: bool = False) -> tuple[np.ndarray, list]:
    """``(grid, starts)``: the registers after each of up to ``ticks`` ticks of document
    ``text`` (fewer where it ends first) and the tick of each voice's first note, or None."""
    player = _Onsets(load(text))
    grid = np.zeros((ticks, REG_COUNT), dtype=np.uint8)
    played = 0
    for t in tqdm(range(ticks), desc="generic", unit="tick", disable=not progress, leave=False):
        regs = player.step()
        if regs is None:
            break
        grid[t], played = regs, t + 1
    return grid[:played], player.starts
