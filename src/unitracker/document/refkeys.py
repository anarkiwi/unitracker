"""Row keys that hold a record reference (a codec key ``{ref: type, field: f}``): the writer
names the record."""


def named_ref(codecs: dict, referrer) -> bool:
    """Whether a writer's referrer ``((type, index), field)`` is a reference key's raw field,
    which names its record as a command does."""
    if not (isinstance(referrer, tuple) and isinstance(referrer[0], tuple)):
        return False
    codec = codecs.get(referrer[0][0])
    return codec is not None and any(f == referrer[1] for f, _ in codec.ref_keys().values())
