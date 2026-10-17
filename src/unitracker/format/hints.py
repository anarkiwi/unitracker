"""The ``hints`` mapping: reading aids the player ignores. Each check returns the reason a
reader or writer gives, or None."""

KEYS = ("ticks_per_step", "phrase_steps")


def problem(value) -> str | None:
    """The reason ``value`` is not a ``hints`` mapping of :data:`KEYS` to positive integers."""
    if value is None:
        return None
    if not isinstance(value, dict):
        return "hints must be a mapping"
    for key, val in value.items():
        if key not in KEYS:
            return f"hints: unknown key {key!r}"
        if isinstance(val, bool) or not isinstance(val, int) or val < 1:
            return f"hints: {key} must be a positive integer"
    return None


def disagreement(stated, steps: set) -> str | None:
    """The reason a stated ``phrase_steps`` contradicts the phrases' lengths ``steps``."""
    if stated is None or steps == {stated}:
        return None
    return f"hints: phrase_steps {stated} disagrees with the phrase lengths {sorted(steps)}"


def written(stated, steps: set) -> tuple[str | None, dict]:
    """``(reason, hints)``: the lifter's hints with ``phrase_steps`` recomputed from the phrases'
    lengths ``steps`` (present when every phrase has one length), or the reason they are refused."""
    reason = problem(stated)
    found = dict(stated or {})
    reason = reason or disagreement(found.pop("phrase_steps", None), steps)
    if len(steps) == 1:
        found["phrase_steps"] = next(iter(steps))
    return reason, found
