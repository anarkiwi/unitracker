"""What the reader and the writer share: the codecs of a playroutine, and the checks that raise
:class:`SongFormatError`."""

import contextlib

from unitracker.format import spelling
from unitracker.format.codec import CodecError, CommandCodec, Evaluator, RowCodec
from unitracker.format.events import column, envelope, lifetime, naming, running
from unitracker.format.header import SongFormatError
from unitracker.format.records import RowEval, record_types
from unitracker.format.yamlstyle import Quoted, Signed


class RowErrors(RowEval):
    """Row expressions, their errors the reader's and writer's."""

    error = SongFormatError


def codec_set(pr, rtypes, tables):
    """``(note offset, {type: RowCodec}, CommandCodec)`` for a playroutine and a song's tables."""
    offset = int(pr.get("note_offset", 0))
    ev = Evaluator(pr.get("functions"), tables, pr.get("constants"))
    try:
        rows = {n: RowCodec(rt, rt.codec, offset, ev) for n, rt in rtypes.items() if rt.codec}
        return offset, rows, CommandCodec(pr.get("commands"), rtypes)
    except CodecError as exc:
        raise SongFormatError(f"{pr.get('name')}: {exc}") from exc


def codecs(pr: dict, tables) -> dict:
    """``{type: RowCodec}`` of a playroutine over a song's tables."""
    return codec_set(pr, record_types(pr), tables)[1]


def expr_fields(codec) -> set:
    """The raw fields a codec writes as expression or reference keys."""
    if codec is None:
        return set()
    keys = [k for shape in codec.shapes for k in shape.keys.values()]
    return {k["expr"] for k in keys if "expr" in k} | {k["field"] for k in keys if "ref" in k}


def refuse(problem) -> None:
    """Raise for a problem, if there is one."""
    if problem is not None:
        raise SongFormatError(problem)


def uniform(pr: dict, rtypes: dict, records: dict) -> None:
    """Refuse rows of a type that set one of its ``uniform`` fields to different values."""
    for name, rtype in rtypes.items():
        field = rtype.disagree(records.get(name) or {})
        if field is not None:
            raise SongFormatError(
                f"{pr['name']}: the {rtype.written} rows set {field} to different values;"
                " the player has one"
            )


def relative(codec, note) -> None:
    """Raise for a written row's ``note`` that is a relative note without its sign, or quoted."""
    if "note" not in codec.keys:
        return
    if isinstance(note, int) and not isinstance(note, (bool, Signed)):
        raise SongFormatError(f"note {note} is written {note:+d} in {codec.rtype.written}")
    if isinstance(note, Quoted):
        raise SongFormatError(f"note {str(note)!r} is written unquoted in {codec.rtype.written}")


def plain_value(codec, written: dict, place, what: str) -> None:
    """Raise for ``what``, a constant expression, where ``written`` with it as the value reads."""
    with contextlib.suppress(CodecError):
        codec.decode(written, place)
        raise SongFormatError(f"{codec.rtype.written}: {what} is written as its value")


def refuse_quoted(value, what: str) -> None:
    """Raise for ``value`` written in quotes where ``what`` is written plain."""
    try:
        spelling.plain(value, what)
    except spelling.SpellingError as exc:
        raise SongFormatError(str(exc)) from exc


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
