"""The ``chords`` pass: a template call's run of relative-note arguments that is a chord's
intervals written as the chord's name, ``arp(min7)`` for ``arp(3,7,10)``, a template whose note
parameters every call gives in one order not ascending first permuted to ascend
(docs/optimiser.md)."""

import functools
import re
from dataclasses import replace
from functools import partial

from unitracker_grammar import code
from unitracker_grammar.chords import CHORDS
from unitracker_grammar.document import Call, Document, Expr, Instrument, Once, Program

from unitracker.optimiser.passes import Edit, _named
from unitracker.optimiser.prelude import core
from unitracker.optimiser.templates import PROGRAM_KINDS
from unitracker.optimiser.tree import transform, walk

_LOWER = re.compile(r"[a-z][a-z0-9]*")


@functools.cache
def chord(run: tuple) -> str | None:
    """music21's name of the chord of root position intervals ``run``, where it is a name of
    ``CHORDS`` standing for exactly ``run``."""
    from music21 import chord as m21chord  # pylint: disable=import-outside-toplevel
    from music21 import harmony  # pylint: disable=import-outside-toplevel

    if list(run) != sorted(set(run)) or run[0] <= 0:
        return None
    figure, kind = harmony.chordSymbolFigureFromChord(
        m21chord.Chord([60] + [60 + i for i in run]), True
    )
    if kind not in harmony.CHORD_TYPES or "/" in figure or not re.match(r"C(?![#-])", figure):
        return None
    names = [a for a in harmony.CHORD_TYPES[kind][1] if _LOWER.fullmatch(a)]
    name = max(names, key=len, default=None)
    return name if name is not None and CHORDS.get(name) == run else None


def _uses(value, param: str) -> int:
    """How often ``value`` (a row key's or field's value) reads ``param`` otherwise than as a
    relative note."""
    if isinstance(value, Call):
        return sum(a == param for a in value.args)
    if isinstance(value, Expr):
        return len(re.findall(rf"(?<![\w.]){re.escape(param)}(?![\w(])", value.text))
    return int(isinstance(value, str) and value.lstrip("+-") == param)


def _program_notes(body: Program, params: tuple) -> set:
    """The positions of ``params`` a program template reads only as ``note:+p``."""
    out = set()
    for i, p in enumerate(params):
        notes = others = 0
        for row in body.rows:
            for key, value in row.keys:
                if key.rpartition(".")[2] == "note" and value == f"+{p}":
                    notes += 1
                else:
                    others += _uses(value, p)
        if notes and not others:
            out.add(i)
    return out


def _instrument_notes(body: Instrument, params: tuple, notes: dict) -> set:
    """The positions of ``params`` an instrument template passes on only as arguments at
    note positions (``notes``) of program template calls."""
    out = set()
    for i, p in enumerate(params):
        forwarded = others = 0
        for _, value in body.fields:
            value = value.value if isinstance(value, Once) else value
            if isinstance(value, Call) and value.name in notes:
                for k, a in enumerate(value.args):
                    forwarded += a == p and k in notes[value.name]
                    others += a == p and k not in notes[value.name]
            else:
                others += _uses(value, p)
        if forwarded and not others:
            out.add(i)
    return out


def note_positions(doc: Document) -> dict:
    """``{template: positions}`` of the program and instrument templates' note parameters."""
    out = {}
    for kind in PROGRAM_KINDS:
        for key, body in doc.records(kind).items():
            if "(" in key and isinstance(body, Program):
                found = _program_notes(body, code.signature(key)[1])
                if found:
                    out[_named(key)] = found
    for key, body in doc.records("ins").items():
        if "(" in key and isinstance(body, Instrument):
            found = _instrument_notes(body, code.signature(key)[1], out)
            if found:
                out[_named(key)] = found
    return out


def _calls(doc: Document, name: str) -> list:
    return [n for _, n in walk(doc) if isinstance(n, Call) and n.name == name]


def permutation(doc: Document, name: str, positions: set) -> list | None:
    """The note positions of template ``name`` in the order that makes every call's values
    ascend, where every call of integers there gives them in one such order not ascending."""
    order = sorted(positions)
    rows = [core(c).args for c in _calls(doc, name)]
    rows = [r for r in rows if all(isinstance(r[k], int) for k in order)]
    if not rows:
        return None
    for i in order:
        for j in order:
            signs = {(r[i] > r[j]) - (r[i] < r[j]) for r in rows} - {0}
            if len(signs) > 1:
                return None
    perm = sorted(order, key=lambda k: tuple(r[k] for r in rows))
    return perm if perm != order else None


def _respelled(args: tuple, positions: set, avoid: frozenset) -> tuple:
    """``args`` with each run of integers at consecutive ``positions`` that is a chord's
    intervals its name: the fewest arguments, the earliest chords of equals."""
    n = len(args)
    best = [(0, ())] * (n + 1)
    for i in range(n - 1, -1, -1):
        best[i] = (best[i + 1][0] + 1, (args[i],) + best[i + 1][1])
        for j in range(i + 1, n + 1):
            if not all(k in positions and isinstance(args[k], int) for k in range(i, j)):
                break
            name = chord(tuple(args[i:j]))
            if name is not None and name not in avoid and best[j][0] + 1 < best[i][0]:
                best[i] = (best[j][0] + 1, (name,) + best[j][1])
    return best[0][1]


def _rewritten(name: str, positions: set, perm: list | None, avoid, node):
    if not isinstance(node, Call) or node.name != name:
        return node
    args = core(node).args
    if perm is not None:
        out = list(args)
        for k, src in zip(sorted(positions), perm):
            out[k] = args[src]
        args = tuple(out)
    return Call(name, _respelled(args, positions, avoid))


def _applied(name: str, positions: set, perm: list | None, doc: Document) -> Document:
    """``doc`` with template ``name``'s parameters permuted by ``perm`` and every call's note
    runs respelled, a call in a template's body never as one of its parameters."""
    defs = []
    for section in doc.defs:
        records = []
        for key, value in section.records:
            params = frozenset(code.signature(key)[1]) if "(" in key else frozenset()
            value = transform(value, partial(_rewritten, name, positions, perm, params))
            if perm is not None and _named(key) == name:
                old = code.signature(key)[1]
                new = list(old)
                for k, src in zip(sorted(positions), perm):
                    new[k] = old[src]
                key = f"{name}({','.join(new)})"
            records.append((key, value))
        defs.append(replace(section, records=tuple(records)))
    rest = transform(replace(doc, defs=()), partial(_rewritten, name, positions, perm, frozenset()))
    return replace(rest, defs=tuple(defs))


def chords(doc: Document) -> list:
    """Per program or instrument template with note parameters (:func:`note_positions`), an
    edit permuting its parameters where :func:`permutation` finds an order and writing each
    call's run of note arguments that is a chord's intervals as the chord's name."""
    edits = []
    for name, positions in note_positions(doc).items():
        perm = permutation(doc, name, positions)
        label = f"{name}" + (f" permuted {perm}" if perm else "")
        edits.append(Edit(label, partial(_applied, name, positions, perm)))
    return edits
