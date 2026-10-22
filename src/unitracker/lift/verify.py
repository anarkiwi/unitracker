"""The lift's proof: its generic document, played by unitracker-player, reproduces the native
player's register grid, each voice from its first note (``compare``, a gridcmp onset verdict),
and why a document, the generic VM or a native player diverges from another or from the
6502 (``explain``)."""

import numpy as np
from pysidtracker import gridcmp, whydiff
from unitracker_player import Player, load, play

from unitracker.player import oracle
from unitracker.player.canonical import REGISTERS
from unitracker.player.vm import SongPlayer

REPORT = {"names": REGISTERS, "subject": "document", "a": "doc", "b": "native"}
"""``gridcmp.runs_report`` keywords naming a verdict's registers and grids."""


def compare(text: str, theirs: np.ndarray, progress: bool = False, *, tol=None) -> gridcmp.Verdict:
    """The onset :class:`~pysidtracker.gridcmp.Verdict` of generic document ``text`` played by
    unitracker-player against native grid ``theirs``, in ticks (``gridcmp.verdict_onset``),
    exempting the cells gridcmp tolerance ``tol`` names: the native playroutine's
    ``tolerance``, as the document names none."""
    theirs = np.asarray(theirs, dtype=np.uint8)
    ours, starts = oracle.play(text, len(theirs), progress)
    return gridcmp.verdict_onset(ours, starts, theirs, tol=tol)


class DocumentPlayer(Player):
    """unitracker-player as a whydiff player: ``play_frame`` plays one tick into ``regs``, the
    register file it writes a register at a time, and ``render_grid`` a fresh run's ticks."""

    play_frame = Player.step

    def render_grid(self, ticks: int) -> np.ndarray:
        """The registers of ``ticks`` ticks played from the start (fewer where it ends)."""
        return play(self.song, ticks)


def label(who) -> str:
    """A side's label: ``document`` (a document's text), ``generic`` (a song on the VM) or
    ``native`` (a player factory)."""
    return "document" if isinstance(who, str) else "generic" if isinstance(who, dict) else "native"


def player(subject):
    """The whydiff player of a document's text (:class:`DocumentPlayer`) or a song (the VM)."""
    return DocumentPlayer(load(subject)) if isinstance(subject, str) else SongPlayer(subject)


def _sources(who) -> dict | None:
    return SongPlayer(who).sources if isinstance(who, dict) else None


def _regs(p, register: int) -> tuple:
    return p.regs, register


def side(who, tick: int, register: int, grid, name: str = ""):
    """whydiff's :class:`~pysidtracker.whydiff.Side` of ``who`` on ``tick``, traced in Python
    and labelled ``name`` (default :func:`label`): a document's text on unitracker-player or a
    song on the VM (their ``regs`` the register cells), or a native player factory. ``grid`` is
    ``who``'s own, for a tick that does not store ``register``."""
    name = name or label(who)
    if not isinstance(who, (str, dict)):
        return whydiff.python_side(who, tick, register, label=name, grid=grid)
    return whydiff.python_side(
        lambda: player(who),
        tick,
        register,
        label=name,
        cell=_regs,
        sources=_sources(who),
        grid=grid,
    )


def explain(
    verdict: gridcmp.Verdict, subject, grids: tuple, against, name: str = "", *, subtune=None
):
    """Why ``subject`` diverges from ``against`` at ``verdict``'s onset, a
    :class:`~pysidtracker.whydiff.Divergence` (None without one); ``grids`` are theirs.

    Each is a document's text (on unitracker-player), a song (on the VM) or a native player
    factory, traced in Python, or ``against`` a raw tune (its ``subtune``, default the first),
    explained on jennings against the reference ``grids[1]``. Two sides of one kind are
    labelled apart by ``reference``.
    """
    # pylint: disable=too-many-arguments
    if verdict.onset is None:
        return None
    tick, register = verdict.onset
    if isinstance(against, (bytes, bytearray)):
        return whydiff.explain_divergence(
            bytes(against),
            lambda _: player(subject),
            grids[1],
            song=(subtune or 0) + 1,
            frame=tick,
            register=register,
            name=name,
            label=label(subject),
            cell=_regs,
            sources=_sources(subject),
        )
    same = label(subject) == label(against)
    return whydiff.explain_cell(
        side(subject, tick, register, grids[0]),
        side(against, tick, register, grids[1], "reference" if same else ""),
        grids,
        frame=tick,
        register=register,
        name=name,
    )
