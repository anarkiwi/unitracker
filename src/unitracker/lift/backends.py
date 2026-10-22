"""Format detection and dispatch to each backend's lifter and native player."""

from pysidtracker import SidError, SidImage, gridcmp

from unitracker.lift import verify as proof
from unitracker.lift.base import start_song


class UnknownFormat(ValueError):
    """No backend recognises the tune."""


def detect(raw: bytes) -> str:
    """The backend that recognises ``raw``: ``defmon``, ``sidwizard`` or ``goattracker2``."""
    from pydefmon import DefmonSidParser  # pylint: disable=import-outside-toplevel
    from pygoattracker import decompile_sid  # pylint: disable=import-outside-toplevel
    from pysidwizard import SidWizardSidParser  # pylint: disable=import-outside-toplevel

    try:
        image = SidImage.from_bytes(raw)
        if DefmonSidParser().recognize(image) is not None:
            return "defmon"
        if SidWizardSidParser().recognize(image) is not None:
            return "sidwizard"
        decompile_sid(raw)
    except SidError as exc:
        raise UnknownFormat(f"no backend recognises this tune: {exc}") from exc
    return "goattracker2"


def lifter(raw: bytes, subtune=None, expressions: bool = True):
    """The :class:`~unitracker.lift.base.Lift` of ``raw``'s ``subtune`` (by default the one its
    header starts on) by the backend that recognises it."""
    if subtune is None:
        subtune = start_song(raw)
    backend = detect(raw)
    if backend == "defmon":
        from unitracker.lift import defmon  # pylint: disable=import-outside-toplevel

        return defmon.DefmonLift.from_sid(raw, subtune, expressions)
    if backend == "sidwizard":
        from unitracker.lift import sidwizard  # pylint: disable=import-outside-toplevel

        return sidwizard.SidWizardLift.from_sid(raw, subtune, expressions)
    from unitracker.lift import goattracker  # pylint: disable=import-outside-toplevel

    return goattracker.GoatTrackerLift.from_sid(raw, subtune, expressions)


def lift(raw: bytes, subtune=None, expressions: bool = True) -> dict:
    """A unitracker song for ``raw``, lifted by the backend that recognises it.

    With ``expressions`` its modulations are written as the format's expressions.
    """
    return lifter(raw, subtune, expressions).run()


def native_player(raw: bytes, subtune=None):
    """The native player for ``raw``, a ``pysidtracker.MemPlayer``."""
    return lifter(raw, subtune).native()


def verify(document_text: str, raw_sid: bytes, subtune=None, ticks: int = 3007) -> gridcmp.Verdict:
    """The :class:`~pysidtracker.gridcmp.Verdict` of a generic document against ``ticks``
    ticks of the native player of ``raw_sid``'s ``subtune`` (by default its header's)."""
    return proof.compare(document_text, native_player(raw_sid, subtune).render_grid(ticks))
