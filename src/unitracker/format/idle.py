"""Program rows that only time their program, by a record type's ``adds`` (``{key: k}``: a row
whose only key is ``key`` sets nothing and lasts ``key + k`` ticks): rows of it one after another
are one row as long as it can last, a program's last row before ``end`` is none of them, and an
expression that holds the value it drives is none on a program whose rows set none."""

from unitracker.format.codec import KEY_DEFAULTS, CodecError, is_expr, row_place
from unitracker.format.xexpr import ExpressionError, source


class IdleError(ValueError):
    """A program row that changes nothing."""


def holds(text) -> bool:
    """Whether expression ``text`` is ``v``, the value it drives held."""
    try:
        return source(str(text))[0] == "v"
    except ExpressionError:
        return False


def _adds(rtype) -> tuple:
    """``(key, k)`` of a record type's ``adds``, or None."""
    return next(iter(rtype.adds.items()), None)


def _lasts(adds, row: dict) -> int | None:
    """The ticks a written row that only times its program lasts, else None."""
    key, extra = adds
    if not set(row) <= {key}:
        return None
    return int(row.get(key, KEY_DEFAULTS.get(key, 0))) + int(extra)


def _row(adds, ticks: int) -> dict:
    key, extra = adds
    value = ticks - int(extra)
    return {} if value == KEY_DEFAULTS.get(key, 0) else {key: value}


def _fits(codec, adds, ticks: int, place: dict) -> bool:
    """Whether one row lasts ``ticks`` at ``place``."""
    try:
        codec.decode(_row(adds, ticks), place)
    except CodecError:
        return False
    return ticks > int(adds[1]) - 1


def check(rtype, codec, rows: list, loop: int | None) -> None:
    """Raise for a written program's rows (``loop`` its loop row, None for one that ends) with
    a row that changes nothing."""
    adds = _adds(rtype)
    if codec is None or adds is None or not rows:
        return
    if loop is None and len(rows) > 1 and _lasts(adds, rows[-1]) is not None:
        raise IdleError(
            f"{rtype.written}: a last row that only lasts changes nothing: leave it out"
        )
    for k in range(len(rows) - 1):
        first, second = _lasts(adds, rows[k]), _lasts(adds, rows[k + 1])
        if first is not None and second is not None and loop != k + 1:
            if _fits(codec, adds, first + 1, row_place(k, len(rows), loop is None)):
                raise IdleError(
                    f"{rtype.written}: rows that only last are one row, as long as it can last"
                )
    for key in codec.expr_keys():
        value = rows[0].get(key)
        if isinstance(value, str) and holds(value) and set(rows[0]) <= {adds[0], key}:
            if not any(key in r for r in rows[1:]):
                raise IdleError(
                    f"{rtype.written}: {key}: v on a program that sets no {key} holds it as the"
                    " program does: leave it out"
                )


def settled(rtype, codec, shared: list, loop: int | None, held) -> tuple[list, int | None]:
    """``(rows, loop)``: a program's rows (each ``(raw, shared form)``) without the rows
    :func:`check` refuses; ``held(index)`` tells whether an expression holds its value."""
    adds = _adds(rtype)
    if codec is None or adds is None or not shared:
        return [raw for raw, _ in shared], loop
    shared = [(dict(raw), dict(form or {})) for raw, form in shared]
    raw, form = shared[0]
    for key, value in list(form.items()):
        field = _expr_field(codec, key)
        if field and is_expr(value) and held(value[1]) and set(form) <= {adds[0], key}:
            if not any(key in f for _, f in shared[1:]):
                raw[field] = rtype.default(field)
                del form[key]
    shared, loop = _merged(codec, adds, shared, loop)
    raws = [raw for raw, _ in shared]
    while loop is None and len(shared) > 1 and _lasts(adds, shared[-1][1]) is not None:
        shared.pop()
        raws.pop()
        raws[-1] = {**raws[-1], **{f: int(v) for f, v in rtype.end.items()}}
    return raws, loop


def _expr_field(codec, key: str) -> str | None:
    return next((s.keys[key]["expr"] for s in codec.shapes if "expr" in s.keys.get(key, {})), None)


def _merged(codec, adds, shared: list, loop):
    """Runs of rows that only last as rows each as long as it can last; a run starts at the row
    a loop returns to."""
    out, at, k = [], {}, 0
    place = row_place(1, 3, True)
    while k < len(shared):
        at[k], end = len(out), k + 1
        if _lasts(adds, shared[k][1]) is not None:
            while end < len(shared) and end != loop and _lasts(adds, shared[end][1]) is not None:
                end += 1
        if end - k == 1:
            out.append(shared[k])
            k = end
            continue
        total = sum(_lasts(adds, form) for _, form in shared[k:end])
        while total:
            ticks = next(t for t in range(total, 0, -1) if _fits(codec, adds, t, place))
            out.append((codec.decode(_row(adds, ticks), place), _row(adds, ticks)))
            total -= ticks
        k = end
    return out, None if loop is None else at[loop]
