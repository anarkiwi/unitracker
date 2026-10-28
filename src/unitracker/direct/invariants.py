"""Lifting transliterates: each lifted object stands for a native one, so a document holds no
more objects of a kind than its tune's model has; :func:`check` refuses a lift that does."""

from unitracker_player.compiler import CONTROL, FILTER, PULSE
from unitracker_player.player import load

SLOTS = {CONTROL: "ctrl", PULSE: "pw", FILTER: "filter"}
KINDS = ("instruments", "ctrl", "pw", "filter", "sidtab", "phrases", "song")


class ModelError(ValueError):
    """A lifted document with more objects of a kind than its native tune."""


def lifted(text: str) -> dict:
    """The distinct objects of each kind the compiled document plays, each object once:
    instruments, programs by slot, phrases whatever their transpose, ``sidtab`` (instruments
    and control programs) and ``song``, each voice's phrase entries."""
    song = load(text)
    found = {kind: {} for kind in ("instruments", "phrases", *SLOTS.values())}
    entries = [0] * song.voices
    for row in song.timeline:
        for v, entry in enumerate(row):
            if entry[0] == "phrase":
                entries[v] += 1
                found["phrases"][id(entry[1])] = entry[1]
    todo = [(None, e) for e in song.init]
    todo += [(None, e) for events in found["phrases"].values() for e in events]
    while todo:
        slot, obj = todo.pop()
        if slot is not None:
            programs = found[SLOTS[slot]]
            if obj is None or id(obj) in programs:
                continue
            programs[id(obj)] = obj
            todo += [(None, ops) for _, _, ops in obj.rows]
            continue
        if not isinstance(obj, tuple):
            ins = obj.ins
            if ins is not None and id(ins) not in found["instruments"]:
                found["instruments"][id(ins)] = ins
                todo += [(CONTROL, ins.ctrl), (PULSE, ins.pw), (FILTER, ins.filter)]
            obj = obj.ops
        todo += [value for key, value in obj if key == "run" and value is not None]
    out = {kind: len(objs) for kind, objs in found.items()}
    return out | {"sidtab": out["instruments"] + out["ctrl"], "song": tuple(entries)}


def exceeded(have: dict, native: dict) -> str | None:
    """The first kind ``have`` holds more of than ``native`` (a kind it has no count of
    unchecked; ``song`` voice by voice), named with both counts; None where none is."""
    for kind in KINDS:
        bound = native.get(kind)
        if bound is None:
            continue
        pairs = zip(have[kind], bound) if kind == "song" else [(have[kind], bound)]
        for v, (count, most) in enumerate(pairs):
            if count > most:
                where = f" on voice {v + 1}" if kind == "song" else ""
                return f"{count} lifted {kind}{where} > {most} native"
    return None


def check(text: str, native: dict) -> None:
    """Raise :class:`ModelError` where document ``text`` has more objects of a kind than the
    ``native`` counts of its tune (:func:`exceeded`)."""
    message = exceeded(lifted(text), native)
    if message:
        raise ModelError(message)
