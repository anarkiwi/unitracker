"""The codecs of a playroutine, and the writer's checks that raise :class:`SongFormatError`."""

from unitracker.playroutine import codec as playroutine_codec
from unitracker.document.events import column, envelope, lifetime, naming, running
from unitracker.playroutine.library import SongFormatError
from unitracker.playroutine.records import RowEval


class RowErrors(RowEval):
    """Row expressions, their errors the writer's."""

    error = SongFormatError


def codec_set(pr, rtypes, tables):
    """:func:`unitracker.playroutine.codec.codec_set`, its errors the writer's."""
    try:
        return playroutine_codec.codec_set(pr, rtypes, tables)
    except playroutine_codec.CodecError as exc:
        raise SongFormatError(f"{pr.get('name')}: {exc}") from exc


def expr_fields(codec) -> set:
    """The raw fields a codec writes as expression or reference keys."""
    if codec is None:
        return set()
    keys = [k for shape in codec.shapes for k in shape.keys.values()]
    return {k["expr"] for k in keys if "expr" in k} | {k["field"] for k in keys if "ref" in k}


def uniform(pr: dict, rtypes: dict, records: dict) -> None:
    """Refuse rows of a type that set one of its ``uniform`` fields to different values."""
    for name, rtype in rtypes.items():
        field = rtype.disagree(records.get(name) or {})
        if field is not None:
            raise SongFormatError(
                f"{pr['name']}: the {rtype.written} rows set {field} to different values;"
                " the player has one"
            )


def player(pr, call, *args):
    """``call(*args)``, its lifetime, envelope and column errors the player's."""
    try:
        return call(*args)
    except (
        lifetime.LifetimeError,
        envelope.EnvelopeError,
        column.ColumnError,
        running.RunningError,
        naming.NamingError,
    ) as exc:
        raise SongFormatError(f"{pr['name']}: {exc}") from exc
