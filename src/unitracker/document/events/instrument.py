"""Event instruments: one record naming the programs an event's native columns arm.

With ``instrument: {type: t, base: b, event: {type: ins, column: k, commands: {c: f}, restart: r}}``
an event's instrument column (record ``t`` at index value - ``b``) is field ``k`` of an ``ins``
record, the argument of its command ``c`` field ``f``, and its note's restart (played by command
``r``, see ``restart``) field ``restart``; the event names that record instead.
"""

import numpy as np

from unitracker.document.events import restart

NAME = "event"
SLOTS = (3, 5)


class InstrumentError(ValueError):
    """An event's columns, or an instrument, that the other form can't hold."""


def spec(pr: dict) -> dict | None:
    """The playroutine's event instrument, or None when its events name their instrument."""
    return (pr.get("instrument") or {}).get(NAME)


def document(pr: dict) -> tuple[str | None, int]:
    """``(record type, base)`` of the instrument a document's event names."""
    inst = pr.get("instrument") or {}
    if spec(pr):
        return spec(pr)["type"], 0
    return inst.get("type"), int(inst.get("base", 0))


def written_as(pr: dict, code: int) -> str | None:
    """``type.field``: the instrument key command ``code`` is written as, or None."""
    ins = spec(pr) or {"commands": {}}
    field = {int(c): f for c, f in ins["commands"].items()}.get(code)
    return None if field is None else f"{ins['type']}.{field}"


def fold(arr: np.ndarray, ins: dict, base: int, entry) -> np.ndarray:
    """A compiled phrase with each event's instrument column and commands ``ins`` holds moved
    into one instrument; ``entry(fields)`` is the instrument's index for the fields it holds."""
    out = arr.copy()
    commands = {int(c): str(f) for c, f in ins["commands"].items()}
    commands.update({restart.CODE: restart.NAME} if ins.get(restart.NAME) is not None else {})
    for row in out:
        held = {ins["column"]: int(row[2]) - base} if row[2] else {}
        for slot in SLOTS:
            field = commands.get(int(row[slot]))
            if field is None:
                continue
            if field in held:
                raise InstrumentError(f"an event arms the instrument's {field} twice")
            held[field] = int(row[slot + 1])
            row[slot : slot + 2] = 0
        if held:
            row[2] = entry(held)
    return out
