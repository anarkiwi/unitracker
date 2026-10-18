"""The edits: each a pure function of a song and its arguments, None where it does not apply."""

import copy

from unitracker.format.codec import EXPR, NONE, CodecError, command_code
from unitracker.format.events import column
from unitracker.format.events.running import lasts_step
from unitracker.format.records import RowEval, chained, program_path, ref_type
from unitracker.format.refs import put_slots, redirect, slots, view
from unitracker.format.xexpr import canonical
from unitracker.optimise.model import (
    apart,
    copied,
    currents,
    first_note,
    free_index,
    incoming,
    is_note,
    shape,
    steps_of,
)


def merge(ir: dict, tname: str, keep: int, drop: int) -> dict | None:
    """The song with every reference to record ``drop`` of type ``tname`` naming ``keep``."""
    out = copied(ir)
    return out if redirect(out, tname, drop, keep) else None


def _rewritten(ir: dict, tname: str, entry: int, seq: list, loop: int | None) -> dict | None:
    """The song with program ``entry`` replaced by rows ``seq``, looping to ``loop`` or ending."""
    out = copied(ir)
    rtype = view(ir).rtypes[tname]
    rows = out["records"][tname]
    start = free_index(rows, rtype, len(seq))
    if start is None:
        return None
    rows.update(chained(rtype, seq, start, loop))
    last = rows[start + len(seq) - 1]
    if loop is None:
        last.update({f: int(v) for f, v in rtype.end.items()})
        if rtype.stop is not None and not RowEval(rtype).holds(rtype.stop, last):
            return None
    return out if redirect(out, tname, entry, start) else None


def _body(rtype, rows: dict, path: list) -> list:
    return [rtype.body(rows[i]) for i in path]


def truncate(ir: dict, tname: str, entry: int, length: int) -> dict | None:
    """The song with program ``entry`` cut to its first ``length`` rows and ending there."""
    rtype = view(ir).rtypes[tname]
    rows = ir["records"][tname]
    path, loop = program_path(rtype, rows.get, entry)
    if length > len(path) or (length == len(path) and loop is None):
        return None
    return _rewritten(ir, tname, entry, _body(rtype, rows, path[:length]), None)


def reloop(ir: dict, tname: str, entry: int) -> dict | None:
    """The song with looping program ``entry`` written with its shortest prefix and cycle."""
    rtype = view(ir).rtypes[tname]
    rows = ir["records"][tname]
    path, loop = program_path(rtype, rows.get, entry)
    if loop is None:
        return None
    seq = _body(rtype, rows, path)
    prefix, cycle = seq[:loop], seq[loop:]
    period = next(p for p in range(1, len(cycle) + 1) if cycle == cycle[p:] + cycle[:p])
    cycle = cycle[:period]
    while prefix and prefix[-1] == cycle[-1]:
        cycle = [prefix.pop(), *cycle[:-1]]
    if len(prefix) + len(cycle) == len(seq):
        return None
    return _rewritten(ir, tname, entry, prefix + cycle, len(prefix))


def hoist_value(ir: dict, ins: int, code: int, arg: int):
    """``(field, value)`` of instrument ``ins`` that plays as command ``code``, ``arg``, or None."""
    v = view(ir)
    rtype = v.rtypes[v.ins]
    row = ir["records"][v.ins].get(ins - v.base)
    if row is None or rtype.chain is not None:
        return None
    if code in v.nibbles:
        name, shift = v.nibbles[code]
        if name not in rtype.fields:
            return None
        keep = int(row.get(name, rtype.default(name))) & (0xFF ^ (0x0F << shift))
        return name, keep | ((arg & 0x0F) << shift)
    target = v.commands.ref(code)
    if target is not None:
        named = [f for f in rtype.refs if ref_type(rtype, f, {**row, f: arg}) == target]
        return (named[0], arg) if len(named) == 1 else None
    try:
        name, _ = v.commands.encode(code, arg)
    except CodecError:
        return None
    return (name, arg) if name in rtype.fields else None


def _rehoisted(events: list, start, ins: int, command: list, new: int) -> bool:
    """Rewrite a phrase's notes of ``ins`` carrying ``command`` to name ``new``; whether any did."""
    changed, heard = False, start
    for event, current in zip(events, currents(events, start)):
        if not is_note(event):
            continue
        want = current
        cmds = slots(event)
        if current == ins and command in cmds:
            cmds[cmds.index(command)] = [0, 0]
            put_slots(event, cmds)
            want, changed = new, True
        if event.get("instrument") or want != heard:
            event["instrument"] = want
        heard = want
    return changed


def hoist(ir: dict, ins: int, code: int, arg: int) -> dict | None:
    """The song with command ``code``, ``arg`` on notes of instrument ``ins`` moved into a copy
    of the instrument, which those notes name instead."""
    v = view(ir)
    found = hoist_value(ir, ins, code, arg)
    out = copied(ir)
    rows = out["records"][v.ins]
    new = free_index(rows, v.rtypes[v.ins], 1)
    if found is None or new is None:
        return None
    rows[new] = {**rows[ins - v.base], found[0]: found[1]}
    changed = False
    for name, enter in incoming(ir).items():
        events = out["phrases"][name]
        start = next(iter(enter)) if len(enter) == 1 else None
        if _unsure(events, start, ins, [code, arg]):
            return None
        changed |= _rehoisted(events, start, ins, [code, arg], new + v.base)
    return out if changed else None


def _unsure(events: list, start, ins: int, command: list) -> bool:
    """Whether ``command`` is on an event whose instrument is unknown, or on a non-note of
    ``ins``, which no instrument field can play."""
    return any(
        command in slots(e) and (c is None or (c == ins and not is_note(e)))
        for e, c in zip(events, currents(events, start))
    )


def _command_for(ir: dict, ins: int, fname: str, value: int) -> list | None:
    """``[code, arg]`` of the first command that plays instrument ``ins`` as field ``fname``
    set to ``value``, or None."""
    for code in sorted(int(c) for c in ir["playroutine"].get("commands") or {}):
        if hoist_value(ir, ins, code, value) == (fname, value):
            return [code, value]
    return None


def _difference_command(ir: dict, keep: int, drop: int) -> list | None:
    """The command that plays instrument ``keep`` as ``drop`` when they differ in one field."""
    v = view(ir)
    rtype, rows = v.rtypes[v.ins], ir["records"][v.ins]
    if rtype.chain is not None or keep not in rows or drop not in rows:
        return None
    a, b = (rows[keep], rows[drop])
    diff = [f for f in rtype.fields if _content(ir, rtype, a, f) != _content(ir, rtype, b, f)]
    value = int(b.get(diff[0], rtype.default(diff[0]))) if len(diff) == 1 else None
    return _command_for(ir, keep + v.base, diff[0], value) if value is not None else None


def _content(ir: dict, rtype, row: dict, fname: str):
    """A field's value, a referenced program as its rows."""
    value = int(row.get(fname, rtype.default(fname)))
    target = ref_type(rtype, fname, row) if fname in rtype.refs else None
    ttype = view(ir).rtypes.get(target)
    if ttype is None or ttype.chain is None or value == ttype.none:
        return value
    rows = ir["records"].get(target, {})
    path, loop = program_path(ttype, rows.get, value)
    return repr(_body(ttype, rows, path)), loop


def unhoist(ir: dict, keep: int, drop: int) -> dict | None:
    """The song with instrument ``drop``, one field away from ``keep``, played as ``keep`` with
    the command that sets that field on each of its notes."""
    v = view(ir)
    command = _difference_command(ir, keep, drop)
    if command is None:
        return None
    out = copied(ir)
    for name, enter in incoming(ir).items():
        events = out["phrases"][name]
        start = next(iter(enter)) if len(enter) == 1 else None
        for event, current in zip(events, currents(events, start)):
            if current is None and drop + v.base in enter:
                return None
            if (
                current == drop + v.base
                and is_note(event)
                and not _play_as(event, keep + v.base, command, ir["playroutine"])
            ):
                return None
    return out


def _play_as(event: dict, ins: int, command: list, pr: dict) -> bool:
    """Give a note of the dropped instrument ``command`` in its last free slot, and ``ins`` for
    the instrument it names; whether a slot was free whose column holds it."""
    cmds = slots(event)
    if [0, 0] not in cmds:
        return False
    cmds[len(cmds) - 1 - cmds[::-1].index([0, 0])] = command
    put_slots(event, cmds)
    if event.get("instrument"):
        event["instrument"] = ins
    try:
        column.check(pr, {"": [event]})
    except column.ColumnError:
        return False
    return True


def alias(ir: dict, keep: str, drop: str) -> dict | None:
    """The song with every play of phrase ``drop`` playing ``keep``, moved to its first note."""
    table = ir["phrases"]
    if keep == drop or steps_of(table[keep]) != steps_of(table[drop]):
        return None
    shift = first_note(table[drop]) - first_note(table[keep])
    out = copied(ir)
    for track in out["tracks"]:
        for play in track["plays"]:
            if play["phrase"] == drop:
                play["phrase"] = keep
                play["transpose"] = int(play.get("transpose", 0)) + shift
    return out


def repeats(events: list, count: int) -> bool:
    """Whether ``events`` are their first ``count`` repeated, more than once."""
    return 0 < count < len(events) and events == events[:count] * (len(events) // count)


def _replace_plays(ir: dict, name: str, parts: list) -> None:
    """Each play of phrase ``name`` as plays of ``parts``, in order, at its transpose."""
    for track in ir["tracks"]:
        plays, loop, new = track["plays"], track.get("loop"), []
        for k, play in enumerate(plays):
            if loop == k:
                track["loop"] = len(new)
            many = play["phrase"] == name
            new += [{**play, "phrase": part} for part in parts] if many else [play]
        track["plays"] = new


def factor(ir: dict, name: str, count: int) -> dict | None:
    """The song with each play of phrase ``name`` written as plays of its first ``count`` events,
    when the phrase is those events repeated."""
    events = ir["phrases"][name]
    if not repeats(events, count):
        return None
    out = copied(ir)
    part = f"{name}:{count}"
    out["phrases"][part] = copy.deepcopy(events[:count])
    _replace_plays(out, name, [part] * (len(events) // count))
    return out


def share(ir: dict, key: tuple) -> dict | None:
    """The song with every phrase that holds runs of shape ``key`` played as the parts around
    and of those runs, so that one phrase plays them all."""
    out = copied(ir)
    played = {p["phrase"] for t in ir["tracks"] for p in t["plays"]}
    size, cuts = len(key), {}
    for name in sorted(played):
        events = ir["phrases"][name]
        found = [(name, k, size) for k in range(len(events) - size + 1)]
        for _, k, _ in apart([p for p in found if shape(events[p[1] : p[1] + size]) == key]):
            cuts.setdefault(name, {0, len(events)}).update((k, k + size))
    for name, marks in cuts.items():
        marks = sorted(marks)
        if len(marks) == 2:
            continue
        parts = [f"{name}[{a}:{b}]" for a, b in zip(marks, marks[1:])]
        for part, a, b in zip(parts, marks, marks[1:]):
            out["phrases"][part] = copy.deepcopy(ir["phrases"][name][a:b])
        _replace_plays(out, name, parts)
    return out if any(len(m) > 2 for m in cuts.values()) else None


def drop_command(ir: dict, name: str, k: int, slot: int) -> dict | None:
    """The song without command ``slot`` of event ``k`` of phrase ``name``."""
    out = copied(ir)
    event = out["phrases"][name][k]
    cmds = slots(event)
    if cmds[slot] == [0, 0]:
        return None
    cmds[slot] = [0, 0]
    put_slots(event, cmds)
    return out


def drop_keep(ir: dict, name: str, k: int) -> dict | None:
    """The song with event ``k`` of phrase ``name`` restarting the programs it keeps."""
    if not ir["phrases"][name][k].get("keep"):
        return None
    out = copied(ir)
    del out["phrases"][name][k]["keep"]
    return out


def drop_instrument(ir: dict, name: str, k: int) -> dict | None:
    """The song with event ``k`` of phrase ``name`` naming no instrument."""
    if not ir["phrases"][name][k].get("instrument"):
        return None
    out = copied(ir)
    del out["phrases"][name][k]["instrument"]
    return out


def clear(ir: dict, tname: str, index: int, fname: str) -> dict | None:
    """The song with field ``fname`` of record ``index`` of type ``tname`` at its default."""
    rtype = view(ir).rtypes[tname]
    row = ir["records"][tname][index]
    if fname == rtype.chain or row.get(fname, rtype.default(fname)) == rtype.default(fname):
        return None
    out = copied(ir)
    out["records"][tname][index][fname] = rtype.default(fname)
    return out


def _expression(ir: dict, text: str) -> int:
    """The index of expression ``text`` in ``ir``, added after the others when it is new."""
    exprs = ir.setdefault("exprs", {})
    one = canonical(text)
    found = next((i for i, t in sorted(exprs.items()) if canonical(str(t)) == one), None)
    if found is None:
        found = max(exprs, default=0) + 1
        exprs[found] = one
    return found


def _freq_slot(cmds: list, code: int) -> int:
    return next(s for s, (c, a) in enumerate(cmds) if c == code and a)


def stitch(ir: dict, name: str, start: int, end: int, text: str) -> dict | None:
    """The song with the ``!freq`` of events ``start`` to ``end`` of phrase ``name`` one
    occurrence of expression ``text`` from ``start``: named on each step where ``!freq`` lasts a
    step, else on the first alone; None where an event beside them names it, continuing it."""
    code = command_code(ir["playroutine"].get("commands"), "freq", EXPR)
    out = copied(ir)
    x, step = _expression(out, text), lasts_step(ir["playroutine"])
    beside = out["phrases"][name][max(0, start - 1) : start] + out["phrases"][name][end : end + 1]
    if any([code, x] in slots(e) for e in beside):
        return None
    for k, event in enumerate(out["phrases"][name][start:end]):
        cmds = slots(event)
        cmds[_freq_slot(cmds, code)] = [code, x] if step or k == 0 else [0, 0]
        put_slots(event, cmds)
    return out if out["phrases"][name] != ir["phrases"][name] else None


def hold(ir: dict, name: str, k: int) -> dict | None:
    """The song with event ``k`` of phrase ``name`` playing ``!freq:none`` for its ``!freq``."""
    v = view(ir)
    try:
        code, none = v.commands.decode("freq", NONE)
    except CodecError:
        return None
    out = copied(ir)
    event = out["phrases"][name][k]
    cmds = slots(event)
    cmds[_freq_slot(cmds, code)] = [code, none]
    put_slots(event, cmds)
    return out


def staircase(ir: dict, name: str, start: int, end: int, key: str, text: str) -> dict | None:
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    """The song with the ``key`` commands of events ``start`` to ``end`` of phrase ``name`` one
    command of expression ``text`` at ``start``; None where the player has no such command."""
    v, out = view(ir), copied(ir)
    try:
        code, arg = v.commands.decode(key, (EXPR, _expression(out, text)))
    except CodecError:
        return None
    for k, event in enumerate(out["phrases"][name][start:end]):
        cmds = slots(event)
        s = next(s for s, (c, a) in enumerate(cmds) if c and v.commands.encode(c, a)[0] == key)
        cmds[s] = [code, arg] if k == 0 else [0, 0]
        put_slots(event, cmds)
    return out


EDITS = {
    f.__name__: f
    for f in (
        merge,
        truncate,
        reloop,
        hoist,
        unhoist,
        alias,
        factor,
        share,
        drop_command,
        drop_keep,
        drop_instrument,
        clear,
        staircase,
        stitch,
        hold,
    )
}
