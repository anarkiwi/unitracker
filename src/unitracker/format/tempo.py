"""A song's initial tempo: ``n`` ticks per step, or a swing pair ``[a, b]`` whose steps alternate.

A playroutine declares ``tempo: {cells: {cell: expr}, when: expr}`` over ``a`` and ``b`` (``b``
is ``a`` for one tempo): ``cells`` the cells it starts, ``when`` the tempos it plays
(docs/playroutines/schema.md, *Tempo*).
"""

from unitracker.format.codec import CodecError, evaluate

NAME = "tempo"


def pair(tempo) -> tuple[int, int] | None:
    """``(a, b)`` of a tempo as written, or None for one that is neither ``n`` nor ``[a, b]``."""
    if isinstance(tempo, bool):
        return None
    if isinstance(tempo, int):
        return tempo, tempo
    if (
        isinstance(tempo, (list, tuple))
        and len(tempo) == 2
        and all(isinstance(t, int) and not isinstance(t, bool) for t in tempo)
        and tempo[0] != tempo[1]
    ):
        return int(tempo[0]), int(tempo[1])
    return None


def written(a: int, b: int):
    """The written form of the pair ``(a, b)``: ``a`` when they are equal."""
    return a if a == b else [a, b]


def default(pr: dict):
    """The tempo a song of playroutine ``pr`` has unless it says otherwise, or None."""
    return (pr.get("defaults") or {}).get(NAME)


def problem(pr: dict, tempo) -> str | None:
    """Why playroutine ``pr`` can't play initial tempo ``tempo``, or None."""
    spec = pr.get(NAME)
    ab = pair(tempo)
    if ab is None:
        return f"tempo {tempo!r} is n or [a, b], two different tempos"
    if not spec:
        return f"{pr.get('name')}: the player has no tempo"
    try:
        plays = "when" not in spec or evaluate(spec["when"], {"a": ab[0], "b": ab[1]})
    except CodecError as exc:
        return f"{pr.get('name')}: {exc}"
    return None if plays else f"{pr.get('name')}: the player can't express tempo {tempo!r}"


def cells(pr: dict, tempo) -> dict:
    """``{cell: value}`` the initial tempo ``tempo`` (default: the playroutine's) sets."""
    spec = pr.get(NAME)
    tempo = default(pr) if tempo is None else tempo
    if not spec or tempo is None:
        return {}
    a, b = pair(tempo)
    return {cell: evaluate(expr, {"a": a, "b": b}) for cell, expr in (spec.get("cells") or {}).items()}
