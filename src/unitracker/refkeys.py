"""Row keys that hold a record reference (a codec key ``{ref: type, field: f}``): the writer
names the record, and the reader reads the name before the record may be defined."""

import re


def named_ref(codecs: dict, referrer) -> bool:
    """Whether a writer's referrer ``((type, index), field)`` is a reference key's raw field,
    which names its record as a command does."""
    if not (isinstance(referrer, tuple) and isinstance(referrer[0], tuple)):
        return False
    codec = codecs.get(referrer[0][0])
    return codec is not None and any(f == referrer[1] for f, _ in codec.ref_keys().values())


def is_name(names: dict, rtypes: dict, value, tname: str) -> bool:
    """Whether ``value`` names a ``tname``: defined as one (``names``: ``{name: (type, index)}``),
    or not yet defined and of its name form (its prefix and a number)."""
    if not isinstance(value, str):
        return False
    if value in names:
        return names[value][0] == tname
    return re.fullmatch(f"{re.escape(rtypes[tname].prefix)}[0-9]+", value) is not None


def placeholders(names: dict, rtypes: dict, codec, value: dict) -> dict:
    """``{raw field: ("name", name)}`` of a written row's reference keys, each key in ``value``
    set to its type's first index to decode with; the names resolve once all are defined.

    Raises ValueError for a name that names none of the key's types.
    """
    out = {}
    for key, (field, types) in codec.ref_keys().items():
        if key not in value:
            continue
        kinds = [t for t in sorted(types) if is_name(names, rtypes, value[key], t)]
        if len(kinds) != 1:
            written = sorted(rtypes[t].written for t in types)
            raise ValueError(f"{value[key]!r} names no {' or '.join(written)}")
        out[field] = ("name", value[key])
        value[key] = (kinds[0], rtypes[kinds[0]].first)
    return out
