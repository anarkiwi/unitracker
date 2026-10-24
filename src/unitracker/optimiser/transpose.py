"""The ``transpose`` pass: a phrase equal to an earlier one but for a constant shift of every
note becomes that one, each play entry of it transposed by the shift (docs/optimiser.md)."""

from dataclasses import replace
from functools import partial

from unitracker_grammar.document import Call, Document, Group, Play, Signed
from unitracker_player.document import note_number

from unitracker.optimiser.passes import NO_NOTE, Edit, _dropped
from unitracker.optimiser.tree import transform


def number(note: str) -> int | None:
    """A note atom's format note, None for ``-``, ``off`` and ``on``."""
    return None if note in NO_NOTE else note_number(note)


def _shape(events: tuple) -> tuple:
    """``(shape, notes)``: the phrase with each note blank and its intervals from its first
    note, and its notes in order."""
    notes = [k for k in map(number, (e.note for e in events)) if k is not None]
    shape = tuple(replace(e, note="") if number(e.note) is not None else e for e in events)
    return (shape, tuple(k - notes[0] for k in notes[1:])), tuple(notes)


def _entry(old: str, new: str, shift: int, node):
    if not isinstance(node, Play) or node.phrase != old:
        return node
    total = (node.transpose or 0) + shift
    return Play(new, Signed(total) if total else None)


def _fold(old: str, new: str, shift: int, doc: Document) -> Document:
    """``doc`` without phrase ``old``, its play entries playing ``new`` ``shift`` higher."""
    return transform(_dropped(doc, "phrase", old), partial(_entry, old, new, shift))


def transpose(doc: Document) -> list:
    """Each phrase whose events are an earlier phrase's but for one shift of every note, and
    that has a note, becomes the first such phrase in document order: its record dropped and
    its play entries naming that phrase, the shift added to their transposes."""
    first, edits = {}, []
    for name, events in doc.records("phrase").items():
        if "(" in name or isinstance(events, Call) or any(isinstance(e, Group) for e in events):
            continue
        key, notes = _shape(events)
        if not notes:
            continue
        if key not in first:
            first[key] = (name, notes[0])
            continue
        rep, root = first[key]
        shift = notes[0] - root
        edits.append(Edit(f"phrase {name} -> {rep}{shift:+d}", partial(_fold, name, rep, shift)))
    return edits
