"""The generic unitracker player: compiles a song's playroutine to Python and runs it."""

from collections.abc import Iterator

import numpy as np
from tqdm import tqdm

from unitracker import modulation, xexpr
from unitracker.canonical import CANONICAL_ORDER, REG_COUNT
from unitracker.expr import ExprError, Resolver
from unitracker.records import EXPR, record_types

IR_VERSION = "ir"
NOTE_KINDS = {None: 0, "off": 2, "on": 3}
MAX_COMMANDS = 2
PSEUDO = (
    "ev_kind",
    "ev_note",
    "ev_instr",
    "ev_cmd",
    "ev_arg",
    "ev_cmd2",
    "ev_arg2",
    "at_phrase_start",
    "ended",
)
EVENT_COLUMNS = 3 + 2 * MAX_COMMANDS


CLAMPS = {False: 0, True: 1, "reflect": 2}


class SongError(ValueError):
    """The song document is malformed or uses something the format does not define."""


def event_commands(event) -> list[tuple[int, int]]:
    """An event's ``(command, argument)`` pairs, in the order the playroutine reads them."""
    if "command" in event and "commands" in event:
        raise SongError(f"an event has command or commands, not both: {event!r}")
    commands = event.get("commands")
    if commands is None:
        commands = [event["command"]] if event.get("command") else []
    if len(commands) > MAX_COMMANDS:
        raise SongError(f"an event has at most {MAX_COMMANDS} commands: {event!r}")
    return [(int(c), int(a)) for c, a in commands]


def _event_row(event) -> tuple[int, ...]:
    note = event.get("note")
    if isinstance(note, bool):
        note = "on" if note else "off"
    if isinstance(note, int):
        kind, pitch = 1, note
    elif note in NOTE_KINDS:
        kind, pitch = NOTE_KINDS[note], 0
    else:
        raise SongError(f"bad note {note!r}")
    cmds = event_commands(event)
    cmds += [(0, 0)] * (MAX_COMMANDS - len(cmds))
    return (kind, pitch, int(event.get("instrument") or 0), *(x for pair in cmds for x in pair))


def fold_empty(events) -> list[dict]:
    """Events with each fully empty event folded into the ``steps`` of the one before it."""
    out = []
    for event in events:
        if not event and out:
            out[-1]["steps"] = out[-1].get("steps", 1) + 1
        else:
            out.append(event)
    return out


def compile_phrase(events) -> np.ndarray:
    """A phrase as an ``(steps, 7)`` array of (kind, note, instrument, cmd, arg, cmd2, arg2)."""
    rows = []
    for event in events:
        steps = int(event.get("steps", 1))
        if steps < 1:
            raise SongError(f"event steps must be >= 1: {event!r}")
        rows.append(_event_row(event))
        rows.extend([(0,) * EVENT_COLUMNS] * (steps - 1))
    if not rows:
        raise SongError("empty phrase")
    return np.asarray(rows, dtype=np.int64)


class Cursor:
    """One voice's position in its track."""

    def __init__(self, track, phrases):
        self.plays = []
        for play in track["plays"]:
            if play["phrase"] not in phrases:
                raise SongError(f"unknown phrase {play['phrase']!r}")
            self.plays.append(
                (
                    phrases[play["phrase"]],
                    int(play.get("transpose", 0)),
                    int(play.get("repeat", 1)),
                )
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

    def fetch(self) -> tuple[int, ...]:
        """Read the next step: (kind, note + transpose, instrument, cmd, arg, cmd2, arg2)."""
        if self.ended:
            return (0,) * EVENT_COLUMNS
        rows, transpose, _ = self.plays[self.index]
        kind, note, *rest = (int(x) for x in rows[self.step])
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
        return (kind, note + transpose if kind == 1 else note, *rest)

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
    """Name resolution for code running in global or voice scope, with optional locals."""

    def __init__(self, gen, voice: bool, local=()):
        super().__init__()
        self.functions = gen.resolver_functions
        self.gen = gen
        self.voice = voice
        self.local = set(local)

    def name(self, name):
        gen = self.gen
        if name in self.local:
            return name
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
        default = self.gen.need_field(record, field)
        return f"F[{(record, field)!r}].get({index}, {default})"

    def row_field(self, field):
        record = self.gen.row_ctx
        if record is None:
            return super().row_field(field)
        default = self.gen.need_field(record, field)
        return f"F[{(record, field)!r}].get(_r, {default})"


def _tg(table, index):
    return table[index] if 0 <= index < len(table) else 0


class _Codegen:
    """Generates one Python module of functions from a song's playroutine."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, song, watch=()):
        playroutine = song["playroutine"]
        self.watch = set(watch)
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
        self.resolver_functions = Resolver(playroutine.get("functions")).functions
        self.procs = playroutine.get("procs") or {}
        self.fields = {}
        self.lines = []
        self.tail = []
        self.done = set()
        self.pending = []
        self.counter = 0
        self.row_ctx = None

    def need_field(self, record, field) -> int:
        """Register an int field of a record for lookup tables; return its default."""
        rtype = self.rtypes.get(record)
        default = rtype.default(field) if rtype is not None else 0
        key = (record, field)
        if key not in self.fields:
            self.fields[key] = {
                i: int(row.get(field, default))
                for i, row in self.records[record].items()
                if not isinstance(row.get(field, default), list)
            }
        return default

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
        """Dict source mapping record index to its generated action-list function.

        A record type's template serves every row: rows map to ``(fn, index)``.
        """
        if record not in self.records:
            raise SongError(f"unknown record {record!r}")
        rtype = self.rtypes.get(record)
        if rtype is not None and field in rtype.templates:
            fn = self.request(
                self.fname("tpl", voice, record, field), rtype.templates[field], voice, record
            )
            name = f"D_{'v' if voice else 'g'}_{record}_{field}"
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
        name = f"D_{'v' if voice else 'g'}_{record}_{field}"
        if name not in self.done:
            self.done.add(name)
            self.tail.append(f"{name} = {{{', '.join(items)}}}")
        return name

    def build(self):
        """Python source for the whole playroutine."""
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
        self.lines.append(self.modulators())
        return "\n".join(self.lines + self.tail)

    def modulators(self) -> str:
        """Source of the modulator helpers: ``_F``, ``_T``, ``_n`` and each unit's selector."""
        spec = modulation.declared(self.playroutine)
        if not spec:
            return "_T = {}"
        scope, offset = _Scope(self, True), int(self.playroutine.get("note_offset", 0))
        freq = spec.get("freq") or {}
        if "word" in freq:
            word = f"_tg(T[{freq['word']!r}], k + {offset})"
        else:
            word = f"(_tg(T[{freq['hi']!r}], k + {offset}) << 8) | _tg(T[{freq['lo']!r}], k + {offset})"
        out = [f"def _F(k):\n    return {word}"]
        names = []
        for name, table in (spec.get("tables") or {}).items():
            entry = _Scope(self, True, ("i",)).compile(table["entry"])
            out.append(
                f"def _T_{name}(i):\n    return ({entry}) if 0 <= i < {int(table['size'])} else 0"
            )
            names.append(f"{name!r}: _T_{name}")
        out.append(f"_T = {{{', '.join(names)}}}")
        notes = spec.get("notes") or []
        if notes:
            note = scope.compile(notes[0])
            if spec.get("transpose"):
                note = f"{note} + {scope.compile(spec['transpose'])}"
            out.append(f"def _n(v):\n    return {note} - {offset}")
        for i, unit in enumerate(modulation.units(self.playroutine)):
            if "x" in unit:
                out.append(f"def _x_{i}(v):\n    return {scope.compile(unit['x'])}")
        return "\n".join(out)

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

    def assign(self, cell, src, voice, out, pad):
        """Append a width-wrapped assignment."""
        store, mask = self.target(cell, voice)
        out.append(f"{pad}{store} = {src}" if mask is None else f"{pad}{store} = ({src}) & {mask}")
        self.count(cell, store, out, pad)

    def count(self, cell, store, out, pad):
        """Append the write count of a watched cell (see :mod:`unitracker.modulation`)."""
        if cell in self.watch:
            key = f"({'id(v)' if store.startswith('v[') else 0}, {cell!r})"
            out.append(f"{pad}_W[{key}] = _W.get({key}, 0) + 1")

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
        elif "accumulate" in action:
            self.accumulate(action, scope, voice, out, pad, depth)
        else:
            raise SongError(f"unknown action {action!r}")

    def tmp(self):
        """A fresh local name."""
        self.counter += 1
        return f"_t{self.counter}"

    def accumulate(self, action, scope, voice, out, pad, depth):
        """Append the bounded accumulator, a call to the ``_acc`` runtime helper."""
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        store, mask = self.target(action["accumulate"], voice)
        args = [store, scope.compile(action.get("step", 0)), str(mask)]
        for key in ("min", "max", "toward"):
            args.append(scope.compile(action[key]) if key in action else "None")
        args += map(str, acc_mode(action, mask is not None))
        val, hit = self.tmp(), self.tmp()
        out.append(f"{pad}{val}, {hit} = _acc({', '.join(args)})")
        out.append(f"{pad}{store} = {val}")
        self.count(action["accumulate"], store, out, pad)
        if action.get("on_bound"):
            out.append(f"{pad}if {hit}:")
            self.block(action["on_bound"], voice, out, depth + 1)
            out.append(f"{pad}    pass")


def acc_mode(action: dict, has_width: bool) -> tuple:
    """``(clamp code, signed)`` of an ``accumulate`` action, checked."""
    clamp, signed = action.get("clamp", True), bool(action.get("signed", False))
    if clamp not in CLAMPS:
        raise SongError(f"clamp is true, false or reflect, not {clamp!r}")
    if clamp == "reflect" and "toward" in action:
        raise SongError("clamp: reflect with toward")
    if signed and not has_width:
        raise SongError(f"signed accumulate on {action['accumulate']!r}, a cell with no width")
    return CLAMPS[clamp], signed


def _acc(v0, step, mask, lo, hi, toward, clamp, signed):
    """One bounded accumulation: ``(value, hit)``, bounds tested after wrapping.

    ``clamp`` is 0 (wrap), 1 (clamp) or 2 (reflect); ``signed`` reads values as two's complement.
    """
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    if signed:
        half = mask >> 1
        v0, lo, hi, toward = (
            x if x is None or x <= half else x - mask - 1 for x in (v0, lo, hi, toward)
        )
    if toward is not None:
        if v0 == toward:
            return v0 & mask if signed else v0, False
        if v0 < toward:
            hi = toward
        else:
            step, lo = -step, toward
    value = v0 + step
    if mask is not None:
        value &= mask
    test = value - mask - 1 if signed and value > half else value
    if hi is not None and test > hi:
        bound = hi
    elif lo is not None and test < lo:
        bound = lo
    else:
        return value, False
    value = (value, bound, v0 - step)[clamp]
    return (value if mask is None else value & mask), True


def _voice_cells(song, gen) -> list[list]:
    """Initial cells for each voice, its cursor in the last slot."""
    phrases = {name: compile_phrase(ev) for name, ev in (song.get("phrases") or {}).items()}
    out = []
    for track in song["tracks"]:
        cells = [init for _, init in gen.vcells.values()]
        cursor = Cursor(track, phrases)
        cells[gen.vindex["at_phrase_start"]] = cursor.at_phrase_start
        cells.append(cursor)
        out.append(cells)
    return out


def _fetcher(gen):
    """The ``fetch`` action: advance a voice's cursor into its pseudo-cells."""
    slot = gen.cursor_slot
    idx = [gen.vindex[name] for name in PSEUDO]

    def fetch(v):
        cursor = v[slot]
        for i, value in zip(idx, cursor.fetch()):
            v[i] = value
        v[idx[EVENT_COLUMNS]] = cursor.at_phrase_start
        v[idx[EVENT_COLUMNS + 1]] = int(cursor.ended)

    return fetch


class SongPlayer:
    """Plays a unitracker song dict, one tick per call."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, song: dict, record_reads: bool = False, watch: bool | None = None):
        if song.get("unitracker") != IR_VERSION:
            raise SongError("not an expanded unitracker song; read files with unitracker.songio")
        if len(song["tracks"]) != int(song.get("voices", 3)):
            raise SongError("one track per voice is required")
        self.song = song
        exprs = song.get("exprs") or {}
        if watch is None:
            watch = bool(exprs)
        pr = song["playroutine"]
        gen = _Codegen(song, modulation.watched(pr) if watch else ())
        self.globals = [init for _, init in gen.gcells.values()]
        self._index = (gen.gindex, gen.vindex)
        self.voice_cells = _voice_cells(song, gen)
        self.table_reads: dict[str, set[int]] | None = None
        namespace = {
            "g": self.globals,
            "VS": self.voice_cells,
            "T": gen.tables,
            "F": gen.fields,
            "_tg": self._reader(gen.tables) if record_reads else _tg,
            "_acc": _acc,
            "_fetch": _fetcher(gen),
            "_W": {},
        }
        code = compile(gen.build(), f"<playroutine {pr.get('name', '')}>", "exec")
        exec(code, namespace)  # pylint: disable=exec-used
        self.namespace = namespace
        self._masks = (
            {n: m for n, (m, _) in gen.gcells.items() if m is not None},
            {n: m for n, (m, _) in gen.vcells.items() if m is not None},
        )
        self._entry = (namespace["f_init"], namespace["f_tick"], namespace["f_ghost"])
        self.modulation = _Driven(self, exprs) if exprs else None
        regs = song.get("registers") or [0] * REG_COUNT
        if len(regs) != REG_COUNT:
            raise SongError(f"registers must have {REG_COUNT} values")
        self.regs = [int(r) & 0xFF for r in regs]
        self.ticks = 0

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
        """The complete playback state: every cell, cursor position, register and modulator."""
        voices = tuple(
            (*cells[:-1], c.index, c.repeat, c.step, c.ended)
            for cells, c in ((cells, cells[-1]) for cells in self.voice_cells)
        )
        mods = self.modulation.state() if self.modulation is not None else ()
        return (tuple(self.globals), voices, tuple(self.regs), mods)

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


class _Stepper:
    """One expression's firing: ``(f(v, φ, N), φ + 1)``."""

    # pylint: disable=too-few-public-methods

    def __init__(self, fn):
        self.fn = fn

    def step(self, v: int, phi: int, n: int) -> tuple:
        """``(v', φ')``."""
        return self.fn(v, phi, n), phi + 1


class _Driven(modulation.Modulation):
    """The units of a song with expressions: a start runs the expression its selector names."""

    def __init__(self, player, exprs: dict):
        ns = player.namespace
        self.steppers = {}
        for index, text in exprs.items():
            try:
                self.steppers[int(index)] = _Stepper(xexpr.compile_expr(str(text), ns))
            except xexpr.ExpressionError as exc:
                raise SongError(f"{EXPR}{index}: {exc}") from exc
        pr = player.song["playroutine"]
        self.selectors = {
            u["name"]: ns[f"_x_{i}"] for i, u in enumerate(modulation.units(pr)) if "x" in u
        }
        super().__init__(player, self.choose, keep=False)

    def capture(self, unit: dict, voice) -> dict:
        """The expression index the unit's selector names before its first firing."""
        fn = self.selectors.get(unit["name"])
        cells = self.player.voice_cells[voice] if voice is not None else None
        return {"x": fn(cells) if fn is not None else 0}

    def choose(self, unit: dict, occ) -> object:
        """The stepper of an occurrence's expression, or None for the playroutine's own."""
        occ.key = occ.start["x"]
        if not occ.key:
            return None
        if occ.key not in self.steppers:
            raise SongError(f"{unit['name']} selects expression {occ.key}, which is undefined")
        return self.steppers[occ.key]
