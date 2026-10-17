"""What the format has retired: document keys, record fields, and the raw row forms a record
type declares. Each check returns the reason a reader gives, or None.
"""

_RESTART = "retired in v1.8: write the hard restart as the instrument's restart program"
_VIBRATO = "retired in v1.11: write the vibrato as the instrument's `freq` expression"
_INS_VIBRATO = "retired in v1.16: write the vibrato as the instrument's `freq` expression"
KEYS = {"registers": "retired in v1.6: the player starts from a zeroed SID"}
FIELDS = {
    "gt": _RESTART,
    "fw": _RESTART,
    "hrad": _RESTART,
    "hrsr": _RESTART,
    "firstctrl": _RESTART,
    "chord": "retired in v1.9: a chord is the instrument's `ctrl` program of relative notes",
    "arpspeed": "retired in v1.9: write the arp speed as the `ctrl` program rows' `hold`",
    "vibrato": _VIBRATO,
    "vibdelay": _VIBRATO,
    "speed": _INS_VIBRATO,
    "vd": _INS_VIBRATO,
    "octave": "retired in v1.11: a note is written as the pitch it sounds",
    "flags": "retired in v1.13: its bits are the instrument's restart program, its `freq`"
    " expression, and a note's `!pulse:0` and `!filter:0`",
}


def document(doc: dict) -> str | None:
    """The reason a document's top-level key is refused."""
    for key in KEYS.keys() & doc.keys():
        return f"{key} is {KEYS[key]}"
    return None


def fields(rtype, value: dict) -> str | None:
    """The reason a written record's field is refused."""
    for key in sorted(FIELDS.keys() & value.keys()):
        return f"{rtype.written}.{key} is {FIELDS[key]}"
    return None


def row(rtype, raw: dict, shown, holds) -> str | None:
    """The reason raw row ``raw`` (written as ``shown``) is refused: one of the type's
    ``retired`` expressions holds for it, ``holds(expression, row)`` evaluating them."""
    plain = {k: v for k, v in raw.items() if isinstance(v, int)}
    for when, why in rtype.retired.items():
        if holds(when, plain):
            return f"{rtype.written}: row {shown!r} is {why}"
    return None


def unwritten(rtype, raw: dict) -> str | None:
    """The reason raw row ``raw`` has no written form: it holds retired data."""
    for field in sorted(FIELDS.keys() & rtype.fields.keys()):
        if int(raw.get(field, rtype.default(field))) != rtype.default(field):
            return f"{rtype.written}.{field} has no written form: it is {FIELDS[field]}"
    return None
