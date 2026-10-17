"""Read and write unitracker v1.17 documents: a header, then one timeline of definitions and plays.

The player runs the expanded internal representation (``"unitracker": "ir"``): every
document is read through :func:`loads`, and lifters' output is written with :func:`dumps`.
"""

import copy
import functools
import os
import re
import tempfile
from importlib import resources

import yaml

from unitracker import envelope, eventrestart, hints, inline, lifetime, refkeys, retired
from unitracker import timeline
from unitracker.atoms import format_atom, parse_atom, runs
from unitracker.codec import (
    NONE,
    RETIRED,
    ROW_KEYS,
    CodecError,
    CommandCodec,
    Evaluator,
    RowCodec,
    is_expr,
    row_place,
    note_text,
    written_value,
)
from unitracker.modulation import declared
from unitracker.records import EXPR, RowEval, record_types, ref_type
from unitracker.tablepatch import table_diff, unread_dropped
from unitracker.vm import EVENT_COLUMNS, IR_VERSION, MAX_COMMANDS, compile_phrase
from unitracker import xexpr
from unitracker.xexpr import ExpressionError, text_of
from unitracker.yamlstyle import Atom, Dumper, Flow, FlowMap, parse

VERSION = "1.17"
WIDTH = 100
PHRASE = "phrase"
_ENTRY = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<t>[+-]\d+)?$")
_ONE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SongFormatError(ValueError):
    """A document does not follow the unitracker v1.17 format."""


@functools.cache
def _library_text(name: str) -> str:
    path = resources.files("unitracker").joinpath(f"playroutines/{name}.yaml")
    if not path.is_file():
        raise SongFormatError(f"no playroutine {name!r} in the library")
    return path.read_text()


def library(name: str) -> dict:
    """The library definition of playroutine ``name``."""
    return yaml.safe_load(_library_text(name))


def song_meta(header, source: str) -> dict:
    """The ``meta`` mapping for a tune with PSID ``header``, lifted by ``source``."""
    return {
        "title": header.name,
        "author": header.author,
        "released": header.released,
        "source": source,
    }


class _RowEval(RowEval):
    """Row expressions, their errors the reader's and writer's."""

    error = SongFormatError


def _codecs(pr, rtypes, tables):
    """``(note offset, {type: RowCodec}, CommandCodec)`` for a playroutine and a song's tables."""
    offset = int(pr.get("note_offset", 0))
    ev = Evaluator(pr.get("functions"), tables, pr.get("constants"))
    try:
        rows = {n: RowCodec(rt, rt.codec, offset, ev) for n, rt in rtypes.items() if rt.codec}
        return offset, rows, CommandCodec(pr.get("commands"), rtypes)
    except CodecError as exc:
        raise SongFormatError(f"{pr.get('name')}: {exc}") from exc


def codecs(pr: dict, tables) -> dict:
    """``{type: RowCodec}`` of a playroutine over a song's tables."""
    return _codecs(pr, record_types(pr), tables)[1]


def _expr_fields(codec) -> set:
    """The raw fields a codec writes as expression or reference keys."""
    if codec is None:
        return set()
    keys = [k for shape in codec.shapes for k in shape.keys.values()]
    return {k["expr"] for k in keys if "expr" in k} | {k["field"] for k in keys if "ref" in k}


def _refuse(problem) -> None:
    """Raise for a retired form's reason, if there is one."""
    if problem is not None:
        raise SongFormatError(problem)


def _player(pr, call, *args):
    """``call(*args)``, its lifetime and envelope errors the player's."""
    try:
        return call(*args)
    except (lifetime.LifetimeError, envelope.EnvelopeError) as exc:
        raise SongFormatError(f"{pr['name']}: {exc}") from exc


# ---- writing -----------------------------------------------------------------------


class _Writer:
    """Turns the internal representation into a canonical v1.17 document."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, ir: dict):
        self.pr = ir["playroutine"]
        self.rtypes = record_types(self.pr)
        self.records = {
            name: {int(k): v for k, v in (rows or {}).items()}
            for name, rows in (ir.get("records") or {}).items()
        }
        inst = self.pr.get("instrument") or {}
        self.ins_type = inst.get("type")
        self.ins_base = int(inst.get("base", 0))
        self.offset, self.codecs, self.commands = _codecs(self.pr, self.rtypes, ir.get("tables"))
        self.exprs = {int(k): str(v) for k, v in (ir.get("exprs") or {}).items()}
        canon = {}
        self.xcanon = {i: canon.setdefault(t, i) for i, t in sorted(self.exprs.items())}
        self.first_row = {}
        self.order = []
        self.post = []
        self.referrers = {}
        self.programs = {}
        self.names = {}
        self.row = 0
        self.restarts = {}
        self.phrase_names = {}
        self.ir = _player(self.pr, envelope.Envelope(self.pr, self.rtypes).write, ir, self.records)
        rows = self.records.get(self.ins_type, {})
        written = lifetime.Lifetime(self.pr, self.rtypes).written
        self.kept, rows = _player(self.pr, written, self.ir["tracks"], self.ir["phrases"], rows)
        self.records.update({self.ins_type: rows} if self.ins_type in self.records else {})

    # -- objects: (type, index); chained types are programs keyed by their entry row --

    def _rows(self, rtype):
        return self.records.get(rtype.name, {})

    def _row(self, rtype, index):
        if rtype.name == EXPR:
            if index not in self.exprs:
                raise SongFormatError(f"expression {index} is referenced but not defined")
            return {}
        rows = self._rows(rtype)
        if index in rows:
            return rows[index]
        if rtype.templates:
            raise SongFormatError(f"{rtype.written} row {index} is referenced but not defined")
        return {}

    def program(self, rtype, entry):
        """``(row indices, loop index or None)`` of the program starting at ``entry``."""
        key = (rtype.name, entry)
        if key not in self.programs:
            path, pos, index = [], {}, entry
            while True:
                if index in pos:
                    loop = pos[index]
                    break
                row = self._row(rtype, index)
                pos[index] = len(path)
                path.append(index)
                if rtype.stop is not None and _RowEval(rtype).holds(rtype.stop, row):
                    loop = None
                    break
                index = int(row.get(rtype.chain, rtype.default(rtype.chain)))
                if rtype.none is not None and index == rtype.none:
                    loop = None
                    break
            self.programs[key] = (path, loop)
        return self.programs[key]

    def _refs(self, rtype, row):
        """``(field, (type, index))`` for each reference ``row`` makes."""
        out = []
        for field in rtype.refs:
            if field not in row:
                continue
            target = ref_type(rtype, field, row, _RowEval)
            if target is None:
                continue
            ttype = self.rtypes[target]
            index = int(row[field])
            if ttype.none is not None and index == ttype.none:
                continue
            out.append((field, self.key(target, index)))
        return out

    def key(self, rtype: str, index: int) -> tuple:
        """The object ``(type, index)``; equal expressions are one."""
        return (rtype, self.xcanon.get(index, index) if rtype == EXPR else index)

    def _children(self, key):
        rtype = self.rtypes[key[0]]
        if rtype.chain is not None:
            path, _ = self.program(rtype, key[1])
            return [ref for i in path for ref in self._refs(rtype, self._row(rtype, i))]
        return self._refs(rtype, self._row(rtype, key[1]))

    def visit(self, key, referrer):
        """Record a use of object ``key``, discovering it and its references on first use."""
        if key[0] not in self.rtypes:
            raise SongFormatError(f"reference to unknown record type {key[0]!r}")
        self.referrers.setdefault(key, []).append(referrer)
        if key in self.first_row:
            return
        self.first_row[key] = self.row
        self.order.append(key)
        for field, child in self._children(key):
            self.visit(child, (key, field))
        self.post.append(key)

    # -- phrases --

    def phrases(self, rows):
        """``{name: (base, transpose)}`` for each used phrase, and every phrase's rows.

        Phrases equal up to a transpose share the first-used one as their base.
        """
        compiled = {
            name: lifetime.column(self.kept, name, ev, self._folded(compile_phrase(ev)))
            for name, ev in (self.ir["phrases"] or {}).items()
        }
        bases, alias = {}, {}
        for row in rows:
            for entry in row:
                if not isinstance(entry, tuple) or entry[0] in alias:
                    continue
                key, shift = _transpose_key(compiled[entry[0]])
                base, base_shift = bases.setdefault(key, (entry[0], shift))
                alias[entry[0]] = (base, shift - base_shift)
        return alias, compiled

    def _folded(self, arr):
        """A compiled phrase with its event restarts on their notes (see ``eventrestart``)."""
        spec = self.pr.get(eventrestart.NAME)
        if not spec:
            return arr
        return eventrestart.fold(arr, int(spec["command"]), self._restart_of)

    def _restart_of(self, entry: int, gap: int):
        """The restart index of program ``entry`` run ``gap`` steps before a note, or None."""
        target = self.rtypes[self.commands.ref(int(self.pr[eventrestart.NAME]["command"]))]
        rtype, codec = self.rtypes[eventrestart.NAME], self.codecs[eventrestart.NAME]
        path, loop = self.program(target, entry)
        if loop is not None:
            return None
        rows = [{**target.fields, **self._row(target, i)} for i in path]
        raws = eventrestart.restart_rows(rows, target, rtype, codec, gap)
        if raws is None:
            return None
        return eventrestart.entry_of(
            raws, rtype, self.records.setdefault(rtype.name, {}), self.restarts
        )

    # -- representations --

    def ref_value(self, key):
        """A reference to ``key``: its name, its program written inline, or an expression."""
        if key[0] == EXPR:
            return xexpr.written(self.exprs[key[1]])
        if key in self.names:
            return Atom(self.names[key])
        return self.program_value(key)

    def row_value(self, rtype, row, place=None):
        """A row in its type's form, references resolved; shared keys where the codec fits.

        ``place`` is a program row's position (see ``RowCodec.encode``).
        """
        codec = self.codecs.get(rtype.name)
        shared = codec.encode(row, place) if codec is not None else None
        refs = dict(self._refs(rtype, row))
        if shared is not None:
            return self._shared_row(codec, shared, refs, place)
        if codec is not None and rtype.form == "map":
            raise SongFormatError(f"{rtype.written}: row {row} has no shared-vocabulary form")
        _refuse(retired.row(rtype, row, row, _RowEval(rtype).holds))
        _refuse(retired.unwritten(rtype, row))
        values = {}
        for field in rtype.fields:
            if field == rtype.chain:
                continue
            value = int(row.get(field, rtype.default(field)))
            if field in refs:
                values[field] = self.ref_value(refs[field])
            elif value != rtype.default(field):
                values[field] = value
        unknown = set(row) - set(rtype.fields)
        if unknown:
            raise SongFormatError(f"{rtype.written}: fields {sorted(unknown)} are not declared")
        if rtype.form == "map":
            return FlowMap(values)
        fields = [f for f in rtype.fields if f != rtype.chain]
        out = [values.get(f, rtype.default(f)) for f in fields]
        while out and fields[len(out) - 1] not in values:
            out.pop()
        return Flow(out)

    def _shared_row(self, codec, shared: dict, refs: dict, place=None) -> FlowMap:
        """A row's shared-vocabulary form: notes named, expressions referenced, refs resolved."""
        self._reads_back(codec, shared, place)
        out, number, named = {}, codec.expr_keys(), codec.ref_keys()
        for k, v in shared.items():
            if k == "note":
                v = Atom(note_text(*v))
            elif k in ("route", "ctrl"):
                v = written_value(k, v)
            elif k in named:
                v = Atom(self.names[self.key(*v)])
            elif is_expr(v):
                v = self.ref_value(self.key(EXPR, v[1]))
                v = str(v) if number.get(k) else v
            out[k] = v
        fields = _expr_fields(codec)
        out.update((f, self.ref_value(k)) for f, k in refs.items() if f not in fields)
        return FlowMap(out)

    def _reads_back(self, codec, shared, place):
        """Raise, naming the key, unless the player can read ``shared`` back."""
        try:
            codec.decode({k: written_value(k, v) for k, v in shared.items()}, place)
        except CodecError as exc:
            raise SongFormatError(f"{self.pr['name']}: {exc}") from exc

    def program_value(self, key):
        """A program as rows followed by ``end`` or ``{loop: k}``, or the one expression it is."""
        rtype = self.rtypes[key[0]]
        path, loop = self.program(rtype, key[1])
        if rtype.expr is not None and len(path) == 1 and loop is None:
            row = self._row(rtype, path[0])
            if row.get(rtype.expr) and not any(
                v != rtype.default(f) for f, v in row.items() if f not in (rtype.expr, rtype.chain)
            ):
                return self.ref_value(self.key(EXPR, int(row[rtype.expr])))
        rows = [
            self.row_value(rtype, self._row(rtype, i), row_place(k, len(path), loop is None))
            for k, i in enumerate(path)
        ]
        rows.append(Atom("end") if loop is None else FlowMap(loop=loop))
        return Flow(rows)

    def inline(self, key):
        """Whether ``key`` is written where it is used: an expression no command names, or a
        program inside its single instrument."""
        refs = self.referrers.get(key, [])
        if key[0] == EXPR:
            return "command" not in refs
        if any(refkeys.named_ref(self.codecs, r) for r in refs):
            return False
        rtype = self.rtypes[key[0]]
        if rtype.expr is not None and "command" not in refs:
            if not isinstance(self.program_value(key), Flow):
                return True
        return (
            rtype.chain is not None
            and len(refs) == 1
            and isinstance(refs[0], tuple)
            and isinstance(refs[0][0], tuple)
            and refs[0][0][0] == self.ins_type
        )

    def atoms(self, arr) -> Flow:
        """A phrase's rows as atoms."""
        out = []
        for start, steps in runs(arr):
            kind, note, ins, *cmds, keep = (int(x) for x in arr[start])
            name = None
            if ins:
                name = self.names[(self.ins_type, ins - self.ins_base)]
            commands = [self.command(c, a) for c, a in zip(cmds[::2], cmds[1::2])]
            commands = [c for c in commands if c is not None]
            commands += [f"{n}:0" for n in lifetime.names(keep)]
            out.append(Atom(format_atom(kind, note - self.offset, steps, name, commands)))
        return Flow(out)

    def command(self, cmd: int, raw: int) -> str | None:
        """A command as ``name:arg``, or None for no command."""
        if not (cmd or raw):
            return None
        if cmd == eventrestart.CODE:
            return f"{eventrestart.NAME}:@{self.names[(eventrestart.NAME, raw)]}"
        try:
            name, value = self.commands.encode(cmd, raw)
        except CodecError as exc:
            raise SongFormatError(f"{self.pr['name']}: {exc}") from exc
        if isinstance(value, tuple) and self.commands.inline(name):
            rtype = self.rtypes[value[0]]
            return f"{name}:{inline.written(rtype, self._row(rtype, value[1]))}"
        if isinstance(value, tuple):
            return f"{name}:@{self.names[self.key(*value)]}"
        return f"{name}:{value}"

    # -- document --

    def _event_refs(self, arr):
        for row in arr:
            if row[2]:
                yield (self.ins_type, int(row[2]) - self.ins_base), "event"
            cmds = row[3:EVENT_COLUMNS]
            for cmd, arg in zip(cmds[::2], cmds[1::2]):
                if cmd == eventrestart.CODE:
                    yield (eventrestart.NAME, int(arg)), "command"
                    continue
                target = self.commands.ref(int(cmd))
                if target is not None and not self.commands.inline(int(cmd)):
                    none = self.rtypes[target].none
                    if none is None or int(arg) != none:
                        yield self.key(target, int(arg)), "command"

    def _roots(self):
        """Visit the playroutine's pinned rows and the fixed rows; ``{type: indices}`` fixed."""
        for rtype in self.rtypes.values():
            for index in rtype.pin:
                self.visit((rtype.name, index), "playroutine")
        fixed = {}
        for rtype in (rt for rt in self.rtypes.values() if rt.fixed):
            fixed[rtype.name] = sorted(self._rows(rtype))
            for index in fixed[rtype.name]:
                for field, child in self._refs(rtype, self._row(rtype, index)):
                    self.visit(child, ((rtype.name, index), field))
        return fixed

    def _discover(self, rows, alias, compiled):
        """Visit every object in first-use order and name it; each base phrase's first row."""
        phrase_row = {}
        for r, row in enumerate(rows):
            self.row = r
            for entry in row:
                if isinstance(entry, tuple) and alias[entry[0]][0] not in phrase_row:
                    base = alias[entry[0]][0]
                    phrase_row[base] = r
                    for key, kind in self._event_refs(compiled[base]):
                        self.visit(key, kind)
        counters = {}
        for key in self.order:
            if not self.inline(key):
                counters[key[0]] = counters.get(key[0], 0) + 1
                self.names[key] = f"{self.rtypes[key[0]].prefix}{counters[key[0]]}"
        return phrase_row

    def _defs(self, count, fixed, phrase_row, compiled):
        """Each row's definitions: fixed rows first, then objects and phrases at first use."""
        defs = [{} for _ in range(count)]
        for name, indices in fixed.items():
            rtype = self.rtypes[name]
            defs[0].setdefault(rtype.written, {}).update(
                {i: self.row_value(rtype, self._row(rtype, i)) for i in indices}
            )
        for key in self.post:
            if self.inline(key):
                continue
            rtype = self.rtypes[key[0]]
            if rtype.name == EXPR:
                value = self.ref_value(key)
            elif rtype.chain is not None:
                value = self.program_value(key)
            else:
                value = self.row_value(rtype, self._row(rtype, key[1]))
            defs[self.first_row[key]].setdefault(rtype.written, {})[Atom(self.names[key])] = value
        for i, (base, r) in enumerate(phrase_row.items(), start=1):
            defs[r].setdefault(PHRASE, {})[Atom(f"p{i}")] = self.atoms(compiled[base])
        return defs

    def song(self):
        """``(hints, song rows, loop)`` with definitions placed at first use."""
        rows, loop = timeline.to_rows(
            self.ir["tracks"],
            {n: len(compile_phrase(ev)) for n, ev in (self.ir["phrases"] or {}).items()},
        )
        alias, compiled = self.phrases(rows)
        fixed = self._roots()
        phrase_row = self._discover(rows, alias, compiled)
        defs = self._defs(len(rows), fixed, phrase_row, compiled)
        pnames = {base: f"p{i}" for i, base in enumerate(phrase_row, start=1)}
        self.phrase_names = {p: (pnames[b], d) for p, (b, d) in alias.items()}
        out = []
        for row, item_defs in zip(rows, defs):
            item = {"def": item_defs} if item_defs else {}
            item["play"] = Flow(self._entry(e, alias, pnames) for e in row)
            out.append(item)
        reason, found = hints.written(self.ir.get("hints"), {len(compiled[b]) for b in phrase_row})
        _refuse(reason)
        return found, out, loop

    @staticmethod
    def _entry(entry, alias, pnames):
        if entry is None:
            return None
        if entry == timeline.END:
            return Atom(timeline.END)
        base, delta = alias[entry[0]]
        trans = delta + entry[1]
        name = pnames[base]
        return Atom(name if trans == 0 else f"{name}{trans:+d}")

    def playroutine_entry(self, embed: bool):
        """The ``playroutine`` entry: a library reference with differences, or embedded."""
        name = self.pr["name"]
        tables = {k: [int(x) for x in v] for k, v in (self.ir.get("tables") or {}).items()}
        if embed:
            rest = {k: v for k, v in self.pr.items() if k not in ("name", "constants", "tables")}
            pr = {"name": name, "constants": FlowMap(self.pr.get("constants") or {})}
            pr["tables"] = {k: Flow(v) for k, v in tables.items()}
            pr.update(copy.deepcopy(rest))
            return pr
        lib = library(name)
        own = ("constants", "tables")
        if {k: v for k, v in self.pr.items() if k not in own} != {
            k: v for k, v in lib.items() if k not in own
        }:
            raise SongFormatError(
                f"the song's playroutine differs from the library {name!r}; write with embed"
            )
        pr = FlowMap(ref=name)
        libc = lib.get("constants") or {}
        consts = {k: v for k, v in (self.pr.get("constants") or {}).items() if libc.get(k) != v}
        if consts:
            pr["constants"] = FlowMap(consts)
        diff = unread_dropped(table_diff(tables, lib.get("tables") or {}), self.ir)
        return {**pr, "tables": diff} if diff else pr

    def environment(self) -> dict:
        """``clock`` and ``voices`` where they differ from the defaults."""
        defaults = self.pr.get("defaults") or {}
        env = {}
        for key in ("clock", "voices"):
            if key in self.ir and self.ir[key] != defaults.get(key):
                env[key] = FlowMap(self.ir[key]) if isinstance(self.ir[key], dict) else self.ir[key]
        return env


def _transpose_key(arr):
    """A phrase's rows with notes relative to its first note, and that first note."""
    notes = arr[:, 0] == 1
    shift = int(arr[notes, 1][0]) if notes.any() else 0
    norm = arr.copy()
    norm[notes, 1] -= shift
    return norm.tobytes() + str(norm.shape).encode(), shift


def dumps(ir: dict, embed: bool = False, respell=None) -> str:
    """A canonical v1.17 document for internal-representation song ``ir``.

    With ``embed`` the playroutine and its tables are written in full instead of by reference;
    ``respell(doc)`` rewrites the written structure before it is dumped (spelling studies).
    """
    writer = _Writer(ir)
    meta = ir.get("meta") or {}
    doc = {
        "unitracker": Atom(VERSION),
        "playroutine": writer.playroutine_entry(embed),
        "meta": FlowMap({k: meta[k] for k in ("title", "author", "released") if k in meta}),
    }
    found, rows, loop = writer.song()
    if found:
        doc["hints"] = FlowMap(found)
    doc.update(writer.environment())
    doc["song"] = rows
    doc["loop"] = loop
    doc = respell(doc) if respell else doc
    return yaml.dump(doc, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=WIDTH)


# ---- reading -----------------------------------------------------------------------


class _Reader:
    """Expands a v1.17 document to the internal representation."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, doc: dict):
        self.doc = doc
        self.pr = self._playroutine(doc.get("playroutine"))
        self.rtypes = record_types(self.pr)
        inst = self.pr.get("instrument") or {}
        self.ins_type = inst.get("type")
        self.ins_base = int(inst.get("base", 0))
        self.offset, self.codecs, self.commands = _codecs(self.pr, self.rtypes, self.tables)
        self.exprs = {}
        self.named_tables = set((declared(self.pr).get("tables") or {}))
        self.next_index = {name: rt.first for name, rt in self.rtypes.items()}
        self.names = {}
        self.pending = []
        self.records = {name: {} for name in self.rtypes}
        self.written = {rt.written: rt for rt in self.rtypes.values()}
        self.played = {}
        self.lifetime = lifetime.Lifetime(self.pr, self.rtypes)

    def _playroutine(self, spec) -> dict:
        if not isinstance(spec, dict):
            raise SongFormatError("playroutine must be a mapping")
        if "ref" not in spec:
            pr = copy.deepcopy(spec)
            self.tables = {
                k: [int(x) for x in v] for k, v in (pr.pop("tables", None) or {}).items()
            }
            self.defaults = pr.get("defaults") or {}
            return pr
        pr = library(spec["ref"])
        pr["constants"] = {**(pr.get("constants") or {}), **(spec.get("constants") or {})}
        tables = {k: [int(x) for x in v] for k, v in (pr.pop("tables", None) or {}).items()}
        for name, diff in (spec.get("tables") or {}).items():
            if isinstance(diff, list):
                tables[name] = [int(x) for x in diff]
            else:
                if name not in tables:
                    raise SongFormatError(f"table {name!r} has no library default to patch")
                for i, v in diff.items():
                    tables[name][int(i)] = int(v)
        self.tables = tables
        self.defaults = pr.get("defaults") or {}
        return pr

    def _alloc(self, rtype, count) -> int:
        start = self.next_index[rtype.name]
        if start + count - 1 > rtype.max:
            raise SongFormatError(f"{rtype.written}: more than {rtype.max} rows")
        self.next_index[rtype.name] = start + count
        return start

    def expr(self, value) -> int:
        """The index of a new expression for a written value, checked against the grammar."""
        try:
            text = text_of(value)
            xexpr.source(text, self.named_tables)
        except ExpressionError as exc:
            raise SongFormatError(f"{self.pr['name']}: {exc}") from exc
        self.exprs[len(self.exprs) + 1] = text
        return len(self.exprs)

    def _proto(self, rtype, value, place=None) -> dict:
        """A row's fields; references stay ``("name", str)`` or allocate inline programs.

        ``place`` is a program row's position (see ``RowCodec.encode``).
        """
        # pylint: disable=too-many-branches
        codec = self.codecs.get(rtype.name)
        decoded = set()
        if isinstance(value, dict):
            _refuse(retired.fields(rtype, value))
        if codec is not None and isinstance(value, dict):
            row, decoded = self._decoded(codec, rtype, value, place)
        elif rtype.form == "map":
            if not isinstance(value, dict):
                raise SongFormatError(f"{rtype.written}: a row must be a mapping")
            row = dict(value)
        else:
            row = self._list_row(rtype, codec, value, place)
            _refuse(retired.row(rtype, row, value, _RowEval(rtype).holds))
        for field, val in list(row.items()):
            if field not in rtype.fields or field == rtype.chain:
                raise SongFormatError(f"{rtype.written}: unknown field {field!r}")
            if field in decoded:
                continue
            plain = {k: v for k, v in row.items() if isinstance(v, int)}
            target = ref_type(rtype, field, plain, _RowEval) if field in rtype.refs else None
            if target == EXPR and isinstance(val, (str, int)) and not isinstance(val, bool):
                row[field] = (EXPR, self.expr(val))
            elif (
                target is not None
                and self.rtypes[target].expr
                and isinstance(val, (str, int))
                and not refkeys.is_name(self.names, self.rtypes, val, target)
            ):
                row[field] = self._program(self.rtypes[target], val)
            elif isinstance(val, str):
                row[field] = ("name", val)
            elif isinstance(val, list):
                plain = {k: v for k, v in row.items() if isinstance(v, int)}
                target = ref_type(rtype, field, plain, _RowEval)
                if target is None or self.rtypes[target].chain is None:
                    raise SongFormatError(f"{rtype.written}.{field} cannot hold a program")
                row[field] = self._program(self.rtypes[target], val)
            elif not isinstance(val, int) or isinstance(val, bool):
                raise SongFormatError(f"{rtype.written}.{field}: bad value {val!r}")
        return row

    def _decoded(self, codec, rtype, value: dict, place) -> tuple:
        """``(row, decoded fields)`` of a codec row, its named references kept as written."""
        value = dict(value)
        for key, number in codec.expr_keys().items():
            v = value.get(key)
            if isinstance(v, str) or (not number and isinstance(v, int) and key in value):
                value[key] = (EXPR, self.expr(v))
        named = {
            k: v
            for k, v in value.items()
            if k not in ROW_KEYS and isinstance(v, (str, list)) and k in rtype.refs
        }
        try:
            names = refkeys.placeholders(self.names, self.rtypes, codec, value)
            row = codec.decode({k: v for k, v in value.items() if k not in named}, place)
        except ValueError as exc:
            raise SongFormatError(f"{self.pr['name']}: {exc}") from exc
        return {**row, **named, **names}, set(row) - set(named)

    @staticmethod
    def _list_row(rtype, codec, value, place) -> dict:
        """A list row's fields; a row the codec writes with shared keys has no list form."""
        fields = [f for f in rtype.fields if f != rtype.chain]
        if not isinstance(value, list) or len(value) > len(fields):
            raise SongFormatError(f"{rtype.written}: bad row {value!r}")
        row = dict(zip(fields, value))
        if codec is not None and codec.encode(row, place) is not None:
            raise SongFormatError(f"{rtype.written}: write row {value!r} with shared keys")
        return row

    def _program(self, rtype, value) -> int:
        """Allocate a program's rows, or the one row of an expression program; its entry index."""
        if rtype.expr is not None and isinstance(value, (str, int)) and not isinstance(value, bool):
            start = self._alloc(rtype, 1)
            row = {rtype.expr: (EXPR, self.expr(value))}
            row[rtype.chain] = rtype.none if rtype.none is not None else 0
            self.pending.append((rtype, start, row))
            return start
        if not isinstance(value, list) or len(value) < 2:
            raise SongFormatError(f"{rtype.written}: a program is rows then end or loop")
        rows, term = value[:-1], value[-1]
        start = self._alloc(rtype, len(rows))
        for k, raw in enumerate(rows):
            row = self._proto(rtype, raw, row_place(k, len(rows), term == timeline.END))
            row[rtype.chain] = start + k + 1
            self.pending.append((rtype, start + k, row))
        last = self.pending[-1][2]
        if term == timeline.END:
            last[rtype.chain] = rtype.none if rtype.none is not None else 0
            self._end(rtype, last, rows[-1], row_place(len(rows) - 1, len(rows), True))
            if rtype.stop is not None:
                plain = {f: v for f, v in last.items() if isinstance(v, int)}
                if not _RowEval(rtype).holds(rtype.stop, plain):
                    raise SongFormatError(f"{rtype.written}: a program can only end on a stop row")
        elif isinstance(term, dict) and set(term) == {"loop"} and 0 <= term["loop"] < len(rows):
            last[rtype.chain] = start + int(term["loop"])
        else:
            raise SongFormatError(f"{rtype.written}: bad program end {term!r}")
        return start

    def _end(self, rtype, last, written, place):
        """Apply the type's ``end`` fields to a program's last row; it must still read the same."""
        if not rtype.end:
            return
        last.update({f: int(v) for f, v in rtype.end.items()})
        codec = self.codecs.get(rtype.name)
        if codec is not None and isinstance(written, dict):
            try:
                raw = {k: v[1] if is_expr(v) else v for k, v in last.items()}
                codec.check_end(
                    {k: v for k, v in raw.items() if isinstance(v, int)}, written, place
                )
            except CodecError as exc:
                raise SongFormatError(f"{self.pr['name']}: {exc}") from exc

    def _define(self, written, name, value):
        rtype = self.written.get(written)
        if written in RETIRED:
            raise SongFormatError(f"record type {written!r} is {RETIRED[written]}")
        if rtype is None:
            raise SongFormatError(f"unknown record type {written!r}")
        tname = rtype.name
        if rtype.fixed:
            self.pending.append((rtype, int(name), self._proto(rtype, value)))
            return
        if not isinstance(name, str) or not _ONE.match(name):
            raise SongFormatError(f"{written}: bad name {name!r}")
        if name in self.names:
            raise SongFormatError(f"name {name!r} defined twice")
        if tname == EXPR:
            self.names[name] = (EXPR, self.expr(value))
            return
        if rtype.chain is not None:
            index = self._program(rtype, value)
        else:
            index = self._alloc(rtype, 1)
            self.pending.append((rtype, index, self._proto(rtype, value)))
        self.names[name] = (tname, index)

    def lookup(self, name, tname) -> int:
        """The index of object ``name``, which must be a ``tname``."""
        if name not in self.names:
            raise SongFormatError(f"unknown name {name!r}")
        kind, index = self.names[name]
        if kind != tname:
            kind, tname = self.rtypes[kind].written, self.rtypes[tname].written
            raise SongFormatError(f"{name!r} is a {kind}, not a {tname}")
        return index

    def _resolve(self):
        for rtype, index, row in self.pending:
            out = {}
            for field, val in row.items():
                if isinstance(val, tuple) and val[0] == EXPR:
                    val = val[1]
                elif isinstance(val, tuple):
                    plain = {k: v for k, v in row.items() if isinstance(v, int)}
                    target = ref_type(rtype, field, plain, _RowEval)
                    if target is None:
                        raise SongFormatError(f"{rtype.written}.{field} is not a reference here")
                    val = self.lookup(val[1], target)
                out[field] = int(val)
            if index in self.records[rtype.name]:
                raise SongFormatError(f"{rtype.written} row {index} defined twice")
            self.records[rtype.name][index] = out

    def event(self, atom) -> dict:
        """An internal event for an atom."""
        parsed = parse_atom(atom)
        event = {}
        if parsed["kind"] == 1:
            event["note"] = parsed["note"] + self.offset
        elif parsed["kind"]:
            event["note"] = "off" if parsed["kind"] == 2 else "on"
        if parsed["ins"] is not None:
            event["instrument"] = self.lookup(parsed["ins"], self.ins_type) + self.ins_base
        written = [c for c in parsed["commands"] if c[0] not in lifetime.NAMES]
        if len(written) > MAX_COMMANDS:
            raise SongFormatError(f"more than {MAX_COMMANDS} commands in {atom!r}")
        commands = [self.command(cmd, arg, atom) for cmd, arg in written]
        refs = {self.commands.ref(code) for code, _ in commands}
        keep = _player(self.pr, self.lifetime.mask, parsed["commands"], parsed["kind"], refs)
        event.update({"keep": keep} if keep else {})
        for code, index in [c for c in commands if c[0] == eventrestart.CODE]:
            event["restart"] = index
            commands.remove([code, index])
        if len(commands) == 1:
            event["command"] = commands[0]
        elif commands:
            event["commands"] = commands
        if parsed["steps"] > 1:
            event["steps"] = parsed["steps"]
        return event

    def _event_restart(self, tname: str) -> bool:
        """Whether type ``tname`` holds event restarts, which the reader plays as commands."""
        return tname == eventrestart.NAME and bool(self.pr.get(eventrestart.NAME))

    def _unfolded(self, events: list, phrase: str) -> list:
        """A phrase's events with each restart played before its note (see ``eventrestart``)."""
        restarts = [e for e in events if "restart" in e]
        if not restarts:
            return events
        try:
            return eventrestart.unfold(
                events, int(self.pr[eventrestart.NAME]["command"]), self._played
            )
        except eventrestart.RestartError as exc:
            raise SongFormatError(f"{self.pr['name']}: phrase {phrase}: {exc}") from exc

    def _played(self, index: int) -> tuple:
        """``(entry, steps)``: the program event restart ``index`` runs, and how far ahead."""
        if index not in self.played:
            target = self.rtypes[self.commands.ref(int(self.pr[eventrestart.NAME]["command"]))]
            rtype, codec = self.rtypes[eventrestart.NAME], self.codecs[eventrestart.NAME]
            try:
                self.played[index] = eventrestart.played(
                    index, self.records, (rtype, target), codec, self._alloc
                )
            except eventrestart.RestartError as exc:
                raise SongFormatError(f"{self.pr['name']}: {exc}") from exc
        return self.played[index]

    def command(self, cmd, arg, atom) -> list[int]:
        """``[code, arg]`` for one written command of ``atom``."""
        if cmd in RETIRED:
            raise SongFormatError(f"!{cmd} is {RETIRED[cmd]} in {atom!r}")
        if cmd == eventrestart.NAME and self._event_restart(cmd) and isinstance(arg, str):
            return [eventrestart.CODE, self.lookup(arg[1:], eventrestart.NAME)]
        if isinstance(arg, tuple) or (isinstance(arg, int) and self.commands.inline(cmd)):
            arg = self._inline(cmd, arg if isinstance(arg, tuple) else (arg,), atom)
        elif isinstance(arg, str) and arg != NONE:
            if arg[1:] not in self.names:
                raise SongFormatError(f"unknown name {arg[1:]!r} in {atom!r}")
            arg = self.names[arg[1:]]
        try:
            return list(self.commands.decode(cmd, arg))
        except CodecError as exc:
            raise SongFormatError(f"{self.pr['name']}: {exc} in {atom!r}") from exc

    def _inline(self, cmd: str, values: tuple, atom) -> tuple:
        """``(type, index)`` of the row an inline argument writes, allocated on first use."""
        ref = self.commands.inline(cmd)
        try:
            return inline.allocated(self.rtypes.get(ref), values, self.records, self._alloc)
        except ValueError as exc:
            raise SongFormatError(f"!{cmd}: {exc} in {atom!r}") from exc

    def _collect(self, song):
        """Define every record and phrase; ``(phrase atoms, play rows)``."""
        if not isinstance(song, list) or not song:
            raise SongFormatError("song must be a list of rows")
        phrases, rows = {}, []
        for r, item in enumerate(song):
            if not isinstance(item, dict) or "play" not in item:
                raise SongFormatError(f"song row {r} needs a play list")
            for tname, entries in (item.get("def") or {}).items():
                for name, value in (entries or {}).items():
                    if tname == PHRASE:
                        if name in phrases:
                            raise SongFormatError(f"name {name!r} defined twice")
                        phrases[name] = value
                    else:
                        self._define(tname, name, value)
            rows.append([self._entry(e) for e in item["play"]])
        self._resolve()
        return phrases, rows

    def read(self) -> dict:
        """The internal representation."""
        doc = self.doc
        phrases, rows = self._collect(doc.get("song"))
        events = {
            name: self._unfolded([self.event(a) for a in atoms], name)
            for name, atoms in phrases.items()
        }
        lengths = {name: len(compile_phrase(ev)) for name, ev in events.items()}
        voices = int(doc.get("voices", self.defaults.get("voices", 3)))
        tracks = timeline.from_rows(rows, doc.get("loop"), lengths, voices)
        _player(self.pr, self.lifetime.read, tracks, events, self.records, self.names)
        ir = {
            "unitracker": IR_VERSION,
            "meta": dict(doc.get("meta") or {}),
            "hints": dict(doc.get("hints") or {}),
            "clock": dict(doc.get("clock") or self.defaults.get("clock") or {}),
            "voices": voices,
            "tables": self.tables,
            "playroutine": self.pr,
            "records": {k: v for k, v in self.records.items() if v and not self._event_restart(k)},
            "exprs": self.exprs,
            "tracks": tracks,
            "phrases": events,
        }
        return _player(self.pr, envelope.Envelope(self.pr, self.rtypes).read, ir, self.records)

    @staticmethod
    def _entry(entry):
        if entry is None:
            return None
        if entry == timeline.END:
            return timeline.END
        match = _ENTRY.match(str(entry))
        if match is None:
            raise SongFormatError(f"bad play entry {entry!r}")
        return match["name"], int(match["t"] or 0)


def loads(text: str) -> dict:
    """The internal representation of a v1.17 document."""
    doc = parse(text)
    version = doc.get("unitracker") if isinstance(doc, dict) else None
    if getattr(version, "text", version) != VERSION:
        raise SongFormatError(f"not a unitracker v{VERSION} document")
    _refuse(retired.document(doc) or hints.problem(doc.get("hints")))
    return _Reader(doc).read()


def load(path) -> dict:
    """The internal representation of the v1.17 document at ``path``."""
    with open(path, encoding="utf-8") as fh:
        return loads(fh.read())


def dump(ir: dict, path, embed: bool = False) -> None:
    """Write ``ir`` as a v1.17 document to ``path``, atomically."""
    text = dumps(ir, embed)
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".unitracker-", suffix=".tmp")
    umask = os.umask(0)
    os.umask(umask)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o666 & ~umask)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
