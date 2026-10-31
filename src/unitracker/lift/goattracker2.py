"""A GoatTracker 2 tune as a format 2.5 document, from pygoattracker's typed song and the flags
of its bound playroutine spec; ``verify`` plays the document against the spec player with the
standard note table (docs/lift/goattracker2.md)."""

import re
import sys
from dataclasses import dataclass

import numpy as np
from pygoattracker import Build, PlayerState, TypedSong, decompile_sid, gt2spec, songimage
from pygoattracker import constants as C
from pygoattracker import typed as T
from pygoattracker.canonical import canonical_song
from pygoattracker.model import PlayPattern, Repeat, Transpose
from pysidtracker import NTSC_CLOCK_HZ, PAL_CLOCK_HZ, find_format, gridcmp
from pysidtracker.playspec.player import SpecPlayer
from unitracker_grammar.document import (
    HOLD,
    NONE,
    Document,
    Event,
    Expr,
    Call,
    Code,
    Header,
    Instrument,
    Once,
    Play,
    Program,
    Row,
    Section,
    Signed,
    Slot,
    Voice,
    base,
    printed,
    signature,
)
from unitracker_grammar import code as asm
from unitracker_grammar.expr import canonical
from unitracker_player.player import play

from unitracker.lift import cli
from unitracker.lift.gt2code import ROUTINES, STATE
from unitracker.lift.shared import meta_text, note_name, rests

CYCLES = {PAL_CLOCK_HZ: 19656, NTSC_CLOCK_HZ: 17095}
PRE = 2
FREQ = (T.PortamentoUp, T.PortamentoDown, T.TonePortamento, T.Vibrato)
LOAD = "load.freq"
WAVE_FREQ = "wave.freq"
TICK = "tick.freq"
CHECK = 4000
summary = cli.summary


class LiftError(ValueError):
    """A tune the lift cannot state exactly, or whose document differs from the reference."""


class Refusal(LiftError):
    """A construct of a tune the document cannot state; ``kind`` names its class."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


class Divergence(LiftError):
    """The document's grid differs from the reference player's."""


@dataclass(frozen=True)
class Core:  # pylint: disable=too-many-instance-attributes
    """What a lift needs of a tune: the typed song, the bound spec layout, the build and start
    state, the clock and the title lines."""

    song: TypedSong
    lay: dict
    build: Build
    state: PlayerState | None
    clock: int
    head: tuple
    subtune: int = 0
    play_address: int = 1
    image: object = None
    memory: bytes | None = None
    wrap: object = None

    @property
    def voices(self) -> tuple:
        """The played channels in voice order."""
        offsets = self.build.voices or tuple(7 * c for c in range(self.lay["nchan"]))
        return tuple(c for c in self.lay["exec"] if offsets[c] is not None)


def core_of_sid(raw: bytes, subtune: int = 0) -> Core:
    """The core of a SID's subtune."""
    model = decompile_sid(raw, subtune)
    lay = gt2spec.binder(model)(model.memory, 0)
    head = model.header
    clock = NTSC_CLOCK_HZ if head.is_ntsc else PAL_CLOCK_HZ
    info = model.info
    names = (head.name, head.author, head.released)
    return Core(
        model.typed,
        lay,
        info.playback,
        info.state,
        clock,
        names,
        model.subtune,
        head.play_address,
        memory=model.memory,
        wrap=info.wrap,
    )


def core_of_song(song: TypedSong, build=None, state=None, clock: int = PAL_CLOCK_HZ) -> Core:
    """The core of a typed song packed as the relocator would, played by the spec."""
    build = build or Build()
    img = songimage.song_image(song, build, state, None)
    return Core(song, img.layout, build, state, clock, (song.name, song.author, ""), image=img)


def _refuse(core: Core):
    lay, build = core.lay, core.build
    if core.play_address == 0:
        raise Refusal("interrupt", "play address 0")
    if lay["stereo"] or lay["nchan"] > C.MAX_CHN:
        raise Refusal("multi-SID", f"{lay['nchan']} channels")
    if core.wrap is not None and core.wrap.sink is not None:
        raise Refusal("wrapper", "the play routine flushes a ghost block itself")
    if lay["sid"] != 0xD400:
        raise Refusal("SID address", f"${lay['sid']:04X}")
    if re.search(r"\bnative\b", gt2spec.spec().source or ""):
        raise Refusal("native", "the spec has native blocks")
    for flag in ("noeffects", "directwave", "livegate", "nonormalspeed"):
        if lay[flag]:
            raise Refusal(f"build {flag}")
    if build.pulse_step not in (1, 2) and not lay["pulse2x"]:
        raise Refusal("pulse step", str(build.pulse_step))
    offsets = build.voices or tuple(7 * c for c in range(lay["nchan"]))
    if [offsets[c] for c in core.voices] != [7 * v for v in range(len(core.voices))]:
        raise Refusal("voice layout", str(offsets))
    if any(m not in (None, (0xFF, 0)) for m in build.overlay or ()):
        raise Refusal("control overlay", str(build.overlay))


def _calls(node):
    """The names of the calls under ``node``, in its printed order."""
    if isinstance(node, Call):
        yield node.name
    elif isinstance(node, (tuple, list)):
        for item in node:
            yield from _calls(item)
    elif hasattr(node, "__dataclass_fields__"):
        for name in node.__dataclass_fields__:
            yield from _calls(getattr(node, name))


def _late(key: str, value) -> bool:
    return base(key) in ("res", "route", "mode", "vol", "cut") or (
        base(key) == "run" and str(value).startswith("f")
    )


def _played(core: Core) -> tuple:
    """``(rows, instruments)`` of the patterns the subtune's orderlists play."""
    song = core.song
    orders = song.subtunes[core.subtune].channels
    played = {e.num for o in orders for e in o.entries if isinstance(e, PlayPattern)}
    rows = [r for n in played if n < len(song.patterns) for r in song.patterns[n]]
    return rows, {r.instrument for r in rows if r.instrument} | {1}


def _speeds(core: Core, rows: list, used: set) -> None:
    """Refuse a speedtable row the song uses that the player reads as other bytes than the typed
    song holds; a vibrato of no step reads the same either way."""
    song, lay, mem = core.song, core.lay, core.memory
    cmds = [r.command for r in rows]
    cmds += [r.command for r in song.wavetable if isinstance(r, T.WaveCommand)]
    cmds = [c for c in cmds if hasattr(c, "speed")]
    vibs = {c.speed for c in cmds if isinstance(c, T.Vibrato)}
    vibs |= {song.instrument(n).vibrato_speed for n in used if not lay["noinstrvib"]}
    plain = {c.speed for c in cmds if not isinstance(c, T.Vibrato)}
    for y in (vibs | plain) - {0}:
        row = song.speed(y)
        word = 0 if row is None else Lift.word(row)
        read = mem[lay["speedleft"] + y] << 8 | mem[lay["speedright"] + y]
        if read != word and (y in plain or (read | word) & 255):
            raise Refusal("layout", f"speedtable row {y} is not what the player reads")


def _jumps(core: Core, rows: list, used: set) -> None:
    """Refuse a table jump onto a jump row and a pointer set onto one, which the executors play
    as an ordinary row."""
    song = core.song
    tables = {
        "wave": (song.wavetable, T.SetWavePtr, "wave_ptr"),
        "pulse": (song.pulsetable, T.SetPulsePtr, "pulse_ptr"),
        "filter": (song.filtertable, T.SetFilterPtr, "filter_ptr"),
    }
    for kind, (table, command, field) in tables.items():
        points = [getattr(song.instrument(n), field) for n in used]
        points += [r.command.row for r in rows if type(r.command) is command]
        points += [r.row for r in table if isinstance(r, T.Jump)]
        for ptr in points:
            if (
                ptr
                and ptr <= len(table)
                and isinstance(table[ptr - 1], T.Jump)
                and table[ptr - 1].row
            ):
                raise Refusal("pointer onto a jump row", kind)


class Names:
    """Definitions by kind, numbered in first-use order."""

    def __init__(self):
        self.kinds = {}

    def name(self, kind: str, prefix: str, value) -> str:
        """The name of ``value`` among the ``kind`` records, adding it."""
        store = self.kinds.setdefault(kind, {})
        for name, held in store.items():
            if held == value:
                return name
        store[name := f"{prefix}{len(store) + 1}"] = value
        return name

    def section(self, kind: str) -> list:
        """The records of ``kind``."""
        return list(self.kinds.get(kind, {}).items())


def _program(rows, ptr: int, keys) -> tuple:
    """``(program rows, loop)`` of table ``rows`` from 1-based ``ptr``: ``keys(rows, p)`` gives
    ``(row keys, next pointer)``; jumps are followed, a revisited row is the loop."""
    out, at, p = [], {}, ptr
    while p and p not in at:
        row = rows[p - 1] if p <= len(rows) else T.Stop()
        if isinstance(row, T.Jump):
            p = row.row
        elif isinstance(row, T.Stop):
            p = 0
        else:
            at[p] = len(out)
            keys_, p = keys(rows, p)
            out.append(Row(keys_))
    return out, at.get(p)


def _plus(step: int) -> Expr:
    return Expr(canonical(f"v + {step}"))


class Lift:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """One core's document."""

    def __init__(self, core: Core):
        _refuse(core)
        rows, used = _played(core)
        if core.memory is not None:
            _speeds(core, rows, used)
        _jumps(core, rows, used)
        self.core, self.lay, self.names = core, core.lay, Names()
        self.song = canonical_song(core.song, core.subtune)
        self.rowlen = self.lay["deftempo"] + 1
        self.global_cells = set()
        self.slotted = False
        rows = [r for p in self.song.patterns for r in p]
        self.cut_set = any(isinstance(r.command, T.SetCutoff) for r in rows) or any(
            isinstance(r, T.WaveCommand) and isinstance(r.command, T.SetCutoff)
            for r in self.song.wavetable
        )
        used = {r.instrument for r in rows if r.instrument} | {1}
        self.insvib = not self.lay["noinstrvib"] and any(self.vib_of(n) for n in used)
        self.vib = self.insvib or any(isinstance(r.command, T.Vibrato) for r in rows)
        waved = any(
            isinstance(r, T.WaveCommand) and isinstance(r.command, FREQ[:2])
            for r in self.song.wavetable
        )
        self.effects = self.vib or waved or any(isinstance(r.command, FREQ) for r in rows)
        self.calls = {}
        state = core.state
        self.funk = tuple(v + 1 for v in state.funk) if state else (8, 5)
        funks = {self.funk_of(r.command.speed) for r in rows if isinstance(r.command, T.FunkTempo)}
        self.pairs = funks | {self.funk}
        silent = any(not self.sounds(i.wave_ptr) for n in used for i in [self.song.instrument(n)])
        self.slotted = self.effects or silent
        if self.rowlen < 3:
            raise Refusal("build", "tempo")
        tempos = [max(r.command.tempo, 3) for r in rows if isinstance(r.command, T.SetTempo)]
        self.rowmin = min([self.rowlen, *(min(p) for p in self.pairs), *tempos])

    # ---- tables -----------------------------------------------------------------------------

    @staticmethod
    def word(row) -> int:
        """The 16-bit word of speedtable ``row``."""
        if isinstance(row, T.FixedSpeed):
            return row.value
        return (0x80 | row.rate) << 8 | row.shift

    def call(self, name: str, **params) -> Call:
        """The call of template ``name`` with ``params`` in its signature's order."""
        self.calls[name] = None
        return Call(name, tuple(params[k] for k in asm.params(ROUTINES[name])))

    def vib_of(self, n: int):
        """``(depth, step, delay)`` of instrument ``n``'s vibrato, None without one."""
        ins = self.song.instrument(n)
        if not ins.vibrato_delay or self.lay["noinstrvib"]:
            return None
        row = self.song.speed(ins.vibrato_speed)
        c, s = self.depth_step(row)
        return None if not s else (c, s, max(ins.vibrato_delay - 1, 0))

    def depth_step(self, row) -> tuple:
        """``(depth, step)`` of a vibrato's speedtable ``row``."""
        if row is None:
            return 0, 0
        if isinstance(row, T.CalculatedSpeed):
            raise Refusal("calculated speed")
        c = row.value >> 8
        return (c if self.lay["nocalcspeed"] else c & 0x7F), row.value & 0xFF

    def fx0(self, instr: int):
        """The tick binding command 0 latches: the instrument's vibrato, or none."""
        vib = self.vib_of(instr) if self.insvib else None
        return self.call("vibi", c=vib[0], s=vib[1]) if vib else Expr("v")

    def load(self, instr: int) -> dict:
        """The commands loading instrument ``instr``'s vibrato delay."""
        vib = self.vib_of(instr) if self.insvib else None
        if not vib:
            return {}
        return {LOAD: Once(Code.of(f"lda #{vib[2]}\nsta d\nrts"))}

    def wave_row(self, rows, p):
        """``(keys, next pointer)`` of wavetable row ``p``."""
        row = rows[p - 1]
        if isinstance(row, T.WaveCommand):
            return self.wave_command(row.command), p + 1
        keys = {}
        match row.wave:
            case T.SetWave(w):
                keys["ctrl"] = w
            case T.Delay(t):
                keys["wait"] = t
        if row.note is not None:
            n = row.note
            keys["note"] = Signed(n.value) if n.relative else note_name(n.value)
            if self.slotted:
                keys[WAVE_FREQ] = Once(self.call("note") if self.vib else Expr("F(N)"))
        return keys, p + 1

    def wave_command(self, cmd) -> dict:
        """The row keys of a wavetable command."""
        match cmd:
            case T.PortamentoUp(y) | T.PortamentoDown(y) if not self.vib:
                step = self.step(y) * (1 if isinstance(cmd, T.PortamentoUp) else -1)
                return {WAVE_FREQ: Once(_plus(step))}
            case (
                T.SetAD()
                | T.SetSR()
                | T.SetWave()
                | T.SetPulsePtr()
                | T.SetFilterPtr()
                | T.SetFilterCtrl()
                | T.SetCutoff()
                | T.SetMasterVolume()
            ):
                return self.command(cmd, 0)
        raise Refusal("wavetable command", type(cmd).__name__)

    @staticmethod
    def late(rows: list, loop) -> tuple:
        """``rows`` with each row's filter and volume keys moved to a row of no hold a tick
        on, where the next call's filter routine writes them."""
        out, at = [], []
        for row in rows:
            at.append(len(out))
            later = [(k, v) for k, v in row.keys if _late(k, v)]
            if later:
                out.append(Row([kv for kv in row.keys if kv not in later]))
                out.append(Row(later + [("hold", 0)]))
            else:
                out.append(row)
        return out, None if loop is None else at[loop]

    def wave(self, ptr: int) -> str:
        """The ``ctrl`` program name of the wavetable from ``ptr``."""
        out, loop = _program(self.song.wavetable, ptr, self.wave_row) if ptr else ([], None)
        out, loop = self.late(out, loop)
        return self.names.name("ctrl", "c", Program(out, loop))

    def sounds(self, ptr: int) -> bool:
        """Whether the wavetable from ``ptr`` plays a note on its first row."""
        rows, _ = _program(self.song.wavetable, ptr, self.wave_row) if ptr else ([], None)
        first = rows[0].keys if rows else ()
        return bool(rows) and dict(first).get("note") is not None and "wait" not in dict(first)

    def pulse_row(self, rows, p):
        """``(keys, next pointer)`` of pulse table row ``p``."""
        row = rows[p - 1]
        step = 2 if self.lay["pulse2x"] else 1
        if isinstance(row, T.SimplePulseSet):
            return {"pw": (row.value & 15) << 8 | row.value}, p + 1
        if isinstance(row, T.SimplePulseMod):
            add = f"lo(v) + {row.step}"
            e = f"(({add}) & 255) + (({add}) >> 8)"
            text = f"((({e}) & 15) << 8) | ({e})"
            return {"hold": row.time or 256, "pw": Expr(canonical(text))}, p + 1
        if isinstance(row, T.PulseSet):
            return {"pw": row.width & 0xFFF}, p + 1
        return {"hold": row.time or 256, "pw": _plus(row.speed * step)}, p + 1

    def pulse(self, ptr: int) -> str:
        """The ``pw`` program name of the pulse table from ``ptr``."""
        rows, loop = _program(self.song.pulsetable, ptr, self.pulse_row)
        if loop is None and rows and isinstance(rows[-1].get("pw"), Expr):
            rows.append(Row({"pw": NONE}))
        return self.names.name("pw", "pw", Program(rows, loop))

    def filter_row(self, rows, p):
        """``(keys, next pointer)`` of filter table row ``p``."""
        row = rows[p - 1]
        cut = "filter.cut" if self.cut_set else "cut"
        if isinstance(row, T.FilterSet):
            keys = {"mode": row.passband, "res": row.resonance, "route": row.channels}
            nxt = rows[p] if p < len(rows) else None
            if isinstance(nxt, T.CutoffSet):
                return keys | {cut: nxt.cutoff << 3}, p + 2
            return keys | {cut: NONE}, p + 1
        if isinstance(row, T.CutoffSet):
            return {cut: row.cutoff << 3}, p + 1
        return {"hold": row.time, cut: _plus(row.speed << 3)}, p + 1

    def filter(self, ptr: int) -> str:
        """The ``filter`` program name of the filter table from ``ptr``."""
        self.global_cells |= {"cut", "res", "route", "mode"}
        rows, loop = _program(self.song.filtertable, ptr, self.filter_row)
        cut = "filter.cut" if self.cut_set else "cut"
        if loop is None and rows and isinstance(rows[-1].get(cut), Expr):
            rows.append(Row({cut: NONE}))
        return self.names.name("filter", "f", Program(rows, loop))

    # ---- instruments ------------------------------------------------------------------------

    def first_wave(self, ins) -> tuple:
        """``(wave or None, gate or None)`` a note of ``ins`` sets on its tick 0."""
        lay, raw = self.lay, ins.raw().first_wave
        if lay["fixedparams"]:
            return lay["firstwave"], 1
        if (lay["zeroskip"] or not lay["nofirstwavecmd"]) and raw == 0:
            return None, None
        if not lay["nofirstwavecmd"] and raw >= 0xFE:
            return None, raw & 1
        return raw, 1

    def restart(self, ins) -> str:
        """The name of the restart program of ``ins``: the fetch, then the note's tick 0."""
        lay = self.lay
        timer = lay["gateparam"] if lay["fixedparams"] else ins.gate_timer
        fetch = {"hold": timer} if timer != 1 else {}
        gated = (lay["hrinstr"] and lay["nohrinstr"]) or lay["legato"]
        if not (gated and ins.legato):
            fetch["gate"] = 0
            if lay["hrinstr"] and not (gated and not ins.hard_restart):
                fetch["ad"], fetch["sr"] = lay["adparam"], lay["srparam"]
        wave, gate = self.first_wave(ins)
        init = {"ad": ins.attack_decay, "sr": ins.sustain_release, "run": self.wave(0)}
        if wave is not None:
            init["ctrl"] = wave
        if gate is not None:
            init["gate"] = gate
        return self.names.name("restart", "r", Program([Row(fetch), Row(init), Row()]))

    def instrument(self, n: int) -> str:
        """The name of instrument ``n``."""
        ins = self.song.instrument(n)
        fields = {"ctrl": self.wave(ins.wave_ptr)} if ins.wave_ptr else {}
        if ins.pulse_ptr:
            fields["pw"] = self.pulse(ins.pulse_ptr)
        if ins.filter_ptr:
            fields["filter"] = self.filter(ins.filter_ptr)
        if not self.sounds(ins.wave_ptr):
            fields[WAVE_FREQ] = Expr("v")
        fields.update(self.load(n))
        fields["restart"] = self.restart(ins)
        self.names.kinds.setdefault("ins", {})[f"i{n}"] = Instrument(fields)
        return f"i{n}"

    # ---- rows -------------------------------------------------------------------------------

    def command(  # pylint: disable=too-many-return-statements,too-many-branches,too-many-locals
        self, cmd, instr: int
    ) -> dict:
        """The event commands of a pattern command."""
        match cmd:
            case T.DoNothing():
                return {TICK: self.fx0(instr)} if self.effects else {}
            case T.Vibrato(y):
                c, s = self.depth_step(self.song.speed(y))
                return {TICK: self.call("vib", c=c, s=s)}
            case T.TonePortamento(y):
                step = self.step(y)
                if self.vib:
                    return {TICK: self.call("tporta", s=step) if step else self.call("tsnap")}
                if not step:
                    return {TICK: Expr("F(n)")}
                down = f"ite(u16(u16(v - F(n)) - {step}) >= 32768, F(n), u16(v - {step}))"
                up = f"ite(u16(u16(v - F(n)) + {step}) < 32768, F(n), u16(v + {step}))"
                return {TICK: Expr(canonical(f"ite(v >= F(n), {down}, {up})"))}
            case T.PortamentoUp(y) | T.PortamentoDown(y):
                up = isinstance(cmd, T.PortamentoUp)
                step = self.step(y)
                if self.vib:
                    return {TICK: self.call("portau" if up else "portad", s=step)}
                return {TICK: _plus(step if up else -step)}
            case T.SetAD(v):
                return {"ad": v}
            case T.SetSR(v):
                return {"sr": v}
            case T.SetWave(w):
                return {"ctrl": w}
            case T.SetWavePtr(row):
                return {"run": self.wave(row)}
            case T.SetPulsePtr(row):
                return {"run": self.pulse(row)} if row else {"pw": HOLD}
            case T.SetFilterPtr(row):
                return {"run": self.filter(row)} if row else {"cut": HOLD}
            case T.SetFilterCtrl(res, chans):
                keys = {"res": res, "route": chans}
                return keys | ({} if res or chans else {"cut": HOLD})
            case T.SetCutoff(c):
                return {"set.cut": c << 3}
            case T.SetMasterVolume(v):
                return {"vol": v}
            case T.TimingMark():
                if self.lay["noauthorinfo"]:
                    raise Refusal("timing mark in a build without author info")
                return {}
            case T.SetTempo(t, chan):
                key = "vtempo" if self.lay["noglobaltempo"] or chan else "tempo"
                if t == 1 and self.lay["nofunktempo"]:
                    return {key: 2}
                if t < 2 and (self.lay["nofunktempo"] or len(self.pairs) > 1):
                    raise Refusal("funk tempo recall", "several tables" if t < 2 else "")
                return {key: (self.funk[t], self.funk[1 - t]) if t < 2 else max(t, 3)}
            case T.FunkTempo(y):
                if self.lay["nofunktempo"]:
                    raise Refusal("funk tempo", "build without it")
                return {"vtempo" if self.lay["noglobaltempo"] else "tempo": self.funk_of(y)}
        raise Refusal("pattern command", type(cmd).__name__)

    def funk_of(self, y: int) -> tuple:
        """The two row lengths of speedtable entry ``y``."""
        row = self.song.speed(y)
        if not isinstance(row, T.FixedSpeed):
            raise Refusal("funk tempo", "calculated speed row")
        return row.value >> 8, row.value & 0xFF

    def step(self, y: int) -> int:
        """The 16-bit step of speedtable entry ``y``."""
        row = self.song.speed(y)
        if row is None:
            return 0
        if isinstance(row, T.CalculatedSpeed) and not self.lay["nocalcspeed"]:
            raise Refusal("calculated speed")
        return self.word(row)

    def event(self, row, instr: int) -> Event:
        """The event of a pattern row played by instrument ``instr``."""
        noted = isinstance(row.note, T.Note)
        match row.note:
            case T.Note(p):
                atom = note_name(p)
            case T.KeyOff():
                atom = "off"
            case T.KeyOn():
                atom = "on"
            case T.Rest():
                atom = "-"
            case _:
                raise Refusal("gate note", str(row.note))
        name = self.instrument(row.instrument) if row.instrument else None
        cmds = self.command(row.command, instr)
        tone = isinstance(row.command, T.TonePortamento)
        if tone and noted:
            cmds["legato"] = True
            cmds.update(self.load(instr))
        elif noted and self.effects and not isinstance(row.command, FREQ):
            cmds[TICK] = self.fx0(instr)
        return Event(atom, name, cmds)

    def phrase(self, rows, instr: int) -> tuple:
        """``(name, instrument at the end)`` of the phrase of pattern ``rows`` entered playing
        ``instr``."""
        events, here = [], instr
        for row in rows:
            here = row.instrument or here
            events += [self.event(row, here)] + [Event("-")] * (row.length - 1)
        return self.names.name("phrase", "p", tuple(rests(events))), here

    # ---- orderlists -------------------------------------------------------------------------

    def plays(self, order) -> tuple:
        """``(plays, loop)``: the ``(pattern, transpose)`` each sequencer step starts and the
        index the orderlist loops to."""
        entries, pos, trans, count = order.entries, 0, 0, 0
        seen, out = {}, []
        while len(out) < 4096:
            if pos >= len(entries):
                pos = order.restart
            if (pos, trans) in seen and pos < len(entries):
                return out, seen[(pos, trans)]
            if pos < len(entries):
                seen[(pos, trans)] = len(out)
            if pos >= len(entries):
                raise Refusal("orderlist", "restarts past its end")
            entry = entries[pos]
            pos += 1
            match entry:
                case Transpose(s):
                    trans = s
                case Repeat(c):
                    count = c
                case PlayPattern(num):
                    out += [(num, trans)] * (count + 1)
                    count = 0
        raise Refusal("orderlist", "does not loop")

    def overlap(self, plays: list) -> None:
        """Refuse a note whose restart reads the note before it back to that note's own tick, where
        the document applies the earlier note's gate after the later note's rows."""
        instr, since = 1, None
        for num, _ in plays:
            for row in self.song.patterns[num]:
                noted = isinstance(row.note, T.Note) and not isinstance(row.command, T.TonePortamento)
                if noted and since is not None and self.song.instrument(instr).gate_timer + 1 >= since:
                    raise Refusal("restart overlap", "a restart reaches the previous note's tick")
                since = 0 if noted else since
                instr = row.instrument or instr
                since = None if since is None else since + self.rowmin * row.length

    def voice(self, order) -> Voice:
        """A voice's orderlist."""
        plays, loop = self.plays(order)
        self.overlap(plays + plays[loop : loop + 1])
        items, instr = [Play("p0")], 1
        for num, trans in plays:
            name, instr = self.phrase(self.song.patterns[num], instr)
            items.append(Play(name, Signed(trans) if trans else None))
        return Voice(tuple(items), loop + 1)

    # ---- the document -----------------------------------------------------------------------

    def state(self):
        """The state line: the variables the routines in use name."""
        named = {x for n in self.calls for x in asm.symbols(ROUTINES[n])}
        named |= {"d"} if self.insvib else set()
        keep = tuple((k, n) for k, n in STATE if k in named)
        return (("freq", keep),) if keep else None

    def mods(self, doc_text_calls) -> tuple:
        """The ``mod`` records of the templates in use, in order of first call."""
        return tuple(
            (signature(n, asm.params(ROUTINES[n])), Code(ROUTINES[n])) for n in doc_text_calls
        )

    def start(self) -> list:
        """The registers before tick 0: a ghost image, else the init call's writes."""
        state = self.core.state
        if state and state.ghost:
            return list(state.registers)
        regs = [0] * 25
        for reg, value in (*self.core.build.init_regs, *(state.init if state else ())):
            regs[reg] = value
        regs[24] = state.volume if state else 15
        return regs

    def opening(self, voice: int) -> dict:
        """``{cell: (value the first call writes, value before it)}`` of the voice cells that
        change: the channel variables the song-init call leaves as loaded."""
        state = self.core.state
        if not state or state.ghost:
            return {}
        b = 7 * voice
        now, then = (r[b : b + 7] for r in (self.start(), state.registers))
        cells = {
            "freq": lambda r: r[0] | r[1] << 8,
            "pw": lambda r: r[2] | (r[3] & 15) << 8,
            "ctrl": lambda r: r[4],
            "ad": lambda r: r[5],
            "sr": lambda r: r[6],
        }
        return {k: (f(then), f(now)) for k, f in cells.items() if f(then) != f(now)}

    def first(self, voice: int = 0) -> dict:
        """The commands setting ``voice``'s cells (the song's, on voice 0) to the values the
        first call writes, where they differ from the registers before it."""
        state, regs = self.core.state, self.start()
        out = {}
        if voice == 0:
            cut = "set.cut" if self.cut_set else "cut"
            want = {
                cut: ((state.cutoff << 3) if state else 0, regs[21] & 7 | regs[22] << 3),
                "res": (0, regs[23] >> 4),
                "route": (0, regs[23] & 15),
                "mode": (state.filttype if state else 0, regs[24] & 0xF0),
                "vol": (state.volume if state else 15, regs[24] & 15),
            }
            out = {k: v for k, (v, have) in want.items() if v != have}
        for key, (then, now) in self.opening(voice).items():
            if key in ("freq", "pw"):
                out["boot.pw" if key == "pw" else key] = Once(_plus(then - now))
            else:
                out[key] = then
        return out

    def header(self, voices: int) -> Header:
        """The header lines."""
        core, lay = self.core, self.lay
        regs = self.start()
        pulseopt = lay["pulseopt"]
        events = ("read", "note-1", "last-1") if pulseopt else ("note-1",)
        boot = any("pw" in self.opening(v) for v in range(voices))
        slots = {"pw": (Slot("pulse", hold=events),) + ((Slot("boot"),) if boot else ())}
        if self.slotted:
            real = "step-1" if lay["realtimeopt"] else "note-1"
            tick = Slot("tick", hold=(real, "row.freq"))
            slots["freq"] = ((Slot("load"),) if self.insvib else ()) + (Slot("wave"), tick)
        if self.cut_set:
            slots["cut"] = (Slot("set"), Slot("filter"))
        glob = tuple(c for c in ("cut", "res", "route", "mode", "vol") if c in self.global_cells)
        name, author, released = (meta_text(s) for s in core.head)
        return Header(
            (("title", name), ("author", author), ("released", released)),
            clock=(CYCLES[core.clock], core.clock),
            voices=voices if voices != C.MAX_CHN else None,
            slots=slots,
            holds={"pw": events},
            state=self.state(),
            regs=tuple(regs) if any(regs) else None,
            lag=self.lag or None,
            global_cells=glob or None,
            calls=(core.build.calls, None) if core.build.calls > 1 else None,
        )

    @property
    def lead(self) -> int:
        """The GoatTracker calls before the document's first tick: its first filter step
        writes the start state."""
        return 1 if self.lay["fullghost"] or any(self.first(v) for v in range(len(self.core.voices))) else 0

    @property
    def lag(self) -> int:
        """The calls the registers lag the cells."""
        return self.lead + int(self.lay["fullghost"])

    def document(self) -> Document:
        """The document of the subtune."""
        channels = self.song.subtunes[0].channels
        voices = [self.voice(channels[c]) for c in self.core.voices]
        for n in range(1, len(self.song.instruments) + 1):
            self.instrument(n)
        init = [
            Event("-", "i1", ({"tempo": PRE - self.lead} if v == 0 else {}) | self.first(v))
            for v in range(len(voices))
        ]
        intro = {"tempo": self.rowlen} | ({TICK: self.fx0(1)} if self.insvib else {})
        phrases = [("p0", (Event("-"), Event("-", commands=intro)))]
        phrases += self.names.section("phrase")
        defs = [Section(k, tuple(self.names.section(k))) for k in ("restart", "ins", "ctrl", "pw")]
        defs += [Section("filter", tuple(self.names.section("filter")))]
        defs = [d for d in defs if d.records] + [Section("phrase", tuple(phrases))]
        head = self.header(len(voices))
        doc = Document(head, (), (), defs, tuple(init), voices, None)
        order = [n for n in dict.fromkeys(_calls(doc)) if n in self.calls]
        return Document(head, (), self.mods(order), defs, tuple(init), voices, None)


def document_text(core: Core) -> str:
    """The document of ``core``, printed."""
    return printed(Lift(core).document())


# ---- the reference --------------------------------------------------------------------------


def player_of(core: Core, notes: bool = True):
    """The spec player of ``core`` with the standard note table (``notes``) or its own."""
    img = core.image
    if img is None:
        raise Refusal("reference", "a core of a SID is played by its format")
    return SpecPlayer(
        gt2spec.spec(),
        img.data,
        songimage.BASE,
        img.init,
        img.play,
        0,
        bind=lambda view, entry: img.layout,
        entries=lambda view: {img.entry},
        notes=core.clock if notes else None,
    )


def reference(raw: bytes, subtune: int = 0, frames: int = 20000, notes: bool = True):
    """The register grid of the spec player over ``frames`` frames."""
    fmt = find_format(raw)
    model = decompile_sid(raw, subtune)
    return np.asarray(fmt.spec_player(model, notes=notes).render_grid(frames), dtype=np.uint8)


def compare(core: Core, frames: int, grid=None) -> tuple:
    """``(cells, text)``: the cells where the document of ``core`` differs from ``grid`` (the
    reference over ``frames`` frames; for a core of a song, its player's)."""
    text = document_text(core)
    ours = np.asarray(play(text, frames), dtype=np.uint8)
    if grid is None:
        grid = np.asarray(player_of(core).render_grid(len(ours)), dtype=np.uint8)
    return gridcmp.cell_diff(ours, grid[: len(ours)]), text


def checked(text_cells: tuple, frames: int) -> str:
    """The text of ``(cells, text)``, or a Divergence naming the first differing cell."""
    cells, text = text_cells
    if cells.size:
        frame, reg = (int(c) for c in cells[0][:2])
        raise Divergence(f"frame {frame} register {reg}: {len(cells)} cells over {frames} frames")
    return text


def verify(raw: bytes, subtune: int = 0, frames: int = 20000) -> tuple:
    """``(cells, text)`` of a SID: the cells where its document differs from the reference."""
    core = core_of_sid(raw, subtune)
    text = document_text(core)
    ours = np.asarray(play(text, frames), dtype=np.uint8)
    theirs = reference(raw, subtune, len(ours))
    return gridcmp.cell_diff(ours, theirs), text


def lift(raw: bytes, subtune: int = 0, frames: int | None = None) -> str:
    """The document of a SID's subtune, checked against the reference over ``frames`` frames
    (default :data:`CHECK`); a LiftError otherwise."""
    frames = frames or CHECK
    return checked(verify(raw, subtune, frames), frames)


def main(argv=None) -> int:
    """Lift a SID, or verify SIDs against the spec player."""
    return cli.main(argv, __doc__.split("\n", 1)[0], lift, verify)


if __name__ == "__main__":
    sys.exit(main())
