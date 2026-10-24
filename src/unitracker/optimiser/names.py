"""The ``names`` rename: each program template the ``template`` pass named ``{kind}_k`` named by
its shape instead, a looping control program of relative-note parameters ``arp``, a restart
that gates off before its note gates on ``hr`` (docs/optimiser.md)."""

import re
from dataclasses import replace
from functools import partial

from unitracker_grammar.document import Call, Document, Program, signature

from unitracker.optimiser.passes import _named
from unitracker.optimiser.templates import PROGRAM_KINDS
from unitracker.optimiser.tree import transform

GENERATED = re.compile(r"(?:ctrl|restart|pw|filter)_\d+")
_STEMS = {"ctrl": "wave", "restart": "rs", "pw": "pwm", "filter": "flt"}
_RESERVED = frozenset({"abs", "min", "max", "none", "hold", "reset", "end", "loop", "on", "off"})


def _relative(note) -> bool:
    """Whether a row's note reads a parameter as a relative note (``+t``)."""
    return isinstance(note, str) and note[:1] in "+-" and note[1:2].isalpha()


def stem(kind: str, body: Program) -> str:
    """A program template's name by its shape: ``arp`` a looping control program whose rows
    read a parameter as a relative note, ``hr`` a restart with a row gating off (its note
    gates on), else its kind's (``wave``, ``rs``, ``pwm``, ``flt``)."""
    relative = any(_relative(row.get("note")) for row in body.rows)
    if kind == "ctrl" and body.loop is not None and relative:
        return "arp"
    if kind == "restart" and any(row.get("gate") == 0 for row in body.rows):
        return "hr"
    return _STEMS[kind]


def _keyed(names: dict, section):
    if section.kind not in PROGRAM_KINDS:
        return section
    records = []
    for key, value in section.records:
        name = _named(key)
        if name in names:
            params = key[len(name) + 1 : -1]
            key = signature(names[name], params.split(", ") if params else ())
        records.append((key, value))
    return replace(section, records=tuple(records))


def _called(names: dict, node):
    if isinstance(node, Call) and node.name in names:
        return Call(names[node.name], node.args)
    return node


def _renamed(names: dict, doc: Document) -> Document:
    doc = replace(doc, defs=tuple(_keyed(names, s) for s in doc.defs))
    return transform(doc, partial(_called, names))


def rename(doc: Document) -> tuple:
    """``(doc, names)``: ``doc`` with each generated program template (:data:`GENERATED`), in
    document order, named its :func:`stem`, numbered from 2 where the stem is taken (by a
    template, a reserved word, or an earlier rename), and ``names`` ``{old: new}``."""
    templates = [
        (kind, _named(key), value)
        for kind in PROGRAM_KINDS
        for key, value in doc.records(kind).items()
        if "(" in key
    ]
    taken = {name for _, name, _ in templates} | {_named(k) for k, _ in doc.mods} | _RESERVED
    names = {}
    for kind, name, body in templates:
        if not GENERATED.fullmatch(name):
            continue
        new, k = stem(kind, body), 1
        while new in taken:
            k += 1
            new = f"{stem(kind, body)}{k}"
        taken.add(new)
        names[name] = new
    return (_renamed(names, doc) if names else doc), names
