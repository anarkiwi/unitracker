"""Lifter songs (the in-memory IR the lifters produce and the VM plays) built in Python."""

import copy

from unitracker.playroutine import tempo as ir_tempo
from unitracker.playroutine.library import DEFAULTS, library


def plays(*entries) -> list:
    """Track plays from phrase names or ``(name, transpose)`` pairs."""
    return [
        {"phrase": e, "transpose": 0} if isinstance(e, str) else {"phrase": e[0], "transpose": e[1]}
        for e in entries
    ]


def song(playroutine: str, records=None, phrases=None, play=None, loop=None, **extra) -> dict:
    """A song of library ``playroutine``: ``records`` ``{type: {index: fields}}``, ``phrases``
    ``{name: [event, ...]}``; ``play`` one phrase entry (see :func:`plays`) per voice, each voice
    looping to play ``loop`` (None: it ends), unless ``tracks`` is given. ``exprs``, ``tempo``,
    ``meta``, ``clock``, ``voices``, ``constants`` and ``tables`` override the defaults; any other
    keyword is a top-level key."""
    pr = library(playroutine)
    tables = pr.pop("tables", None) or {}
    pr["constants"] = {**(pr.get("constants") or {}), **extra.pop("constants", {})}
    tables.update(extra.pop("tables", {}))
    out = {"unitracker": "ir", **copy.deepcopy(DEFAULTS), "meta": {}, "exprs": {}, "tables": tables}
    if pr.get(ir_tempo.NAME):
        out["tempo"] = ir_tempo.default(pr)
    if play is not None:
        out["tracks"] = [{"plays": plays(p), "loop": loop} for p in play]
    out.update(records=records or {}, phrases=phrases or {}, playroutine=pr, **extra)
    return out
