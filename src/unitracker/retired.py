"""What the format has retired: document keys, record fields and flag bits, and the raw row
forms a record type declares. Each check returns the reason a reader gives, or None.

A field's retired bits are written without, and read as its default's.
"""

_RESTART = "retired in v1.8: write the hard restart as the instrument's restart program"
_VIBRATO = "retired in v1.11: write the vibrato as the instrument's `freq` expression"
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
}
BITS = {"flags": ((0x0F, _RESTART), (0x30, _VIBRATO))}


def document(doc: dict) -> str | None:
    """The reason a document's top-level key is refused."""
    for key in KEYS.keys() & doc.keys():
        return f"{key} is {KEYS[key]}"
    return None


def fields(rtype, value: dict) -> str | None:
    """The reason a written record's field or flag bit is refused."""
    for key in FIELDS.keys() & value.keys():
        return f"{rtype.name}.{key} is {FIELDS[key]}"
    for key, spans in BITS.items():
        for bits, why in spans:
            if isinstance(value.get(key), int) and value[key] & bits:
                return f"{rtype.name}.{key} bits {bits:#04x} are {why}"
    return None


def row(rtype, raw: dict, shown, holds) -> str | None:
    """The reason raw row ``raw`` (written as ``shown``) is refused: one of the type's
    ``retired`` expressions holds for it, ``holds(expression, row)`` evaluating them."""
    plain = {k: v for k, v in raw.items() if isinstance(v, int)}
    for when, why in rtype.retired.items():
        if holds(when, plain):
            return f"{rtype.name}: row {shown!r} is {why}"
    return None


def mask(field: str) -> int:
    """The bits of ``field`` the format has retired."""
    out = 0
    for bits, _ in BITS.get(field, ()):
        out |= bits
    return out


def unwritten(rtype, raw: dict) -> str | None:
    """The reason raw row ``raw`` has no written form: it holds retired data."""
    for field in FIELDS.keys() & rtype.fields.keys():
        if int(raw.get(field, rtype.default(field))) != rtype.default(field):
            return f"{rtype.name}.{field} has no written form: it is {FIELDS[field]}"
    for field in BITS.keys() & rtype.fields.keys() & raw.keys():
        for bits, why in BITS[field]:
            if (int(raw[field]) ^ rtype.default(field)) & bits:
                return f"{rtype.name}.{field} bits {bits:#04x} have no written form: they are {why}"
    return None


def written_fields(rtype, raw: dict) -> dict:
    """A raw row's fields as written, retired bits left out, and a field left out whose written
    value is its default's."""
    out = dict(raw)
    for field in BITS.keys() & rtype.fields.keys() & raw.keys():
        out[field] = int(raw[field]) & ~mask(field)
        if out[field] == rtype.default(field) & ~mask(field):
            del out[field]
    return out


def read_fields(rtype, read: dict) -> dict:
    """A read row's fields, each retired bit its default's."""
    for field in BITS.keys() & rtype.fields.keys() & read.keys():
        if isinstance(read[field], int):
            read[field] |= rtype.default(field) & mask(field)
    return read
