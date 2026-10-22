"""Dispatch to the lifter of the installed ``pysidtracker`` format that recognises a tune."""

import importlib

from pysidtracker import SidParseError, find_format, gridcmp

from unitracker.lift import verify as proof
from unitracker.playroutine.library import library

LIFTS = {
    "defmon": ("unitracker.lift.defmon", "DefmonLift"),
    "goattracker2": ("unitracker.lift.goattracker", "GoatTrackerLift"),
    "sidwizard": ("unitracker.lift.sidwizard", "SidWizardLift"),
}
"""Each lifted format's lifter, by its ``pysidtracker.formats`` name."""


def detect(raw: bytes) -> str:
    """The name of the installed format that recognises ``raw`` (``pysidtracker.find_format``)."""
    return find_format(raw).name


def lifter(raw: bytes, subtune=None, expressions: bool = True):
    """The :class:`~unitracker.lift.base.Lift` of ``raw``'s ``subtune`` (by default the one its
    header starts on) by the lifter of the format that recognises it."""
    name = detect(raw)
    if name not in LIFTS:
        raise SidParseError(f"no lifter for the {name} format")
    module, cls = LIFTS[name]
    return getattr(importlib.import_module(module), cls).from_sid(raw, subtune, expressions)


def lift(raw: bytes, subtune=None, expressions: bool = True) -> dict:
    """A unitracker song for ``raw``, lifted by the backend that recognises it.

    With ``expressions`` its modulations are written as the format's expressions.
    """
    return lifter(raw, subtune, expressions).run()


def verify(document_text: str, raw_sid: bytes, subtune=None, ticks: int = 3007) -> gridcmp.Verdict:
    """The :class:`~pysidtracker.gridcmp.Verdict` of a generic document against ``ticks``
    ticks of the native player of ``raw_sid``'s ``subtune`` (by default its header's)."""
    native = find_format(raw_sid).play(raw_sid, subtune)
    return proof.compare(document_text, native.render_grid(ticks), tol=tolerance(raw_sid, subtune))


def tolerance(raw: bytes, subtune=None):
    """The gridcmp tolerance the playroutine of ``raw``'s lifter declares (``tolerance``), or
    None: its grids compare exactly."""
    return library(lifter(raw, subtune).playroutine).get("tolerance")
