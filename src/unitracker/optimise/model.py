"""Views of the internal representation the edits change: programs, plays and runs."""

import copy

from unitracker.playroutine.records import EXPR, program_path
from unitracker.optimise.refs import entries, view

ERRORS = (ValueError, KeyError, IndexError, TypeError)


def copied(ir: dict) -> dict:
    """A copy whose records, expressions, tracks and phrases can change."""
    out = dict(ir)
    for key in ("records", "exprs", "tracks", "phrases"):
        out[key] = copy.deepcopy(ir.get(key) or {})
    return out


def is_note(event: dict) -> bool:
    """Whether ``event`` plays a note."""
    note = event.get("note")
    return isinstance(note, int) and not isinstance(note, bool)


def free_index(rows: dict, rtype, count: int) -> int | None:
    """The first of ``count`` unused indices after every used one, None past the type's max."""
    start = rtype.free(rows)
    return start if start + count - 1 <= min(rtype.max, 255) else None


def walk(ir: dict):
    """``(phrase, incoming instrument or None)`` per play, each voice twice through its loop."""
    for track in ir["tracks"]:
        current = None
        plays, loop = track["plays"], track.get("loop")
        again = range(loop, len(plays)) if loop is not None else range(0)
        for k in [*range(len(plays)), *again]:
            name = plays[k]["phrase"]
            for _ in range(int(plays[k].get("repeat", 1))):
                yield name, current
                for event in ir["phrases"][name]:
                    current = event.get("instrument") or current


def incoming(ir: dict) -> dict:
    """``{phrase: instruments}`` the voices hold entering each phrase they play."""
    out = {}
    for name, current in walk(ir):
        out.setdefault(name, set()).add(current)
    return out


def currents(events: list, start) -> list:
    """The instrument each event plays under, from ``start`` entering the phrase."""
    out = []
    for event in events:
        start = event.get("instrument") or start
        out.append(start)
    return out


def first_note(events) -> int:
    """The first note a run of events plays, 0 for none."""
    return next((int(e["note"]) for e in events if is_note(e)), 0)


def steps_of(events) -> int:
    """The steps a run of events lasts."""
    return sum(int(e.get("steps", 1)) for e in events)


def _frozen(value):
    if isinstance(value, dict):
        return tuple(sorted((k, _frozen(v)) for k, v in value.items()))
    return tuple(_frozen(v) for v in value) if isinstance(value, list) else value


def shape(events: list) -> tuple:
    """A run of events as a key, equal for runs alike up to a transpose."""
    base = first_note(events)
    return tuple(_frozen({**e, "note": e["note"] - base} if is_note(e) else e) for e in events)


def apart(places: list) -> list:
    """The places ``(phrase, start, size)`` that do not overlap an earlier one."""
    out, end = [], {}
    for name, k, size in sorted(places):
        if k >= end.get(name, 0):
            out.append((name, k, size))
            end[name] = k + size
    return out


def programs_of(ir: dict):
    """``(type, entry, row indices, loop)`` of every program something references."""
    v = view(ir)
    for tname, indices in entries(ir).items():
        rtype = v.rtypes[tname]
        if rtype.chain is not None and tname != EXPR:
            for entry in indices:
                yield (tname, entry, *program_path(rtype, ir["records"].get(tname, {}).get, entry))


def play_order(ir: dict):
    """A sort key putting phrases in the order the voices first play them."""
    first = {}
    for name, _ in walk(ir):
        first.setdefault(name, len(first))
    return lambda name: (first.get(name, len(first)), name)
