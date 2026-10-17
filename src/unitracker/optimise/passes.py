"""The passes: each lists the candidate edits of a song, in a deterministic order."""

from unitracker.optimise.edits import hoist_value, repeats
from unitracker.optimise.model import (
    apart,
    entries,
    is_note,
    play_order,
    programs_of,
    shape,
    slots,
    steps_of,
    view,
    walk,
)
from unitracker.records import EXPR


def _pairs(indices):
    return [(a, b) for i, a in enumerate(indices) for b in indices[i + 1 :]]


def _merges(ir: dict, chosen) -> list:
    v = view(ir)
    out = []
    for tname, indices in entries(ir).items():
        if chosen(v, tname):
            pin = set(v.rtypes[tname].pin)
            out += [("merge", (tname, a, b)) for a, b in _pairs(indices) if b not in pin]
    return out


def instruments(ir: dict) -> list:
    """Merge two instruments; play one as another with the command that sets the field they
    differ in."""
    used = entries(ir).get(view(ir).ins, [])
    return _merges(ir, lambda v, t: t == v.ins) + [
        ("unhoist", (a, b)) for a in used for b in used if a != b
    ]


def programs(ir: dict) -> list:
    """Merge two programs; cut a program short; write a loop with its shortest cycle."""
    out = _merges(ir, lambda v, t: t not in (v.ins, EXPR) and v.rtypes[t].chain is not None)
    for tname, entry, path, _ in programs_of(ir):
        if entry not in view(ir).rtypes[tname].pin:
            out.append(("reloop", (tname, entry)))
            out += [("truncate", (tname, entry, n)) for n in range(1, len(path) + 1)]
    return out


def expressions(ir: dict) -> list:
    """Merge two expressions."""
    return _merges(ir, lambda v, t: t == EXPR)


def hoists(ir: dict) -> list:
    """Move a command on an instrument's notes into a copy of the instrument."""
    found = set()
    for name, current in walk(ir):
        for event in ir["phrases"][name]:
            current = event.get("instrument") or current
            if current is None or not is_note(event):
                continue
            for code, arg in slots(event):
                if code and hoist_value(ir, current, code, arg) is not None:
                    found.add((current, code, arg))
    return [("hoist", t) for t in sorted(found)]


def phrases(ir: dict) -> list:
    """Play one phrase for another; write a repeating phrase as plays of its part."""
    played = sorted({p["phrase"] for t in ir["tracks"] for p in t["plays"]}, key=play_order(ir))
    steps = {name: steps_of(ir["phrases"][name]) for name in played}
    out = [("alias", (a, b)) for a, b in _pairs(played) if steps[a] == steps[b]]
    for name in played:
        events = ir["phrases"][name]
        out += [("factor", (name, n)) for n in range(1, len(events)) if repeats(events, n)]
    return out + [("share", (key,)) for key in shared_runs(ir)]


def shared_runs(ir: dict) -> list:
    """The shapes of runs of two or more events that recur apart in the played phrases, each
    extending to no one longer shape at every place it occurs."""
    played = sorted({p["phrase"] for t in ir["tracks"] for p in t["plays"]}, key=play_order(ir))
    places = {}
    for name in played:
        events = ir["phrases"][name]
        for size in range(2, len(events) + 1):
            for k in range(len(events) - size + 1):
                places.setdefault(shape(events[k : k + size]), []).append((name, k, size))
    out = []
    for key, where in places.items():
        spots = apart(where)
        if len(spots) < 2:
            continue
        sides = [{_extended(ir, p, d) for p in spots} for d in ((-1, 0), (0, 1))]
        if not any(len(s) == 1 and None not in s for s in sides):
            out.append(key)
    return out


def _extended(ir: dict, place: tuple, by: tuple):
    name, k, size = place
    events = ir["phrases"][name]
    a, b = k + by[0], k + size + by[1]
    return shape(events[a:b]) if a >= 0 and b <= len(events) else None


def commands(ir: dict) -> list:
    """Drop an event's command, its kept programs, or its instrument."""
    out = []
    for name in sorted(ir["phrases"], key=play_order(ir)):
        for k, event in enumerate(ir["phrases"][name]):
            out += [("drop_command", (name, k, s)) for s, c in enumerate(slots(event)) if c[0]]
            out += [("drop_keep", (name, k))] if event.get("keep") else []
            out += [("drop_instrument", (name, k))] if event.get("instrument") else []
    return out


def fields(ir: dict) -> list:
    """Set a record's field to its default."""
    v = view(ir)
    out = []
    for tname, rows in sorted((ir.get("records") or {}).items()):
        rtype = v.rtypes[tname]
        for index, row in sorted(rows.items()):
            out += [
                ("clear", (tname, index, f))
                for f in sorted(row)
                if f != rtype.chain and row[f] != rtype.default(f)
            ]
    return out


LARGER_UNPROVED = frozenset({"factor", "share"})


PASSES = {
    "hoist": hoists,
    "instruments": instruments,
    "programs": programs,
    "expressions": expressions,
    "phrases": phrases,
    "commands": commands,
    "fields": fields,
}
