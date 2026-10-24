"""The ``inline`` pass: named definitions written inline at their uses (docs/optimiser.md)."""

from collections import Counter
from dataclasses import replace

from unitracker_grammar.document import (
    Call,
    Document,
    Event,
    Instrument,
    Play,
    Program,
    Row,
    Run,
    base,
    first_use,
    printed,
)

from unitracker.optimiser.passes import program_templates
from unitracker.optimiser.tree import transform, walk

MODES = ("named", "single", "all")
_FIELDS = ("ctrl", "pw", "filter", "restart")  # the instrument fields that name a record
_CELL = {"pw": "pw", "filter": "cut"}  # a binding record's cell, run as a program of one row
_RUNS = {Row: "keys", Event: "commands"}


def definitions(doc: Document) -> dict:
    """``{name: (kind, value)}`` of every named definition but a template: instruments,
    phrases and ``restart``, ``ctrl``, ``pw`` and ``filter`` records."""
    return {
        name: (section.kind, value)
        for section in doc.defs
        for name, value in section.records
        if "(" not in name
    }


def _named(defs: dict, value) -> bool:
    return isinstance(value, str) and value in defs


def references(node, defs: dict):
    """The names of ``defs`` one node references: an event's instrument, a play entry's phrase,
    an instrument's program field, and a row's or an event's ``run``."""
    if isinstance(node, Event) and _named(defs, node.ins):
        yield node.ins
    if isinstance(node, Play) and _named(defs, node.phrase):
        yield node.phrase
    if isinstance(node, Instrument):
        yield from (v for k, v in node.fields if base(k) in _FIELDS and _named(defs, v))
    if type(node) in _RUNS:
        yield from (
            v for k, v in getattr(node, _RUNS[type(node)]) if k == "run" and _named(defs, v)
        )


def _counts(node, defs: dict) -> Counter:
    return Counter(name for _, n in walk(node) for name in references(n, defs))


def cyclic(defs: dict) -> set:
    """The names of ``defs`` whose definition reaches itself, which no inline form spells."""
    graph = {name: set(_counts(value, defs)) for name, (_, value) in defs.items()}
    out = set()
    for name in graph:
        seen, stack = set(), list(graph[name])
        while stack:
            other = stack.pop()
            if other == name:
                out.add(name)
                break
            if other not in seen:
                seen.add(other)
                stack += graph[other]
    return out


def candidates(doc: Document, mode: str) -> set:
    """The definitions ``mode`` writes anonymously inline: those referenced exactly once for
    ``single``, never one that reaches itself; none for ``named`` and ``all``."""
    if mode not in MODES:
        raise ValueError(f"{mode} is not one of {MODES}")
    if mode != "single":
        return set()
    defs = definitions(doc)
    once = {name for name, count in _counts(doc, defs).items() if count == 1}
    return once - cyclic(defs)


class _Inliner:  # pylint: disable=too-few-public-methods
    """One document's definitions of ``targets``, each expanded once, and their uses."""

    def __init__(self, doc: Document, targets: set):
        self.defs = {n: d for n, d in definitions(doc).items() if n in targets}
        self.memo, self.forms = {}, program_templates(doc)

    def body(self, name: str):
        """Definition ``name`` with its own references of the targets inline."""
        if name not in self.memo:
            self.memo[name] = transform(self.defs[name][1], self.node)
        return self.memo[name]

    def program(self, name: str):
        """Definition ``name`` as a program or a program template's call: a binding (a
        ``pw`` or ``filter`` record's) as the program of its one row, which, unlike an
        instrument's own binding, keeps its φ where it binds again."""
        kind, value = self.defs[name][0], self.body(name)
        if isinstance(value, Program) or isinstance(value, Call) and value.name in self.forms:
            return value
        return Program((Row(((_CELL[kind], value),)),))

    def run(self, name: str):
        """A ``run``'s inline form: the program after its kind, a call as itself."""
        value = self.program(name)
        return value if isinstance(value, Call) else Run(self.defs[name][0], value)

    def node(self, node):
        """``node`` with its references of the targets inline."""
        defs = self.defs
        if isinstance(node, Event) and _named(defs, node.ins):
            node = replace(node, ins=self.body(node.ins))
        if isinstance(node, Play) and _named(defs, node.phrase):
            node = replace(node, phrase=self.body(node.phrase))
        if isinstance(node, Instrument):
            fields = tuple(
                (k, self.program(v) if base(k) in _FIELDS and _named(defs, v) else v)
                for k, v in node.fields
            )
            node = replace(node, fields=fields)
        if type(node) in _RUNS:
            attr = _RUNS[type(node)]
            pairs = tuple(
                (k, self.run(v) if k == "run" and _named(defs, v) else v)
                for k, v in getattr(node, attr)
            )
            node = replace(node, **{attr: pairs})
        return node


def _kept(sections: tuple, targets: set) -> tuple:
    out = (replace(s, records=tuple(r for r in s.records if r[0] not in targets)) for s in sections)
    return tuple(s for s in out if s.records)


def inlined(doc: Document, targets: set) -> Document:
    """``doc`` with each definition of ``targets`` written at its uses and its record dropped,
    with any section it leaves empty."""
    if not targets:
        return doc
    doc = transform(doc, _Inliner(doc, targets).node)
    return replace(doc, defs=_kept(doc.defs, targets))


def _reached(doc: Document) -> Document:
    """``doc`` without the definitions its song never reaches, which :func:`first_use` leaves in
    their sections."""
    rest = first_use(doc)
    return replace(
        doc,
        mods=tuple(r for r in doc.mods if r not in rest.mods),
        defs=_kept(doc.defs, {k for s in rest.defs for k, _ in s.records}),
    )


def inline(doc: Document, mode: str, holds=None) -> tuple:
    """``(document, refused)``: ``doc`` with the definitions of ``mode`` inline where
    ``holds`` of the text (the guarantee) keeps them, proved together and split in halves on
    refusal, ``refused`` the names kept named; ``all`` every definition at its first use."""
    targets, kept, refused = sorted(candidates(doc, mode)), set(), []
    if mode == "all":
        out = first_use(_reached(doc))
        return (out, []) if holds is None or holds(printed(out)) else (doc, ["first use"])

    def prove(group: list):
        if not group:
            return
        if holds is None or holds(printed(inlined(doc, kept | set(group)))):
            kept.update(group)
        elif len(group) == 1:
            refused.append(group[0])
        else:
            prove(group[: len(group) // 2])
            prove(group[len(group) // 2 :])

    prove(targets)
    return inlined(doc, kept), refused
