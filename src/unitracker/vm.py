"""The generic unitracker player: compiles a song's playroutine to Python and runs it."""

from collections.abc import Iterator

import numpy as np
from tqdm import tqdm

from unitracker.canonical import CANONICAL_ORDER, REG_COUNT
from unitracker.expr import ExprError, Resolver
from unitracker.records import record_types

IR_VERSION = "ir"
NOTE_KINDS = {None: 0, "off": 2, "on": 3}
PSEUDO = ("ev_kind", "ev_note", "ev_instr", "ev_cmd", "ev_arg", "at_phrase_start", "ended")


class SongError(ValueError):
    """The song document is malformed or uses something the format does not define."""


def _event_row(event) -> tuple[int, int, int, int, int]:
    note = event.get("note")
    if isinstance(note, bool):
        note = "on" if note else "off"
    if isinstance(note, int):
        kind, pitch = 1, note
    elif note in NOTE_KINDS:
        kind, pitch = NOTE_KINDS[note], 0
    else:
        raise SongError(f"bad note {note!r}")
    cmd, arg = event.get("command") or (0, 0)
    return kind, pitch, int(event.get("instrument") or 0), int(cmd), int(arg)


def compile_phrase(events) -> np.ndarray:
    """A phrase as an ``(steps, 5)`` array of (kind, note, instrument, cmd, arg)."""
    rows = []
    for event in events:
        steps = int(event.get("steps", 1))
        if steps < 1:
            raise SongError(f"event steps must be >= 1: {event!r}")
        rows.append(_event_row(event))
        rows.extend([(0, 0, 0, 0, 0)] * (steps - 1))
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

    def fetch(self) -> tuple[int, int, int, int, int]:
        """Read the next step: (kind, note + transpose, instrument, cmd, arg)."""
        if self.ended:
            return 0, 0, 0, 0, 0
        rows, transpose, _ = self.plays[self.index]
        kind, note, instr, cmd, arg = (int(x) for x in rows[self.step])
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
        return kind, note + transpose if kind == 1 else note, instr, cmd, arg

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

    def assign(self, cell, src, voice, out, pad):
        """Append a width-wrapped assignment."""
        store, mask = self.target(cell, voice)
        out.append(f"{pad}{store} = {src}" if mask is None else f"{pad}{store} = ({src}) & {mask}")

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
        args.append(str(bool(action.get("clamp", True))))
        val, hit = self.tmp(), self.tmp()
        out.append(f"{pad}{val}, {hit} = _acc({', '.join(args)})")
        out.append(f"{pad}{store} = {val}")
        if action.get("on_bound"):
            out.append(f"{pad}if {hit}:")
            self.block(action["on_bound"], voice, out, depth + 1)
            out.append(f"{pad}    pass")


def _acc(v0, step, mask, lo, hi, toward, clamp):
    """One bounded accumulation: ``(value, hit)``, bounds tested after wrapping."""
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    if toward is not None:
        if v0 == toward:
            return v0, False
        if v0 < toward:
            hi = toward
        else:
            step, lo = -step, toward
    value = v0 + step
    if mask is not None:
        value &= mask
    if hi is not None and value > hi:
        return (hi if clamp else value), True
    if lo is not None and value < lo:
        return (lo if clamp else value), True
    return value, False


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
        v[idx[5]] = cursor.at_phrase_start
        v[idx[6]] = int(cursor.ended)

    return fetch


class SongPlayer:
    """Plays a unitracker song dict, one tick per call."""

    def __init__(self, song: dict):
        if song.get("unitracker") != IR_VERSION:
            raise SongError("not an expanded unitracker song; read files with unitracker.songio")
        if len(song["tracks"]) != int(song.get("voices", 3)):
            raise SongError("one track per voice is required")
        self.song = song
        gen = _Codegen(song)
        self.globals = [init for _, init in gen.gcells.values()]
        self._index = (gen.gindex, gen.vindex)
        self.voice_cells = _voice_cells(song, gen)
        namespace = {
            "g": self.globals,
            "VS": self.voice_cells,
            "T": gen.tables,
            "F": gen.fields,
            "_tg": _tg,
            "_acc": _acc,
            "_fetch": _fetcher(gen),
        }
        code = compile(gen.build(), f"<playroutine {song['playroutine'].get('name', '')}>", "exec")
        exec(code, namespace)  # pylint: disable=exec-used
        self._entry = (namespace["f_init"], namespace["f_tick"], namespace["f_ghost"])
        regs = song.get("registers") or [0] * REG_COUNT
        if len(regs) != REG_COUNT:
            raise SongError(f"registers must have {REG_COUNT} values")
        self.regs = [int(r) & 0xFF for r in regs]
        self.ticks = 0

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
