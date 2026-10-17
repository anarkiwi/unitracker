"""Event restarts: a note's ``restart`` program, played by one command on the steps before it.

With ``restart: {command: c}`` a note's ``!restart:@r`` is command ``c``, run as many steps
(ticks) before the note as the program's lookahead rows hold, with those rows as a program of
the command's record type; the program's last row, on the note's first tick, is empty.
"""

import numpy as np

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
