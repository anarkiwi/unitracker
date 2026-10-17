"""Edits described in the written document's names and forms, for findings and reports."""

from unitracker.format import songio
from unitracker.format.records import EXPR
from unitracker.format.refs import view
from unitracker.optimise.edits import EDITS, hoist_value
from unitracker.optimise.model import ERRORS


def one_line(value) -> str:
    """A written value on one line."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {one_line(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(one_line(v) for v in value) + "]"
    return "~" if value is None else str(value)


def _path(path: list) -> str:
    """A written place: ``i3.ctrl[0]``, ``p6[9]``, ``row 4 play``."""
    if len(path) >= 4 and path[1] == "def":
        head = f"{path[2]} {path[3]}" if isinstance(path[3], int) else str(path[3])
        rest = path[4:]
    elif len(path) == 3 and path[1] == "def":
        head, rest = str(path[2]), []
    else:
        head, rest = f"row {path[0]} {path[1]}" if len(path) > 1 else f"row {path[0]}", path[2:]
    return head + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in rest)


def first_difference(a, b, path=()):
    """``(place, before, after)`` of the first written value that differs, or None."""
    if isinstance(a, dict) and isinstance(b, dict):
        return _dict_difference(a, b, path)
    if isinstance(a, list) and isinstance(b, list):
        return _list_difference(a, b, path)
    return None if a == b else (_path(list(path)), one_line(a), one_line(b))


def _dict_difference(a: dict, b: dict, path: tuple):
    for key in [*a, *(k for k in b if k not in a)]:
        if key not in a or key not in b:
            side = [f"{key}: {one_line(x[key])}" if key in x else "-" for x in (a, b)]
            return (_path([*path, key]), *side)
        found = first_difference(a[key], b[key], (*path, key))
        if found:
            return found
    return None


def _list_difference(a: list, b: list, path: tuple):
    """A row or atom that differs, whole; a nested list or a document row is searched."""
    for k in range(max(len(a), len(b))):
        x, y = (a[k] if k < len(a) else None), (b[k] if k < len(b) else None)
        if x == y:
            continue
        if isinstance(x, list) or isinstance(y, list) or not path:
            return first_difference(x, y, (*path, k))
        return (_path([*path, k]), one_line(x), one_line(y))
    return None


def _named(w, tname: str, index: int) -> str:
    """A record as the document writes it, with its name or the place it is written inline."""
    key = w.key(tname, index)
    rtype = w.rtypes[tname]
    if tname == EXPR:
        value = w.ref_value(key)
    elif rtype.chain is not None:
        value = w.program_value(key)
    else:
        value = w.row_value(rtype, w.records[tname][index])
    if key in w.names:
        return f"{w.names[key]}: {one_line(value)}"
    owners = [r for r in w.referrers.get(key, []) if isinstance(r, tuple)]
    places = [f"{_owner(w, o)}.{f}" for o, f in owners if isinstance(o, tuple)]
    return f"{', '.join(places) or rtype.written}: {one_line(value)}"


def _owner(w, key: tuple) -> str:
    """A referring record's name, else its written type and index."""
    return w.names.get(key) or f"{w.rtypes[key[0]].written} {key[1]}"


def _phrase(w, name: str):
    """The written atoms of the phrase that plays ``name``, and its written name."""
    pname = w.phrase_names[name][0]
    for row in w.rows:
        atoms = (row.get("def") or {}).get("phrase", {}).get(pname)
        if atoms is not None:
            return pname, list(atoms)
    return pname, []


def _phrase_name(entry: tuple) -> str:
    return f"{entry[0]}{entry[1]:+d}" if entry[1] else entry[0]


def _hoist_forms(ir: dict, w, args: tuple) -> tuple:
    v = view(ir)
    name, value = hoist_value(ir, *args)
    ins = w.names[(v.ins, args[0] - v.base)]
    target = v.commands.ref(args[1])
    shown = w.names.get(w.key(target, value), value) if target else value
    nibble = v.nibbles.get(args[1])
    command = f"!{nibble[0]}:{value}" if nibble else f"!{w.command(*args[1:])}"
    return (ins, f"X.{ins}{command}", f"X.{ins} with {ins} + {{{name}: {shown}}}")


def describe(ir: dict, edit: tuple) -> tuple:
    """``(where, before, after)`` of an edit, in the written document's names and forms."""
    op, args = edit
    try:
        w = writer(ir)
        if op == "merge":
            where = w.rtypes[args[0]].written
            return (where, _named(w, args[0], args[2]), _named(w, args[0], args[1]))
        if op == "alias":
            keep, drop = (w.phrase_names[n] for n in args)
            return ("phrase", _phrase_name(drop), _phrase_name(keep))
        if op == "hoist":
            return _hoist_forms(ir, w, args)
        after = writer(EDITS[op](ir, *args))
        if op in ("drop_command", "drop_keep", "drop_instrument"):
            pname, atoms = _phrase(w, args[0])
            found = first_difference(atoms, _phrase(after, args[0])[1], (0, "def", "phrase", pname))
        else:
            found = first_difference(w.rows, after.rows)
    except ERRORS:
        found = None
    return found or (op, one_line(list(args)), "")


def writer(ir: dict):
    """The canonical writer of ``ir`` with its names given, and ``rows`` the rows it writes."""
    out = songio._Writer(ir)  # pylint: disable=protected-access
    out.rows = out.song()[1]
    return out
