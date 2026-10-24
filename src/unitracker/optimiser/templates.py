"""The ``template`` pass: programs of one kind identical up to integers become one program
template with a parameter per differing position, each original a call of it."""

import re
from collections import defaultdict
from dataclasses import replace
from functools import partial

from unitracker_grammar.document import (
    CELLS,
    KINDS,
    Call,
    Document,
    Expr,
    Instrument,
    Program,
    Row,
    Section,
    Signed,
    base,
    signature,
)

from unitracker.optimiser.passes import Edit, _dropped, _named, _renamed
from unitracker.optimiser.tree import transform, walk

PROGRAM_KINDS = ("ctrl", "restart", "pw", "filter")
# a row key that takes an integer a parameter may stand for, and the parameter's stem
_STEMS = {
    "wait": "w",
    "hold": "h",
    "ctrl": "c",
    "mask": "mk",
    "gate": "g",
    "note": "t",
    "ad": "ad",
    "sr": "sr",
    "res": "r",
    "route": "ro",
    "route_on": "ron",
    "route_off": "rof",
    "mode": "m",
    "vol": "vo",
    "pw": "p",
    "cut": "k",
    "freq": "f",
}
_HOLE = object()


_DEFAULTS = (("wait", 0), ("hold", 1))


def _filled(row: Row) -> tuple:
    """A row's keys with ``wait`` and ``hold`` written where they are at their defaults."""
    keys = dict(row.keys)
    return Row(tuple(d for d in _DEFAULTS if d[0] not in keys) + row.keys).keys


def _holes(program: Program) -> tuple:
    """``(shape, values)``: the program with each integer a parameter may stand for a hole,
    and those integers in row and key order."""
    shape, values = [], []
    for i, row in enumerate(program.rows):
        for j, (key, value) in enumerate(_filled(row)):
            if base(key) in _STEMS and isinstance(value, int) and not isinstance(value, bool):
                if base(key) != "note" or isinstance(value, Signed):
                    shape.append((i, j, key, _HOLE))
                    values.append(value)
                    continue
            shape.append((i, j, key, value))
    return (len(program.rows), program.loop, tuple(shape)), tuple(values)


def _members(doc: Document, kind: str) -> list:
    """``(reference, program)`` of every program of ``kind``: a record's name, or an
    instrument's inline program as ``None``."""
    out = [(n, v) for n, v in doc.records(kind).items() if "(" not in n and isinstance(v, Program)]
    field = "ctrl" if kind == "ctrl" else kind
    for _, node in walk(doc):
        if isinstance(node, Instrument):
            out += [(None, v) for k, v in node.fields if k == field and isinstance(v, Program)]
    return out


# what a parameter may not be named: the cells' symbols, the stops and a record's name
RESERVED = re.compile(r"v|phi|n|nn|ne|none|hold|reset|(?:pw|[cfiprs])\d+")


def _stem(key: str) -> str:
    return _STEMS[base(key)]


def _parameters(shape: tuple, differ: list, vectors: list, stem=_stem) -> dict:
    """A parameter name per differing hole, holes whose members' values are equal sharing
    one: ``stem`` of its key, then a count where the stem repeats (after ``_`` where the name
    would be :data:`RESERVED`)."""
    names, seen, by_vector = {}, defaultdict(int), {}
    holes = [s for s in shape[2] if s[3] is _HOLE]
    for k, vector in zip(differ, vectors):
        if vector not in by_vector:
            name = stem(holes[k][2])
            seen[name] += 1
            count = "" if seen[name] == 1 else str(seen[name])
            sep = "_" if RESERVED.fullmatch(name + count) else ""
            by_vector[vector] = name + sep + count
        names[k] = by_vector[vector]
    return names


def _template(program: Program, names: dict) -> Program:
    """``program`` with each hole of ``names`` (by its index) reading that parameter, and
    ``wait`` and ``hold`` at their defaults left out."""
    rows, count = [], 0
    for row in program.rows:
        keys = []
        for key, value in _filled(row):
            hole = base(key) in _STEMS and isinstance(value, int)
            hole = hole and (base(key) != "note" or isinstance(value, Signed))
            name = names.get(count) if hole else None
            count += hole
            if name is None:
                if (key, value) not in _DEFAULTS:
                    keys.append((key, value))
            elif base(key) == "note":
                keys.append((key, f"+{name}"))
            else:
                keys.append((key, Expr(name) if base(key) in CELLS else name))
        rows.append(Row(tuple(keys)))
    return Program(tuple(rows), program.loop)


def _section_added(doc: Document, kind: str, record: tuple) -> Document:
    """``doc`` with ``record`` appended to its section of ``kind``, a new one in the grammar's
    order of kinds."""
    top = {s.kind: s for s in doc.defs}
    current = top.get(kind, Section(kind))
    top[kind] = replace(current, records=current.records + (record,))
    return replace(doc, defs=tuple(top[k] for k in KINDS if k in top))


def _inline(kind: str, calls: dict, node):
    if not isinstance(node, Instrument):
        return node
    fields = tuple(
        (k, calls.get(v, v) if k == kind and isinstance(v, Program) else v) for k, v in node.fields
    )
    return replace(node, fields=fields) if fields != node.fields else node


def _apply(kind: str, key: str, body: Program, members: list, doc: Document) -> Document:
    """``doc`` with template ``key`` of ``body`` and each member ``(reference, program, call)``
    a call of it."""
    inline = {p: c for ref, p, c in members if ref is None}
    doc = transform(doc, partial(_inline, kind, inline))
    for ref, _, call in members:
        if ref is not None:
            doc = transform(_dropped(doc, kind, ref), partial(_renamed, kind, ref, call))
    return _section_added(doc, kind, (key, body))


def _taken(doc: Document) -> set:
    names = {_named(k) for k, _ in doc.mods}
    return names | {_named(n) for kind in PROGRAM_KINDS for n in doc.records(kind)}


def _edit(kind: str, name: str, shape: tuple, group: list) -> Edit:
    """The edit making ``group``'s programs, of one ``shape``, calls of template ``name``."""
    distinct = {values for _, _, values in group}
    differ = [k for k in range(len(group[0][2])) if len({v[k] for v in distinct}) > 1]
    vectors = [tuple(values[k] for _, _, values in group) for k in differ]
    names = _parameters(shape, differ, vectors)
    params = list(dict.fromkeys(names[k] for k in differ))
    first = {names[k]: k for k in reversed(differ)}
    members = [
        (ref, program, Call(name, tuple(int(values[first[p]]) for p in params)))
        for ref, program, values in group
    ]
    key = signature(name, params)
    body = _template(group[0][1], names)
    return Edit(f"{kind} {key}: {len(group)}", partial(_apply, kind, key, body, members))


def template(doc: Document) -> list:
    """Per kind, each group of two or more programs of one shape (every integer a hole) whose
    holes differ becomes a template ``{kind}_k`` of a parameter per differing hole, and each
    member (a record, or an instrument's inline program) a call of it."""
    edits, taken = [], _taken(doc)
    for kind in PROGRAM_KINDS:
        groups = defaultdict(list)
        for ref, program in _members(doc, kind):
            shape, values = _holes(program)
            if (ref, program) not in [(r, p) for r, p, _ in groups[shape]]:
                groups[shape].append((ref, program, values))
        count = 0
        for shape, group in groups.items():
            if len(group) < 2 or len({values for _, _, values in group}) < 2:
                continue
            count += 1
            while f"{kind}_{count}" in taken:
                count += 1
            edits.append(_edit(kind, f"{kind}_{count}", shape, group))
    return edits
