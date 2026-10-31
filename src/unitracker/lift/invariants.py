"""Lifting transliterates: each lifted object stands for a native one. :func:`provenance` holds a
lift to that: every named object of the document has the native object (and the entry context its
walk reached it in) it stands for, no two share one, none repeats another's value and none is
unnamed; :func:`check` bounds the counts and each voice's orderlist entries. An unrolled loop or a
per-occurrence copy is state rammed in, not lifted. :func:`sizes` and :func:`grown` keep a lift's
song bytes from growing unseen against a committed baseline."""

from typing import NamedTuple

from unitracker_grammar.document import Def, Event, Group, Instrument, Part, Play, Program, Run
from unitracker_grammar.document import Section, SongRow, Voice, read
from unitracker_grammar.model import base

KINDS = ("instruments", "restart", "ctrl", "pw", "filter", "phrases", "song")
SECTIONS = {"ins": "instruments", "restart": "restart", "ctrl": "ctrl", "pw": "pw"}
SECTIONS |= {"filter": "filter", "phrase": "phrases"}
VOICES = 3


class Origin(NamedTuple):
    """What a lifted object transliterates: the native object, and the entry context the lift's
    walk reached it in where the native object lifts to more than one."""

    native: tuple
    context: tuple = ()


def _inline(value) -> bool:
    """A phrase written in place, not a name nor a definition at its first use."""
    return isinstance(value, tuple) and not isinstance(value, Def)


class ModelError(ValueError):
    """A lifted document with more objects of a kind than its native tune."""


PROGRAMS = ("restart", "ctrl", "pw", "filter")


def _value(node):
    """A definition at its first use stands for its value."""
    return node.value if isinstance(node, Def) else node


def _entries(items, sections: dict) -> int:
    """Play entries of an orderlist or song, a group's body and a section's items once whatever
    their repeats."""
    count = 0
    for item in items:
        if isinstance(item, Group):
            count += sum(_entries(b, sections) for b in (item.body, *item.endings))
        elif isinstance(item, Part):
            section = item.section
            count += _entries(
                sections.get(section, ()) if isinstance(section, str) else _value(section), sections
            )
        elif isinstance(item, (Play, SongRow)):
            count += 1
    return count


def lifted(text: str) -> dict:
    """The distinct objects of each kind ``text`` writes, named or inline, each distinct value
    once (groups and sections unexpanded), and ``song``: each voice's orderlist entries (a song
    of rows: its rows, every voice)."""
    doc = read(text)
    found = {kind: set() for kind in KINDS[:-1]}
    todo = list(doc.defs) + list(doc.init or ()) + list(doc.rows)
    while todo:
        node = todo.pop()
        if isinstance(node, Section) and node.kind in SECTIONS:
            found[SECTIONS[node.kind]].update(repr(v) for _, v in node.records)
        elif isinstance(node, Run) and isinstance(_value(node.program), Program):
            found[node.kind].add(repr(_value(node.program)))
        elif isinstance(node, Instrument):
            found["instruments"].add(repr(node))
            for key, value in node.fields:
                if base(key) in PROGRAMS and isinstance(_value(value), Program):
                    found[base(key)].add(repr(_value(value)))
        elif isinstance(node, Play) and isinstance(_value(node.phrase), tuple):
            found["phrases"].add(repr(_value(node.phrase)))
        if isinstance(node, (tuple, list)):
            todo.extend(node)
        elif hasattr(node, "__dataclass_fields__"):
            todo.extend(getattr(node, f) for f in node.__dataclass_fields__)
    sections = {
        name: items
        for s in doc.defs
        if isinstance(s, Section) and s.kind == "section"
        for name, items in s.records
    }
    voices = [r for r in doc.rows if isinstance(r, Voice)]
    if voices:
        song = tuple(_entries(v.play, sections) for v in voices)
    else:
        song = (_entries(doc.rows, sections),) * (doc.header.voices or VOICES)
    return {kind: len(objs) for kind, objs in found.items()} | {"song": song}


def exceeded(have: dict, native: dict) -> str | None:
    """The first kind ``have`` holds more of than ``native`` (a kind without a native count is
    unchecked; ``song`` voice by voice), with both counts; None where none is."""
    for kind in KINDS:
        bound = native.get(kind)
        if bound is None:
            continue
        pairs = zip(have[kind], bound) if kind == "song" else [(have[kind], bound)]
        for v, (count, most) in enumerate(pairs):
            if count > most:
                where = f" on voice {v + 1}" if kind == "song" else ""
                return f"{count} lifted {kind}{where} > {most} native"
    return None


def check(text: str, native: dict) -> dict:
    """``text``'s counts, raising :class:`ModelError` where a kind exceeds ``native``'s."""
    have = lifted(text)
    if (why := exceeded(have, native)) is not None:
        raise ModelError(why)
    return have


DEF_KINDS = {"i": "instruments", "r": "restart", "c": "ctrl", "pw": "pw", "f": "filter"}
DEF_KINDS |= {"p": "phrases"}
FIXED = frozenset("unitracker meta clock voices tempo slots layers holds order state".split())
FIXED |= frozenset("regs lag calls global tables mod".split())


def _kind_of(name: str) -> str | None:
    """The kind a record name or a definition at first use names (``pw3``, ``c12``, ``p0``)."""
    stem = name.rstrip("0123456789")
    return DEF_KINDS.get(stem) if stem != name else None


def named(text: str) -> dict:
    """Each kind's named objects ``{name: value repr}`` in ``text``, its sections' records and
    its definitions at first use; ``inline``: the objects of a kind written without a name."""
    doc = read(text)
    out = {kind: {} for kind in KINDS[:-1]}
    inline = []
    todo = list(doc.defs) + list(doc.init or ()) + list(doc.rows)
    while todo:
        node = todo.pop()
        if isinstance(node, Section) and node.kind in SECTIONS:
            out[SECTIONS[node.kind]].update((n, repr(v)) for n, v in node.records)
        elif isinstance(node, Def) and (kind := _kind_of(node.name)) is not None:
            out[kind][node.name] = repr(node.value)
        elif isinstance(node, Run) and isinstance(node.program, Program):
            inline.append(node.kind)
        elif isinstance(node, Event) and isinstance(node.ins, Instrument):
            inline.append("instruments")
        elif isinstance(node, Play) and _inline(node.phrase):
            inline.append("phrases")
        elif isinstance(node, Instrument):
            inline += [
                base(k) for k, v in node.fields if base(k) in PROGRAMS and isinstance(v, Program)
            ]
        if isinstance(node, (tuple, list)):
            todo.extend(node)
        elif hasattr(node, "__dataclass_fields__"):
            todo.extend(getattr(node, f) for f in node.__dataclass_fields__)
    return out | {"inline": inline}


def provenance(text: str, origins: dict) -> dict:
    """``text``'s named objects against ``origins``, ``{kind: {name: Origin}}``. Raises
    :class:`ModelError` for an object written without a name, a named object without an origin or
    an origin without one, two objects of one origin, or two objects of a kind with the same
    value; returns each kind's ``(objects, native objects)``."""
    have = named(text)
    if have["inline"]:
        raise ModelError(f"unnamed lifted {have['inline'][0]}")
    out = {}
    for kind in KINDS[:-1]:
        names, given = have[kind], origins.get(kind, {})
        if set(names) != set(given):
            odd = sorted(set(names) ^ set(given))[0]
            raise ModelError(f"{kind} {odd} has no origin" if odd in names else f"no {kind} {odd}")
        if len(set(given.values())) != len(given):
            raise ModelError(f"two lifted {kind} of one origin")
        if len(set(names.values())) != len(names):
            raise ModelError(f"two lifted {kind} of one value")
        out[kind] = (len(given), len({o.native for o in given.values()}))
    return out


def sizes(text: str) -> dict:
    """``text``'s bytes in its header, tables and templates (``fixed``, the format's) and in its
    song's objects and timeline (``song``)."""
    out = {"fixed": 0, "song": 0}
    key = None
    for line in text.encode().splitlines(keepends=True):
        if not line[:1].isspace() and b":" in line:
            key = line.split(b":", 1)[0].decode()
        out["fixed" if key in FIXED else "song"] += len(line)
    return out


def grown(have: dict, baseline: dict) -> list:
    """The names in ``have`` whose song bytes exceed ``baseline``'s, or that it lacks."""
    return sorted(n for n, s in have.items() if n not in baseline or s > baseline[n])
