"""The lift's proof: its generic document, played by unitracker-player, reproduces the native
player's register grid, each voice from its first note (``first_mismatch``)."""

from dataclasses import dataclass

import numpy as np

from unitracker.player import oracle
from unitracker.player.vm import carry_cells, first_mismatch


@dataclass(frozen=True)
class Verdict:
    """A document's grid against the native one over ``ticks``: the ticks it played (fewer
    where it ends first), each voice's first note, and the first ``(tick, register)`` that
    differs from the voices' first notes (``onset``) and from tick 0 (``whole``)."""

    ticks: int
    played: int
    starts: tuple
    onset: tuple | None
    whole: tuple | None

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


def compare(text: str, theirs: np.ndarray, progress: bool = False) -> Verdict:
    """The :class:`Verdict` of generic document ``text`` against native grid ``theirs``.

    The native's 1-LSB freq and cutoff carry is not a difference (``carry_cells``).
    """
    theirs = np.asarray(theirs, dtype=np.uint8)
    ours, starts = oracle.play(text, len(theirs), progress)
    head = theirs[: len(ours)]
    carry = carry_cells(ours, head)
    bad = np.argwhere((ours != head) & ~carry)
    whole = (int(bad[0][0]), int(bad[0][1])) if len(bad) else None
    return Verdict(len(theirs), len(ours), tuple(starts), first_mismatch(ours, head, starts), whole)
