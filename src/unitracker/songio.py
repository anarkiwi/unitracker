"""Read and write unitracker v2 documents: a header, then one timeline of definitions and plays.

The player runs the expanded internal representation (``"unitracker": "ir"``): every
document is read through :func:`loads`, and lifters' output is written with :func:`dumps`.
"""

import copy
import json
import os
import tempfile

import yaml

from unitracker import spec as specs
from unitracker import timeline
from unitracker.atoms import format_atom
from unitracker.docbase import SongFormatError, View, is_live, is_spec, library
from unitracker.reach import ReachabilityError, table_reads
from unitracker.reader import read
from unitracker.vm import OFF, event_note, phrase_key, phrase_steps

VERSION = 2
WIDTH = 100
PHRASE = "phrase"
SPEC = "spec"
SPEC_PREFIX = "v"


def song_meta(header, source: str) -> dict:
    """The ``meta`` mapping for a tune with PSID ``header``, lifted by ``source``."""
    return {
        "title": header.name,
        "author": header.author,
        "released": header.released,
        "source": source,
    }


# ---- writing -------------------------------------------------------------------------


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


def _atom(dumper, data):
    tag = "tag:yaml.org,2002:bool" if data in ("off", "on") else "tag:yaml.org,2002:str"
    if data.lstrip("+-").isdigit():
        tag = "tag:yaml.org,2002:int"
    return dumper.represent_scalar(tag, str(data), style="A")


_Dumper.add_representer(Flow, lambda d, x: d.represent_sequence("tag:yaml.org,2002:seq", x, True))
_Dumper.add_representer(FlowMap, lambda d, x: d.represent_mapping("tag:yaml.org,2002:map", x, True))
_Dumper.add_representer(Atom, _atom)
_Dumper.add_representer(type(None), lambda d, _: d.represent_scalar("tag:yaml.org,2002:null", "~"))

ON = Atom("on")
OFF_ATOM = Atom("off")


def _plain(text: str):
    """``text`` as a plain scalar when YAML reads it back as the same string."""
    if text in ("off", "on", "-"):
        return Atom(text)
    try:
        same = yaml.safe_load(text) == text and "{" not in text and "," not in text
    except yaml.YAMLError:
        same = False
    return Atom(text) if same else text


def _flow(value):
    """A rendered spec or nested value with flow maps and plain scalars where possible."""
    if isinstance(value, dict):
        return FlowMap({k: _flow(v) for k, v in value.items()})
    if isinstance(value, list):
        return Flow(_flow(v) for v in value)
    if value is True:
        return ON
    if isinstance(value, str):
        return _plain(value)
    return value


class _SpecRef:
    """A spec occurrence in the written document, named once it is used twice."""

    # pylint: disable=too-few-public-methods

    def __init__(self, key):
        self.key = key


class _Writer:
    """Turns the internal representation into a canonical v2 document."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, ir: dict):
        self.ir = ir
        self.pr = ir["playroutine"]
        view = View(self.pr)
        self.rtypes, self.commands, self.offset = view.rtypes, view.commands, view.offset
        self.ins_type, self.ins_base = view.ins_type, view.ins_base
        self.records = {
            name: {int(k): v for k, v in (rows or {}).items()}
            for name, rows in (ir.get("records") or {}).items()
        }
        self.first_row = {}
        self.order = []
        self.post = []
        self.referrers = {}
        self.programs = {}
        self.names = {}
        self.row = 0
        self.spec_uses = []
        self.spec_bodies = {}

    # -- objects: (type, index); chained types are programs keyed by their entry row --

    def _row(self, rtype, index):
        rows = self.records.get(rtype.name, {})
        if index not in rows:
            raise SongFormatError(f"{rtype.name} row {index} is referenced but not defined")
        return rows[index]

    def program(self, rtype, entry):
        """``(row indices, loop index or None)`` of the program starting at ``entry``."""
        k = (rtype.name, entry)
        if k not in self.programs:
            path, pos, index = [], {}, entry
            while True:
                if index in pos:
                    loop = pos[index]
                    break
                row = self._row(rtype, index)
                pos[index] = len(path)
                path.append(index)
                index = int(row.get(rtype.chain, rtype.default(rtype.chain)))
                if rtype.none is not None and index == int(rtype.none):
                    loop = None
                    break
            self.programs[k] = (path, loop)
        return self.programs[k]

    def _refs(self, rtype, row):
        """``(key, (type, index))`` for each reference ``row`` makes."""
        out = []
        for key, target in rtype.refs().items():
            if key not in row:
                continue
            ttype = self.rtypes[target]
            index = int(row[key])
            if ttype.none is not None and index == int(ttype.none):
                continue
            out.append((key, (target, index)))
        return out

    def _children(self, k):
        rtype = self.rtypes[k[0]]
        if rtype.chain is not None:
            path, _ = self.program(rtype, k[1])
            return [ref for i in path for ref in self._refs(rtype, self._row(rtype, i))]
        return self._refs(rtype, self._row(rtype, k[1]))

    def visit(self, k, referrer):
        """Record a use of object ``k``, discovering it and its references on first use."""
        if k[0] not in self.rtypes:
            raise SongFormatError(f"reference to unknown record type {k[0]!r}")
        self.referrers.setdefault(k, []).append(referrer)
        if k in self.first_row:
            return
        self.first_row[k] = self.row
        self.order.append(k)
        for key, child in self._children(k):
            self.visit(child, (k, key))
        self.post.append(k)

    # -- values --

    def spec_value(self, spec):
        """A spec occurrence: recorded for naming, rendered at the end."""
        k = specs.spec_key(spec)
        self.spec_bodies.setdefault(k, spec)
        ref = _SpecRef(k)
        self.spec_uses.append((self.row, ref))
        return ref

    def ref_value(self, k):
        """A reference to ``k``: ``@name``, or its program written inline."""
        if k in self.names:
            return "@" + self.names[k]
        return self.program_value(k)

    def key_value(self, rtype, key, value):
        """One row value in shared form; the key it is written under."""
        # pylint: disable=too-many-return-statements
        kind = rtype.keys.get(key)
        if kind is None:
            raise SongFormatError(f"{rtype.name}: key {key!r} is not declared")
        if kind.ref is not None:
            ttype = self.rtypes[kind.ref]
            if ttype.none is not None and int(value) == int(ttype.none):
                return key, OFF_ATOM
            return key, self.ref_value((kind.ref, int(value)))
        if kind.scalar == "flag":
            return key, ON if value else OFF_ATOM
        if kind.scalar == "abs":
            return key, f"={int(value) + self.offset}"
        if kind.scalar == "note":
            return key, Atom(f"{int(value):+d}")
        if kind.scalar == "int":
            return key, int(value)
        if value == OFF:
            return key, OFF_ATOM
        if is_live(value):
            return key, _plain(value["live"])
        if is_spec(value):
            return key, self.spec_value(value)
        return key, specs.render_value(specs.number(value, key))

    def row_value(self, rtype, row):
        """A row as a flow map of shared keys in declared order, references resolved."""
        unknown = set(row) - set(rtype.keys) - {rtype.chain, "noteabs"}
        if unknown:
            raise SongFormatError(f"{rtype.name}: keys {sorted(unknown)} are not declared")
        out = FlowMap()
        for key in rtype.keys:
            if key == "note" and "noteabs" in row:
                out["note"] = f"={int(row['noteabs']) + self.offset}"
            elif key in row:
                name, rendered = self.key_value(rtype, key, row[key])
                out[name] = rendered
        return out

    def program_value(self, k):
        """A program as rows followed by ``end`` or ``{loop: k}``."""
        rtype = self.rtypes[k[0]]
        path, loop = self.program(rtype, k[1])
        rows = [self.row_value(rtype, self._row(rtype, i)) for i in path]
        rows.append(Atom("end") if loop is None else FlowMap(loop=loop))
        return Flow(rows)

    def inline(self, k):
        """Whether ``k`` is a program written inside its single instrument."""
        refs = self.referrers.get(k, [])
        rtype = self.rtypes[k[0]]
        return (
            rtype.chain is not None
            and len(refs) == 1
            and isinstance(refs[0], tuple)
            and isinstance(refs[0][0], tuple)
            and refs[0][0][0] == self.ins_type
        )

    def command_arg(self, name, arg):
        """A command argument as text, or a spec occurrence."""
        kind = str((self.commands.get(name) or {}).get("arg", "none"))
        if arg is None:
            return None
        if kind == "spec":
            return self.spec_value(arg)
        if kind.startswith("ref "):
            target = kind.split()[1]
            ttype = self.rtypes[target]
            if ttype.none is not None and int(arg) == int(ttype.none):
                return "off"
            return "@" + self.names[(target, int(arg))]
        if isinstance(arg, list):
            return f"{int(arg[0])},{int(arg[1])}"
        return str(int(arg))

    def atoms(self, events) -> list:
        """A phrase's events as atom parts (text, or spec occurrences), merged runs."""
        out = []
        for event in events:
            kind, pitch = event_note(event)
            ins = None
            if event.get("instrument"):
                ins = self.names[(self.ins_type, int(event["instrument"]) - self.ins_base)]
            cmd, arg = None, None
            if event.get("command"):
                cmd, raw = event["command"]
                arg = self.command_arg(cmd, raw)
            steps = int(event.get("steps", 1))
            out.append((kind, pitch + self.offset if kind == 1 else 0, ins, cmd, arg, steps))
        return out

    # -- document --

    def _event_refs(self, events):
        for event in events:
            if event.get("instrument"):
                yield (self.ins_type, int(event["instrument"]) - self.ins_base), "event"
            if not event.get("command"):
                continue
            name, arg = event["command"]
            kind = str((self.commands.get(name) or {}).get("arg", "none"))
            if kind.startswith("ref "):
                target = kind.split()[1]
                none = self.rtypes[target].none
                if none is None or int(arg) != int(none):
                    yield (target, int(arg)), "command"

    def _roots(self):
        """Visit the playroutine's pinned rows and the fixed rows; ``{type: indices}`` fixed."""
        for rtype in self.rtypes.values():
            for index in rtype.pin:
                self.visit((rtype.name, index), "playroutine")
        fixed = {}
        for rtype in (rt for rt in self.rtypes.values() if rt.fixed):
            fixed[rtype.name] = sorted(self.records.get(rtype.name, {}))
            for index in fixed[rtype.name]:
                for key, child in self._refs(rtype, self._row(rtype, index)):
                    self.visit(child, ((rtype.name, index), key))
        return fixed

    def phrases(self, rows):
        """``{name: (base, transpose)}`` for each used phrase.

        Phrases equal up to a transpose share the first-used one as their base.
        """
        phrases = self.ir["phrases"] or {}
        bases, alias = {}, {}
        for row in rows:
            for entry in row:
                if not isinstance(entry, tuple) or entry[0] in alias:
                    continue
                k, shift = _transpose_key(phrases[entry[0]])
                base, base_shift = bases.setdefault(k, (entry[0], shift))
                alias[entry[0]] = (base, shift - base_shift)
        return alias

    def _discover(self, rows, alias):
        """Visit every object in first-use order and name it; each base phrase's first row."""
        phrase_row = {}
        for r, row in enumerate(rows):
            self.row = r
            for entry in row:
                if isinstance(entry, tuple) and alias[entry[0]][0] not in phrase_row:
                    base = alias[entry[0]][0]
                    phrase_row[base] = r
                    for k, kind in self._event_refs(self.ir["phrases"][base]):
                        self.visit(k, kind)
        counters = {}
        for k in self.order:
            if not self.inline(k):
                counters[k[0]] = counters.get(k[0], 0) + 1
                self.names[k] = f"{self.rtypes[k[0]].prefix}{counters[k[0]]}"
        return phrase_row

    def _defs(self, count, fixed, phrase_row):
        """Each row's definitions: fixed rows first, then objects and phrases at first use."""
        defs = [{} for _ in range(count)]
        self.row = 0
        for name, indices in fixed.items():
            rtype = self.rtypes[name]
            defs[0].setdefault(name, {}).update(
                {i: self.row_value(rtype, self._row(rtype, i)) for i in indices}
            )
        for k in self.post:
            if self.inline(k):
                continue
            self.row = self.first_row[k]
            rtype = self.rtypes[k[0]]
            value = (
                self.program_value(k)
                if rtype.chain is not None
                else self.row_value(rtype, self._row(rtype, k[1]))
            )
            defs[self.row].setdefault(k[0], {})[Atom(self.names[k])] = value
        for i, (base, r) in enumerate(phrase_row.items(), start=1):
            self.row = r
            parts = self.atoms(self.ir["phrases"][base])
            defs[r].setdefault(PHRASE, {})[Atom(f"p{i}")] = parts
        return defs

    def _name_specs(self, defs):
        """Name specs used more than once, in first-use order; resolve every occurrence."""
        counts = {}
        for _, ref in self.spec_uses:
            counts[ref.key] = counts.get(ref.key, 0) + 1
        named, first = {}, {}
        for r, ref in self.spec_uses:
            if counts[ref.key] > 1 and ref.key not in named:
                named[ref.key] = f"{SPEC_PREFIX}{len(named) + 1}"
                first[ref.key] = r
        for k, r in first.items():
            body = _flow(specs.render(self.spec_bodies[k]))
            defs[r].setdefault(SPEC, {})[Atom(named[k])] = body

        def resolve(value, in_atom=False):
            if isinstance(value, _SpecRef):
                if value.key in named:
                    return "@" + named[value.key]
                body = _flow(specs.render(self.spec_bodies[value.key]))
                return _inline_text(body) if in_atom else body
            if isinstance(value, dict):
                return type(value)({k: resolve(v) for k, v in value.items()})
            if isinstance(value, Flow):
                return Flow(resolve(v) for v in value)
            return value

        out = []
        for item in defs:
            row = {}
            if SPEC in item:
                row[SPEC] = item[SPEC]
            for tname, entries in item.items():
                if tname in (SPEC, PHRASE):
                    continue
                row[tname] = {n: resolve(v) for n, v in entries.items()}
            if PHRASE in item:
                row[PHRASE] = {
                    n: Flow(_atom_text(p, resolve) for p in parts)
                    for n, parts in item[PHRASE].items()
                }
            out.append(row)
        return out

    def song(self):
        """``(hints, song rows, loop)`` with definitions placed at first use."""
        lengths = {n: len(phrase_steps(ev)) for n, ev in (self.ir["phrases"] or {}).items()}
        rows, loop = timeline.to_rows(self.ir["tracks"], lengths)
        alias = self.phrases(rows)
        fixed = self._roots()
        phrase_row = self._discover(rows, alias)
        defs = self._name_specs(self._defs(len(rows), fixed, phrase_row))
        pnames = {base: f"p{i}" for i, base in enumerate(phrase_row, start=1)}
        out = []
        for row, item_defs in zip(rows, defs):
            item = {"def": item_defs} if item_defs else {}
            item["play"] = Flow(self._entry(e, alias, pnames) for e in row)
            out.append(item)
        steps = {lengths[b] for b in phrase_row}
        return self._hints(steps), out, loop

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
        """``clock``, ``voices`` and ``registers`` where they differ from the defaults."""
        defaults = self.pr.get("defaults") or {}
        env = {}
        for k in ("clock", "voices"):
            if k in self.ir and self.ir[k] != defaults.get(k):
                env[k] = FlowMap(self.ir[k]) if isinstance(self.ir[k], dict) else self.ir[k]
        regs = [int(x) for x in self.ir.get("registers") or []]
        default_regs = [int(x) for x in defaults.get("registers") or [0] * 25]
        if regs and regs != default_regs:
            env["registers"] = FlowMap(
                {i: v for i, (v, d) in enumerate(zip(regs, default_regs)) if v != d}
            )
        return env


def _inline_text(body) -> str:
    """A spec mapping as one-line flow text, for use inside an atom."""
    text = yaml.dump(body, Dumper=_Dumper, default_flow_style=True, width=10**6, sort_keys=False)
    return text.strip()


def _atom_text(part, resolve):
    """One atom from its parts; quoted when a spec argument makes it need it."""
    kind, note, ins, cmd, arg, steps = part
    arg = resolve(arg, in_atom=True) if arg is not None else None
    text = format_atom((kind, note, ins, cmd, arg, steps))
    return _plain(text)


def _transpose_key(events):
    """A phrase's steps with notes relative to its first note, and that first note."""
    steps = [phrase_key(e) for e in phrase_steps(events)]
    notes = [s[1] for s in steps if s[0] == 1]
    shift = notes[0] if notes else 0
    norm = [(k, n - shift if k == 1 else n, i, c) for k, n, i, c in steps]
    return json.dumps(norm), shift


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
    """A canonical v2 document for internal-representation song ``ir``.

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


def loads(text: str) -> dict:
    """The internal representation of a v2 document."""
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict) or doc.get("unitracker") != VERSION:
        raise SongFormatError(f"not a unitracker v{VERSION} document")
    return read(doc)


def load(path) -> dict:
    """The internal representation of the v2 document at ``path``."""
    with open(path, encoding="utf-8") as fh:
        return loads(fh.read())


def dump(ir: dict, path, embed: bool = False) -> None:
    """Write ``ir`` as a v2 document to ``path``, atomically."""
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


__all__ = ["SongFormatError", "dump", "dumps", "library", "load", "loads", "song_meta"]
