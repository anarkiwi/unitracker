"""The written tree of a lifter's song, which the generic writer respells: ``meta``, then one
timeline of definitions and plays, each record named and each row in the shared vocabulary."""

import re

import yaml

from unitracker.document import idle, refkeys, timeline
from unitracker.playroutine import tempo, xexpr
from unitracker.playroutine.atoms import format_atom, runs
from unitracker.playroutine.codec import (
    CodecError,
    command_text,
    envelope_names,
    inline_text,
    is_expr,
    note_text,
    plain_constants,
    row_place,
    written_value,
)
from unitracker.document.events import column, envelope, fold, instrument, lifetime, naming, running
from unitracker.playroutine.library import META_KEYS, SongFormatError
from unitracker.playroutine.schema import first_only
from unitracker.document.checks import (
    RowErrors as _RowEval,
    codec_set as _codecs,
    expr_fields as _expr_fields,
    uniform as _uniform,
    player as _player,
)
from unitracker.playroutine.records import (
    EXPR,
    program_path,
    record_types,
    ref_type,
)
from unitracker.document.yamlstyle import Atom, Dumper, Flow, FlowMap
from unitracker.player.modulation import declared
from unitracker.player.vm import EVENT_COLUMNS, compile_phrase

VERSION = "1.20"
WIDTH = 100
PHRASE = "phrase"
_ENTRY = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<t>[+-][1-9]\d*)?$")
_INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)")


def song_rows(doc: dict) -> list:
    """The rows of a written document's ``song``, without its ``end`` or ``{loop: k}``."""
    return doc["song"][:-1]


# ---- writing -----------------------------------------------------------------------


def _legato_note(kind: int, names, atom, error) -> None:
    """Raise ``error`` for ``!legato`` on an event of note ``kind`` that is no note."""
    if "legato" in names and kind != 1:
        raise error(f"!legato needs a note: {atom!r}")


class _Writer:
    """Turns a lifter's song into its written tree: named records, rows in the shared vocabulary."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, ir: dict):
        self.pr = ir["playroutine"]
        self.rtypes = record_types(self.pr)
        self.records = {
            name: {int(k): v for k, v in (rows or {}).items()}
            for name, rows in (ir.get("records") or {}).items()
        }
        self.ins_type, self.ins_base = instrument.document(self.pr)
        self.offset, self.codecs, self.commands = _codecs(self.pr, self.rtypes, ir.get("tables"))
        self.slotted = envelope_names(self.pr.get("commands"))
        self.exprs = {int(k): xexpr.canonical(str(v)) for k, v in (ir.get("exprs") or {}).items()}
        canon = {}
        self.xcanon = {i: canon.setdefault(t, i) for i, t in sorted(self.exprs.items())}
        self.first_row = {}
        self.equal, self.signatures = {}, {}
        self.order = []
        self.post = []
        self.referrers = {}
        self.programs = {}
        self.names = {}
        self.row = 0
        self.first_only = first_only(self.pr)
        self.driven = {
            u["site"]["record"]: u["key"]
            for u in declared(self.pr).get("units") or []
            if u.get("site") and self.rtypes[u["site"]["record"]].expr is not None
        }
        _player(self.pr, column.check, self.pr, ir["phrases"])
        native = (self.pr.get("instrument") or {}).get("type")
        names = naming.Naming(self.pr, self.rtypes)
        phrases, rows = names.write(ir["tracks"], ir["phrases"], self.records.get(native, {}))
        ir = {**ir, "phrases": phrases}
        self.records.update({native: rows} if native in self.records else {})
        self.ir = _player(self.pr, envelope.Envelope(self.pr, self.rtypes).write, ir, self.records)
        settled = running.Running(
            self.pr, self.rtypes, self.commands, self.codecs, self.records, self.exprs
        )
        self.ir = _player(self.pr, settled.write, self.ir)
        written = lifetime.Lifetime(self.pr, self.rtypes).written
        flagged = self.records.get(native, {})
        self.kept, rows = _player(self.pr, written, self.ir["tracks"], self.ir["phrases"], flagged)
        self.records.update({native: rows} if native in self.records else {})
        self.folds = fold.EventFolds(self.pr, self.rtypes, self.codecs, self.commands, self.records)
        plain_constants(self.codecs, self.rtypes, self.records, self.exprs)
        _uniform(self.pr, self.rtypes, self.records)
        if native in self.rtypes:
            phrases = names.same_written(
                self.ir["phrases"], flagged, lambda index: self.key(native, index)
            )
            self.ir = {**self.ir, "phrases": phrases}

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
            self.programs[key] = program_path(rtype, lambda i: self._row(rtype, i), entry, _RowEval)
        return self.programs[key]

    def program_rows(self, rtype, path: list, loop: int | None) -> tuple[list, int | None]:
        """``(rows, loop)`` of a program's ``path`` looping to ``loop``, without the expressions
        no start reads (those of :func:`first_only` past its first row) and the rows that change
        nothing (see ``idle``)."""
        unread = self.first_only.get(rtype.name, ())
        rows = [
            {f: v for f, v in self._row(rtype, i).items() if not (k and f in unread)}
            for k, i in enumerate(path)
        ]
        codec = self.codecs.get(rtype.name)
        if codec is None:
            return rows, loop
        places = [row_place(k, len(rows), loop is None) for k in range(len(rows))]
        forms = [(r, codec.encode(r, p)) for r, p in zip(rows, places)]
        return idle.settled(rtype, codec, forms, loop, self._holds)

    def _holds(self, index: int) -> bool:
        """Whether expression ``index`` holds the value it drives."""
        return idle.holds(self.exprs.get(index, ""))

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
        """The object ``(type, index)``; equal expressions are one, and equal records."""
        if rtype == EXPR:
            return (rtype, self.xcanon.get(index, index))
        if self.rtypes[rtype].fixed:
            return (rtype, index)
        return self.equal.setdefault(self._signature(rtype, index), (rtype, index))

    def _signature(self, rtype: str, index: int) -> tuple:
        """What record ``(type, index)`` holds, its references as what they hold."""
        if (rtype, index) not in self.signatures:
            rt = self.rtypes[rtype]
            rows, loop = [self._row(rt, index)], None
            if rt.chain is not None:
                rows, loop = self.program_rows(rt, *self.program(rt, index))
            refs = [dict(self._refs(rt, row)) for row in rows]
            body = tuple(
                tuple(
                    sorted(
                        (
                            (f, self._signature(*ref[f]) if ref[f][0] != EXPR else ref[f])
                            if f in ref
                            else (f, int(v))
                        )
                        for f, v in row.items()
                        if f != rt.chain
                    )
                )
                for row, ref in zip(rows, refs)
            )
            self.signatures[(rtype, index)] = (rtype, body, loop)
        return self.signatures[(rtype, index)]

    def _children(self, key):
        rtype = self.rtypes[key[0]]
        if rtype.chain is not None:
            rows, _ = self.program_rows(rtype, *self.program(rtype, key[1]))
            return [ref for row in rows for ref in self._refs(rtype, row)]
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

    def compiled(self) -> dict:
        """Every phrase's rows, its restarts and instruments folded and its lifetime marks."""
        return {
            name: lifetime.column(self.kept, name, ev, self._folded(compile_phrase(ev)))
            for name, ev in (self.ir["phrases"] or {}).items()
        }

    @staticmethod
    def phrases(rows, compiled: dict) -> dict:
        """``{name: (base, transpose)}`` for each used phrase: phrases equal up to a transpose
        share the first-used one as their base."""
        bases, alias = {}, {}
        for row in rows:
            for entry in row:
                if not isinstance(entry, tuple) or entry[0] in alias:
                    continue
                key, shift = _transpose_key(compiled[entry[0]])
                base, base_shift = bases.setdefault(key, (entry[0], shift))
                alias[entry[0]] = (base, shift - base_shift)
        return alias

    def _folded(self, arr):
        """A compiled phrase with its event restarts and instruments named (see ``events.fold``)."""
        try:
            return self.folds.fold(arr, self.program)
        except fold.FoldError as exc:
            raise SongFormatError(f"{self.pr['name']}: {exc}") from exc

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
        if codec is not None:
            raise SongFormatError(f"{rtype.written}: row {row} has no shared-vocabulary form")
        for field in sorted(rtype.unwritten):
            if int(row.get(field, rtype.default(field))) != rtype.default(field):
                raise SongFormatError(f"{rtype.written}.{field} has no written form")
        values = {}
        for field in rtype.own:
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
        fields = rtype.own
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
        raws, loop = self.program_rows(rtype, path, loop)
        rows = [
            self.row_value(rtype, row, row_place(k, len(raws), loop is None))
            for k, row in enumerate(raws)
        ]
        if (
            loop is None
            and len(rows) == 1
            and list(rows[0]) == [self.driven.get(rtype.name)]
            and isinstance(rows[0][self.driven[rtype.name]], int)
        ):
            return rows[0][self.driven[rtype.name]]
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
                name = self.names[self.key(self.ins_type, ins - self.ins_base)]
            pairs = list(zip(cmds[::2], cmds[1::2]))
            commands = [self.command(c, a) for c, a in pairs]
            commands += [also for c, a in pairs if (also := self.commands.also(c, a)) is not None]
            commands = column.ordered([c for c in commands if c is not None], self.slotted)
            commands += lifetime.names(keep)
            text = format_atom(kind, note - self.offset, steps, name, commands)
            _legato_note(kind, [c.split(":")[0] for c in commands], text, SongFormatError)
            out.append(Atom(text))
        return Flow(out)

    def command(self, cmd: int, raw: int) -> str | None:
        """A command as ``name[:arg]``, or None for no command."""
        if not (cmd or raw):
            return None
        try:
            name, value = self.commands.encode(cmd, raw)
        except CodecError as exc:
            raise SongFormatError(f"{self.pr['name']}: {exc}") from exc
        if isinstance(value, tuple) and self.commands.inline(name):
            rtype = self.rtypes[value[0]]
            row = {f: int(self._row(rtype, value[1]).get(f, rtype.default(f))) for f in rtype.own}
            try:
                self.commands.check_inline(name, row)
            except CodecError as exc:
                raise SongFormatError(f"{self.pr['name']}: {exc}") from exc
            single = tempo.one(name, tuple(row.values()))
            return f"{name}:{inline_text(rtype, row) if single is None else single}"
        if isinstance(value, tuple) and not self.commands.values(name):
            return f"{name}:{self.names[self.key(*value)]}"
        single = tempo.one(name, value)
        return command_text(name, value if single is None else single)[1:]

    # -- document --

    def _event_refs(self, arr):
        for row in arr:
            if row[2]:
                yield self.key(self.ins_type, int(row[2]) - self.ins_base), "event"
            cmds = row[3:EVENT_COLUMNS]
            for cmd, arg in zip(cmds[::2], cmds[1::2]):
                target = self.commands.ref(int(cmd))
                if target is not None and not self.commands.inline(int(cmd)):
                    none = self.rtypes[target].none
                    if none is None or int(arg) != none:
                        yield self.key(target, int(arg)), "command"

    def _roots(self):
        """Visit the playroutine's pinned rows and the fixed rows; ``{type: indices}`` fixed."""
        for rtype in self.rtypes.values():
            for index in rtype.pin:
                self.visit(self.key(rtype.name, index), "playroutine")
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
        """``(song rows, loop)`` with definitions placed at first use."""
        compiled = self.compiled()
        keys = {name: _transpose_key(arr) for name, arr in compiled.items()}

        def key(play):
            base, shift = keys[play["phrase"]]
            return base, shift + int(play.get("transpose", 0))

        rows, loop = timeline.to_rows(
            [timeline.folded(trk, key) for trk in self.ir["tracks"]],
            {n: len(arr) for n, arr in compiled.items()},
        )
        alias = self.phrases(rows, compiled)
        fixed = self._roots()
        phrase_row = self._discover(rows, alias, compiled)
        defs = self._defs(len(rows), fixed, phrase_row, compiled)
        pnames = {base: f"p{i}" for i, base in enumerate(phrase_row, start=1)}
        out = []
        for row, item_defs in zip(rows, defs):
            item = {"def": item_defs} if item_defs else {}
            item["play"] = Flow(self._entry(e, alias, pnames) for e in row)
            out.append(item)
        return out, loop

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


def _named(written: str, prefix: str, name) -> None:
    """Raise unless ``name`` is a name of a ``written`` type: its ``prefix`` and a number."""
    if not (isinstance(name, str) and re.fullmatch(rf"{re.escape(prefix)}[1-9][0-9]*", name)):
        raise SongFormatError(f"{written}: name {name!r} is not {prefix} and a number")


def _transpose_key(arr):
    """A phrase's rows with notes relative to its first note, and that first note."""
    notes = arr[:, 0] == 1
    shift = int(arr[notes, 1][0]) if notes.any() else 0
    norm = arr.copy()
    norm[notes, 1] -= shift
    return norm.tobytes() + str(norm.shape).encode(), shift


def respelled(ir: dict, respell) -> str:
    """The text of song ``ir``'s written tree as ``respell(tree)`` rewrites it: the version,
    ``meta`` and the timeline of definitions and plays."""
    writer = _Writer(ir)
    meta = ir.get("meta") or {}
    doc = {
        "unitracker": Atom(VERSION),
        "meta": FlowMap({k: meta[k] for k in META_KEYS if k in meta}),
    }
    rows, loop = writer.song()
    doc["song"] = [*rows, Atom(timeline.END) if loop is None else FlowMap(loop=loop)]
    return yaml.dump(respell(doc), Dumper=Dumper, sort_keys=False, allow_unicode=True, width=WIDTH)
