"""Event atoms: ``note[.instrument][!cmd:arg][*steps]``."""

import re

import numpy as np

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_NOTE_INDEX = {name: i for i, name in enumerate(NOTE_NAMES)}
_ATOM = re.compile(
    r"^(?P<note>-|off|on|(?P<pc>[A-G]#?)(?P<oct>\d+))"
    r"(?:\.(?P<ins>[A-Za-z_]\w*))?"
    r"(?:!(?P<cmd>\d+):(?P<arg>@[A-Za-z_]\w*|\d+))?"
    r"(?:\*(?P<steps>\d+))?$"
)
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


def format_atom(row, steps: int, ins: str | None, arg: str | None) -> str:
    """The atom for a phrase row ``(kind, note, instrument, cmd, arg)`` lasting ``steps``.

    ``ins`` names the instrument, and ``arg`` is the command argument as written.
    """
    kind, note, _, cmd, _ = (int(x) for x in row)
    text = note_name(note) if kind == 1 else KINDS[kind]
    if ins is not None:
        text += f".{ins}"
    if cmd or arg is not None:
        text += f"!{cmd}:{arg}"
    if steps > 1:
        text += f"*{steps}"
    return text


def parse_atom(atom) -> dict:
    """``{kind, note, ins, cmd, arg, steps}`` for an atom; ``ins`` and ``@`` args stay names."""
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
    arg = match["arg"]
    return {
        "kind": kind,
        "note": pitch,
        "ins": match["ins"],
        "cmd": int(match["cmd"] or 0),
        "arg": None if arg is None else (arg if arg.startswith("@") else int(arg)),
        "steps": int(match["steps"] or 1),
    }


def runs(rows: np.ndarray) -> list[tuple[int, int]]:
    """``(row index, steps)`` per event: an all-zero row after the first extends the event."""
    empty = ~rows.any(axis=1)
    starts = [0] + [i for i in range(1, len(rows)) if not empty[i]]
    ends = starts[1:] + [len(rows)]
    return [(s, e - s) for s, e in zip(starts, ends)]
