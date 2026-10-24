"""The ``instruments`` and ``phrases`` passes: records of one kind identical up to integers (an
instrument's) or notes (a phrase's) become one template with a parameter per differing
position, each record a call of it (docs/optimiser.md)."""

import itertools
import re
from collections import defaultdict
from dataclasses import replace
from functools import partial

from unitracker_grammar import code
from unitracker_grammar.document import (
    Call,
    Document,
    Expr,
    Group,
    Instrument,
    Once,
    Section,
    base,
    signature,
)
from unitracker_grammar.prelude import PRELUDE

from unitracker.optimiser.passes import Edit, _named
from unitracker.optimiser.templates import _HOLE, RESERVED, _parameters

_FIELD_STEMS = {"ctrl": "c", "pw": "p", "filter": "k", "freq": "f", "restart": "rs"}
_NOTE = re.compile(r"[A-G]#?-?\d+")
_LETTERS = "xyzuw"
_STEM = {"ins": "ins", "phrase": "ph"}


def taken(doc: Document) -> set:
    """Every template's name, the prelude's too."""
    names = {_named(k) for k, _ in doc.mods} | {code.signature(k)[0] for k in PRELUDE}
    return names | {_named(k) for s in doc.defs for k, _ in s.records if "(" in k}


def _fresh(stem: str, names: set) -> str:
    k = 1
    while f"{stem}_{k}" in names:
        k += 1
    names.add(f"{stem}_{k}")
    return f"{stem}_{k}"


def _signatures(doc: Document) -> dict:
    """``{name: parameters}`` of every template a call may name."""
    out = {code.signature(k)[0]: code.signature(k)[1] for k in PRELUDE}
    out |= {_named(k): code.signature(k)[1] for k, _ in doc.mods}
    return out | {
        _named(k): code.signature(k)[1] for s in doc.defs for k, _ in s.records if "(" in k
    }


def _integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _stem(key: str, callee: tuple, i: int) -> str:
    """A hole's parameter stem: the callee's parameter it fills (without its count), else the
    field's."""
    name = callee[i].rstrip("0123456789_") if i < len(callee) else ""
    if name and not RESERVED.fullmatch(name):
        return name
    return base(key) if base(key) in ("ad", "sr") else _FIELD_STEMS[base(key)]


def _field_holes(key: str, value, signatures: dict, holes: list):
    """A field's value with each integer a parameter may stand for a hole, appending each
    hole's ``(stem, integer)`` to ``holes``."""
    if isinstance(value, Once):
        return Once(_field_holes(key, value.value, signatures, holes))
    if _integer(value):
        holes.append((_stem(key, (), 0), value))
        return _HOLE
    if isinstance(value, Call):
        callee = signatures.get(value.name, ())
        args = []
        for i, a in enumerate(value.args):
            if _integer(a):
                holes.append((_stem(key, callee, i), a))
            args.append(_HOLE if _integer(a) else a)
        return Call(value.name, tuple(args))
    return value


def _instrument(ins: Instrument, signatures: dict) -> tuple:
    """``(shape, stems, values)`` of an instrument, every integer a hole."""
    holes = []
    fields = tuple((k, _field_holes(k, v, signatures, holes)) for k, v in ins.fields)
    return fields, tuple(s for s, _ in holes), tuple(v for _, v in holes)


def _filled(shape, names, keyed: bool = False):
    """A shape's field value with its holes, in order, each the next of ``names`` (an iterator
    of a parameter or an integer)."""
    if isinstance(shape, Once):
        return Once(_filled(shape.value, names, keyed))
    if shape is _HOLE:
        value = next(names)
        return value if _integer(value) or keyed else Expr(value)
    if isinstance(shape, Call):
        return Call(shape.name, tuple(next(names) if a is _HOLE else a for a in shape.args))
    return shape


def _body(fields: tuple, names) -> Instrument:
    return Instrument(tuple((k, _filled(v, names, base(k) in ("ad", "sr"))) for k, v in fields))


def _records(doc: Document, kind: str, calls: dict, template: tuple) -> Document:
    """``doc`` with records ``calls`` of ``kind`` each its call, and ``template`` the
    section's last record."""
    defs = []
    for s in doc.defs:
        if s.kind == kind:
            records = tuple((k, calls.get(k, v)) for k, v in s.records) + (template,)
            s = Section(kind, records)
        defs.append(s)
    return replace(doc, defs=tuple(defs))


def _edits(kind: str, members: list, names: set, naming, fill) -> list:
    """Per shape of two or more ``members``, ``(name, shape, values)``, whose values differ, an
    edit adding a template of a parameter per differing position (``naming(shape, differ,
    vectors)`` names them by position) and ``fill(shape, values)``'s body, each member record a
    call of it."""
    groups = defaultdict(list)
    for name, shape, values in members:
        groups[shape].append((name, values))
    edits = []
    for shape, group in groups.items():
        values = [v for _, v in group]
        differ = [k for k in range(len(values[0])) if len({v[k] for v in values}) > 1]
        if differ:
            params = naming(shape, differ, [tuple(v[k] for v in values) for k in differ])
            body = fill(shape, iter([params.get(k, x) for k, x in enumerate(values[0])]))
            edits.append(_edit(kind, group, params, body, names))
    return edits


def _edit(kind: str, group: list, params: dict, body, names: set) -> Edit:
    """The edit adding template ``{stem}_k`` of ``body`` and parameters ``params`` (by
    position), each record of ``group``, ``(name, values)``, a call of it."""
    order = list(dict.fromkeys(params.values()))
    first = {p: k for k, p in reversed(params.items())}
    key = signature(_fresh(_STEM[kind], names), order)
    calls = {n: Call(_named(key), tuple(v[first[p]] for p in order)) for n, v in group}
    apply = partial(_records, kind=kind, calls=calls, template=(key, body))
    return Edit(f"{kind} {key}: {len(group)}", apply)


def _stemmed(shape: tuple, differ: list, vectors: list) -> dict:
    holes = (0, None, tuple((0, 0, s, _HOLE) for s in shape[1]))
    return _parameters(holes, differ, vectors, lambda s: s)


def instruments(doc: Document) -> list:
    """Each group of two or more instrument records alike but for integers (``ad``, ``sr``,
    an integer binding, a call's integer arguments) becomes one template ``ins_k`` with a
    parameter per differing position, those of equal values sharing one named by the field's
    stem (or the callee's parameter), and each record a call of it."""
    signatures, members = _signatures(doc), []
    for name, value in doc.records("ins").items():
        if "(" not in name and isinstance(value, Instrument):
            fields, stems, values = _instrument(value, signatures)
            members.append((name, (fields, stems), values))
    return _edits("ins", members, taken(doc), _stemmed, lambda shape, it: _body(shape[0], it))


# ---- phrases ------------------------------------------------------------------------------


def _note_holes(events: tuple, notes: list) -> tuple:
    """A phrase's events with each note atom that is a note a hole, appending it to
    ``notes``."""
    out = []
    for e in events:
        if isinstance(e, Group):
            body = _note_holes(e.body, notes)
            out.append(
                replace(e, body=body, endings=tuple(_note_holes(x, notes) for x in e.endings))
            )
        elif _NOTE.fullmatch(e.note):
            notes.append(e.note)
            out.append(replace(e, note=_HOLE))
        else:
            out.append(e)
    return tuple(out)


def _note_filled(events: tuple, notes) -> tuple:
    out = []
    for e in events:
        if isinstance(e, Group):
            body = _note_filled(e.body, notes)
            out.append(
                replace(e, body=body, endings=tuple(_note_filled(x, notes) for x in e.endings))
            )
        else:
            out.append(replace(e, note=next(notes)) if e.note is _HOLE else e)
    return tuple(out)


def _note_parameters():
    """``x, y, z, u, w, x2, y2, ...``: a phrase template's parameters, no note letter."""
    for k in itertools.count(1):
        yield from (f"{a}{k if k > 1 else ''}" for a in _LETTERS)


def _lettered(_shape, differ: list, vectors: list) -> dict:
    by_vector, fresh = {}, _note_parameters()
    for v in vectors:
        if v not in by_vector:
            by_vector[v] = next(fresh)
    return {k: by_vector[v] for k, v in zip(differ, vectors)}


def phrases(doc: Document) -> list:
    """Each group of two or more phrase records alike but for their notes becomes one template
    ``ph_k`` with a parameter per differing note (``x``, ``y``, ...; notes equal in every
    record shared, the same in all literal), each record a call of it."""
    members = []
    for name, value in doc.records("phrase").items():
        if "(" not in name and not isinstance(value, Call):
            notes = []
            shape = _note_holes(value, notes)
            members.append((name, shape, tuple(notes)))
    return _edits("phrase", members, taken(doc), _lettered, _note_filled)
