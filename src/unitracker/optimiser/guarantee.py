"""The optimiser's guarantee: a candidate plays unitracker-player's register grid of the input."""

import numpy as np
from unitracker_player.document import read
from unitracker_player.player import load, play
from unitracker_player.render import RenderError, render


class GuaranteeError(ValueError):
    """An input whose grid has no proved horizon: a looping song with code needs ``ticks``."""


def grid(text: str, ticks: int | None = None) -> tuple:
    """``(frames, loop)``: ``ticks`` frames when given, else to an ending song's end, else to
    the recurrence render's loop proof finds, ``frames[loop:]`` then repeating forever."""
    song = load(text)
    if ticks is not None or song.loop is None:
        return play(song, ticks), None
    try:
        out = render(read(text))
    except RenderError as exc:
        raise GuaranteeError(f"{exc}; give a tick count") from exc
    return out.frames, out.loop


def _extended(frames: np.ndarray, loop: int | None, n: int) -> np.ndarray:
    if loop is None or n <= len(frames):
        return frames[:n]
    t = np.arange(n)
    period = len(frames) - loop
    return frames[np.where(t < len(frames), t, loop + (t - loop) % period)]


def same(a: tuple, b: tuple) -> bool:
    """Whether two grids play the same frames forever: two eventually periodic sequences agree
    everywhere where they agree over both preperiods and both periods (Fine and Wilf)."""
    (fa, la), (fb, lb) = a, b
    if (la is None) != (lb is None):
        return False
    if la is None:
        return fa.shape == fb.shape and np.array_equal(fa, fb)
    n = max(la, lb) + (len(fa) - la) + (len(fb) - lb)
    return np.array_equal(_extended(fa, la, n), _extended(fb, lb, n))


class Guarantee:  # pylint: disable=too-few-public-methods
    """The input's grid, and whether a candidate's text plays it."""

    def __init__(self, text: str, ticks: int | None = None):
        self.ticks = ticks
        self.reference = grid(text, ticks)

    def holds(self, text: str) -> bool:
        """Whether ``text`` plays the input's grid; a text the player refuses does not."""
        try:
            return same(self.reference, grid(text, self.ticks))
        except ValueError:
            return False
