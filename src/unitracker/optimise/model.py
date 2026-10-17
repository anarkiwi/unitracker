"""Views of the internal representation the edits change: references, programs, plays."""

import copy

from unitracker.format import songio
from unitracker.format.codec import CommandCodec
from unitracker.format.records import EXPR, record_types, ref_type
from unitracker.player.vm import EVENT_COLUMNS, event_commands

ERRORS = (ValueError, KeyError, IndexError, TypeError)


class View:
    """A song's record types, instrument type and command codes."""

    # pylint: disable=too-few-public-methods

    def __init__(self, pr: dict):
        self.rtypes = record_types(pr)
        inst = pr.get("instrument") or {}
        self.ins = inst.get("type")
        self.base = int(inst.get("base", 0))
        self.commands = CommandCodec(pr.get("commands"), self.rtypes)
        self.nibbles = {
            int(code): (str(v["name"]), int(v["nibble"]))
            for code, variants in (pr.get("commands") or {}).items()
            for v in (variants if isinstance(variants, list) else [variants])
            if "nibble" in v
        }


_VIEWS = {}


def view(ir: dict) -> View:
    """The song's :class:`View`, cached per playroutine."""
    pr = ir["playroutine"]
    if id(pr) not in _VIEWS:
        _VIEWS[id(pr)] = (pr, View(pr))
    return _VIEWS[id(pr)][1]


def copied(ir: dict) -> dict:
    """A copy whose records, expressions, tracks and phrases can change."""
    out = dict(ir)
    for key in ("records", "exprs", "tracks", "phrases"):
        out[key] = copy.deepcopy(ir.get(key) or {})
    return out


def slots(event: dict) -> list:
    """An event's two command slots as ``[code, arg]`` lists, empty slots ``[0, 0]``."""
    cmds = [list(c) for c in event_commands(event)]
    return cmds + [[0, 0]] * ((EVENT_COLUMNS - 3) // 2 - len(cmds))


def put_slots(event: dict, cmds: list) -> None:
    """Store command slots, dropping trailing empty ones."""
    event.pop("command", None)
    event.pop("commands", None)
    while cmds and cmds[-1] == [0, 0]:
        cmds = cmds[:-1]
    if len(cmds) == 1:
        event["command"] = cmds[0]
    elif cmds:
        event["commands"] = cmds


def is_note(event: dict) -> bool:
    """Whether ``event`` plays a note."""
    note = event.get("note")
    return isinstance(note, int) and not isinstance(note, bool)


def references(ir: dict):
    """``(place, type, index)`` for every record field, event instrument and command argument
    that references a record; ``place`` is what :func:`set_ref` changes."""
    v = view(ir)
    for tname, rows in (ir.get("records") or {}).items():
        rtype = v.rtypes[tname]
        for i, row in rows.items():
            for f in rtype.refs:
                target = ref_type(rtype, f, row) if f in row else None
                if target is not None and int(row[f]) != v.rtypes[target].none:
                    yield ("record", tname, i, f), target, int(row[f])
    for pname, events in ir["phrases"].items():
        for k, event in enumerate(events):
            yield from _event_refs(v, pname, k, event)


def _event_refs(v: View, pname: str, k: int, event: dict):
    if event.get("instrument"):
        yield ("instrument", pname, k), v.ins, int(event["instrument"]) - v.base
    for s, (code, arg) in enumerate(slots(event)):
        target = v.commands.ref(code) if code else None
        if target is not None and arg != v.rtypes[target].none:
            yield ("command", pname, k, s), target, arg


def set_ref(ir: dict, place: tuple, index: int) -> None:
    """Point the reference at ``place`` (see :func:`references`) to ``index``."""
    v = view(ir)
    if place[0] == "record":
        ir["records"][place[1]][place[2]][place[3]] = index
        return
    event = ir["phrases"][place[1]][place[2]]
    if place[0] == "instrument":
        event["instrument"] = index + v.base
        return
    cmds = slots(event)
    cmds[place[3]][1] = index
    put_slots(event, cmds)


def entries(ir: dict) -> dict:
    """``{type: sorted indices}`` of the records something references."""
    out = {}
    for _, target, index in references(ir):
        out.setdefault(target, set()).add(index)
    return {k: sorted(v) for k, v in out.items()}


def program(rtype, rows: dict, entry: int) -> tuple[list, int | None]:
    """``(row indices, loop position or None)`` of a chained type's program at ``entry``."""
    path, pos, index = [], {}, entry
    while index in rows and index not in pos:
        pos[index] = len(path)
        path.append(index)
        row = rows[index]
        if rtype.stop is not None and holds(rtype, rtype.stop, row):
            return path, None
        index = int(row.get(rtype.chain, rtype.default(rtype.chain)))
        if rtype.none is not None and index == rtype.none:
            return path, None
    return path, pos.get(index)


def holds(rtype, text, row) -> bool:
    """Whether a record type's expression ``text`` holds for ``row``."""
    return songio._RowEval(rtype).holds(text, row)  # pylint: disable=protected-access


def free_index(rows: dict, rtype, count: int) -> int | None:
    """The first of ``count`` unused indices after every used one, None past the type's max."""
    start = max([rtype.first - 1, *rows]) + 1
    return start if start + count - 1 <= min(rtype.max, 255) else None


def redirect(ir: dict, tname: str, old: int, new: int) -> bool:
    """Point every reference to record ``old`` of ``tname`` to ``new``; whether there was one."""
    hits = [p for p, t, i in references(ir) if t == tname and i == old]
    for place in hits:
        set_ref(ir, place, new)
    return bool(hits)


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
                yield (tname, entry, *program(rtype, ir["records"].get(tname, {}), entry))


def play_order(ir: dict):
    """A sort key putting phrases in the order the voices first play them."""
    first = {}
    for name, _ in walk(ir):
        first.setdefault(name, len(first))
    return lambda name: (first.get(name, len(first)), name)
