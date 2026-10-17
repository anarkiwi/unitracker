"""Event restarts: a note's ``restart`` program, played by one command on the steps before it.

With ``restart: {command: c}`` a note's ``!restart:@r`` is command ``c``, run as many steps
(ticks) before the note as the program's lookahead rows hold, with those rows as a program of
the command's record type; the program's last row, on the note's first tick, is empty.
"""

import numpy as np

from unitracker.format.codec import CodecError, row_place
from unitracker.format.records import chained, program_path

CODE = -1
NAME = "restart"


class RestartError(ValueError):
    """An event restart that the steps before its note can't play."""


def fold(arr: np.ndarray, code: int, restart_of) -> np.ndarray:
    """A compiled phrase with each foldable command ``code`` moved onto the note it precedes.

    The command must be alone on its step, the steps up to the note empty, and the note have a
    free command slot; ``restart_of(arg, gap)`` gives the restart index or None.
    """
    out = arr.copy()
    filled = np.flatnonzero(arr.any(axis=1))
    for prev, note in zip(filled[:-1], filled[1:]):
        step, row = arr[prev], out[note]
        alone = not (step[:3].any() or step[5:].any()) and step[3] == code
        slot = 3 if not row[3:5].any() else 5 if not row[5:7].any() else None
        if row[0] != 1 or not alone or slot is None:
            continue
        index = restart_of(int(step[4]), int(note - prev))
        if index is not None:
            out[prev] = 0
            row[slot : slot + 2] = (CODE, index)
    return out


def restart_rows(rows: list, target, rtype, codec, gap: int) -> list | None:
    """The raw rows of the restart a program's ``rows`` (of type ``target``) make ``gap`` steps
    before a note, or None when the program sets another field or doesn't fit."""
    fields = rtype.own
    other = [f for f in target.own if f not in fields]
    if any(r[f] != target.default(f) for r in rows for f in other):
        return None
    rows = [{f: r[f] for f in fields} for r in rows]
    rows[-1].update({f: target.default(f) for f in target.end})
    try:
        shared = [codec.encode(r, row_place(k, len(rows) + 1, True)) for k, r in enumerate(rows)]
        if None in shared:
            return None
        rest = gap - sum(s.get("hold", 1) for s in shared[:-1])
        if rest < 1:
            return None
        shared[-1] = {k: v for k, v in shared[-1].items() if k != "hold"}
        shared[-1].update({"hold": rest} if rest != 1 else {})
        shared.append({})
        return [codec.decode(s, row_place(k, len(shared), True)) for k, s in enumerate(shared)]
    except CodecError:
        return None


def entry_of(raws: list, rtype, table: dict, entries: dict) -> int:
    """The entry index in ``table`` of the restart of raw rows ``raws``, allocated on first use
    and remembered in ``entries``."""
    key = tuple(tuple(sorted(r.items())) for r in raws)
    if key not in entries:
        entries[key] = start = rtype.free(table)
        table.update(chained(rtype, raws, start))
    return entries[key]


def lookahead(path: list, target, codec) -> tuple[list, int]:
    """``(rows, steps)``: a restart's raw ``path`` as the rows of the program it runs, the last
    one ending it, and the steps their holds put it before the note."""
    ahead = path[:-1]
    if not ahead:
        raise RestartError("a restart needs a row before its note")
    holds = [
        codec.encode(r, row_place(k, len(path), True)).get("hold", 1) for k, r in enumerate(ahead)
    ]
    rows = [dict(r) for r in ahead]
    rows[-1].update(target.end)
    return rows, sum(holds)


def played(index: int, records: dict, types: tuple, codec, alloc) -> tuple[int, int]:
    """``(entry, steps)`` of restart ``index``: the program of its lookahead rows, allocated in
    ``records`` by ``alloc(target, rows)``, and its steps before the note; ``types`` is
    ``(restart type, target type)``."""
    rtype, target = types
    rows = records[rtype.name]
    path, loop = program_path(rtype, rows.get, index)
    if loop is not None:
        raise RestartError("a restart's program can't loop")
    ahead, steps = lookahead([rtype.body(rows[i]) for i in path], target, codec)
    start = alloc(target, len(ahead))
    records[target.name].update(chained(target, ahead, start))
    return start, steps


def unfold(events: list, code: int, program) -> list:
    """Phrase events with each note's ``restart`` played as command ``code`` before it.

    ``program(index)`` is ``(entry, gap)``: the command's argument and its steps before the note.
    """
    out = []
    for event in events:
        if "restart" not in event:
            out.append(event)
            continue
        event = dict(event)
        entry, gap = program(event.pop("restart"))
        prev = out[-1] if out else {}
        steps = int(prev.get("steps", 1))
        run = {"command": [code, entry], **({"steps": gap} if gap > 1 else {})}
        if not isinstance(event.get("note"), int):
            raise RestartError("a restart needs a note")
        if steps > gap:
            prev["steps"] = steps - gap
            if prev["steps"] == 1:
                del prev["steps"]
            out.append(run)
        elif steps == gap and set(prev) <= {"steps"} and out:
            out[-1] = run
        else:
            raise RestartError(f"a restart needs {gap} empty steps before its note")
        out.append(event)
    return out
