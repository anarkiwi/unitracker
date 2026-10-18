"""The lift framework: a tune through its build check, structure, normalisations and expressions."""

import abc
from collections import deque
from collections.abc import Callable, Iterator

from pysidtracker import SidImage

from unitracker.modfit.convert import convert

Pass = Callable[["Lift", dict], dict]


class LiftError(ValueError):
    """The tune uses something its playroutine or lifter does not cover."""


def start_song(raw: bytes) -> int:
    """The 0-based subtune the PSID header of ``raw`` starts on (0 without a header)."""
    header = SidImage.from_bytes(raw).header
    return (header.start_song or 1) - 1 if header is not None else 0


class Lift(abc.ABC):
    """One decoded tune through the lift passes, named in order by :meth:`passes`.

    A subclass names its ``playroutine``, decodes a tune, transcribes it in :meth:`structure` and
    lists its player-specific ``normalisations``, each ``(lift, song) -> song``.
    """

    playroutine: str
    normalisations: tuple[Pass, ...] = ()

    def __init__(self, tune, expressions: bool = True):
        self.tune, self.expressions = tune, expressions

    @classmethod
    def from_sid(cls, raw: bytes, subtune: int | None = None, expressions: bool = True) -> "Lift":
        """The lift of ``.sid`` ``raw``'s ``subtune``, by default the one its header starts on."""
        return cls(cls.decode(raw, start_song(raw) if subtune is None else subtune), expressions)

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
        """The tune transcribed to the internal song, before the normalisations."""

    def convert(self, song: dict) -> dict:
        """``song`` with its native modulations written as the format's expressions."""
        return convert(song)

    @classmethod
    def passes(cls) -> tuple[str, ...]:
        """The names of the passes, in the order :meth:`stages` runs them."""
        return ("structure", *(p.__name__ for p in cls.normalisations), "convert")

    def stages(self) -> Iterator[tuple[str, dict]]:
        """Each pass's name and the song after it, after the build check; ``convert`` only with
        ``expressions``."""
        self.check_build()
        song = self.structure()
        yield "structure", song
        for normalise in self.normalisations:
            song = normalise(self, song)
            yield normalise.__name__, song
        if self.expressions:
            yield "convert", self.convert(song)

    def run(self) -> dict:
        """The lifted song: the last stage's."""
        return deque(self.stages(), maxlen=1)[0][1]
