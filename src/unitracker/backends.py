"""Format detection and dispatch to each backend's lifter and native player."""

from pysidtracker import SidError, SidImage


class UnknownFormat(ValueError):
    """No backend recognises the tune."""


def _start_song(raw: bytes) -> int:
    header = SidImage.from_bytes(raw).header
    return (header.start_song or 1) - 1 if header is not None else 0


def detect(raw: bytes) -> str:
    """The backend that recognises ``raw``: ``defmon`` or ``goattracker2``."""
    from pydefmon import DefmonSidParser  # pylint: disable=import-outside-toplevel
    from pygoattracker import decompile_sid  # pylint: disable=import-outside-toplevel

    try:
        if DefmonSidParser().recognize(SidImage.from_bytes(raw)) is not None:
            return "defmon"
        decompile_sid(raw)
    except SidError as exc:
        raise UnknownFormat(f"no backend recognises this tune: {exc}") from exc
    return "goattracker2"


def lift(raw: bytes, subtune=None) -> dict:
    """A unitracker song for ``raw``, lifted by the backend that recognises it."""
    if subtune is None:
        subtune = _start_song(raw)
    if detect(raw) == "defmon":
        from unitracker import defmon  # pylint: disable=import-outside-toplevel

        return defmon.lift(raw, subtune)
    from pygoattracker import decompile_sid  # pylint: disable=import-outside-toplevel

    from unitracker import goattracker  # pylint: disable=import-outside-toplevel

    return goattracker.lift(decompile_sid(raw, subtune=subtune))


def native_player(raw: bytes, subtune=None):
    """The native player for ``raw``, a ``pysidtracker.MemPlayer``."""
    if subtune is None:
        subtune = _start_song(raw)
    if detect(raw) == "defmon":
        from pydefmon import DefmonPlayer  # pylint: disable=import-outside-toplevel

        return DefmonPlayer(raw, subtune=subtune)
    from pygoattracker import decompile_sid  # pylint: disable=import-outside-toplevel

    return decompile_sid(raw, subtune=subtune).player()
