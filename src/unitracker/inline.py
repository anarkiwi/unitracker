"""Command arguments written as the row they reference: ``!funk:6/7`` is a speed-table row.

The argument is the row's fields in declaration order, each a byte, joined by ``/``.
"""

SEP = "/"


def _fields(rtype) -> list:
    return [f for f in rtype.fields if f != rtype.chain]


def written(rtype, row: dict) -> str:
    """The written argument of a row of ``rtype``."""
    return SEP.join(str(int(row.get(f, rtype.default(f)))) for f in _fields(rtype))


def read(rtype, values: tuple) -> dict:
    """The row of ``rtype`` an argument's values write; raises ValueError naming what is wrong."""
    fields = _fields(rtype)
    if len(values) != len(fields):
        raise ValueError(f"the argument is {len(fields)} values, {SEP.join(fields)}")
    if not all(0 <= v <= 0xFF for v in values):
        raise ValueError("each value is a byte (0-255)")
    return dict(zip(fields, values))
