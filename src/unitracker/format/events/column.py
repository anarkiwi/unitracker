"""One-command columns: an event's commands that a player reads into one column.

A playroutine's ``exclusive`` is an expression over a command's ``code`` and the event's
``instrument`` (0 for none), true for a command read into a column that holds one command;
an event with two such commands is refused, naming both.
"""

from unitracker.format.codec import command_variants, evaluate
from unitracker.player.vm import event_commands


class ColumnError(ValueError):
    """An event with two commands for a column that holds one."""


def check(pr: dict, phrases: dict) -> None:
    """Refuse an event of ``phrases`` (native command codes) with two of the playroutine's
    ``exclusive`` commands."""
    expr = pr.get("exclusive")
    if expr is None:
        return
    names = {}
    for code, var in command_variants(pr.get("commands")):
        names.setdefault(code, str(var.get("name", f"command {code}")))
    for phrase, events in phrases.items():
        for event in events:
            ins = int(event.get("instrument") or 0)
            cmds = [
                c
                for c, _ in event_commands(event)
                if evaluate(expr, {"code": c, "instrument": ins})
            ]
            if len(cmds) > 1:
                shown = " and ".join(f"!{names.get(c, c)}" for c in cmds)
                raise ColumnError(
                    f"the player can't play {shown} on one event of phrase {phrase}:"
                    " they share a column that holds one command"
                )
