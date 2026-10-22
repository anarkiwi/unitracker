"""The lift's proof: its generic document, played by unitracker-player, reproduces the native
player's register grid, each voice from its first note (``first_mismatch``), with the
differing cells themselves on the :class:`Verdict` and as its machine-readable summary, and why
the generic VM's grid diverges (``explain``)."""

from dataclasses import dataclass

import numpy as np
from pysidtracker import whydiff

from unitracker.player import oracle
from unitracker.player.vm import SongPlayer, diff, runs


@dataclass(frozen=True)
class Verdict:
    """A document's grid against the native one over ``ticks``: the ticks it played (fewer
    where it ends first), each voice's first note, the first ``(tick, register)`` that differs
    from the voices' first notes (``onset``) and from tick 0 (``whole``), and the differing
    cells (``cells``, as ``(tick, register, doc, native)``)."""

    ticks: int
    played: int
    starts: tuple
    onset: tuple | None
    whole: tuple | None
    cells: tuple = ()

    @property
    def ok(self) -> bool:
        """Whether the document plays the native grid from its voices' first notes."""
        return self.onset is None and self.played == self.ticks

    def line(self) -> str:
        """One line: ``verified`` or the first mismatch under both criteria."""
        if self.ok:
            return f"verified: {self.ticks} ticks play the native grid"
        if self.onset is None:
            return f"mismatch: the document ends at tick {self.played} of {self.ticks}"
        whole = "none" if self.whole is None else f"tick {self.whole[0]}, ${self.whole[1]:02X}"
        return (
            f"mismatch: tick {self.onset[0]}, register ${self.onset[1]:02X}"
            f" (from tick 0: {whole})"
        )

    def summary(self) -> dict:
        """The machine-readable diff: ``{"ok", "ticks", "played", "ended", "starts",
        "first", "whole", "registers", "cells"}``, the registers their runs and both grids'
        values over each (``runs``), and a cell as ``[tick, register, doc, native]``."""
        return {
            "ok": self.ok,
            "ticks": self.ticks,
            "played": self.played,
            "ended": self.ticks - self.played,
            "starts": list(self.starts),
            "first": list(self.onset) if self.onset is not None else None,
            "whole": list(self.whole) if self.whole is not None else None,
            "registers": runs(list(self.cells)),
            "cells": [list(cell) for cell in self.cells],
        }


def compare_grids(ours: np.ndarray, starts: list, theirs: np.ndarray) -> Verdict:
    """The :class:`Verdict` of a played grid ``ours`` (its voices' first notes in ``starts``)
    over the native grid ``theirs``."""
    theirs = np.asarray(theirs, dtype=np.uint8)
    ours = np.asarray(ours, dtype=np.uint8)
    head = theirs[: len(ours)]
    cells = diff(ours, head, starts)
    whole = diff(ours, head)
    return Verdict(
        len(theirs),
        len(ours),
        tuple(starts),
        (cells[0][0], cells[0][1]) if cells else None,
        (whole[0][0], whole[0][1]) if whole else None,
        tuple(cells),
    )


def compare(text: str, theirs: np.ndarray, progress: bool = False) -> Verdict:
    """The :class:`Verdict` of generic document ``text`` against native grid ``theirs``.

    The differing cells the verdict counts are its ``cells``; the native's 1-LSB freq and
    cutoff carry is not a difference (``carry_cells``).
    """
    theirs = np.asarray(theirs, dtype=np.uint8)
    ours, starts = oracle.play(text, len(theirs), progress)
    return compare_grids(ours, starts, theirs)


def explain(verdict: Verdict, song: dict, grids: tuple, native, name: str = ""):
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
