"""Read and write unitracker v1.8 documents: a header, then one timeline of definitions and plays.

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

from unitracker import eventrestart, timeline
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
    note_text,
    written_value,
)
from unitracker.expr import ExprError, Resolver
from unitracker.modulation import declared
from unitracker.reach import ReachabilityError, table_reads
from unitracker.records import EXPR, record_types
from unitracker.vm import IR_VERSION, MAX_COMMANDS, compile_phrase
from unitracker import xexpr
from unitracker.xexpr import ExpressionError, text_of

VERSION = 1.8
WIDTH = 100
PHRASE = "phrase"
_ENTRY = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<t>[+-]\d+)?$")
_ONE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
RETIRED_KEYS = {"registers": "retired in v1.6: the player starts from a zeroed SID"}
_RESTART = "retired in v1.8: write the hard restart as the instrument's restart program"
RETIRED_FIELDS = {"gt": _RESTART, "fw": _RESTART, "hrad": _RESTART, "hrsr": _RESTART}
RETIRED_FIELDS["firstctrl"] = _RESTART
RETIRED_BITS = {"flags": (0x0F, _RESTART)}


class SongFormatError(ValueError):
    """A document does not follow the unitracker v1.8 format."""


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


class _RowEval(Resolver):
    """Evaluates a record type's ``when`` expressions over one row's fields."""

    def __init__(self, rtype):
        super().__init__()
        self.rtype = rtype

    def name(self, name):
        return f"R.get({name!r}, {self.rtype.default(name)})"

    def holds(self, text, row) -> bool:
        """Whether ``text`` is true for ``row``."""
        try:
            return bool(eval(self.compile(text), {"R": row}))  # pylint: disable=eval-used
        except ExprError as exc:
            raise SongFormatError(f"{self.rtype.name}: {exc}") from exc


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
    """The raw fields a codec writes as expression keys."""
    if codec is None:
        return set()
    return {k["expr"] for shape in codec.shapes for k in shape.keys.values() if "expr" in k}


def _ref_type(rtype, field, row):
    """The record type ``field`` of ``row`` references, or None."""
    for target, when in rtype.refs.get(field, ()):
        if when is None or _RowEval(rtype).holds(when, row):
            return target
    return None


def _place(k: int, count: int, ends: bool) -> dict:
    """The position of row ``k`` of a program of ``count`` rows that ends or loops."""
    return {"first": int(k == 0), "last": int(ends and k == count - 1)}


def _retired(rtype, value: dict) -> None:
    """Raise, naming it, for a field or flag bit the format has retired."""
    for key in RETIRED_FIELDS.keys() & value.keys():
        raise SongFormatError(f"{rtype.name}.{key} is {RETIRED_FIELDS[key]}")
    for key, (mask, why) in RETIRED_BITS.items():
        if isinstance(value.get(key), int) and value[key] & mask:
            raise SongFormatError(f"{rtype.name}.{key} bits {mask:#04x} are {why}")


# ---- writing -----------------------------------------------------------------------


class Flow(list):
    """A list written in flow style."""


class FlowMap(dict):
    """A mapping written in flow style."""


class Atom(str):
    """A plain scalar written as is: event atoms, names, entries."""


class _Dumper(yaml.SafeDumper):
    """Block structure, flow leaves, and atoms as unquoted plain scalars."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)

    def choose_scalar_style(self):
        if self.event.style == "A":
            return ""
        return super().choose_scalar_style()


_SIGNED = re.compile(r"^[+-]\d+$")


def _atom(dumper, data):
    if data in ("off", "on"):
        tag = "tag:yaml.org,2002:bool"
    elif _SIGNED.match(data):
        tag = "tag:yaml.org,2002:int"
    else:
        tag = "tag:yaml.org,2002:str"
    return dumper.represent_scalar(tag, str(data), style="A")


_Dumper.add_representer(Flow, lambda d, x: d.represent_sequence("tag:yaml.org,2002:seq", x, True))
_Dumper.add_representer(FlowMap, lambda d, x: d.represent_mapping("tag:yaml.org,2002:map", x, True))
_Dumper.add_representer(Atom, _atom)
_Dumper.add_representer(type(None), lambda d, _: d.represent_scalar("tag:yaml.org,2002:null", "~"))


class _Writer:
    """Turns the internal representation into a canonical v1.8 document."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, ir: dict):
        self.ir = ir
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
            raise SongFormatError(f"{rtype.name} row {index} is referenced but not defined")
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
            target = _ref_type(rtype, field, row)
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
            name: self._folded(compile_phrase(ev))
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
        fields = [f for f in rtype.fields if f != rtype.chain]
        rows = [{**target.fields, **self._row(target, i)} for i in path]
        if loop is not None or any(
            r[f] != d
            for r in rows
            for f, d in target.fields.items()
            if f not in fields + [target.chain]
        ):
            return None
        rows = [{f: r[f] for f in fields} for r in rows]
        rows[-1].update({f: target.default(f) for f in target.end})
        try:
            shared = [codec.encode(r, _place(k, len(rows) + 1, True)) for k, r in enumerate(rows)]
            if None in shared:
                return None
            rest = gap - sum(s.get("hold", 1) for s in shared[:-1])
            if rest < 1:
                return None
            shared[-1] = {k: v for k, v in shared[-1].items() if k != "hold"}
            shared[-1].update({"hold": rest} if rest != 1 else {})
            shared.append({})
            raws = [codec.decode(s, _place(k, len(shared), True)) for k, s in enumerate(shared)]
        except CodecError:
            return None
        key = tuple(tuple(sorted(r.items())) for r in raws)
        if key not in self.restarts:
            table = self.records.setdefault(rtype.name, {})
            self.restarts[key] = start = max([rtype.first - 1, *table]) + 1
            for k, raw in enumerate(raws):
                nxt = start + k + 1 if k + 1 < len(raws) else rtype.none
                table[start + k] = {**raw, rtype.chain: nxt}
        return self.restarts[key]

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
            raise SongFormatError(f"{rtype.name}: row {row} has no shared-vocabulary form")
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
            raise SongFormatError(f"{rtype.name}: fields {sorted(unknown)} are not declared")
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
        out, number = {}, codec.expr_keys()
        for k, v in shared.items():
            if k == "note":
                v = Atom(note_text(*v))
            elif k in ("route", "ctrl"):
                v = written_value(k, v)
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
            self.row_value(rtype, self._row(rtype, i), _place(k, len(path), loop is None))
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
            kind, note, ins, *cmds = (int(x) for x in arr[start])
            name = None
            if ins:
                name = self.names[(self.ins_type, ins - self.ins_base)]
            commands = [self.command(c, a) for c, a in zip(cmds[::2], cmds[1::2])]
            commands = [c for c in commands if c is not None]
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
        if isinstance(value, tuple):
            return f"{name}:@{self.names[self.key(*value)]}"
        return f"{name}:{value}"

    # -- document --

    def _event_refs(self, arr):
        for row in arr:
            if row[2]:
                yield (self.ins_type, int(row[2]) - self.ins_base), "event"
            for cmd, arg in zip(row[3::2], row[4::2]):
                if cmd == eventrestart.CODE:
                    yield (eventrestart.NAME, int(arg)), "command"
                    continue
                target = self.commands.ref(int(cmd))
                if target is not None:
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
            defs[0].setdefault(name, {}).update(
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
            defs[self.first_row[key]].setdefault(key[0], {})[Atom(self.names[key])] = value
        for i, (base, r) in enumerate(phrase_row.items(), start=1):
            defs[r].setdefault(PHRASE, {})[Atom(f"p{i}")] = self.atoms(compiled[base])
        return defs

    def song(self):
        """``(hints, song rows, loop)`` with definitions placed at first use."""
        lengths = {n: len(compile_phrase(ev)) for n, ev in (self.ir["phrases"] or {}).items()}
        rows, loop = timeline.to_rows(self.ir["tracks"], lengths)
        alias, compiled = self.phrases(rows)
        fixed = self._roots()
        phrase_row = self._discover(rows, alias, compiled)
        defs = self._defs(len(rows), fixed, phrase_row, compiled)
        pnames = {base: f"p{i}" for i, base in enumerate(phrase_row, start=1)}
        out = []
        for row, item_defs in zip(rows, defs):
            item = {"def": item_defs} if item_defs else {}
            item["play"] = Flow(self._entry(e, alias, pnames) for e in row)
            out.append(item)
        return self._hints({len(compiled[b]) for b in phrase_row}), out, loop

    def _hints(self, steps):
        """The lifter's hints, plus ``phrase_steps`` when every phrase has one length."""
        hints = dict(self.ir.get("hints") or {})
        if len(steps) == 1:
            hints["phrase_steps"] = steps.pop()
        return hints

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
        diff = _unread_dropped(_table_diff(tables, lib.get("tables") or {}), self.ir)
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


def _table_diff(tables, defaults) -> dict:
    """Tables as sparse ``{index: value}`` differences from the library defaults."""
    out = {}
    for name, values in tables.items():
        base = [int(x) for x in defaults.get(name) or []]
        if len(base) != len(values):
            out[name] = Flow(values)
            continue
        diff = FlowMap({i: v for i, (v, d) in enumerate(zip(values, base)) if v != d})
        if diff:
            out[name] = diff
    return out


def _unread_dropped(diff: dict, ir: dict) -> dict:
    """``diff`` without the patch entries the song never reads.

    An entry no reachable tick reads cannot change the output, so the reader's library
    default stands in for it. If reachability can't be established the patch is kept whole.
    """
    if not diff:
        return diff
    try:
        reads = table_reads(ir)[0]
    except ReachabilityError:
        return diff
    out = {}
    for name, values in diff.items():
        if isinstance(values, Flow):
            out[name] = values
            continue
        kept = FlowMap({i: v for i, v in values.items() if i in reads.get(name, ())})
        if kept:
            out[name] = kept
    return out


def dumps(ir: dict, embed: bool = False) -> str:
    """A canonical v1.8 document for internal-representation song ``ir``.

    With ``embed`` the playroutine and its tables are written in full instead of by reference.
    """
    writer = _Writer(ir)
    meta = ir.get("meta") or {}
    doc = {
        "unitracker": VERSION,
        "playroutine": writer.playroutine_entry(embed),
        "meta": FlowMap({k: meta[k] for k in ("title", "author", "released") if k in meta}),
    }
    hints, rows, loop = writer.song()
    if hints:
        doc["hints"] = FlowMap(hints)
    doc.update(writer.environment())
    doc["song"] = rows
    doc["loop"] = loop
    return yaml.dump(doc, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=WIDTH)


# ---- reading -----------------------------------------------------------------------


class _Reader:
    """Expands a v1.8 document to the internal representation."""

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
        self.played = {}

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
            raise SongFormatError(f"{rtype.name}: more than {rtype.max} rows")
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
            _retired(rtype, value)
        if codec is not None and isinstance(value, dict):
            row, decoded = self._decoded(codec, rtype, value, place)
        elif rtype.form == "map":
            if not isinstance(value, dict):
                raise SongFormatError(f"{rtype.name}: a row must be a mapping")
            row = dict(value)
        else:
            row = self._list_row(rtype, codec, value, place)
        for field, val in list(row.items()):
            if field not in rtype.fields or field == rtype.chain:
                raise SongFormatError(f"{rtype.name}: unknown field {field!r}")
            if field in decoded:
                continue
            plain = {k: v for k, v in row.items() if isinstance(v, int)}
            target = _ref_type(rtype, field, plain) if field in rtype.refs else None
            if target == EXPR and isinstance(val, (str, int)) and not isinstance(val, bool):
                row[field] = (EXPR, self.expr(val))
            elif target is not None and self.rtypes[target].expr and isinstance(val, (str, int)):
                row[field] = self._program(self.rtypes[target], val)
            elif isinstance(val, str):
                row[field] = ("name", val)
            elif isinstance(val, list):
                plain = {k: v for k, v in row.items() if isinstance(v, int)}
                target = _ref_type(rtype, field, plain)
                if target is None or self.rtypes[target].chain is None:
                    raise SongFormatError(f"{rtype.name}.{field} cannot hold a program")
                row[field] = self._program(self.rtypes[target], val)
            elif not isinstance(val, int) or isinstance(val, bool):
                raise SongFormatError(f"{rtype.name}.{field}: bad value {val!r}")
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
            row = codec.decode({k: v for k, v in value.items() if k not in named}, place)
        except CodecError as exc:
            raise SongFormatError(f"{self.pr['name']}: {exc}") from exc
        return {**row, **named}, set(row)

    @staticmethod
    def _list_row(rtype, codec, value, place) -> dict:
        """A list row's fields; a row the codec writes with shared keys has no list form."""
        fields = [f for f in rtype.fields if f != rtype.chain]
        if not isinstance(value, list) or len(value) > len(fields):
            raise SongFormatError(f"{rtype.name}: bad row {value!r}")
        row = dict(zip(fields, value))
        if codec is not None and codec.encode(row, place) is not None:
            raise SongFormatError(f"{rtype.name}: write row {value!r} with shared keys")
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
            raise SongFormatError(f"{rtype.name}: a program is rows then end or loop")
        rows, term = value[:-1], value[-1]
        start = self._alloc(rtype, len(rows))
        for k, raw in enumerate(rows):
            row = self._proto(rtype, raw, _place(k, len(rows), term == timeline.END))
            row[rtype.chain] = start + k + 1
            self.pending.append((rtype, start + k, row))
        last = self.pending[-1][2]
        if term == timeline.END:
            last[rtype.chain] = rtype.none if rtype.none is not None else 0
            self._end(rtype, last, rows[-1], _place(len(rows) - 1, len(rows), True))
            if rtype.stop is not None:
                plain = {f: v for f, v in last.items() if isinstance(v, int)}
                if not _RowEval(rtype).holds(rtype.stop, plain):
                    raise SongFormatError(f"{rtype.name}: a program can only end on a stop row")
        elif isinstance(term, dict) and set(term) == {"loop"} and 0 <= term["loop"] < len(rows):
            last[rtype.chain] = start + int(term["loop"])
        else:
            raise SongFormatError(f"{rtype.name}: bad program end {term!r}")
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

    def _define(self, tname, name, value):
        rtype = self.rtypes.get(tname)
        if tname in RETIRED:
            raise SongFormatError(f"record type {tname!r} is {RETIRED[tname]}")
        if rtype is None:
            raise SongFormatError(f"unknown record type {tname!r}")
        if rtype.fixed:
            self.pending.append((rtype, int(name), self._proto(rtype, value)))
            return
        if not isinstance(name, str) or not _ONE.match(name):
            raise SongFormatError(f"{tname}: bad name {name!r}")
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
                    target = _ref_type(rtype, field, plain)
                    if target is None:
                        raise SongFormatError(f"{rtype.name}.{field} is not a reference here")
                    val = self.lookup(val[1], target)
                out[field] = int(val)
            if index in self.records[rtype.name]:
                raise SongFormatError(f"{rtype.name} row {index} defined twice")
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
        if len(parsed["commands"]) > MAX_COMMANDS:
            raise SongFormatError(f"more than {MAX_COMMANDS} commands in {atom!r}")
        commands = [self.command(cmd, arg, atom) for cmd, arg in parsed["commands"]]
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
        """Whether record type ``tname`` holds event restarts, which the reader plays as commands."""
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
            rtype, codec = self.rtypes[eventrestart.NAME], self.codecs[eventrestart.NAME]
            target = self.rtypes[self.commands.ref(int(self.pr[eventrestart.NAME]["command"]))]
            path, rows, entry = [], self.records[rtype.name], index
            while entry != rtype.none:
                path.append({f: v for f, v in rows[entry].items() if f != rtype.chain})
                entry = rows[entry][rtype.chain]
            ahead = path[:-1]
            if not ahead:
                raise SongFormatError(f"{self.pr['name']}: a restart needs a row before its note")
            holds = [
                codec.encode(r, _place(k, len(path), True)).get("hold", 1)
                for k, r in enumerate(ahead)
            ]
            start = self._alloc(target, len(ahead))
            for k, row in enumerate(ahead):
                last = k + 1 == len(ahead)
                row = {**row, target.chain: 0 if last else start + k + 1}
                self.records[target.name][start + k] = {**row, **(target.end if last else {})}
            self.played[index] = (start, sum(holds))
        return self.played[index]

    def command(self, cmd, arg, atom) -> list[int]:
        """``[code, arg]`` for one written command of ``atom``."""
        if cmd in RETIRED:
            raise SongFormatError(f"!{cmd} is {RETIRED[cmd]} in {atom!r}")
        if cmd == eventrestart.NAME and self._event_restart(cmd) and isinstance(arg, str):
            return [eventrestart.CODE, self.lookup(arg[1:], eventrestart.NAME)]
        if isinstance(arg, str) and arg != NONE:
            if arg[1:] not in self.names:
                raise SongFormatError(f"unknown name {arg[1:]!r} in {atom!r}")
            arg = self.names[arg[1:]]
        try:
            return list(self.commands.decode(cmd, arg))
        except CodecError as exc:
            raise SongFormatError(f"{self.pr['name']}: {exc} in {atom!r}") from exc

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
                        if name in phrases or name in self.names:
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
        return {
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
    """The internal representation of a v1.8 document."""
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict) or doc.get("unitracker") != VERSION:
        raise SongFormatError(f"not a unitracker v{VERSION} document")
    for key in RETIRED_KEYS.keys() & doc.keys():
        raise SongFormatError(f"{key} is {RETIRED_KEYS[key]}")
    return _Reader(doc).read()


def load(path) -> dict:
    """The internal representation of the v1.8 document at ``path``."""
    with open(path, encoding="utf-8") as fh:
        return loads(fh.read())


def dump(ir: dict, path, embed: bool = False) -> None:
    """Write ``ir`` as a v1.8 document to ``path``, atomically."""
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
