"""References between the records, events and commands of the internal representation."""

from unitracker.format.codec import CommandCodec
from unitracker.format.records import record_types, ref_type
from unitracker.player.vm import EVENT_COLUMNS, event_commands


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


def redirect(ir: dict, tname: str, old: int, new: int) -> bool:
    """Point every reference to record ``old`` of ``tname`` to ``new``; whether there was one."""
    hits = [p for p, t, i in references(ir) if t == tname and i == old]
    for place in hits:
        set_ref(ir, place, new)
    return bool(hits)
