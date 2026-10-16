"""The generic unitracker player: compiles a song's playroutine to Python and runs it.

Effects run here, in :mod:`unitracker.spec` lanes; a playroutine only sequences them.
"""

import ast
import json
from collections.abc import Iterator

import numpy as np
from tqdm import tqdm

from unitracker.canonical import CANONICAL_ORDER, REG_COUNT
from unitracker.expr import ExprError, Resolver, parse
from unitracker.records import record_types
from unitracker.spec import Compiled, Lane, SpecError, scale_number, spec_key
from unitracker.spec import parse as parse_spec

IR_VERSION = "ir"
NOTE_KINDS = {None: 0, "off": 2, "on": 3}
PSEUDO = (
    "ev_kind",
    "ev_note",
    "ev_instr",
    "ev_cmd",
    "ev_arg",
    "ev_arg2",
    "ev_argn",
    "at_phrase_start",
    "ended",
)
LANE_TESTS = ("running", "on", "hit")
OFF = "off"


class SongError(ValueError):
    """The song document is malformed or uses something the format does not define."""


def event_note(event) -> tuple[int, int]:
    """``(kind, pitch)`` of an event's note: kind 0 empty, 1 note, 2 off, 3 on."""
    note = event.get("note")
    if isinstance(note, bool):
        note = "on" if note else "off"
    if isinstance(note, int):
        return 1, note
    if note in NOTE_KINDS:
        return NOTE_KINDS[note], 0
    raise SongError(f"bad note {note!r}")


def phrase_steps(events) -> list:
    """A phrase's steps: each event, then ``steps - 1`` empty steps (None)."""
    out = []
    for event in events:
        steps = int(event.get("steps", 1))
        if steps < 1:
            raise SongError(f"event steps must be >= 1: {event!r}")
        out.append(event)
        out.extend([None] * (steps - 1))
    if not out:
        raise SongError("empty phrase")
    return out


def phrase_key(event) -> tuple:
    """A hashable form of one step, for comparing phrases."""
    if event is None:
        return (0, 0, 0, None)
    kind, pitch = event_note(event)
    cmd = event.get("command")
    return kind, pitch, int(event.get("instrument") or 0), json.dumps(cmd, default=str)


class Cursor:
    """One voice's position in its track; phrases are lists of int step rows."""

    def __init__(self, track, phrases):
        self.plays = []
        for play in track["plays"]:
            if play["phrase"] not in phrases:
                raise SongError(f"unknown phrase {play['phrase']!r}")
            self.plays.append(
                (phrases[play["phrase"]], int(play.get("transpose", 0)), int(play.get("repeat", 1)))
            )
        if not self.plays:
            raise SongError("track has no plays")
        self.loop = track.get("loop")
        if self.loop is not None and not 0 <= self.loop < len(self.plays):
            raise SongError(f"loop index out of range: {self.loop}")
        self.index = 0
        self.repeat = self.plays[0][2]
        self.step = 0
        self.ended = False

    def fetch(self) -> tuple:
        """The next step: (kind, note + transpose, instrument, cmd, arg, arg2, argn)."""
        if self.ended:
            return (0,) * 7
        rows, transpose, _ = self.plays[self.index]
        row = rows[self.step]
        self.step += 1
        if self.step == len(rows):
            self.step = 0
            self.repeat -= 1
            if self.repeat <= 0:
                self.index += 1
                if self.index == len(self.plays):
                    if self.loop is None:
                        self.ended = True
                        self.index = 0
                    else:
                        self.index = self.loop
                self.repeat = self.plays[self.index][2]
        return (row[0], row[1] + transpose if row[0] == 1 else row[1], *row[2:])

    @property
    def at_phrase_start(self) -> int:
        """1 when the next fetch reads step 0 of a phrase."""
        return int(self.step == 0 and not self.ended)


def _cells(spec) -> dict:
    out = {}
    for name, cell in (spec or {}).items():
        cell = cell or {}
        width = cell.get("width")
        out[name] = (None if width is None else (1 << int(width)) - 1, int(cell.get("init", 0)))
    return out


class _Scope(Resolver):
    """Name resolution for code running in global or voice scope."""

    builtins = LANE_TESTS + ("has", "towards")

    def __init__(self, gen, voice: bool):
        super().__init__()
        self.functions = gen.resolver_functions
        self.gen = gen
        self.voice = voice

    def name(self, name):
        gen = self.gen
        if self.voice and name in gen.vindex:
            return f"v[{gen.vindex[name]}]"
        if name in gen.gindex:
            return f"g[{gen.gindex[name]}]"
        if name in gen.constants:
            return str(gen.constants[name])
        raise ExprError(f"unknown name {name!r}" + ("" if self.voice else " in global scope"))

    def table(self, name, index):
        if name not in self.gen.tables:
            raise ExprError(f"unknown table {name!r}")
        return f"_tg(T[{name!r}], {index})"

    def field(self, record, index, field):
        if record not in self.gen.records:
            raise ExprError(f"unknown record {record!r}")
        self.gen.need_field(record, field)
        return f"F[{(record, field)!r}].get({index}, {self.gen.default(record, field)})"

    def row_field(self, field):
        record = self.gen.row_ctx
        if record is None:
            return super().row_field(field)
        self.gen.need_field(record, field)
        return f"F[{(record, field)!r}].get(_r, {self.gen.default(record, field)})"

    def builtin(self, name, args):
        if len(args) != 1:
            raise ExprError(f"{name} takes one argument")
        arg = args[0]
        if name == "towards":
            return f"_towards({self._emit(arg)})"
        if name == "has":
            record, index, field = self._place(arg)
            self.gen.need_field(record, field)
            return f"int(({index}) in P[{(record, field)!r}])"
        if not isinstance(arg, ast.Name):
            raise ExprError(f"{name} takes a lane name")
        lane = self.gen.lane_ref(arg.id, self.voice)
        attr = {"running": "driver == 2", "on": "driver != 0", "hit": "hit"}[name]
        return f"int({lane}.{attr})"

    def _place(self, node):
        """``(record, index source, field)`` of ``row.field`` or ``record[index].field``."""
        if not isinstance(node, ast.Attribute):
            raise ExprError("has() takes row.field or record[index].field")
        sub = node.value
        if isinstance(sub, ast.Name):
            if self.gen.row_ctx is None:
                raise ExprError("row.field is only defined in a record template")
            return self.gen.row_ctx, "_r", node.attr
        if sub.value.id not in self.gen.records:
            raise ExprError(f"unknown record {sub.value.id!r}")
        return sub.value.id, self._emit(sub.slice), node.attr


class _SpecScope(_Scope):
    """Expressions inside specs and live values: ``semi``, ``note`` and ``v`` resolve."""

    def __init__(self, gen, voice):
        super().__init__(gen, voice)
        self.functions = dict(self.functions)
        pitch = gen.playroutine.get("pitch") or {}
        if "semi" in pitch:
            semi = pitch["semi"]
            self.functions["semi"] = (list(semi.get("args", [])), parse(semi["expr"]))
        self.note = parse(pitch["note"]) if "note" in pitch else None

    def rewrite(self, tree):
        scope = self

        class _Names(ast.NodeTransformer):
            def visit_Name(self, node):  # pylint: disable=invalid-name
                """Bind the spec names."""
                if node.id == "semi":
                    return ast.Call(ast.Name("semi", ast.Load()), [ast.Constant(0)], [])
                if node.id == "note":
                    if scope.note is None:
                        raise ExprError("the playroutine defines no pitch.note")
                    return scope.note
                return node

            def visit_Call(self, node):  # pylint: disable=invalid-name
                """Leave the called name alone."""
                node.args = [self.visit(a) for a in node.args]
                return node

        return _Names().visit(tree)

    def name(self, name):
        if name == "v":
            return "_x"
        return super().name(name)


def _tg(table, index):
    return table[index] if 0 <= index < len(table) else 0


class _Obj:
    """A record value a lane can take: set, off, live or a spec."""

    # pylint: disable=too-few-public-methods

    __slots__ = ("kind", "value")

    def __init__(self, kind, value):
        self.kind = kind
        self.value = value


def _apply(lane, obj, cells, by, once):
    """Give ``lane`` the value ``obj``; the lane's new value, or None when unchanged."""
    if obj.kind == "set":
        return lane.set(obj.value)
    if obj.kind == "off":
        lane.stop()
        return None
    if obj.kind == "live":
        lane.set_live(obj.value)
        return None
    if once:
        return lane.once(obj.value, cells)
    return lane.start(obj.value, by)


class _Codegen:
    """Generates one Python module of functions from a song's playroutine."""

    # pylint: disable=too-many-instance-attributes,too-many-public-methods

    def __init__(self, song):
        playroutine = song["playroutine"]
        self.playroutine = playroutine
        self.constants = {k: int(v) for k, v in (playroutine.get("constants") or {}).items()}
        self.tables = {k: [int(x) for x in v] for k, v in (song.get("tables") or {}).items()}
        self.rtypes = record_types(playroutine)
        self.records = {}
        for source in (song.get("records") or {}, playroutine.get("records") or {}):
            for name, rows in source.items():
                if name in self.records:
                    raise SongError(f"record {name!r} defined twice")
                self.records[name] = {int(k): (v or {}) for k, v in (rows or {}).items()}
        for name in self.rtypes:
            self.records.setdefault(name, {})
        self.gcells = _cells(playroutine.get("global"))
        vcells = _cells(playroutine.get("voice"))
        for name in PSEUDO:
            if name in vcells:
                raise SongError(f"{name} is reserved")
            vcells[name] = (None, 0)
        self.vcells = vcells
        self.gindex = {n: i for i, n in enumerate(self.gcells)}
        self.vindex = {n: i for i, n in enumerate(vcells)}
        self.cursor_slot = len(vcells)
        self.lanes = self._lanes(playroutine.get("lanes") or {})
        self.carry = dict(playroutine.get("carry") or {})
        self.resolver_functions = Resolver(playroutine.get("functions")).functions
        self.procs = playroutine.get("procs") or {}
        self.specs = {}
        self.spec_ids = {}
        self.compiled = {}
        self.fields = {}
        self.present = {}
        self.objs = {}
        self.lines = []
        self.tail = []
        self.done = set()
        self.pending = []
        self.counter = 0
        self.row_ctx = None

    # -- lanes and specs --

    def _lanes(self, spec):
        """``{(voice, name): (slot, mask, scale)}`` for the declared lanes."""
        out = {}
        for scope, voice in (("voice", True), ("global", False)):
            cells = self.vcells if voice else self.gcells
            base = len(cells) + (1 if voice else 0)
            for k, (name, opts) in enumerate((spec.get(scope) or {}).items()):
                if name not in cells:
                    raise SongError(f"lane {name!r} is not a {scope} cell")
                scale = int((opts or {}).get("scale", 1))
                if scale < 1 or scale & (scale - 1):
                    raise SongError(f"lane {name!r}: scale must be a power of two")
                out[(voice, name)] = (base + k, cells[name][0], scale)
        return out

    def lane_info(self, name, voice):
        """``(voice scope, slot, mask, scale)`` of lane ``name`` as seen from ``voice`` scope."""
        if voice and (True, name) in self.lanes:
            return (True, *self.lanes[(True, name)])
        if (False, name) in self.lanes:
            return (False, *self.lanes[(False, name)])
        raise SongError(f"unknown lane {name!r}")

    def lane_ref(self, name, voice) -> str:
        """Source for the Lane object of ``name``."""
        lane_voice, slot, _, _ = self.lane_info(name, voice)
        return f"{'v' if lane_voice else 'g'}[{slot}]"

    def spec_id(self, spec) -> int:
        """The id of a spec dict (1-based)."""
        k = spec_key(spec)
        if k not in self.spec_ids:
            self.spec_ids[k] = len(self.spec_ids) + 1
            self.specs[self.spec_ids[k]] = spec
        return self.spec_ids[k]

    def expr_fn(self, text, voice, what):
        """A callable ``(cells, value)`` for a spec or live expression in shared units."""
        try:
            src = _SpecScope(self, voice).compile(text)
        except ExprError as exc:
            raise SongError(f"{what}: {exc}") from exc
        code = f"lambda {'v' if voice else '_c'}, _x: {src}"
        return eval(code, self.namespace)  # pylint: disable=eval-used

    def carry_fn(self, lane, voice):
        """A callable of cells reading the carry cell declared for ``lane``."""
        cell = self.carry.get(lane)
        if cell is None:
            raise SongError(f"no carry declared for {lane!r}")
        if voice and cell in self.vindex:
            idx = self.vindex[cell]
            return lambda c: c[idx]
        if cell in self.gindex:
            idx = self.gindex[cell]
            return lambda c: self.namespace["g"][idx]
        raise SongError(f"carry cell {cell!r} is not a cell")

    def compiled_spec(self, sid, lane, voice):
        """The spec ``sid`` compiled for ``lane``."""
        lane_voice, _, _, scale = self.lane_info(lane, voice)
        k = (sid, lane, lane_voice)
        if k not in self.compiled:
            try:
                self.compiled[k] = Compiled(
                    parse_spec(self.specs[sid], lane),
                    scale,
                    lambda t, w: self.expr_fn(t, lane_voice, f"{lane}.{w}"),
                    lambda name: self.carry_fn(name, lane_voice),
                )
            except SpecError as exc:
                raise SongError(f"{lane}: {exc}") from exc
        return self.compiled[k]

    def value_obj(self, value, lane, voice):
        """The :class:`_Obj` for a record value given to ``lane``."""
        lane_voice, _, _, scale = self.lane_info(lane, voice)
        if value == OFF:
            return _Obj("off", None)
        if isinstance(value, dict) and set(value) == {"live"}:
            return _Obj("live", self.expr_fn(value["live"], lane_voice, f"{lane} live"))
        if isinstance(value, dict):
            return _Obj("spec", self.compiled_spec(self.spec_id(value), lane, voice))
        try:
            return _Obj("set", scale_number(value, scale, lane))
        except (SpecError, TypeError, ValueError) as exc:
            raise SongError(f"{lane}: bad value {value!r}") from exc

    def obj_table(self, record, field, lane, voice) -> str:
        """Name of the table mapping row index to the lane value of ``record.field``."""
        name = f"O_{record}_{field}_{lane}_{'v' if voice else 'g'}"
        if name not in self.objs:
            if record not in self.records:
                raise SongError(f"unknown record {record!r}")
            self.objs[name] = {
                i: self.value_obj(row[field], lane, voice)
                for i, row in self.records[record].items()
                if field in row
            }
        return name

    def int_value(self, value) -> int:
        """A record value as an int for expressions: a spec's id, 0 for off or live."""
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, dict) and set(value) != {"live"}:
            return self.spec_id(value)
        return 0

    def default(self, record, field) -> int:
        """The int a missing field reads as."""
        rtype = self.rtypes.get(record)
        return rtype.default(field) if rtype is not None else 0

    def need_field(self, record, field):
        """Register an int field of a record for lookup tables."""
        k = (record, field)
        if k not in self.fields:
            rows = self.records[record]
            self.fields[k] = {
                i: self.int_value(row[field])
                for i, row in rows.items()
                if field in row and not isinstance(row[field], list)
            }
            self.present[k] = frozenset(i for i, row in rows.items() if field in row)

    # -- generation --

    def fname(self, kind, voice, *parts):
        """Function name for a proc, record list or entry point in a scope."""
        tag = "v" if voice else "g"
        return "_".join(["f", tag, kind] + [str(p) for p in parts])

    def request(self, name, actions, voice, row=None):
        """Queue generation of ``actions`` as function ``name``, a template if ``row``."""
        if name not in self.done:
            self.done.add(name)
            self.pending.append((name, actions, voice, row))
        return name

    def proc(self, name, voice):
        """The function generated for proc ``name``."""
        if name not in self.procs:
            raise SongError(f"unknown proc {name!r}")
        return self.request(self.fname("proc", voice, name), self.procs[name], voice)

    def record_lists(self, record, field, voice):
        """Dict source mapping record index to its generated action-list function."""
        if record not in self.records:
            raise SongError(f"unknown record {record!r}")
        rtype = self.rtypes.get(record)
        name = f"D_{'v' if voice else 'g'}_{record}_{field}"
        if rtype is not None and field in rtype.templates:
            fn = self.request(
                self.fname("tpl", voice, record, field), rtype.templates[field], voice, record
            )
            if name not in self.done:
                self.done.add(name)
                rows = tuple(sorted(self.records[record]))
                self.tail.append(f"{name} = {{i: ({fn}, i) for i in {rows!r}}}")
            return name
        items = []
        for index, row in sorted(self.records[record].items()):
            actions = row.get(field)
            if actions:
                fn = self.request(self.fname("rec", voice, record, field, index), actions, voice)
                items.append(f"{index}: ({fn}, None)")
        if name not in self.done:
            self.done.add(name)
            self.tail.append(f"{name} = {{{', '.join(items)}}}")
        return name

    def build(self, namespace):
        """Python source for the whole playroutine; ``namespace`` holds the runtime."""
        self.namespace = namespace  # pylint: disable=attribute-defined-outside-init
        self.request("f_init", self.playroutine.get("init") or [], False)
        self.request("f_tick", self.playroutine.get("tick") or [], False)
        while self.pending:
            name, actions, voice, row = self.pending.pop()
            body = []
            self.row_ctx = row
            self.block(actions, voice, body, 1)
            self.row_ctx = None
            args = (["v"] if voice else []) + (["_r=None"] if row is None else ["_r"])
            self.lines.append(f"def {name}({', '.join(args)}):")
            self.lines.extend(body or ["    pass"])
        self.lines.append(self.ghost())
        return "\n".join(self.lines + self.tail)

    def ghost(self):
        """Source of ``f_ghost(out)``, filling the 25 register values."""
        ghost = self.playroutine.get("ghost") or {}
        gscope, vscope = _Scope(self, False), _Scope(self, True)
        out = ["def f_ghost(out):"]
        for reg, text in (ghost.get("global") or {}).items():
            out.append(f"    out[{int(reg)}] = ({gscope.compile(text)}) & 0xFF")
        out.append("    for i, v in enumerate(VS):")
        out.append("        pass")
        for off, text in (ghost.get("voice") or {}).items():
            if not 0 <= int(off) < 7:
                raise SongError(f"voice register offset out of range: {off}")
            out.append(f"        out[7 * i + {int(off)}] = ({vscope.compile(text)}) & 0xFF")
        return "\n".join(out)

    def target(self, cell, voice):
        """Store source and width mask for assigning ``cell``."""
        if voice and cell in self.vindex:
            if cell in PSEUDO:
                raise SongError(f"{cell} is read-only")
            return f"v[{self.vindex[cell]}]", self.vcells[cell][0]
        if cell in self.gindex:
            return f"g[{self.gindex[cell]}]", self.gcells[cell][0]
        raise SongError(f"unknown cell {cell!r}")

    def is_lane(self, cell, voice):
        """Whether assigning ``cell`` from this scope assigns a lane."""
        in_voice = voice and cell in self.vindex
        return (in_voice, cell) in self.lanes

    def assign(self, cell, src, voice, out, pad):
        """Append a width-wrapped assignment; a lane's stored value follows its cell."""
        store, mask = self.target(cell, voice)
        if self.is_lane(cell, voice):
            out.append(f"{pad}{store} = {self.lane_ref(cell, voice)}.set({src})")
        else:
            wrap = src if mask is None else f"({src}) & {mask}"
            out.append(f"{pad}{store} = {wrap}")

    def lane_store(self, lane, voice, src, out, pad):
        """Append: run ``src`` (a lane call), store a changed value and the lane's carry."""
        lane_voice, slot, _, _ = self.lane_info(lane, voice)
        store, _ = self.target(lane, lane_voice)
        tmp = self.tmp()
        out.append(f"{pad}{tmp} = {src}")
        out.append(f"{pad}if {tmp} is not None:")
        out.append(f"{pad}    {store} = {tmp}")
        cell = self.carry.get(lane)
        if cell is not None:
            cstore, _ = self.target(cell, lane_voice)
            out.append(f"{pad}{cstore} = {'v' if lane_voice else 'g'}[{slot}].carry")

    def block(self, actions, voice, out, depth):
        """Append source for an action list."""
        if not isinstance(actions, list):
            raise SongError(f"expected an action list, got {actions!r}")
        for action in actions:
            self.action(action, voice, out, depth)

    def action(self, action, voice, out, depth):
        """Append source for one action."""
        # pylint: disable=too-many-branches
        pad = "    " * depth
        scope = _Scope(self, voice)
        if not isinstance(action, dict):
            raise SongError(f"bad action {action!r}")
        if "set" in action:
            for cell, text in action["set"].items():
                self.assign(cell, scope.compile(text), voice, out, pad)
        elif "if" in action:
            out.append(f"{pad}if {scope.compile(action['if'])}:")
            self.block(action.get("then") or [], voice, out, depth + 1)
            out.append(f"{pad}    pass")
            if action.get("else"):
                out.append(f"{pad}else:")
                self.block(action["else"], voice, out, depth + 1)
        elif "call" in action:
            for cell, text in (action.get("with") or {}).items():
                self.assign(cell, scope.compile(text), voice, out, pad)
            out.append(f"{pad}{self.proc(action['call'], voice)}({'v' if voice else ''})")
        elif "run" in action:
            run = action["run"]
            table = self.record_lists(run["record"], run["field"], voice)
            fn = self.tmp()
            out.append(f"{pad}{fn} = {table}.get({scope.compile(run['index'])})")
            out.append(f"{pad}if {fn} is not None:")
            out.append(f"{pad}    {fn}[0]({'v, ' if voice else ''}{fn}[1])")
        elif "for_voices" in action:
            fn = self.request(self.fname("each", True, self.tmp()), action["for_voices"], True)
            out.append(f"{pad}for _v in VS:")
            out.append(f"{pad}    {fn}(_v)")
        elif "fetch" in action:
            if not voice:
                raise SongError("fetch outside voice scope")
            out.append(f"{pad}_fetch(v)")
        elif "return" in action:
            out.append(f"{pad}return")
        elif "apply" in action or "start" in action:
            self.lane_action(action, scope, voice, out, pad)
        elif any(k in action for k in ("advance", "stop", "restart")):
            self.lane_simple(action, voice, out, pad)
        else:
            raise SongError(f"unknown action {action!r}")

    def lane_action(self, action, scope, voice, out, pad):
        """``apply`` (a record value or an int) and ``start`` (a spec id) on lanes."""
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        verb = "apply" if "apply" in action else "start"
        opts = (
            bool(action.get("once")),
            action.get("by", "row" if verb == "apply" else "command"),
            "v" if voice else "None",
        )
        for lane, src in action[verb].items():
            if verb == "start":
                self._lane_start(lane, src, opts, scope, (voice, out, pad))
            else:
                self._lane_apply(lane, src, opts, scope, (voice, out, pad))

    def _lane_start(self, lane, src, opts, scope, where):
        """Start (or run once) the spec whose id ``src`` gives on ``lane``."""
        # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
        voice, out, pad = where
        once, by, cells = opts
        ref = self.lane_ref(lane, voice)
        sid = self.tmp()
        out.append(f"{pad}{sid} = {scope.compile(src)}")
        spec = f"_spec({sid}, {lane!r}, {voice})"
        call = f"_once({ref}, {spec}, {cells})" if once else f"_start({ref}, {spec}, {by!r})"
        self.lane_store(lane, voice, call, out, pad)

    def _lane_apply(self, lane, src, opts, scope, where):
        """Give ``lane`` a record value (set, off, live or spec), or an int expression."""
        # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
        voice, out, pad = where
        once, by, cells = opts
        ref = self.lane_ref(lane, voice)
        place = self._source(src)
        if place is None:
            scale = self.lane_info(lane, voice)[3]
            self.lane_store(lane, voice, f"{ref}.set(({scope.compile(src)}) * {scale})", out, pad)
            return
        record, index, field = place
        table = self.obj_table(record, field, lane, voice)
        obj = self.tmp()
        out.append(f"{pad}{obj} = {table}.get({scope.compile(index) if index else '_r'})")
        out.append(f"{pad}if {obj} is not None:")
        call = f"_apply({ref}, {obj}, {cells}, {by!r}, {once})"
        self.lane_store(lane, voice, call, out, pad + "    ")

    def _source(self, src):
        """``(record, index expression or None, field)`` for a row or record source."""
        if not isinstance(src, str):
            return None
        tree = parse(src)
        if not isinstance(tree, ast.Attribute):
            return None
        sub = tree.value
        if isinstance(sub, ast.Name) and sub.id == "row":
            if self.row_ctx is None:
                raise SongError("row.field is only defined in a record template")
            return self.row_ctx, None, tree.attr
        if isinstance(sub, ast.Subscript) and sub.value.id in self.records:
            return sub.value.id, ast.unparse(sub.slice), tree.attr
        return None

    def lane_simple(self, action, voice, out, pad):
        """``advance``, ``stop`` and ``restart`` of lanes."""
        for verb in ("advance", "stop", "restart"):
            if verb not in action:
                continue
            lanes = action[verb]
            for lane in lanes if isinstance(lanes, list) else [lanes]:
                ref = self.lane_ref(lane, voice)
                if verb == "advance":
                    self.lane_store(
                        lane, voice, f"{ref}.advance({'v' if voice else 'None'})", out, pad
                    )
                else:
                    out.append(f"{pad}{ref}.{verb}()")

    def tmp(self):
        """A fresh local name."""
        self.counter += 1
        return f"_t{self.counter}"


class SongPlayer:
    """Plays a unitracker song dict, one tick per call."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, song: dict, record_reads: bool = False):
        if song.get("unitracker") != IR_VERSION:
            raise SongError("not an expanded unitracker song; read files with unitracker.songio")
        if len(song["tracks"]) != int(song.get("voices", 3)):
            raise SongError("one track per voice is required")
        self.song = song
        gen = _Codegen(song)
        self.globals = [init for _, init in gen.gcells.values()]
        self.globals += [Lane(mask) for (voice, _), (_, mask, _) in gen.lanes.items() if not voice]
        self._index = (gen.gindex, gen.vindex)
        self.table_reads: dict[str, set[int]] | None = None
        namespace = {
            "g": self.globals,
            "T": gen.tables,
            "F": gen.fields,
            "P": gen.present,
            "_tg": self._reader(gen.tables) if record_reads else _tg,
            "_apply": _apply,
            "_start": lambda lane, spec, by: lane.start(spec, by),
            "_once": lambda lane, spec, cells: lane.once(spec, cells),
            "_towards": lambda sid: int("toward" in gen.specs.get(sid, {})),
            "_spec": lambda sid, lane, voice: (
                gen.compiled_spec(sid, lane, voice) if sid else _STOP
            ),
        }
        commands = self._commands(gen)
        self.voice_cells = self._voices(gen, commands)
        namespace["VS"] = self.voice_cells
        namespace["_fetch"] = _fetcher(gen)
        code = compile(
            gen.build(namespace), f"<playroutine {song['playroutine'].get('name', '')}>", "exec"
        )
        for name, table in gen.objs.items():
            namespace[name] = table
        exec(code, namespace)  # pylint: disable=exec-used
        self._entry = (namespace["f_init"], namespace["f_tick"], namespace["f_ghost"])
        regs = song.get("registers") or [0] * REG_COUNT
        if len(regs) != REG_COUNT:
            raise SongError(f"registers must have {REG_COUNT} values")
        self.regs = [int(r) & 0xFF for r in regs]
        self.ticks = 0

    def _commands(self, gen):
        """A function turning an event's ``command`` into ``(cmd, arg, arg2, argn)``."""
        table = gen.playroutine.get("commands") or {}

        def convert(command):
            if not command:
                return 0, 0, 0, 0
            name, arg = command
            if name not in table:
                raise SongError(f"the playroutine has no command {name!r}")
            code = int(table[name]["code"])
            if arg is None:
                return code, 0, 0, 0
            if isinstance(arg, list):
                return code, int(arg[0]), int(arg[1]), 2
            if isinstance(arg, dict):
                return code, gen.spec_id(arg), 0, 1
            return code, int(arg), 0, 1

        return convert

    def _voices(self, gen, convert):
        """Each voice's cells: the declared cells, its cursor, then its lanes."""
        phrases = {}
        for name, events in (self.song.get("phrases") or {}).items():
            rows = []
            for event in phrase_steps(events):
                if event is None:
                    rows.append((0, 0, 0, 0, 0, 0, 0))
                    continue
                kind, pitch = event_note(event)
                rows.append(
                    (kind, pitch, int(event.get("instrument") or 0), *convert(event.get("command")))
                )
            phrases[name] = rows
        lanes = [mask for (voice, _), (_, mask, _) in gen.lanes.items() if voice]
        out = []
        for track in self.song["tracks"]:
            cells = [init for _, init in gen.vcells.values()]
            cursor = Cursor(track, phrases)
            cells[gen.vindex["at_phrase_start"]] = cursor.at_phrase_start
            cells.append(cursor)
            cells.extend(Lane(mask) for mask in lanes)
            out.append(cells)
        return out

    def _reader(self, tables):
        """A table read that records each in-range index it reads, per table."""
        self.table_reads = {name: set() for name in tables}
        reads = {id(table): self.table_reads[name] for name, table in tables.items()}

        def tg(table, index):
            if 0 <= index < len(table):
                reads[id(table)].add(index)
                return table[index]
            return 0

        return tg

    def state(self) -> tuple:
        """The complete playback state: every cell, cursor position, lane and register."""

        def part(value):
            if isinstance(value, Lane):
                return value.state()
            if isinstance(value, Cursor):
                return (value.index, value.repeat, value.step, value.ended)
            return value

        voices = tuple(tuple(part(c) for c in cells) for cells in self.voice_cells)
        return (tuple(part(c) for c in self.globals), voices, tuple(self.regs))

    def cell(self, name: str, voice: int | None = None) -> int:
        """The current value of a global cell, or of ``voice``'s cell ``name``."""
        gindex, vindex = self._index
        if voice is None:
            return self.globals[gindex[name]]
        return self.voice_cells[voice][vindex[name]]

    def play_frame(self) -> list[tuple[int, int]]:
        """Run one tick; return its flush, changed registers in canonical order.

        The first tick returns every register, as a SID render needs the full file.
        """
        init, tick, ghost = self._entry
        (init if self.ticks == 0 else tick)()
        out = list(self.regs)
        ghost(out)
        first = self.ticks == 0
        writes = [(r, out[r]) for r in CANONICAL_ORDER if first or out[r] != self.regs[r]]
        self.regs = out
        self.ticks += 1
        return writes

    def iter_flushes(self, n: int) -> Iterator[list[tuple[int, int]]]:
        """Yield ``n`` ticks of canonical flushes."""
        for _ in range(n):
            yield self.play_frame()

    def render_grid(self, n: int) -> np.ndarray:
        """``(n, 25)`` uint8 grid: row i holds the registers after tick i."""
        grid = np.empty((n, REG_COUNT), dtype=np.uint8)
        for i in range(n):
            self.play_frame()
            grid[i] = self.regs
        return grid

    def render_wav(self, path, seconds: float, model: str = "8580", progress: bool = False):
        """Render ``seconds`` to a WAV file through an emulated SID."""
        from pysidtracker.audio import render_wav  # pylint: disable=import-outside-toplevel

        clock = self.song["clock"]
        cycles = int(clock["cycles_per_tick"])
        hz = float(clock["clock_hz"])
        n = int(seconds * hz / cycles)
        flushes = tqdm(
            self.iter_flushes(n), total=n, desc="render", unit="tick", disable=not progress
        )
        return render_wav(flushes, path, model=model, cycles_per_frame=cycles, clock_frequency=hz)


class _Stop:
    """The spec of id 0: starting it stops the lane."""

    # pylint: disable=too-few-public-methods

    start = None
    moves = False


_STOP = _Stop()


def _fetcher(gen):
    """The ``fetch`` action: advance a voice's cursor into its pseudo-cells."""
    slot = gen.cursor_slot
    idx = [gen.vindex[name] for name in PSEUDO]

    def fetch(v):
        cursor = v[slot]
        for i, value in zip(idx, cursor.fetch()):
            v[i] = value
        v[idx[7]] = cursor.at_phrase_start
        v[idx[8]] = int(cursor.ended)

    return fetch
