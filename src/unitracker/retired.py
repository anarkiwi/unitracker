"""What the format has retired: document keys, record fields and flag bits, and the raw row
forms a record type declares. Each check returns the reason a reader gives, or None."""

_RESTART = "retired in v1.8: write the hard restart as the instrument's restart program"
KEYS = {"registers": "retired in v1.6: the player starts from a zeroed SID"}
FIELDS = {
    "gt": _RESTART,
    "fw": _RESTART,
    "hrad": _RESTART,
    "hrsr": _RESTART,
    "firstctrl": _RESTART,
    "chord": "retired in v1.9: a chord is the instrument's `ctrl` program of relative notes",
    "arpspeed": "retired in v1.9: write the arp speed as the `ctrl` program rows' `hold`",
}
BITS = {"flags": (0x0F, _RESTART)}


def document(doc: dict) -> str | None:
    """The reason a document's top-level key is refused."""
    for key in KEYS.keys() & doc.keys():
        return f"{key} is {KEYS[key]}"
    return None


def fields(rtype, value: dict) -> str | None:
    """The reason a written record's field or flag bit is refused."""
    for key in FIELDS.keys() & value.keys():
        return f"{rtype.name}.{key} is {FIELDS[key]}"
    for key, (mask, why) in BITS.items():
        if isinstance(value.get(key), int) and value[key] & mask:
            return f"{rtype.name}.{key} bits {mask:#04x} are {why}"
    return None


def row(rtype, raw: dict, shown, holds) -> str | None:
    """The reason raw row ``raw`` (written as ``shown``) is refused: one of the type's
    ``retired`` expressions holds for it, ``holds(expression, row)`` evaluating them."""
    plain = {k: v for k, v in raw.items() if isinstance(v, int)}
    for when, why in rtype.retired.items():
        if holds(when, plain):
            return f"{rtype.name}: row {shown!r} is {why}"
    return None
