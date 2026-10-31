"""Lifting transliterates: each lifted object stands for a native one, so a document holds no
more objects of a kind than its tune's model has, and its song no more entries a voice than the
native sequence; :func:`check` refuses a lift that does (an unrolled loop or a per-occurrence copy
is state rammed in, not lifted)."""

from unitracker_grammar.document import Def, Group, Instrument, Part, Play, Program, Run, Section
from unitracker_grammar.document import SongRow, Voice, read
from unitracker_grammar.model import base

KINDS = ("instruments", "restart", "ctrl", "pw", "filter", "phrases", "song")
SECTIONS = {"ins": "instruments", "restart": "restart", "ctrl": "ctrl", "pw": "pw"}
SECTIONS |= {"filter": "filter", "phrase": "phrases"}
VOICES = 3


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
