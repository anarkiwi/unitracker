# pylint: disable=too-many-lines
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
from pysidtracker.wrap import count_entries
from pysidtracker.playspec.tv import TVError
from pysidtracker.playspec.player import SpecPlayer
from unitracker_grammar.document import (
    HOLD,
    NONE,
    Document,
    Event,
    Group,
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

from unitracker.lift import cli, invariants
from unitracker.lift.gt2code import ROUTINES, STATE
from unitracker.lift import gt2tables, gt2wrap
from unitracker.lift.shared import meta_text, note_name, rests

CYCLES = {PAL_CLOCK_HZ: 19656, NTSC_CLOCK_HZ: 17095}
PRE = 2
ORDER = ("events", "ctrl", "read", "pulse", "filter", "freq", "pw", "cut")
FREQ = (T.PortamentoUp, T.PortamentoDown, T.TonePortamento, T.Vibrato)
LOAD = "load.freq"
WAVE_FREQ = "wave.freq"
TICK = "tick.freq"
CHECK = 20000
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
    entries: int = 1
    own: tuple = ()

    @property
    def voices(self) -> tuple:
        """The played channels in voice order."""
        offsets = self.build.voices or tuple(7 * c for c in range(self.lay["nchan"]))
        return tuple(c for c in self.lay["exec"] if offsets[c] is not None)


def _entries(model) -> int:
    """The calls of the player one frame of the tune's play routine makes."""
    wrap = model.info.wrap
    if wrap is None:
        return 1
    return count_entries(model.image, wrap.song, wrap.play, wrap.entries)


def _own_writes(model) -> tuple:
    """The SID registers the tune's play routine writes other than as the ghost byte of the
    register (:mod:`unitracker.lift.gt2wrap`); a Refusal where that cannot be decided."""
    wrap = model.info.wrap
    if wrap is None or wrap.sink is None:
        return ()
    try:
        return gt2wrap.own_writes(model.memory, wrap)
    except TVError as exc:
        raise Refusal("wrapper", f"the play routine is outside what is checked: {exc}") from exc


def _landed(song: TypedSong, subtune: int = 0) -> TypedSong:
    """``song`` with the landings on jump rows stated, a Refusal where they cannot be."""
    try:
        return gt2tables.landings(song, subtune)
    except gt2tables.Landing as exc:
        raise Refusal("pointer onto a jump row", str(exc)) from exc


def core_of_sid(raw: bytes, subtune: int = 0) -> Core:
    """The core of a SID's subtune."""
    model = decompile_sid(raw, subtune)
    lay = gt2spec.binder(model)(model.memory, 0)
    head = model.header
    clock = NTSC_CLOCK_HZ if head.is_ntsc else PAL_CLOCK_HZ
    info = model.info
    names = (head.name, head.author, head.released)
    return Core(
        _landed(model.typed, model.subtune),
        lay,
        info.playback,
        info.state,
        clock,
        names,
        model.subtune,
        head.play_address,
        memory=model.memory,
        wrap=info.wrap,
        entries=_entries(model),
        own=_own_writes(model),
    )


def core_of_song(song: TypedSong, build=None, state=None, clock: int = PAL_CLOCK_HZ) -> Core:
    """The core of a typed song packed as the relocator would, played by the spec."""
    build = build or Build()
    img = songimage.song_image(song, build, state, None)
    return Core(
        _landed(song), img.layout, build, state, clock, (song.name, song.author, ""), image=img
    )


PENDING = ("newnote", "newfx", "newparam", "waveptr", "pulseptr")
UNUSED = {"noeffects": ("newfx", "newparam")}


def _pending(core: Core) -> None:
    """Refuse a channel whose song start leaves a note, a command or a table program running: the
    variables it does not zero keep what the init left."""
    unused = {n for flag, names in UNUSED.items() if core.lay[flag] for n in names}
    for name in [n for n in PENDING if n not in unused]:
        for c in core.voices:
            if value := _started(core, name, c):
                raise Refusal(
                    "start state", f"{name} of channel {c} is {value} after the song start"
                )


def _refuse(core: Core):
    lay, build = core.lay, core.build
    if core.play_address == 0:
        raise Refusal("interrupt", "play address 0")
    if lay["stereo"] or lay["nchan"] > C.MAX_CHN:
        raise Refusal("multi-SID", f"{lay['nchan']} channels")
    _pending(core)
    if core.own:
        raise Refusal("wrapper", f"the play routine writes registers {list(core.own)} of its own")
    if lay["sid"] != 0xD400:
        raise Refusal("SID address", f"${lay['sid']:04X}")
    if re.search(r"\bnative\b", gt2spec.spec().source or ""):
        raise Refusal("native", "the spec has native blocks")
    if lay["directwave"]:
        raise Refusal("build directwave")
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
    _, rows, used = gt2tables.played(core.song, core.subtune)
    return rows, used


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


def _first_wave(lay, ins) -> tuple:
    """``(wave or None, gate or None)`` a note of ``ins`` sets on its tick 0."""
    raw = ins.raw().first_wave
    if lay["fixedparams"]:
        return lay["firstwave"], 1
    if (lay["zeroskip"] or not lay["nofirstwavecmd"]) and raw == 0:
        return None, None
    if not lay["nofirstwavecmd"] and raw >= 0xFE:
        return None, raw & 1
    return raw, 1


def _drops_gate(lay, ins) -> bool:
    """Whether the row fetch before a note of ``ins`` turns the gate off."""
    gated = (lay["hrinstr"] and lay["nohrinstr"]) or lay["legato"]
    return not (gated and ins.legato)


def _keeps_gate(lay, ins) -> bool:
    """Whether a note of ``ins`` leaves the gate as it was: neither its fetch nor its first
    waveform sets it."""
    return _first_wave(lay, ins)[1] is None and not _drops_gate(lay, ins)


def _gate_after(lay, ins, gate: int) -> int:
    """The gate after a note of ``ins`` that leaves it ``gate`` before."""
    first = _first_wave(lay, ins)[1]
    if first is not None:
        return first
    return gate if _keeps_gate(lay, ins) else 0


def _context_after(lay, song, rows, context: tuple, track: bool) -> tuple:
    """``(instrument, gate)`` at the end of pattern ``rows`` entered with ``context``; the gate is
    followed only where ``track`` (an instrument keeps it) and is 1 otherwise."""
    instr, gate = context
    for row in rows:
        instr = row.instrument or instr
        if not track:
            continue
        if isinstance(row.note, T.KeyOff):
            gate = 0
        elif isinstance(row.note, T.KeyOn):
            gate = 1
        elif isinstance(row.note, T.Note) and not isinstance(row.command, T.TonePortamento):
            gate = _gate_after(lay, song.instrument(instr), gate)
    return instr, gate


def _var(core: Core, name: str, c: int) -> int:
    """The byte of channel variable ``name`` of channel ``c`` in the image the tune's init
    leaves."""
    at = core.lay[name] + 7 * c
    return core.memory[at] if core.memory is not None else core.image.data[at - songimage.BASE]


def _started(core: Core, name: str, c: int) -> int:
    """The channel variable ``name`` of channel ``c`` after the song start, which zeroes the bytes
    from ``songptr`` on (``resetlen`` + 1 of them) and leaves the others as the init left them."""
    first, at = core.lay["songptr"], core.lay[name] + 7 * c
    return 0 if first <= at <= first + core.lay["resetlen"] else _var(core, name, c)


def _gates(core) -> tuple:
    """The gate of each played channel before the first call."""
    return tuple(_var(core, "gate", c) & 1 for c in core.voices)


def native_counts(core: Core) -> dict:
    """The orderlist entries a lift of ``core`` may write per played channel (``song``, bounded by
    :func:`unitracker.lift.invariants.check`): its entries, a repeat entry counted once, the
    entry that starts the voice, and the entries of its loop body once more, which the lift plays
    a second time when the context the loop is re-entered in changes its phrases. The objects of
    the other kinds are held to their origins by :func:`unitracker.lift.invariants.provenance`."""
    channels = canonical_song(core.song, core.subtune).subtunes[0].channels
    orders = [channels[c] for c in core.voices]
    return {"song": tuple(2 * len(o.entries) - min(o.restart, len(o.entries)) + 1 for o in orders)}


class Names:
    """Definitions by kind, numbered in first-use order, each with the origin it was first named
    for."""

    def __init__(self):
        self.kinds = {}
        self.origins = {}

    def name(self, kind: str, prefix: str, value, origin: invariants.Origin = None) -> str:
        """The name of ``value`` among the ``kind`` records, adding it with ``origin``."""
        store = self.kinds.setdefault(kind, {})
        for name, held in store.items():
            if held == value:
                return name
        store[name := f"{prefix}{len(store) + 1}"] = value
        self.origins.setdefault(kind, {})[name] = origin
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


def _carried(entries, pos: int, trans: int):
    """The transpose in force at orderlist ``pos`` that the next pattern played can read: None where
    a transpose entry sets it first."""
    for entry in entries[pos:]:
        if isinstance(entry, PlayPattern):
            break
        if isinstance(entry, Transpose):
            return None
    return trans


def _plus(step: int) -> Expr:
    return Expr(canonical(f"v + {step}"))


class Lift:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """One core's document."""

    def __init__(self, core: Core):
        _refuse(core)
        rows, used = _played(core)
        if core.memory is not None:
            _speeds(core, rows, used)
        self.core, self.lay, self.names = core, core.lay, Names()
        self.entered = {}
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
        self.used = used
        self.track = any(_keeps_gate(self.lay, self.song.instrument(n)) for n in used)
        self.insvib = not self.lay["noinstrvib"] and any(self.vib_of(n) for n in used)
        if self.lay["noeffects"] and self.insvib:
            raise Refusal(
                "noeffects vibrato",
                "the vibrato reads the live instrument from the row fetch, which a first note "
                "and an instrument-only row have no restart row for",
            )
        cmds = [r.command for r in rows]
        cmds += [r.command for r in self.song.wavetable if isinstance(r, T.WaveCommand)]
        rates = {c.speed for c in cmds if hasattr(c, "speed")} | {
            self.song.instrument(n).vibrato_speed for n in used if self.vib_speed(n)
        }
        calc = any(self.rate(self.song.speed(y))[0] for y in rates if y)
        self.vib = self.insvib or calc or any(isinstance(c, T.Vibrato) for c in cmds)
        self.effects = self.vib or any(isinstance(c, FREQ) for c in cmds)
        self.calls = {}
        state = core.state
        self.funk = tuple(v + 1 for v in state.funk) if state else (8, 5)
        funks = {self.funk_of(r.command.speed) for r in rows if isinstance(r.command, T.FunkTempo)}
        self.recall = self.recalled(funks)
        recalls = any(isinstance(r.command, T.SetTempo) and r.command.tempo < 2 for r in rows)
        pairs = [self.cadence(p, 0) for p in funks]
        if recalls and self.recall and not self.lay["nofunktempo"]:
            pairs += [self.cadence(self.recall, t) for t in (0, 1)]
        pairs = [p for p in pairs if p]
        silent = any(not self.sounds(i.wave_ptr) for n in used for i in [self.song.instrument(n)])
        self.slotted = self.effects or silent
        if self.rowlen < 3:
            raise Refusal("build", "tempo")
        tempos = [max(r.command.tempo, 3) for r in rows if isinstance(r.command, T.SetTempo)]
        self.rowmin = min([self.rowlen, *(min(p) for p in pairs), *tempos])
        self.tempo_rows = [*(max(p) for p in pairs), *tempos]

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

    def vib_speed(self, n: int) -> bool:
        """Whether instrument ``n`` has a vibrato at all."""
        return bool(self.song.instrument(n).vibrato_delay) and not self.lay["noinstrvib"]

    def vib_of(self, n: int):
        """``(depth, rate, delay)`` of instrument ``n``'s vibrato, None without one."""
        ins = self.song.instrument(n)
        if not ins.vibrato_delay or self.lay["noinstrvib"]:
            return None
        c, rate = self.depth_step(self.song.speed(ins.vibrato_speed))
        return None if rate == (False, 0) else (c, rate, max(ins.vibrato_delay - 1, 0))

    def rate(self, row) -> tuple:
        """``(calculated, value)`` of speedtable ``row``: the shift of a calculated step, else the
        16-bit step."""
        if isinstance(row, T.CalculatedSpeed) and not self.lay["nocalcspeed"]:
            return True, row.shift
        return False, 0 if row is None else self.word(row)

    def depth_step(self, row) -> tuple:
        """``(depth, rate)`` of a vibrato's speedtable ``row``: the step is a byte."""
        if isinstance(row, T.CalculatedSpeed) and not self.lay["nocalcspeed"]:
            return row.rate, (True, row.shift)
        word = 0 if row is None else self.word(row)
        c = word >> 8
        return (c if self.lay["nocalcspeed"] else c & 0x7F), (False, word & 0xFF)

    def effect(self, name: str, rate: tuple, **params) -> Call:
        """The call of template ``name`` stepping by ``rate``."""
        calc, value = rate
        return (
            self.call(name + "c", k=value, **params) if calc else self.call(name, s=value, **params)
        )

    def fx0(self, instr: int):
        """The tick binding command 0 latches: the instrument's vibrato, or none."""
        vib = self.vib_of(instr) if self.insvib else None
        return self.effect("vibi", vib[1], c=vib[0]) if vib else Expr("v")

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
            case T.PortamentoUp(y) | T.PortamentoDown(y):
                up = isinstance(cmd, T.PortamentoUp)
                return {WAVE_FREQ: Once(self.porta(up, y))}
            case T.TonePortamento(y):
                return {WAVE_FREQ: Once(self.toneporta(y))}
            case T.Vibrato(y):
                c, rate = self.depth_step(self.song.speed(y))
                return {WAVE_FREQ: Once(self.effect("vib", rate, c=c))}
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
                keys = self.command(cmd, 0)
                return keys | ({WAVE_FREQ: Once(Expr("v"))} if self.slotted else {})
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

    def wave(self, ptr: int, quiet: bool = False) -> str:
        """The ``ctrl`` program name of the wavetable from ``ptr``, ``quiet`` with the gate
        turned off in the tick it starts."""
        out, loop = _program(self.song.wavetable, ptr, self.wave_row) if ptr else ([], None)
        out, loop = self.late(out, loop)
        if quiet:
            out, loop = [Row({"gate": 0, "hold": 0}), *out], None if loop is None else loop + 1
        origin = invariants.Origin(("wave", ptr), ("quiet",) if quiet else ())
        return self.names.name("ctrl", "c", Program(out, loop), origin)

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
        return self.names.name("pw", "pw", Program(rows, loop), invariants.Origin(("pulse", ptr)))

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
        return self.names.name(
            "filter", "f", Program(rows, loop), invariants.Origin(("filter", ptr))
        )

    # ---- instruments ------------------------------------------------------------------------

    def restart(self, ins, n: int) -> str:
        """The name of the restart program of ``ins``: the fetch, then the note's tick 0."""
        lay = self.lay
        timer = lay["gateparam"] if lay["fixedparams"] else ins.gate_timer
        fetch = {"hold": timer} if timer != 1 else {}
        gated = (lay["hrinstr"] and lay["nohrinstr"]) or lay["legato"]
        if _drops_gate(lay, ins):
            fetch["gate"] = 0
            if lay["hrinstr"] and not (gated and not ins.hard_restart):
                fetch["ad"], fetch["sr"] = lay["adparam"], lay["srparam"]
        wave, gate = _first_wave(lay, ins)
        init = {"ad": ins.attack_decay, "sr": ins.sustain_release, "run": self.wave(0)}
        if wave is not None:
            init["ctrl"] = wave
        if gate is not None:
            init["gate"] = gate
        program = Program([Row(fetch), Row(init), Row()])
        return self.names.name("restart", "r", program, invariants.Origin(("ins", n)))

    def instrument(self, n: int) -> str:
        """The name of instrument ``n``."""
        ins = self.song.instrument(n)
        off = _first_wave(self.lay, ins)[1] is None and not _keeps_gate(self.lay, ins)
        fields = {"ctrl": self.wave(ins.wave_ptr, off)} if ins.wave_ptr or off else {}
        if ins.pulse_ptr:
            fields["pw"] = self.pulse(ins.pulse_ptr)
        if ins.filter_ptr:
            fields["filter"] = self.filter(ins.filter_ptr)
        if not self.sounds(ins.wave_ptr):
            fields[WAVE_FREQ] = Expr("v")
        fields.update(self.load(n))
        fields["restart"] = self.restart(ins, n)
        self.names.kinds.setdefault("ins", {})[f"i{n}"] = Instrument(fields)
        self.names.origins.setdefault("instruments", {})[f"i{n}"] = invariants.Origin(("ins", n))
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
                c, rate = self.depth_step(self.song.speed(y))
                return {TICK: self.effect("vib", rate, c=c)}
            case T.TonePortamento(y):
                return {TICK: self.toneporta(y)}
            case T.PortamentoUp(y) | T.PortamentoDown(y):
                return {TICK: self.porta(isinstance(cmd, T.PortamentoUp), y)}
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
                if t < 2 and (self.lay["nofunktempo"] or self.recall is None):
                    raise Refusal("funk tempo recall", "the table in force is not known")
                return {key: self.lengths(self.recall, t) if t < 2 else max(t, 3)}
            case T.FunkTempo(y):
                if self.lay["nofunktempo"]:
                    raise Refusal("funk tempo", "build without it")
                return {
                    "vtempo" if self.lay["noglobaltempo"] else "tempo": self.lengths(
                        self.funk_of(y), 0
                    )
                }
        raise Refusal("pattern command", type(cmd).__name__)

    def porta(self, up: bool, y: int):
        """The binding moving the frequency up or down by speedtable entry ``y``."""
        rate = self.rate(self.song.speed(y))
        if self.vib:
            return self.effect("portau" if up else "portad", rate)
        return _plus(rate[1] if up else -rate[1])

    def toneporta(self, y: int):
        """The binding moving the frequency toward the event note by speedtable entry ``y``."""
        rate = self.rate(self.song.speed(y))
        if self.vib:
            return self.effect("tporta", rate) if rate[0] or rate[1] else self.call("tsnap")
        if rate == (False, 0):
            return Expr("F(n)")
        step = rate[1]
        down = f"ite(u16(u16(v - F(n)) - {step}) >= 32768, F(n), u16(v - {step}))"
        up = f"ite(u16(u16(v - F(n)) + {step}) < 32768, F(n), u16(v + {step}))"
        return Expr(canonical(f"ite(v >= F(n), {down}, {up})"))

    def funk_of(self, y: int) -> tuple:
        """The two tempo bytes of speedtable entry ``y``, whatever the row is: the bytes the
        player loads into its funk table."""
        row = self.song.speed(y) if y <= len(self.song.speedtable) else None
        word = 0 if row is None else self.word(row)
        return word >> 8, word & 0xFF

    @staticmethod
    def cadence(pair: tuple, start: int):
        """The two row lengths in calls the funk table ``pair`` gives from its entry ``start``, None
        where a row never ends. The player reloads its counter at one less than the entry's byte
        when the counter has gone negative, which a counter of 128 or more already is, and moves to
        the other entry on each reload, so a byte above 129 or of 1 spends reloads before the row
        counts. The counter and the entry are the whole state of a row: it never ends exactly when
        a state comes twice before the counter reaches zero. The lengths are the document's when
        the entry is the same again after two rows."""
        t, out, states = start, [], []
        for _ in range(2):
            calls, counter, seen = 0, 0, set()
            while True:
                calls += 1
                counter = (counter - 1) & 255
                if counter == 0:
                    break
                if counter >= 128:
                    counter, t = (pair[t] - 1) & 255, t ^ 1
                if (counter, t) in seen:
                    return None
                seen.add((counter, t))
            out.append(calls)
            states.append(t)
        if states[1] != start:
            raise Refusal("funk tempo", "a funk table whose rows do not alternate from the start")
        return out[0], out[1]

    @property
    def forever(self) -> int:
        """The calls a row that never ends is stated to last: the calls of the check, which
        plays at least :data:`CHECK` frames and the song's span."""
        return CHECK * max(self.core.build.calls * self.core.entries, 1) + self.span()

    def lengths(self, pair: tuple, start: int) -> tuple:
        """The row lengths of :meth:`cadence`, both :attr:`forever` where a row never ends."""
        return self.cadence(pair, start) or (self.forever, self.forever)

    def recalled(self, funks: set):
        """The funk pair a tempo recall reads, None where it is not one pair: the start state's
        before any table is loaded; else the one table every load names, where a voice loads it
        in its first row, which is call 0, and no voice ahead of it recalls in a first row."""
        if not funks:
            return self.funk
        if len(funks) > 1:
            return None
        channels = self.song.subtunes[0].channels
        heads = []
        for c in self.core.voices:
            firsts = [
                self.song.patterns[e.num][0].command
                for e in channels[c].entries
                if isinstance(e, PlayPattern)
            ]
            heads.append(firsts)
        loads = [i for i, h in enumerate(heads) if h and isinstance(h[0], T.FunkTempo)]
        if not loads:
            return None
        recalls = (isinstance(c, T.SetTempo) and c.tempo < 2 for h in heads[: loads[0]] for c in h)
        return None if any(recalls) else next(iter(funks))

    def event(self, row, instr: int, gate: int = 1) -> Event:
        """The event of a pattern row played by instrument ``instr`` with the voice's gate
        ``gate``."""
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
        ins = self.song.instrument(instr)
        if noted and not tone and not gate and _keeps_gate(self.lay, ins):
            if "run" in cmds:
                raise Refusal("first waveform", "a note that keeps the gate off and sets a table")
            cmds["run"] = self.wave(ins.wave_ptr, True)
        return Event(atom, name, cmds)

    def events(self, rows, context: tuple) -> tuple:
        """``(events, context at the end)`` of pattern ``rows`` entered with ``context``, the
        instrument and gate."""
        events = []
        for row in rows:
            after = _context_after(self.lay, self.song, [row], context, self.track)
            events += [self.event(row, after[0], context[1])] + [Event("-")] * (row.length - 1)
            context = after
        return tuple(rests(events)), context

    def segments(self, rows, context: tuple, times: int) -> tuple:
        """``([(events, times)], context at the end, [context each starts in])`` of a pattern
        played ``times`` in a row from ``context``: its first pass, then the repeats, which start
        from the context the first pass leaves."""
        first, end = self.events(rows, context)
        if times > 1 and end != context:
            rest, after = self.events(rows, end)
            if rest != first:
                return [(first, 1), (rest, times - 1)], after, [context, end]
        return [(first, times)], end, [context]

    # ---- orderlists -------------------------------------------------------------------------

    @staticmethod
    def plays(order) -> tuple:
        """``(units, loop)``: the ``(pattern, transpose, times)`` each sequencer step starts, a
        repeat entry's pattern once with its count, and the index the orderlist loops to."""
        entries, pos, trans, count = order.entries, 0, 0, 0
        seen, out = {}, []
        while len(out) < 4096:
            if pos >= len(entries):
                pos = order.restart
            state = (pos, _carried(entries, pos, trans))
            if state in seen and pos < len(entries):
                return out, seen[state]
            if pos < len(entries):
                seen[state] = len(out)
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
                    out.append((num, trans, count + 1))
                    count = 0
        raise Refusal("orderlist", "does not loop")

    def overlap(self, units: list) -> None:
        """Refuse a note whose restart reads the note before it back past that note's own tick,
        which the player never fetches."""
        instr, since = 1, None
        for num, _, times in units:
            for row in [r for _ in range(times) for r in self.song.patterns[num]]:
                noted = isinstance(row.note, T.Note) and not isinstance(
                    row.command, T.TonePortamento
                )
                if (
                    noted
                    and since is not None
                    and self.song.instrument(instr).gate_timer + 1 > since
                ):
                    raise Refusal("restart overlap", "a restart reaches before the previous note")
                since = 0 if noted else since
                self.refetch(instr, row.instrument or instr)
                instr = row.instrument or instr
                since = None if since is None else since + self.rowmin * row.length

    def refetch(self, before: int, after: int) -> None:
        """Refuse a row change to an instrument of a shorter gate timer in a build that reads the
        timer from the live instrument: the row fetch has loaded the later instrument by the time
        the counter reaches its timer, and fetches again."""
        if self.lay["livegate"] and (
            self.song.instrument(after).gate_timer < self.song.instrument(before).gate_timer
        ):
            raise Refusal("build livegate", "a row fetches again at a shorter gate timer")

    def voice(self, order, gate: int) -> Voice:
        """A voice's orderlist, a repeat entry a group. The loop body is played a second time
        when entered with another instrument changes what it plays, and the loop re-enters that
        copy."""
        units, loop = self.plays(order)
        self.overlap(units + units[loop : loop + 1])
        walks, context = self.walk(units, (1, gate if self.track else 1))
        again, _ = self.walk(units[loop:], context)
        copy = [w[0] for w in again] != [w[0] for w in walks[loop:]]
        rolled = zip(units + units[loop:] * copy, walks + again * copy)
        items, starts = self.items(list(rolled))
        return Voice(tuple(items), starts[len(units) if copy else loop])

    def items(self, rolled: list) -> tuple:
        """``(play entries, index each unit starts at)`` of ``(unit, (segments, contexts))``."""
        items, starts = [Play("p0")], []
        for (num, trans, _), (segs, entered) in rolled:
            starts.append(len(items))
            move = Signed(trans) if trans else None
            for (events, times), entry in zip(segs, entered):
                phrase = Play(self.names.name("phrase", "p", events), move)
                self.entered.setdefault(events, (num, entry))
                items.append(Group((phrase,), times) if times > 1 else phrase)
        return items, starts

    def walk(self, units: list, context: tuple) -> tuple:
        """``([(segments, contexts entered in)] per unit, context at the end)`` of ``units``
        played from ``context``."""
        out = []
        for num, _, times in units:
            segs, context, entered = self.segments(self.song.patterns[num], context, times)
            out.append((segs, entered))
        return out, context

    def span(self) -> int:
        """The calls of the player after which every voice's orderlist has looped once, at the
        slowest row length any tempo gives."""
        rows = 0
        for c in self.core.voices:
            units, _ = self.plays(self.song.subtunes[0].channels[c])
            steps = sum(
                r.length for n, _, t in units for r in self.song.patterns[n] for _ in range(t)
            )
            rows = max(rows, steps)
        slowest = max([self.rowlen, *self.tempo_rows])
        return rows * slowest

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
            return self.masked(state.registers)
        regs = [0] * 25
        regs[24] = 15
        for reg, value in (*self.core.build.init_regs, *(state.init if state else ())):
            regs[reg] = value
        return self.masked(regs)

    @staticmethod
    def masked(regs) -> list:
        """``regs`` without the bits the SID lacks: the pulse width's high nibbles' upper bits
        and the cutoff's."""
        out = list(regs)
        for reg in (3, 10, 17):
            out[reg] &= 15
        out[21] &= 7
        return out

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
        booted = any("pw" in self.opening(v) for v in range(voices))
        slots = {"pw": (Slot("pulse", hold=events),) + ((Slot("boot"),) if booted else ())}
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
            order=ORDER,
            state=self.state(),
            regs=tuple(regs) if any(regs) else None,
            lag=self.lag or None,
            global_cells=glob or None,
            calls=(
                (core.build.calls * core.entries, None)
                if core.build.calls * core.entries > 1
                else None
            ),
        )

    @property
    def started(self) -> dict:
        """``{voice: ctrl}`` of the voices whose control register the song start changes in a
        full-ghosting build: ``initchn`` loads it as ``wave & gate`` from the zeroed wave, so the
        frame after the start differs from the ghost the init left."""
        if not self.lay["fullghost"]:
            return {}
        regs, out = self.start(), {}
        for v, c in enumerate(self.core.voices):
            ctrl = _started(self.core, "wave", c) & _var(self.core, "gate", c)
            if ctrl != regs[7 * v + 4]:
                out[v] = ctrl
        return out

    @property
    def lead(self) -> int:
        """The GoatTracker calls before the document's first tick: its first filter step
        writes the start state."""
        if self.started:
            return int(any(self.first(v) for v in range(len(self.core.voices))))
        return (
            1
            if self.lay["fullghost"] or any(self.first(v) for v in range(len(self.core.voices)))
            else 0
        )

    @property
    def lag(self) -> int:
        """The calls the registers lag the cells: a full ghost is output a call after it is
        written, and the document begins with the song-start call where that changes it."""
        if self.started:
            return 1
        return self.lead + int(self.lay["fullghost"])

    def seed(self, v: int) -> dict:
        """The once binding loading the channel variables the song start leaves (``vibtime``,
        ``vibdelay``, ``lastnote``) where the vibrato command 0 runs from the start with them:
        the document holds the first instrument's delay and zeroes."""
        fx = self.fx0(1)
        if not isinstance(fx, Call):
            return {}
        c, names = self.core.voices[v], asm.symbols(ROUTINES[fx.name])
        vib = self.vib_of(1)
        have = {"t": 0, "d": vib[2] if vib else 0, "ln": 0}
        want = {
            "t": _started(self.core, "vibtime", c) if not self.lay["novib"] else 0,
            "d": _started(self.core, "vibdelay", c),
            "ln": _started(self.core, "lastnote", c) if not self.lay["nocalcspeed"] else 0,
        }
        lines = [f"lda #{v}\nsta {k}\n" for k, v in want.items() if k in names and v != have[k]]
        return {WAVE_FREQ: Once(Code.of("".join(lines) + "rts"))} if lines else {}

    def origins(self) -> dict:
        """Each named object's :class:`~unitracker.lift.invariants.Origin`: the phrases' native
        pattern, with the context only where a pattern lifts to more than one phrase."""
        out = {k: dict(v) for k, v in self.names.origins.items()}
        phrases = self.names.kinds.get("phrase", {})
        natives = {}
        for name, events in phrases.items():
            natives.setdefault(self.entered[events][0], []).append(name)
        out["phrases"] = {"p0": invariants.Origin(("intro",))}
        for num, names in natives.items():
            for name in names:
                context = self.entered[phrases[name]][1] if len(names) > 1 else ()
                out["phrases"][name] = invariants.Origin(("pattern", num), context)
        return out

    def document(self) -> Document:
        """The document of the subtune."""
        channels = self.song.subtunes[0].channels
        gates = _gates(self.core)
        voices = [self.voice(channels[c], g) for c, g in zip(self.core.voices, gates)]
        self.instrument(1)
        started = self.started
        firsts = [self.first(v) for v in range(len(voices))]
        init = [
            Event(
                "-",
                "i1",
                ({"tempo": PRE - self.lead} if v == 0 else {})
                | ({"ctrl": started[v]} if v in started else {})
                | ({} if started else firsts[v])
                | self.seed(v),
            )
            for v in range(len(voices))
        ]
        intro = {"tempo": self.rowlen} | ({TICK: self.fx0(1)} if self.insvib else {})
        rows = (Event("-"), Event("-", commands=intro))
        if started and firsts[0]:
            rows = (rows[0], Event("-", commands={"tempo": 1} | firsts[0]), rows[1])
        phrases = [("p0", rows)]
        phrases += self.names.section("phrase")
        defs = [Section(k, tuple(self.names.section(k))) for k in ("restart", "ins", "ctrl", "pw")]
        defs += [Section("filter", tuple(self.names.section("filter")))]
        defs = [d for d in defs if d.records] + [Section("phrase", tuple(phrases))]
        head = self.header(len(voices))
        doc = Document(head, (), (), defs, tuple(init), voices, None)
        order = [n for n in dict.fromkeys(_calls(doc)) if n in self.calls]
        return Document(head, (), self.mods(order), defs, tuple(init), voices, None)


def lifted(core: Core) -> tuple:
    """``(document text, origins)`` of ``core``: the document printed, and the native object each
    of its named objects transliterates (:func:`unitracker.lift.invariants.provenance`)."""
    lifter = Lift(core)
    return printed(lifter.document()), lifter.origins()


def document_text(core: Core) -> str:
    """The document of ``core``, printed."""
    return lifted(core)[0]


def check_frames(core: Core, frames: int = CHECK) -> int:
    """The frames a check of ``core`` plays: at least ``frames``, and until every voice's
    orderlist has looped once at the slowest tempo."""
    lifter = Lift(core)
    return max(frames, -(-lifter.span() // max(core.build.calls * core.entries, 1)))


def checked_text(core: Core) -> tuple:
    """``(text, objects per kind)`` of ``core``'s document, a ModelError where it holds an object
    without its native one or more song entries than the orderlists have."""
    text, origins = lifted(core)
    objects = invariants.provenance(text, origins)
    invariants.check(text, native_counts(core))
    return text, objects


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


def reference(raw: bytes, subtune: int = 0, frames: int = CHECK, notes: bool = True):
    """The register grid of the spec player over ``frames`` frames."""
    fmt = find_format(raw)
    model = decompile_sid(raw, subtune)
    return np.asarray(
        fmt.spec_player(model, notes=notes or None).render_grid(frames), dtype=np.uint8
    )


def compare(core: Core, frames: int, grid=None) -> tuple:
    """``(cells, text)``: the cells where the document of ``core`` differs from ``grid`` (the
    reference over ``frames`` frames; for a core of a song, its player's)."""
    text, _ = checked_text(core)
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


def verify(raw: bytes, subtune: int = 0, frames: int = CHECK) -> tuple:
    """``(cells, text)`` of a SID: the cells where its document differs from the reference over
    :func:`check_frames` frames, ``frames`` at least."""
    core = core_of_sid(raw, subtune)
    text, _ = checked_text(core)
    frames = check_frames(core, frames)
    ours = np.asarray(play(text, frames), dtype=np.uint8)
    theirs = reference(raw, subtune, len(ours))
    return gridcmp.cell_diff(ours, theirs), text


def lift(raw: bytes, subtune: int = 0, frames: int | None = None) -> str:
    """The document of a SID's subtune, checked against the reference over the whole song
    (:func:`check_frames`, ``frames`` at least, default :data:`CHECK`); a LiftError otherwise."""
    frames = frames or CHECK
    return checked(verify(raw, subtune, frames), frames)


def main(argv=None) -> int:
    """Lift a SID, or verify SIDs against the spec player."""
    return cli.main(argv, __doc__.split("\n", 1)[0], lift, verify)


if __name__ == "__main__":
    sys.exit(main())
