"""Lifting transliterates: each lifted object stands for a native one, so a document holds no
more objects of a kind than its tune's model has; :func:`check` refuses a lift that does."""

from unitracker_grammar.document import Group, read
from unitracker_player.compiler import CONTROL, FILTER, PULSE
from unitracker_player.player import load

SLOTS = {CONTROL: "ctrl", PULSE: "pw", FILTER: "filter"}
KINDS = ("instruments", "ctrl", "ctrl_layers", "pw", "filter", "sidtab", "phrases", "song")


class ModelError(ValueError):
    """A lifted document with more objects of a kind than its native tune."""


def lifted(text: str) -> dict:
    """The distinct objects of each kind the compiled document plays, each object once:
    instruments, programs by kind and per control layer (``ctrl_layers``), phrases whatever
    their transpose, ``sidtab`` (instruments and control programs) and ``song``, each voice's
    phrase entries (an orderlist's as written, a group's once)."""
    song = load(text)
    plays = [e for row in song.timeline for e in row]
    plays += [e for order in song.orders or () for e in order[0]]
    phrases = {id(e[1]): e[1] for e in plays if e[0] == "phrase"}
    instruments, slots = _played(song, phrases.values())
    out = {"instruments": len(instruments), "phrases": len(phrases)}
    for kind, name in SLOTS.items():
        out[name] = len({k for s, own in enumerate(slots) if song.kinds[s] == kind for k in own})
    out["ctrl_layers"] = tuple(len(own) for s, own in enumerate(slots) if song.kinds[s] == CONTROL)
    entries = [sum(e[0] == "phrase" for e in voice) for voice in zip(*song.timeline)]
    if song.orders is not None:
        entries = [_written(items) for items in read(text).rows]
    return out | {"sidtab": out["instruments"] + out["ctrl"], "song": tuple(entries)}


def _played(song, phrases) -> tuple:
    """``(instruments, slots)``: the instruments the init and ``phrases`` play and per program
    slot the programs they and their rows start, each ``{id: object}``."""
    instruments, slots = {}, [{} for _ in song.kinds]
    todo = [(None, e) for e in song.init] + [(None, e) for events in phrases for e in events]
    while todo:
        slot, obj = todo.pop()
        if slot is not None:
            if obj is None or id(obj) in slots[slot]:
                continue
            slots[slot][id(obj)] = obj
            todo += [(None, ops) for _, _, ops in obj.rows]
            continue
        if not isinstance(obj, tuple):
            ins = obj.ins
            if ins is not None and id(ins) not in instruments:
                instruments[id(ins)] = ins
                todo += [(CONTROL, ins.ctrl), (PULSE, ins.pw), (FILTER, ins.filter)]
                todo += list(ins.layers)
            obj = obj.ops
        todo += [value for key, value in obj if key == "run" and value is not None]
    return instruments, slots


def _written(voice) -> int:
    """The play entries an orderlist writes, a group's body and endings once."""
    items = voice.play if hasattr(voice, "play") else voice
    return sum(
        _written(i.body) + sum(map(_written, i.endings)) if isinstance(i, Group) else 1
        for i in items
    )


def exceeded(have: dict, native: dict) -> str | None:
    """The first kind ``have`` holds more of than ``native`` (a kind it has no count of
    unchecked; ``song`` voice by voice, ``ctrl_layers`` layer by layer), named with both
    counts; None where none is."""
    for kind in KINDS:
        bound = native.get(kind)
        if bound is None:
            continue
        each = isinstance(bound, tuple)
        pairs = zip(have[kind], bound) if each else [(have[kind], bound)]
        for v, (count, most) in enumerate(pairs):
            if count > most:
                where = f" on {'voice' if kind == 'song' else 'layer'} {v + 1}" if each else ""
                return f"{count} lifted {kind}{where} > {most} native"
    return None


def check(text: str, native: dict) -> None:
    """Raise :class:`ModelError` where document ``text`` has more objects of a kind than the
    ``native`` counts of its tune (:func:`exceeded`)."""
    message = exceeded(lifted(text), native)
    if message:
        raise ModelError(message)
