"""A song's initial tempo: ``n`` ticks per step, or a swing pair ``[a, b]`` whose steps alternate.

A playroutine declares ``tempo: {cells: {cell: expr}}`` over ``a`` and ``b`` (``b`` is ``a`` for
one tempo): the cells it starts (docs/playroutines/schema.md, *Tempo*).
"""

from unitracker.playroutine.codec import evaluate

NAME = "tempo"
COMMANDS = ("tempo", "vtempo")


def one(name: str, value):
    """``a`` for a tempo command's pair argument ``(a, a)``, else None: a swing is two
    different tempos, and ``!tempo:a/a`` is written ``!tempo:a``."""
    if name in COMMANDS and isinstance(value, tuple) and len(value) == 2 and value[0] == value[1]:
        return value[0]
    return None


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


def cells(pr: dict, tempo) -> dict:
    """``{cell: value}`` the initial tempo ``tempo`` (default: the playroutine's) sets."""
    spec = pr.get(NAME)
    tempo = default(pr) if tempo is None else tempo
    if not spec or tempo is None:
        return {}
    a, b = pair(tempo)
    return {
        cell: evaluate(expr, {"a": a, "b": b}) for cell, expr in (spec.get("cells") or {}).items()
    }
