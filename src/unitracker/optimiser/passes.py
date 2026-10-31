"""The passes: each proposes deterministic candidate edits of a document model, every edit a
pure function of a document (docs/optimiser.md)."""

import re
from dataclasses import dataclass, replace
from functools import partial
from typing import Callable

from unitracker_grammar import PAL_HZ
from unitracker_grammar import code
from unitracker_grammar.document import (
    CELLS,
    HOLD,
    NONE,
    Call,
    Code,
    Document,
    Event,
    Expr,
    Instrument,
    Once,
    Play,
    Program,
    Row,
    Section,
    Slot,
    base,
)

from unitracker.optimiser.tree import transform, update, walk

PAL_CYCLES = 63 * 312

TEMPO, VOICES = 6, 3
NO_NOTE = frozenset({"-", "off", "on"})
KINDS = ("ins", "ctrl", "pw", "filter", "restart", "phrase")
_INS_CELL = {"freq": "freq", "pw": "pw", "filter": "cut"}
_PROGRAM_NAME = re.compile(r"(?:c|pw|f|r)\d+")
_STOPS = (NONE, HOLD)


@dataclass(frozen=True)
class Edit:
    """A candidate edit: its label and the pure function it applies."""

    label: str
    apply: Callable[[Document], Document]


def program_templates(doc: Document) -> set:
    """The names of a document's program templates (records keyed by a signature)."""
    return {
        code.signature(name)[0]
        for kind in ("restart", "ctrl", "pw", "filter")
        for name in doc.records(kind)
        if "(" in name
    }


def _program(value, templates: set) -> bool:
    """Whether an instrument's pw or filter field is a program (a name, a call of a program
    template of ``templates``, or inline), not a binding."""
    if isinstance(value, Program) or isinstance(value, Call) and value.name in templates:
        return True
    return isinstance(value, str) and bool(_PROGRAM_NAME.fullmatch(value))


def _slot(doc: Document, key: str, cell: str) -> str:
    """The slot of ``cell`` a key binds: its prefix's, else the cell's first."""
    return key.rpartition(".")[0] or doc.header.slot(cell)[0].name


def _bindings(doc: Document):
    """``(path, attr, key, cell, slot, value)`` of every cell key of a row, an instrument and an
    event's commands, then each slot's default as ``(None, None, None, cell, slot, value)``."""
    rows, templates = {c: c for c in CELLS}, program_templates(doc)
    for path, node in walk(doc):
        attr, cells = {Row: ("keys", rows), Instrument: ("fields", _INS_CELL)}.get(
            type(node), ("commands", rows) if isinstance(node, Event) else (None, None)
        )
        for key, value in getattr(node, attr) if attr else ():
            cell = cells.get(base(key))
            if cell and not (attr == "fields" and _program(value, templates)):
                yield path, attr, key, cell, _slot(doc, key, cell), value
    for cell, slots in doc.header.slots or ():
        for slot in slots:
            if slot.mod is not None:
                yield None, None, None, cell, slot.name, slot.mod


def _without(pairs: tuple, pair: tuple) -> tuple:
    out = list(pairs)
    if pair in out:
        out.remove(pair)
    return tuple(out)


def _drop(path: tuple, attr: str, pair: tuple, doc: Document) -> Document:
    return update(doc, path, lambda n: replace(n, **{attr: _without(getattr(n, attr), pair)}))


def _respelled(path: tuple, attr: str, pair: tuple, new: tuple, doc: Document) -> Document:
    def edit(node):
        return replace(node, **{attr: tuple(new if p == pair else p for p in getattr(node, attr))})

    return update(doc, path, edit)


def _set(path: tuple, changes: dict, doc: Document) -> Document:
    return update(doc, path, lambda n: replace(n, **changes))


def _header(changes: dict, doc: Document) -> Document:
    return replace(doc, header=replace(doc.header, **changes))


def _label(path: tuple, what: str) -> str:
    return f"{'/'.join(map(str, path))}: {what}"


def _header_defaults(doc: Document) -> list:
    head, edits = doc.header, []
    if head.tempo in (TEMPO, (TEMPO, TEMPO)):
        edits.append(Edit(f"tempo:{TEMPO}", partial(_header, {"tempo": None})))
    elif isinstance(head.tempo, tuple) and head.tempo[0] == head.tempo[1]:
        edits.append(Edit("tempo:[a,a]", partial(_header, {"tempo": head.tempo[0]})))
    if head.voices == VOICES:
        edits.append(Edit(f"voices:{VOICES}", partial(_header, {"voices": None})))
    if head.clock == (PAL_CYCLES, PAL_HZ):
        edits.append(Edit("clock:PAL", partial(_header, {"clock": None})))
    return edits


def _started_by_notes(doc: Document) -> set:
    """The control programs only an instrument's ``ctrl`` field starts (no ``run`` names one)."""
    named, run = set(), set()
    for _, node in walk(doc):
        if isinstance(node, Instrument) and isinstance(node.get("ctrl"), str):
            named.add(node.get("ctrl"))
        elif isinstance(node, Row):
            run.add(node.get("run"))
        elif isinstance(node, Event):
            run.update(v for k, v in node.commands if k == "run")
    return named - run


def _first_notes(doc: Document) -> list:
    """A first row's ``note:+0`` dropped from each control program only a note starts: the
    note clears the rows' notes as it starts the program."""
    names, programs = _started_by_notes(doc), []
    for path, node in walk(doc):
        if isinstance(node, Section) and node.kind == "ctrl":
            records = enumerate(node.records)
            programs += [(path + ("records", i, 1), v) for i, (n, v) in records if n in names]
        elif isinstance(node, Instrument):
            fields = enumerate(node.fields)
            programs += [
                (path + ("fields", i, 1), v)
                for i, (k, v) in fields
                if k == "ctrl" and isinstance(v, Program)
            ]
    edits = []
    for path, program in programs:
        note = program.rows[0].get("note") if program.rows else None
        if isinstance(note, int) and note == 0:
            row = path + ("rows", 0)
            edits.append(Edit(_label(row, "note:+0"), partial(_drop, row, "keys", ("note", note))))
    return edits


def _event_defaults(path: tuple, event: Event) -> list:
    edits = []
    for key, value in event.commands:
        if key in ("tempo", "vtempo") and isinstance(value, tuple) and value[0] == value[1]:
            respell = partial(_respelled, path, "commands", (key, value), (key, value[0]))
            edits.append(Edit(_label(path, f"{key}:a/a"), respell))
    if event.note in NO_NOTE:
        if ("legato", True) in event.commands:
            legato = partial(_drop, path, "commands", ("legato", True))
            edits.append(Edit(_label(path, "legato"), legato))
        for flag in ("keep_pw", "keep_filter"):
            if getattr(event, flag):
                edits.append(Edit(_label(path, flag), partial(_set, path, {flag: False})))
    if event.steps == 1:
        edits.append(Edit(_label(path, "*1"), partial(_set, path, {"steps": None})))
    return edits


def _bare(event) -> bool:
    """Whether an event is a rest and nothing else, which a rest before it may take as steps."""
    return isinstance(event, Event) and event == Event("-", steps=event.steps)


def _rests(events: tuple) -> tuple:
    """``events`` with each run of bare rests one rest of their steps."""
    out = []
    for event in events:
        if out and _bare(out[-1]) and _bare(event):
            out[-1] = replace(out[-1], steps=(out[-1].steps or 1) + (event.steps or 1))
        else:
            out.append(event)
    return tuple(out)


def defaults(doc: Document) -> list:
    """Drop what a document spells at the default it has anyway (semantics.md): header lines,
    ``wait:0``, ``hold:1``, ``a/a`` tempos, ``*1``, a non-note's ``legato``, ``pw`` and
    ``filter`` flags, a ``none`` of a slot nothing binds (nor, of ``freq``, holds), an instrument's
    ``none`` and binding equal to its slot's default, a first row's ``note:+0`` only a note
    starts, and a row of only ``wait`` and ``hold`` folded into the hold of the row before."""
    edits = _header_defaults(doc)
    for path, node in walk(doc):
        if isinstance(node, Row):
            edits += [
                Edit(_label(path, f"{k}:{v}"), partial(_drop, path, "keys", (k, v)))
                for k, v in node.keys
                if (k, v) in (("wait", 0), ("hold", 1))
            ]
        elif isinstance(node, Event):
            edits += _event_defaults(path, node)
        elif isinstance(node, tuple) and node and _rests(node) != node:
            edits.append(Edit(_label(path, "rests"), partial(update, path=path, fn=_rests)))
    return edits + _binding_defaults(doc) + _first_notes(doc) + _empty_rows(doc)


def _folded(path: tuple, i: int, doc: Document) -> Document:
    """``doc`` with row ``i`` of the program at ``path``, empty but for its timing, folded into
    the hold of the row before it."""

    def fold(program):
        rows, empty = list(program.rows), program.rows[i]
        ticks = empty.get("wait", 0) + empty.get("hold", 1)
        keys = tuple(p for p in rows[i - 1].keys if p[0] != "hold")
        rows[i - 1] = Row(keys + (("hold", rows[i - 1].get("hold", 1) + ticks),))
        del rows[i]
        loop = program.loop
        return replace(program, rows=tuple(rows), loop=loop - (loop > i) if loop else loop)

    return update(doc, path, fold)


def _empty_rows(doc: Document) -> list:
    """A program row of nothing but ``wait`` and ``hold`` folded into the hold of the row
    before it, which then lasts as long as both (not where a loop starts at it)."""
    edits = []
    for path, node in walk(doc):
        if not isinstance(node, Program):
            continue
        for i in reversed(range(1, len(node.rows))):  # each fold keeps the rows before it
            keys, before = dict(node.rows[i].keys), node.rows[i - 1].get("hold", 1)
            timing = all(isinstance(keys.get(k, 0), int) for k in ("wait", "hold"))
            if set(keys) <= {"wait", "hold"} and timing and isinstance(before, int):
                if node.loop != i:
                    edit = partial(_folded, path, i)
                    edits.append(Edit(_label(path + ("rows", i), "{}"), edit))
    return edits


def _binding_defaults(doc: Document) -> list:
    """A ``none`` of a slot nothing binds (nor, of ``freq``, holds) dropped, an instrument's
    ``none`` (it ends a binding only where one lasts to its note) and its binding equal to its
    slot's default."""
    bindings = list(_bindings(doc))
    bound = {(c, s) for _, _, _, c, s, v in bindings if v not in _STOPS and not isinstance(v, int)}
    held = any(c == "freq" and v == HOLD for _, _, _, c, _, v in bindings)
    default = {(c, s.name): s.mod for c, slots in doc.header.slots or () for s in slots}
    edits = []
    for path, attr, key, cell, slot, value in bindings:
        if path is None:
            continue
        dead = value == NONE and (cell, slot) not in bound and not (cell == "freq" and held)
        mod = default.get((cell, slot))
        if dead or attr == "fields" and (value == NONE or mod is not None and value == mod):
            drop = partial(_drop, path, attr, (key, value))
            edits.append(Edit(_label(path, f"{key}:{value}"), drop))
    return edits


# ---- merge --------------------------------------------------------------------------------


def _named(key: str) -> str:
    """A record's name: its key, or a template's signature's name."""
    return code.signature(key)[0] if "(" in key else key


def _dropped(doc: Document, kind: str, name: str) -> Document:
    """``doc`` without record ``name`` of ``kind``, nor a section it leaves empty."""
    if kind == "mod":
        mods = tuple((k, v) for k, v in doc.mods if code.signature(k)[0] != name)
        if mods != doc.mods:
            return replace(doc, mods=mods)

    def kept(sections) -> tuple:
        out = []
        for section in sections:
            if section.kind == kind:
                records = tuple(r for r in section.records if _named(r[0]) != name)
                section = replace(section, records=records)
            if section.records:
                out.append(section)
        return tuple(out)

    return replace(doc, defs=kept(doc.defs))


_REFS = {  # the instrument fields, and the row keys and commands, that name a record of a kind
    "ctrl": ({"ctrl"}, {"run"}),
    "pw": ({"pw"}, {"run"}),
    "filter": ({"filter"}, {"run"}),
    "restart": ({"restart"}, set()),
}


def _renamed(kind: str, old: str, new: str, node):
    """``node`` with its references of ``kind`` to ``old`` naming ``new``."""
    if kind == "ins":
        return replace(node, ins=new) if isinstance(node, Event) and node.ins == old else node
    if kind == "phrase":
        return replace(node, phrase=new) if isinstance(node, Play) and node.phrase == old else node
    if kind == "mod":
        return Call(new, node.args) if isinstance(node, Call) and node.name == old else node
    attr = {Instrument: "fields", Row: "keys", Event: "commands"}.get(type(node))
    if attr is None:
        return node
    names = _REFS[kind][0 if attr == "fields" else 1]
    pairs = getattr(node, attr)
    out = tuple((k, new if v == old and base(k) in names else v) for k, v in pairs)
    return replace(node, **{attr: out}) if out != pairs else node


def _merge(kind: str, old: str, new: str, doc: Document, refs: str | None = None) -> Document:
    """``doc`` with record ``old`` dropped and its references (of kind ``refs``, by default
    ``kind``'s) naming ``new``."""
    return transform(_dropped(doc, kind, old), partial(_renamed, refs or kind, old, new))


_PARAM = r"(?<![\w.]){}(?![\w(])"


def _alpha(body, params: tuple):
    """A template's body with its parameters named by their positions."""
    names = {p: f"_{i}" for i, p in enumerate(params)}
    if isinstance(body, (Program, Instrument, tuple)):
        return transform(body, partial(_alpha_row, names))
    if isinstance(body, Code):
        out = []
        for s in body.statements:
            operand = getattr(s, "operand", None)
            if isinstance(operand, code.Imm) and operand.value in names:
                s = s._replace(operand=operand._replace(value=names[operand.value]))
            out.append(s)
        return Code(tuple(out))
    text = body.text
    for param, name in names.items():
        text = re.sub(_PARAM.format(re.escape(param)), name, text)
    return Expr(text)


def _alpha_row(names: dict, node):
    """A program, instrument or phrase template's row, field, event or call with its
    parameters named by their positions."""
    if isinstance(node, Call):
        return Call(node.name, tuple(names.get(a, a) for a in node.args))
    if isinstance(node, Event):
        return replace(node, note=names.get(node.note, node.note))
    if not isinstance(node, (Row, Instrument)):
        return node

    def named(value):
        if isinstance(value, Expr):
            return _alpha(value, tuple(names))
        if isinstance(value, str) and value[:1] in "+-" and value[1:] in names:
            return value[0] + names[value[1:]]
        return names.get(value, value) if isinstance(value, str) else value

    attr = "keys" if isinstance(node, Row) else "fields"
    return replace(node, **{attr: tuple((k, named(v)) for k, v in getattr(node, attr))})


def merge(doc: Document) -> list:
    """A definition equal to an earlier one of its kind (a template's up to its parameters'
    names) becomes that one: its record dropped and its references renamed."""
    edits = []
    for kind in KINDS + ("mod",):
        first = {}
        records = doc.mods if kind == "mod" else doc.records(kind).items()
        for key, value in records:
            if "(" in key:
                name, params = code.signature(key)
                shape, refs = ("(", len(params), _alpha(value, params)), "mod"
            else:
                name, shape, refs = key, value, kind
            if shape in first:
                edit = partial(_merge, kind, name, first[shape], refs=refs)
                edits.append(Edit(f"{kind} {name} -> {first[shape]}", edit))
            else:
                first[shape] = name
    return edits


# ---- hoist --------------------------------------------------------------------------------


def _binds(doc: Document, pair: tuple, cell: str, slot: str, templates: set) -> bool:
    """Whether an instrument's field ``(key, value)`` binds ``slot`` of ``cell``."""
    key, value = pair
    on = _INS_CELL.get(base(key)) == cell and _slot(doc, key, cell) == slot
    return on and not _program(value, templates)


def _with_note(reset: tuple | None) -> tuple:
    """A slot's reset events with ``note``, on which an instrument's binding restarts phi."""
    return tuple(reset or ()) + (() if "note" in (reset or ()) else ("note",))


def _reads_phi(doc: Document, value) -> bool:
    """Whether a binding reads phi: its expression, its code or its template's."""
    if isinstance(value, Call):
        templates = {code.signature(k)[0]: body for k, body in doc.mods}
        value = templates.get(value.name, value)
    if isinstance(value, Code):
        return "phi" in code.symbols(value.statements)
    return not isinstance(value, (int, Expr)) or bool(re.search(r"\bphi\b", str(value)))


def _hoist(cell: str, name: str, value, doc: Document) -> Document:
    head = doc.header
    slots = dict(head.slots or ())
    current = slots.get(cell, (Slot(cell),))
    phi = _reads_phi(doc, value)
    slots[cell] = tuple(
        (
            replace(s, mod=value, reset=_with_note(s.reset) if phi else s.reset)
            if s.name == name
            else s
        )
        for s in current
    )

    templates = program_templates(doc)

    def bare(node):
        if not isinstance(node, Instrument):
            return node
        keep = (f for f in node.fields if not _binds(doc, f, cell, name, templates))
        return replace(node, fields=tuple(keep))

    doc = transform(doc, bare)
    order = tuple((c, slots[c]) for c in CELLS if c in slots)
    return replace(doc, header=replace(head, slots=order))


def hoist(doc: Document) -> list:
    """A binding every instrument carries identically for a slot without a default becomes
    that slot's default in the slots line, reset on ``note`` where it reads phi, which the
    instruments' binding restarted, and leaves the instruments."""
    instruments = [n for _, n in walk(doc) if isinstance(n, Instrument)]
    if not instruments:
        return []
    edits, templates = [], program_templates(doc)
    for cell in CELLS:
        for slot in doc.header.slot(cell):
            values = {
                tuple(v for k, v in ins.fields if _binds(doc, (k, v), cell, slot.name, templates))
                for ins in instruments
            }
            if slot.mod is not None or len(values) != 1:
                continue
            (found,) = values
            if len(found) == 1 and found[0] not in _STOPS and not isinstance(found[0], int):
                hoisted = partial(_hoist, cell, slot.name, found[0])
                edits.append(Edit(f"{slot.name}.{cell}:{found[0]}", hoisted))
    return edits


# ---- once ---------------------------------------------------------------------------------


def _slot_key(doc: Document, key: str) -> tuple | None:
    """``(cell, slot)`` a command's key binds or stops, None for a key of no modulated cell."""
    cell = base(key)
    return (cell, _slot(doc, key, cell)) if cell in CELLS else None


def _starts(events: tuple) -> list:
    """Each event's first step in its phrase, which merging bare rests leaves as it is."""
    out, at = [], 0
    for event in events:
        out.append(at)
        at += event.steps or 1
    return out


def _onced(path: tuple, starts: tuple, key: str, doc: Document) -> Document:
    """``doc`` with the event of the phrase at ``path`` starting at step ``starts[0]`` binding
    ``key`` once and the one at ``starts[1]`` without its ``none``, then its bare rests merged."""

    def edit(events):
        i, j = (_starts(events).index(at) for at in starts)
        first, last = events[i], events[j]
        commands = tuple((k, Once(v) if k == key else v) for k, v in first.commands)
        out = list(events)
        out[i] = replace(first, commands=commands)
        out[j] = replace(last, commands=_without(last.commands, (key, NONE)))
        return _rests(tuple(out))

    return update(doc, path, edit)


def _stopped_at(events: tuple, i: int, key: str, cell: str) -> int | None:
    """The event after ``i`` whose commands of ``cell`` are only ``key``'s ``none``, where it is
    the first after ``i`` with a command of ``cell``."""
    for j in range(i + 1, len(events)):
        later = [(k, v) for k, v in events[j].commands if base(k) == cell]
        if later:
            return j if later == [(key, NONE)] else None
    return None


def once(doc: Document) -> list:
    """A command binding a slot that a later event of its phrase stops with ``none``, no command
    of the cell between, becomes a binding that fires once (``b!``), the ``none`` dropped; a plain
    integer is a write already and is left alone."""
    edits = []
    for path, node in walk(doc):
        if not isinstance(node, tuple) or not node or not all(isinstance(e, Event) for e in node):
            continue
        for i, event in enumerate(node):
            for key, value in event.commands:
                slot = _slot_key(doc, key)
                if slot is None or value in (NONE, HOLD, "reset") or isinstance(value, (int, Once)):
                    continue
                j = _stopped_at(node, i, key, slot[0])
                if j is not None:
                    label = _label(path + (i,), f"{key}:{value}!")
                    starts = _starts(node)
                    edits.append(Edit(label, partial(_onced, path, (starts[i], starts[j]), key)))
    return edits
