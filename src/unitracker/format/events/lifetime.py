"""Program lifetime: a note's ``!pw`` or ``!filter``, that it does not restart that program.

A playroutine declares ``lifetime: {slot: {field: f, flag: {g: mask}}}``, or the list of slots
whose entries are empty: ``f`` is the instrument field naming the program (default: the slot), and
``flag`` the header bit that keeps it (docs/format/vocabulary.md).
"""

import numpy as np

from unitracker.format.codec import command_code
from unitracker.format.records import program_path, ref_type
from unitracker.player.vm import event_commands

NAME = "lifetime"
NAMES = ("pw", "filter")


class LifetimeError(ValueError):
    """A lifetime command the player can't play where it is written."""


def is_lifetime(name: str, arg) -> bool:
    """Whether written command ``name`` with argument ``arg`` is a lifetime command."""
    return name in NAMES and arg is None


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
        specs = dict.fromkeys(specs) if isinstance(specs, list) else specs
        self.specs = {k: {"field": k, **(v or {})} for k, v in specs.items()}
        self.flagged = {n: _flag(s) for n, s in self.specs.items() if "flag" in s}
        self.legato = command_code(pr.get("commands"), "legato")

    def target(self, name: str) -> str:
        """The record type of the program command ``name`` keeps."""
        return self.rtype.refs[self.specs[name]["field"]][0][0]

    def mask(self, commands, kind: int, refs: set) -> int:
        """The mask of an event's lifetime commands among ``commands`` (``(name, arg)``; a
        lifetime command has no argument), for an event of note ``kind`` whose other commands
        reference record types ``refs``."""
        out = 0
        for cmd, arg in commands:
            if cmd not in NAMES or arg is not None:
                continue
            if cmd not in self.specs:
                raise LifetimeError(f"the player can't express !{cmd}")
            if kind != 1:
                raise LifetimeError(f"!{cmd} needs a note")
            if self.target(cmd) in refs:
                raise LifetimeError(f"!{cmd} on a note that starts the program")
            out |= bit(cmd)
        return out

    def starts(self, records: dict, index: int) -> set:
        """The record types instrument ``index`` starts: those its fields name, and every type a
        row of its programs references."""
        row = records.get(self.ins_type, {}).get(index, {})
        out = set()
        for field in self.rtype.refs:
            target = ref_type(self.rtype, field, row)
            value = int(row.get(field, self.rtype.default(field)))
            if target is None or value == self.rtypes[target].none:
                continue
            out.add(target)
            ttype, rows = self.rtypes[target], records.get(target, {})
            for i in program_path(ttype, rows.get, value)[0] if ttype.chain is not None else ():
                out |= {ref_type(ttype, f, rows[i]) for f in ttype.refs} - {None}
        return out

    def _flagged(self, tracks, phrases: dict, rows: dict):
        """``(phrase, index, instrument, command)`` for each note that starts instrument row
        ``rows[instrument]`` without selecting it, per flagged command."""
        for name, k, ins, selects in notes(tracks, phrases, self.base, self.legato):
            if not selects and ins in rows:
                for cmd in self.flagged:
                    yield name, k, ins, cmd

    def _keeps(self, row: dict, cmd: str) -> bool:
        field, mask = self.flagged[cmd]
        return bool(int(row.get(field, self.rtype.default(field))) & mask)

    def written(self, tracks, phrases: dict, rows: dict) -> tuple[dict, dict]:
        """``({(phrase, index): mask}, rows)``: the commands each note is written with, and the
        instrument rows without the flag bits they replace."""
        seen = {}
        for name, k, ins, cmd in self._flagged(tracks, phrases, rows):
            seen.setdefault((name, cmd, k), set()).add(self._keeps(rows[ins], cmd))
        marks = {}
        for (name, cmd, k), keep in _one(seen, _MIXED).items():
            marks[(name, k)] = marks.get((name, k), 0) | bit(cmd) * keep
        out = {index: dict(row) for index, row in rows.items()}
        for row in out.values():
            for field, mask in self.flagged.values():
                if field in row:
                    row[field] = int(row[field]) & ~mask | self.rtype.default(field) & mask
        return marks, out

    def _check(self, mask: int, ins: int, selects: bool, records: dict, label: dict) -> None:
        """Raise unless a note of instrument ``ins`` plays the commands of ``mask``."""
        for cmd in names(mask):
            if cmd in self.flagged and selects:
                raise LifetimeError(
                    f"!{cmd} on a note that selects an instrument, which restarts the {cmd}"
                    " program"
                )
            if cmd not in self.flagged and ins and self.target(cmd) in self.starts(records, ins):
                raise LifetimeError(
                    f"!{cmd} on a note whose instrument {label[ins]} starts the {cmd} program"
                )

    def read(self, tracks, phrases: dict, records: dict, labels: dict) -> None:
        """Check and remove each event's ``keep`` mask, and set the instruments' flag bits;
        ``labels`` maps each name to its ``(type, index)``."""
        marks = _marks(phrases)
        rows = records.get(self.ins_type, {})
        label = {v[1]: k for k, v in labels.items() if v[0] == self.ins_type}
        for name, k, ins, selects in notes(tracks, phrases, self.base, self.legato):
            self._check(marks.get((name, k), 0), ins, selects, records, label)
        seen = {}
        for name, k, ins, cmd in self._flagged(tracks, phrases, rows):
            seen.setdefault((cmd, label[ins], ins), set()).add(
                bool(marks.get((name, k), 0) & bit(cmd))
            )
        for (cmd, _, ins), keep in _one(dict(sorted(seen.items())), _DISAGREE).items():
            self._set(rows[ins], cmd, keep)

    def _set(self, row: dict, cmd: str, keep: bool) -> None:
        """Set or clear the flag bit of ``cmd`` in instrument ``row``."""
        if keep != self._keeps(row, cmd):
            field, mask = self.flagged[cmd]
            row[field] = int(row.get(field, self.rtype.default(field))) ^ mask


def _marks(phrases: dict) -> dict:
    """``{(phrase, index): mask}``, each event's ``keep`` removed."""
    return {
        (name, k): event.pop("keep")
        for name, events in phrases.items()
        for k, event in enumerate(events)
        if "keep" in event
    }


_MIXED = "a note of phrase {} plays under instruments that keep and restart the {} program"
_DISAGREE = "!{} is on some notes that start instrument {} without selecting it and not on others"
