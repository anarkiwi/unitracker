"""What the format lifts share: note names, quoted meta values, bare rest runs and repeat
detection."""

import re

from unitracker_grammar.document import Event

NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
REST = Event("-")
_PLAIN = re.compile(r"[^\s,\[\]{}'\"#&*!|>%@`:?-][^,\[\]{}]*")
_YAML_WORDS = re.compile(r"(?i:y|n|yes|no|true|false|on|off|null|~)|[-+]?\d[\d_.:eE+-]*")


def note_name(k: int) -> str:
    """Generic note ``k`` (12 * octave + pitch class) by name."""
    return f"{NAMES[k % 12]}{k // 12}"


def meta_text(value: str) -> str:
    """A meta value as the document writes it: plain where YAML reads it back as that text,
    else single-quoted."""
    plain = _PLAIN.fullmatch(value) and not _YAML_WORDS.fullmatch(value)
    if plain and ": " not in value and " #" not in value and value == value.strip():
        return value
    return "'" + value.replace("'", "''") + "'"


def rests(events: list) -> list:
    """Consecutive bare rests as one ``-*k``."""
    out = []
    for event in events:
        last = out[-1] if out else None
        if event == REST and last is not None and Event("-", steps=last.steps) == last:
            out[-1] = Event("-", steps=(last.steps or 1) + 1)
        else:
            out.append(event)
    return out


def repeat(rows: list, gaps: list):
    """``(k, period)``: the earliest row from which the rows and their lengths repeat with the
    shortest period, seen at least twice; None where they do not."""
    keyed = list(zip(rows, gaps))
    n = len(keyed)
    for k in range(n):
        for period in range(1, (n - k) // 2 + 1):
            if all(keyed[i] == keyed[i + period] for i in range(k, n - period)):
                return k, period
    return None
