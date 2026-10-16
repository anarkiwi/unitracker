"""Event atoms: ``note[.instrument][!cmd:arg[!cmd:arg]][*steps]``."""

import re

import numpy as np

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_NOTE_INDEX = {name: i for i, name in enumerate(NOTE_NAMES)}
_ATOM = re.compile(
    r"^(?P<note>-|off|on|(?P<pc>[A-G]#?)(?P<oct>\d+))"
    r"(?:\.(?P<ins>[A-Za-z_]\w*))?"
    r"(?P<cmds>(?:![a-z][a-z0-9]*:(?:@[A-Za-z_]\w*|none|\d+))*)"
    r"(?:\*(?P<steps>\d+))?$"
)
_COMMAND = re.compile(r"!(?P<cmd>[a-z][a-z0-9]*):(?P<arg>@[A-Za-z_]\w*|none|\d+)")
KINDS = {0: "-", 2: "off", 3: "on"}


class AtomError(ValueError):
    """An event atom does not follow the grammar."""


def note_name(note: int) -> str:
    """Scientific-style name of semitone index ``note`` (24 is C2)."""
    if note < 0:
        raise AtomError(f"note {note} is below C0")
    return f"{NOTE_NAMES[note % 12]}{note // 12}"


def note_index(name: str) -> int:
    """Semitone index of a note name."""
    match = _ATOM.match(name)
    if match is None or match["pc"] is None or match.end() != len(name):
        raise AtomError(f"not a note name: {name!r}")
    return 12 * int(match["oct"]) + _NOTE_INDEX[match["pc"]]


def format_atom(kind: int, note: int, steps: int, ins: str | None, commands=()) -> str:
    """The atom for an event lasting ``steps``; ``note`` is in the format's numbering.

    ``ins`` names the instrument and each of ``commands`` is ``name:arg`` as written.
    """
    text = note_name(note) if kind == 1 else KINDS[kind]
    if ins is not None:
        text += f".{ins}"
    for command in commands:
        text += f"!{command}"
    if steps > 1:
        text += f"*{steps}"
    return text


def parse_atom(atom) -> dict:
    """``{kind, note, ins, commands, steps}`` for an atom; ``commands`` is ``[(cmd, arg)]``.

    Names, ``@`` args and ``none`` stay text.
    """
    if isinstance(atom, bool):
        atom = "on" if atom else "off"
    if not isinstance(atom, str):
        raise AtomError(f"not an atom: {atom!r}")
    match = _ATOM.match(atom)
    if match is None:
        raise AtomError(f"bad atom {atom!r}")
    note = match["note"]
    if match["pc"] is not None:
        kind, pitch = 1, note_index(note)
    else:
        kind, pitch = {"-": 0, "off": 2, "on": 3}[note], 0
    commands = [
        (m["cmd"], m["arg"] if m["arg"][0] in "@n" else int(m["arg"]))
        for m in _COMMAND.finditer(match["cmds"])
    ]
    return {
        "kind": kind,
        "note": pitch,
        "ins": match["ins"],
        "commands": commands,
        "steps": int(match["steps"] or 1),
    }


def runs(rows: np.ndarray) -> list[tuple[int, int]]:
    """``(row index, steps)`` per event: an all-zero row after the first extends the event."""
    empty = ~rows.any(axis=1)
    starts = [0] + [i for i in range(1, len(rows)) if not empty[i]]
    ends = starts[1:] + [len(rows)]
    return [(s, e - s) for s, e in zip(starts, ends)]
