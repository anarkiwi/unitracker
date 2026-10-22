"""The lift's proof: its generic document, played by unitracker-player, reproduces the native
player's register grid, each voice from its first note (``compare``, a gridcmp onset verdict),
and why the generic VM's grid diverges (``explain``)."""

import numpy as np
from pysidtracker import gridcmp, whydiff

from unitracker.player import oracle
from unitracker.player.canonical import REGISTERS
from unitracker.player.vm import SongPlayer

REPORT = {"names": REGISTERS, "subject": "document", "a": "doc", "b": "native"}
"""``gridcmp.runs_report`` keywords naming a verdict's registers and grids."""


def compare(text: str, theirs: np.ndarray, progress: bool = False) -> gridcmp.Verdict:
    """The onset :class:`~pysidtracker.gridcmp.Verdict` of generic document ``text`` played by
    unitracker-player against native grid ``theirs``, in ticks (``gridcmp.verdict_onset``)."""
    theirs = np.asarray(theirs, dtype=np.uint8)
    ours, starts = oracle.play(text, len(theirs), progress)
    return gridcmp.verdict_onset(ours, starts, theirs)


def explain(verdict: gridcmp.Verdict, song: dict, grids: tuple, native, name: str = ""):
    """Why ``song`` diverges at ``verdict``'s onset, a :class:`~pysidtracker.whydiff.Divergence`
    (None without one): the VM's tick beside ``native``'s, a player factory's traced in Python
    or a raw tune's play call on jennings; ``grids`` are the VM's and the native's."""
    if verdict.onset is None:
        return None
    tick, register = verdict.onset
    generic = {
        "label": "generic",
        "cell": lambda p, r: (p.regs, r),
        "sources": SongPlayer(song).sources,
    }
    if not callable(native):
        return whydiff.explain_divergence(
            native,
            lambda _: SongPlayer(song),
            grids[1],
            frame=tick,
            register=register,
            name=name,
            **generic,
        )
    return whydiff.explain_cell(
        whydiff.python_side(lambda: SongPlayer(song), tick, register, grid=grids[0], **generic),
        whydiff.python_side(native, tick, register, label="native", grid=grids[1]),
        grids,
        frame=tick,
        register=register,
        name=name,
    )
