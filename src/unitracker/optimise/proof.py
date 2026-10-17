"""The proof: a candidate plays the input's register grid over the joint recurrence bound."""

import functools
import math

import numpy as np

from unitracker.optimise.model import ERRORS
from unitracker.player import reach
from unitracker.player.vm import SongPlayer, first_mismatch

ENCODING = "cl100k_base"


@functools.cache
def _encoding():
    import tiktoken  # pylint: disable=import-outside-toplevel

    return tiktoken.get_encoding(ENCODING)


def tokens(text: str) -> int:
    """The document's size: its token count under :data:`ENCODING`."""
    return len(_encoding().encode(text))


class Trace:
    """A song's register grid to the recurrence of its state, and its voices' starts.

    Row ``t`` repeats with ``period`` from ``mu`` on, so :meth:`row` gives every tick. With a
    ``reference`` the run stops at the first tick that differs from it in the song's own state.
    """

    def __init__(self, ir: dict, reference=None, max_ticks: int = reach.MAX_TICKS):
        player = SongPlayer(ir)
        self.rows = []
        self.bad = None

        def each(p):
            self.rows.append(tuple(p.regs))
            if reference is not None:
                self.bad = reference.differs(len(self.rows) - 1, self.rows[-1], p.starts)
            return self.bad is not None

        found = reach.recurrence(player, max_ticks, each=each)
        self.starts = list(player.starts)
        self.mu, self.period = (0, 0) if found is None else (found[0] - found[1], found[1])

    def row(self, t: int) -> tuple:
        """The registers after tick ``t``."""
        return self.rows[t if t < self.mu else self.mu + (t - self.mu) % self.period]

    def grid(self, n: int) -> np.ndarray:
        """The ``(n, 25)`` register grid."""
        t = np.arange(n)
        t = np.where(t < self.mu, t, self.mu + (t - self.mu) % self.period)
        return np.asarray(self.rows, dtype=np.uint8)[t]

    def differs(self, t: int, regs: tuple, starts: list):
        """``(tick, register)`` where ``regs`` and ``starts`` at tick ``t`` break the match."""
        begun = [s if s is not None and s <= t else None for s in self.starts]
        for voice, (ours, theirs) in enumerate(zip(starts, begun)):
            if ours != theirs:
                return (t, 7 * voice)
        if regs == self.row(t):
            return None
        voices = 7 * len(begun)
        first = min((s for s in begun if s is not None), default=None)
        for reg, (a, b) in enumerate(zip(regs, self.row(t))):
            started = begun[reg // 7] if reg < voices else first
            if a != b and started is not None:
                return (t, reg)
        return None


def mismatch(reference: Trace, ir: dict):
    """The first ``(tick, register)`` where ``ir`` departs from ``reference``, None for none.

    Both grids repeat from their recurrences, so they agree forever when they agree over the
    later recurrence plus the periods' least common multiple (``SongPlayer.mismatch``'s test).
    """
    try:
        cand = Trace(ir, reference)
    except (reach.ReachabilityError, *ERRORS) as exc:
        return ("unproved", str(exc))
    if cand.bad is not None:
        return cand.bad
    if cand.starts != reference.starts:
        return ("starts", cand.starts)
    n = max(reference.mu, cand.mu) + math.lcm(reference.period, cand.period)
    if n > reach.MAX_TICKS:
        return ("unproved", f"bound {n}")
    return first_mismatch(cand.grid(n), reference.grid(n), reference.starts)
