"""Program lifetime: a note's ``!pw`` or ``!filter``, that it does not restart that program.

A playroutine declares ``lifetime: {slot: {field: f, flag: {g: mask}}}``: ``f`` is the instrument
field naming the program (default: the slot), and ``flag`` the header bit that keeps it
(docs/playroutines/schema.md, *Lifetime*).
"""

import numpy as np

from unitracker.playroutine.codec import command_code
from unitracker.player.vm import event_commands

NAME = "lifetime"
NAMES = ("pw", "filter")


class LifetimeError(ValueError):
    """A lifetime command the player can't play where it is written."""


def bit(name: str) -> int:
    """The bit of command ``name`` in an event's mask."""
    return 1 << NAMES.index(name)


def names(mask: int) -> list[str]:
    """The commands of an event's mask, in writing order."""
    return [n for n in NAMES if mask & bit(n)]


def column(marks: dict, name: str, events: list, arr: np.ndarray) -> np.ndarray:
    """Compiled phrase ``arr`` of ``events`` with a last column of each step's mask in ``marks``."""
    steps = np.cumsum([0] + [int(e.get("steps", 1)) for e in events[:-1]])
    out = np.zeros(len(arr), dtype=arr.dtype)
    out[steps] = [marks.get((name, k), 0) for k in range(len(events))]
    return np.column_stack([arr, out])


def starts(event: dict, legato) -> bool:
    """Whether ``event`` starts its note: a note without the ``legato`` command."""
    note = event.get("note")
    if not isinstance(note, int) or isinstance(note, bool):
        return False
    return legato is None or all(code != legato for code, _ in event_commands(event))


def order(track: dict) -> list[tuple[int, str]]:
    """``(play, phrase)`` for each phrase a voice plays, repeats written out, twice through its
    loop; ``play`` is the index of the play that plays it."""
    plays, loop = track["plays"], track.get("loop")
    again = range(loop, len(plays)) if loop is not None else range(0)
    return [
        (k, plays[k]["phrase"])
        for k in [*range(len(plays)), *again]
        for _ in range(int(plays[k].get("repeat", 1)))
    ]


def selected(steps):
    """``(key, event, head)`` for each ``(key, event)`` a voice plays, in order: ``head`` is the
    key of the event that names the instrument it plays, its own or the voice's last before it,
    None for none."""
    head = None
    for key, event in steps:
        if event.get("instrument"):
            head = key
        yield key, event, head


def notes(tracks, phrases: dict, base: int, legato):
    """``(phrase, index, instrument, selects)`` for each note that starts an instrument, per voice
    twice through its loop; the instrument is the one ``selected`` names, 0 for none."""
    for trk in tracks:
        steps = (((name, k), e) for _, name in order(trk) for k, e in enumerate(phrases[name]))
        for (name, k), event, head in selected(steps):
            if starts(event, legato):
                ins = 0 if head is None else int(phrases[head[0]][head[1]]["instrument"]) - base
                yield name, k, ins, bool(event.get("instrument"))


def _flag(spec: dict) -> tuple[str, int]:
    ((field, mask),) = spec["flag"].items()
    return str(field), int(mask)


def _one(seen: dict, problem: str) -> dict:
    """``{key: value}`` of ``{key: set of values}``, raising ``problem`` (formatted with the key)
    for a key with more than one."""
    for key, values in seen.items():
        if len(values) > 1:
            raise LifetimeError(problem.format(*key))
    return {key: values.pop() for key, values in seen.items()}


class Lifetime:
    """A playroutine's lifetime commands over a song's records of types ``rtypes``."""

    def __init__(self, pr: dict, rtypes: dict):
        inst = pr.get("instrument") or {}
        self.ins_type, self.base = inst.get("type"), int(inst.get("base", 0))
        self.rtypes, self.rtype = rtypes, rtypes.get(self.ins_type)
        specs = pr.get(NAME) or {}
        if not isinstance(specs, dict) or not all("flag" in (v or {}) for v in specs.values()):
            raise LifetimeError(f"{NAME}: each entry is {{field, flag}}, with a flag")
        self.specs = {k: {"field": k, **v} for k, v in specs.items()}
        self.flagged = {n: _flag(s) for n, s in self.specs.items()}
        self.legato = command_code(pr.get("commands"), "legato")

    def _flagged(self, tracks, phrases: dict, rows: dict):
        """``(phrase, index, instrument, command)`` for each note that starts instrument row
        ``rows[instrument]`` without selecting it, per flagged command."""
        for name, k, ins, selects in notes(tracks, phrases, self.base, self.legato):
            if not selects and ins in rows:
                for cmd in self.flagged:
                    yield name, k, ins, cmd

    def keeps(self, row: dict, cmd: str) -> bool:
        """Whether instrument ``row`` keeps the program of lifetime command ``cmd``."""
        field, mask = self.flagged[cmd]
        return bool(int(row.get(field, self.rtype.default(field))) & mask)

    def written(self, tracks, phrases: dict, rows: dict) -> tuple[dict, dict]:
        """``({(phrase, index): mask}, rows)``: the commands each note is written with, and the
        instrument rows without the flag bits they replace."""
        seen = {}
        for name, k, ins, cmd in self._flagged(tracks, phrases, rows):
            seen.setdefault((name, cmd, k), set()).add(self.keeps(rows[ins], cmd))
        marks = {}
        for (name, cmd, k), keep in _one(seen, _MIXED).items():
            marks[(name, k)] = marks.get((name, k), 0) | bit(cmd) * keep
        return marks, self.unflagged(rows)

    def unflagged(self, rows: dict, keep=()) -> dict:
        """Instrument ``rows`` with the flag bits at their defaults, but for the indices of
        ``keep``."""
        out = {index: dict(row) for index, row in rows.items()}
        for index, row in out.items():
            for field, mask in self.flagged.values():
                if field in row and index not in keep:
                    row[field] = int(row[field]) & ~mask | self.rtype.default(field) & mask
        return out


_MIXED = "a note of phrase {} plays under instruments that keep and restart the {} program"
