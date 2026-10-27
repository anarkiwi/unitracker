"""The format-neutral event stream a direct tracer yields and :mod:`unitracker.direct.builder`
turns into a document; docs/direct/builder.md."""

from collections.abc import Hashable, Iterator
from typing import NamedTuple, Protocol

from unitracker_grammar import document as d

# shared effect kinds and their parameter names; a tracer binds each to its own code templates
EFFECTS = {
    "pitch": (),
    "glide": ("rate", "down"),
    "detune": ("interval",),
    "pulse": ("set", "width", "speed"),
    "cutoff": ("layer", "set", "offset", "value", "step"),
}


class Effect(NamedTuple):
    """A row's effect of a shared ``kind`` (:data:`EFFECTS`) with its parameters by name."""

    kind: str
    params: tuple = ()


class Step(NamedTuple):
    """A song step from ``tick``; ``key`` is the tracer state whose recurrence loops the song."""

    tick: int
    key: Hashable


class Note(NamedTuple):
    """A note on ``voice`` at ``tick``: generic note name ``note`` with instrument ``ins``."""

    tick: int
    voice: int
    note: str
    ins: str | None = None
    legato: bool = False


class Start(NamedTuple):
    """A program start on ``voice`` at ``tick`` from tracer table position ``pos``."""

    tick: int
    voice: int
    pos: Hashable


class Command(NamedTuple):
    """An event command ``key: value`` on ``voice`` at ``tick``."""

    tick: int
    voice: int
    key: str
    value: object


class Tracer(Protocol):
    """A format's native player instrumented: its header parts, its event stream to the
    song's loop, and its table walk one tick at a time."""

    def head(self) -> dict:
        """``meta``, ``clock``, ``slots``, ``state``, ``tables``, ``code``, ``instruments``."""

    def init(self) -> tuple:
        """Each voice's init Event; a ``run`` of a Program is the builder's to name."""

    def events(self) -> Iterator:
        """Steps, Notes, Starts and Commands in tick order, a step's events after it."""

    def step(self, pos: Hashable) -> tuple:
        """``(pos, rows)``: the position after one tick from ``pos`` (None stops) and the
        rows that tick fetches, each a tuple of document keys and Effects."""

    def bind(self, effect: Effect, voice: int) -> tuple:
        """The document keys of ``effect`` on ``voice``."""


def program(rows, loop: int | None = None) -> d.Program:
    """A Program of ``(hold, keys)`` rows."""
    return d.Program([d.Row(((("hold", h),) if h != 1 else ()) + tuple(k)) for h, k in rows], loop)
