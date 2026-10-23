"""The lift framework: a tune through its build check, structure, normalisations, expressions,
their canonical form, and the proof of its generic document against the native player."""

import abc
import dataclasses
from collections import deque
from collections.abc import Callable, Iterator

from pysidtracker import gridcmp, start_subtune
from unitracker_player import PlayError

from unitracker import document
from unitracker.playroutine.library import SongFormatError
from unitracker.lift import verify
from unitracker.modfit.convert import convert, window
from unitracker.optimise import canonical
from unitracker.player.reach import ReachabilityError

Pass = Callable[["Lift", dict], dict]


class LiftError(ValueError):
    """The tune uses something its playroutine or lifter does not cover."""


def song_meta(psid, source: str) -> dict:
    """The ``meta`` mapping for a tune with PSID header ``psid``, lifted by ``source``."""
    return {
        "title": psid.name,
        "author": psid.author,
        "released": psid.released,
        "source": source,
    }


class Lift(abc.ABC):
    """One decoded tune through the lift passes, named in order by :meth:`passes`.

    A subclass names its ``playroutine``, decodes a tune, transcribes it in :meth:`structure` and
    lists its player-specific ``normalisations``, each ``(lift, song) -> song``.
    """

    playroutine: str
    normalisations: tuple[Pass, ...] = ()

    def __init__(self, tune, expressions: bool = True):
        self.tune, self.expressions = tune, expressions
        self.ticks: int | None = None
        self.document: str | None = None
        self.verdict: gridcmp.Verdict | None = None
        self.gap: str | None = None

    @classmethod
    def from_sid(cls, raw: bytes, subtune: int | None = None, expressions: bool = True) -> "Lift":
        """The lift of ``.sid`` ``raw``'s ``subtune``, by default the one its header starts on."""
        return cls(cls.decode(raw, start_subtune(raw) if subtune is None else subtune), expressions)

    @staticmethod
    @abc.abstractmethod
    def decode(raw: bytes, subtune: int):
        """The native package's decoded tune of ``raw``'s ``subtune``."""

    @abc.abstractmethod
    def native(self):
        """A fresh native player (a ``pysidtracker.MemPlayer``) of the tune."""

    def check_build(self) -> None:
        """Raise :class:`LiftError` naming the build features the playroutine does not cover."""

    @abc.abstractmethod
    def structure(self) -> dict:
        """The tune transcribed to the lifter's song, before the normalisations."""

    def convert(self, song: dict) -> dict:
        """``song`` with its native modulations written as the format's expressions."""
        return convert(song)

    def canonical(self, song: dict) -> dict:
        """``song`` with modulation written shattered in the format's canonical form: the
        optimiser's canonical passes, proved on the written text."""
        return canonical(song)

    def verify(self, song: dict) -> gridcmp.Verdict | None:
        """The verdict of ``song``'s generic document, played by unitracker-player, against
        the native grid over the conversion's ticks; :attr:`document` holds the document, and
        :attr:`gap` the lifting gap that keeps it from being written or played."""
        self.document, self.verdict = None, None
        if self.ticks is None:
            self.gap = "the song's state does not recur: no ticks to verify it over"
            return None
        native = document.native_state(song, self.ticks, self.ticks)
        try:
            self.document = document.write(song, dataclasses.replace(native, ticks=self.ticks))
        except (document.GenericError, SongFormatError, PlayError) as exc:
            self.gap = str(exc)
            return None
        try:
            theirs = self.native().render_grid(self.ticks)
            tol = song["playroutine"].get("tolerance")
            self.verdict = verify.compare(self.document, theirs, tol=tol)
        except PlayError as exc:
            self.gap = f"unitracker-player refuses the document: {exc}"
            return None
        self.gap = None
        return self.verdict

    @classmethod
    def passes(cls) -> tuple[str, ...]:
        """The names of the passes, in the order :meth:`stages` runs them."""
        normalised = (p.__name__ for p in cls.normalisations)
        return ("structure", *normalised, "convert", "canonical", "verify")

    def stages(self) -> Iterator[tuple[str, dict]]:
        """Each pass's name and the song after it, after the build check; ``convert``,
        ``canonical`` and ``verify`` (which leaves the song as it is) only with
        ``expressions``."""
        self.check_build()
        song = self.structure()
        yield "structure", song
        for normalise in self.normalisations:
            song = normalise(self, song)
            yield normalise.__name__, song
        if self.expressions:
            try:
                self.ticks = window(song)
            except ReachabilityError:
                self.ticks = None
            song = self.convert(song)
            yield "convert", song
            song = self.canonical(song)
            yield "canonical", song
            self.verify(song)
            yield "verify", song

    def run(self) -> dict:
        """The lifted song: the last stage's."""
        return deque(self.stages(), maxlen=1)[0][1]
