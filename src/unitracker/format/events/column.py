"""One-command columns: an event's commands that a player reads into one column.

A playroutine's ``commands`` entry may give the ``column`` its command is read into: a name, or a
list of names of which it takes the first the event leaves free (an event that names an instrument
fills :data:`INSTRUMENT`), else the last. :data:`COLUMNS` are the event slots' columns in slot
order. A column holds one command; an event with two in one column is refused, naming both.
"""

from unitracker.format.codec import command_variants
from unitracker.player.vm import event_commands

INSTRUMENT = "instrument"
COLUMNS = (INSTRUMENT, "fx")


class ColumnError(ValueError):
    """An event with two commands for a column that holds one."""


def column(spec, instrument: int) -> str:
    """The column a command of ``column`` spec ``spec`` is read into on an event naming
    ``instrument`` (0 for none)."""
    cols = spec if isinstance(spec, list) else [spec]
    return next((c for c in cols if not (instrument and c == INSTRUMENT)), cols[-1])


def check(pr: dict, phrases: dict) -> None:
    """Refuse an event of ``phrases`` (native command codes) with two commands in one of the
    playroutine's columns."""
    names, cols = {}, {}
    for code, var in command_variants(pr.get("commands")):
        names.setdefault(code, str(var.get("name", f"command {code}")))
        if "column" in var:
            cols.setdefault(code, var["column"])
    if not cols:
        return
    for phrase, events in phrases.items():
        for event in events:
            ins, seen = int(event.get("instrument") or 0), {}
            for c, _ in event_commands(event):
                if c in cols:
                    seen.setdefault(column(cols[c], ins), []).append(c)
            for cmds in seen.values():
                if len(cmds) > 1:
                    shown = " and ".join(f"!{names[c]}" for c in cmds)
                    raise ColumnError(
                        f"the player can't play {shown} on one event of phrase {phrase}:"
                        " they share a column that holds one command"
                    )
