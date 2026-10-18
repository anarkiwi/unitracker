"""The playroutine schema's conventions: a definition with each key it leaves to one filled in.

Every default here gives way to an explicit key (docs/playroutines/schema.md).
"""

import ast
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
    for unit in (out.get("modulators") or {}).get("units") or []:
        _unit(unit, out.get("voice") or {}, out.get("global") or {})
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


def _unit(unit: dict, voice: dict, glob: dict) -> None:
    """A unit's ``x``, ``label`` and ``program`` from its ``site``, its ``scope`` from its cell,
    and its chip map's ``value`` and ``store`` from ``width`` and ``offset``."""
    site = unit.get("site") or {}
    if {"record", "index", "field"} <= site.keys():
        unit.setdefault("x", f"{site['record']}[{site['index']}].{site['field']}")
        unit.setdefault("label", [site["index"]])
        if unit.get("program") is not None:
            unit["program"].setdefault("record", site["record"])
            unit["program"].setdefault("pointer", site["index"])
    parts = str(unit.get("cell", "")).split(":")
    if "scope" not in unit and parts[0] not in voice and parts[0] in glob:
        unit["scope"] = "global"
    chip = unit.get("chip")
    if chip and "value" not in chip:
        chip.update(_chip(parts, int(chip["width"]), chip.get("offset"), {**glob, **voice}))


def _chip(parts: list, width: int, offset, cells: dict) -> dict:
    """``{value, store}`` of a chip map: the value is the cells less ``offset``, masked to
    ``width`` unless the cells fit it and there is no offset; a cell narrower than the chip holds
    its top bits, and ``store`` inverts ``value``."""
    off = None if offset is None else str(offset)
    if off is not None and not isinstance(
        ast.parse(off, mode="eval").body, (ast.Name, ast.Constant, ast.Subscript, ast.Call)
    ):
        off = f"({off})"
    shift, k = 0, int(parts[2]) if len(parts) > 2 else 8
    bits = int((cells.get(parts[0]) or {}).get("width") or width)
    if len(parts) == 1:
        shift = max(0, width - bits)
        joined = f"{parts[0]} << {shift}" if shift else parts[0]
    else:
        bits += k
        joined = f"(({parts[0]} << {k}) | ({parts[1]} & {_hex(k)}))"
    value = joined if off is None else f"({joined} - {off})"
    if bits > width or off is not None:
        value = f"{value} & {_hex(width)}"
    back = "value" if off is None else f"value + {off}"
    if len(parts) == 1:
        return {"value": value, "store": {parts[0]: f"{_par(back)} >> {shift}" if shift else back}}
    hi = f"({_par(back)} >> {k}) & {_hex(width - k)}"
    return {"value": value, "store": {parts[0]: hi, parts[1]: f"{_par(back)} & {_hex(k)}"}}


def _hex(bits: int) -> str:
    return f"0x{(1 << bits) - 1:X}"


def _par(text: str) -> str:
    return text if text.isidentifier() else f"({text})"
