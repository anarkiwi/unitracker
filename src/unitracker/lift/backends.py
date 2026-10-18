"""Format detection and dispatch to each backend's lifter and native player."""

from pysidtracker import SidError, SidImage

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


def lift(raw: bytes, subtune=None, expressions: bool = True) -> dict:
    """A unitracker song for ``raw``, lifted by the backend that recognises it.

    With ``expressions`` its modulations are written as the format's expressions.
    """
    if subtune is None:
        subtune = start_song(raw)
    backend = detect(raw)
    if backend == "defmon":
        from unitracker.lift import defmon  # pylint: disable=import-outside-toplevel

        return defmon.lift(raw, subtune, expressions)
    if backend == "sidwizard":
        from unitracker.lift import sidwizard  # pylint: disable=import-outside-toplevel

        return sidwizard.lift(raw, subtune, expressions)
    from unitracker.lift import goattracker  # pylint: disable=import-outside-toplevel

    return goattracker.GoatTrackerLift.from_sid(raw, subtune, expressions).run()


def native_player(raw: bytes, subtune=None):
    """The native player for ``raw``, a ``pysidtracker.MemPlayer``."""
    if subtune is None:
        subtune = start_song(raw)
    backend = detect(raw)
    if backend == "defmon":
        from pydefmon import DefmonPlayer  # pylint: disable=import-outside-toplevel

        return DefmonPlayer(raw, subtune=subtune)
    if backend == "sidwizard":
        from pysidwizard import SWMPlayer, parse_sid_song  # pylint: disable=import-outside-toplevel

        return SWMPlayer(parse_sid_song(raw, subtune + 1))
    from unitracker.lift import goattracker  # pylint: disable=import-outside-toplevel

    return goattracker.GoatTrackerLift.from_sid(raw, subtune).native()
