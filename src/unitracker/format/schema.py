"""The playroutine schema's conventions: a definition with each key it leaves to one filled in.

Every default here gives way to an explicit key (docs/playroutines/schema.md).
"""

import copy
import re

from unitracker.format.records import EXPR

NEXT = "next"
INS = "ins"
_PAIR = re.compile(r"^([A-Za-z_]\w*):([A-Za-z_]\w*)$")


def expand(pr: dict) -> dict:
    """A copy of playroutine ``pr`` with its conventions as explicit keys."""
    out = copy.deepcopy(pr)
    types = out.get("record_types") or {}
    for spec in types.values():
        _record_type(spec)
    if "instrument" not in out:
        ins = [n for n, s in types.items() if (s or {}).get("writes_as", n) == INS]
        if ins:
            out["instrument"] = {"type": ins[0], "base": 0}
    out.setdefault("note_offset", 0)
    ghost = out.get("ghost") or {}
    for scope in ("global", "voice"):
        if ghost.get(scope):
            ghost[scope] = _registers(ghost[scope], out.get(scope) or {})
    return out


def _record_type(spec: dict) -> None:
    """A record type is chained by its ``next`` field, ``none`` is 0 but for a ``fixed`` type,
    and its ``expr`` field references ``x``."""
    if "chain" not in spec and NEXT in (spec.get("fields") or ()):
        spec["chain"] = NEXT
    if "none" not in spec and not spec.get("fixed"):
        spec["none"] = 0
    if spec.get("expr") is not None:
        if not spec.get("refs"):
            spec["refs"] = {}
        spec["refs"].setdefault(spec["expr"], EXPR)


def _registers(regs: dict, cells: dict) -> dict:
    """Ghost registers with a word cell or a ``hi:lo`` pair at ``r`` given as ``r`` and ``r + 1``
    (low byte first) unless ``r + 1`` has its own entry."""
    out = {}
    for reg, text in regs.items():
        pair = _PAIR.match(str(text))
        width = (cells.get(text) or {}).get("width") if isinstance(text, str) else None
        if pair:
            parts = {reg: pair[2], reg + 1: pair[1]}
        elif width is not None and width > 8:
            parts = {reg: f"{text} & 0xFF", reg + 1: f"{text} >> 8"}
        else:
            parts = {reg: text}
        out.update((r, t) for r, t in parts.items() if r == reg or r not in regs)
    return out
