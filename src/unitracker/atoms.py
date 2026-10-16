"""Event atoms: ``note[.instrument][!command[:arg]][*steps]``."""

import re

import yaml

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_NOTE_INDEX = {name: i for i, name in enumerate(NOTE_NAMES)}
_NOTE = re.compile(r"^(?P<pc>[A-G]#?)(?P<oct>-?\d+)$")
_ATOM = re.compile(
    r"^(?P<note>-|off|on|[A-G]#?-?\d+)"
    r"(?:\.(?P<ins>[A-Za-z_]\w*))?"
    r"(?:!(?P<cmd>[a-z]\w*)(?::(?P<arg>.+?))?)?"
    r"(?:\*(?P<steps>\d+))?$"
)
_PAIR = re.compile(r"^(-?\d+),(-?\d+)$")
KINDS = {0: "-", 2: "off", 3: "on"}


class AtomError(ValueError):
    """An event atom does not follow the grammar."""


def note_name(note: int) -> str:
    """Scientific-style name of shared semitone index ``note`` (24 is C2, -1 is B-1)."""
    return f"{NOTE_NAMES[note % 12]}{note // 12}"


def note_index(name: str) -> int:
    """Shared semitone index of a note name."""
    match = _NOTE.match(name)
    if match is None:
        raise AtomError(f"not a note name: {name!r}")
    return 12 * int(match["oct"]) + _NOTE_INDEX[match["pc"]]


def format_atom(parts) -> str:
    """The atom for ``(kind, note, instrument, command, arg, steps)``; ``arg`` is text."""
    kind, note, ins, cmd, arg, steps = parts
    text = note_name(note) if kind == 1 else KINDS[kind]
    if ins is not None:
        text += f".{ins}"
    if cmd is not None:
        text += f"!{cmd}" + ("" if arg is None else f":{arg}")
    if steps > 1:
        text += f"*{steps}"
    return text


def _arg(text):
    """A command argument: None, an int, a pair, ``off``, an ``@`` name or a spec mapping."""
    if text is None:
        return None
    if text == "off" or text.startswith("@"):
        return text
    pair = _PAIR.match(text)
    if pair:
        return [int(pair[1]), int(pair[2])]
    if text.startswith("{"):
        try:
            value = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise AtomError(f"bad spec argument {text!r}") from exc
        if not isinstance(value, dict):
            raise AtomError(f"bad spec argument {text!r}")
        return value
    try:
        return int(text)
    except ValueError as exc:
        raise AtomError(f"bad command argument {text!r}") from exc


def parse_atom(atom) -> dict:
    """``{kind, note, ins, cmd, arg, steps}`` for an atom; names and specs stay unresolved."""
    if isinstance(atom, bool):
        atom = "on" if atom else "off"
    if not isinstance(atom, str):
        raise AtomError(f"not an atom: {atom!r}")
    match = _ATOM.match(atom.strip())
    if match is None:
        raise AtomError(f"bad atom {atom!r}")
    note = match["note"]
    if note in ("-", "off", "on"):
        kind, pitch = {"-": 0, "off": 2, "on": 3}[note], 0
    else:
        kind, pitch = 1, note_index(note)
    return {
        "kind": kind,
        "note": pitch,
        "ins": match["ins"],
        "cmd": match["cmd"],
        "arg": _arg(match["arg"]),
        "steps": int(match["steps"] or 1),
    }
