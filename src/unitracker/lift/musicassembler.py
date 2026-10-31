"""A Music Assembler tune as a format 2.5 document, from pymusicassembler's song model and the
layout its playspec binding resolves; ``verify`` plays the document against the spec player with
the standard note table (docs/lift/musicassembler.md)."""

import sys
from collections import Counter
from dataclasses import astuple, dataclass, field, replace

import numpy as np
from pymusicassembler import constants as MA
from pymusicassembler import playspec
from pymusicassembler.formats import MUSIC_ASSEMBLER, Tune
from pymusicassembler.model import ArpeggioRow
from pymusicassembler.model import Instrument as ModelInstrument
from pymusicassembler.model import InstrumentEvent, NoteEvent
from pysidtracker import NTSC_CLOCK_HZ, PAL_CLOCK_HZ, find_format, parse_sid_header
from pysidtracker.playspec.player import SpecPlayer
from unitracker_grammar import code as asm
from unitracker_grammar.document import (
    NONE,
    Call,
    Code,
    Document,
    Event,
    Expr,
    Group,
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
    printed,
    signature,
)
from unitracker_grammar.expr import canonical

from unitracker.lift import cli, invariants
from unitracker.lift.invariants import Origin
from unitracker.lift.macode import ROUTINES, STATE
from unitracker.lift.macore import Core, core_of_sid, core_of_song, refuse
from unitracker.lift import shared
from unitracker.lift.shared import Divergence, LiftError, Refusal, calls_of, cells_of
from unitracker.lift.shared import lifted_text, meta_text, note_name

__all__ = ["Core", "Divergence", "LiftError", "Refusal", "core_of_sid", "core_of_song", "lift"]
CYCLES = {PAL_CLOCK_HZ: 19656, NTSC_CLOCK_HZ: 17095}
CHECK = 4000
VOICES = MA.VOICES
SID = 0xD400
JMP_ABS = 0x4C
MAX_ITEMS = 512
PARSES = 128
MAX_AGE = 1 << 12
HELD = ("event",)
KINDS = {"ins": "instruments", "ctrl": "ctrl", "phrase": "phrases"}
summary = cli.summary


@dataclass(frozen=True)
class Walk:
    """An instrument program as the player reads it: its rows in the order the cursor visits
    them, and the row it continues at after the last (None where it stops)."""

    rows: tuple
    loop: int | None


class Names(shared.Names):
    """Definitions by kind, numbered in first-use order, each with the native object it stands
    for and the context it was first reached in."""

    def name(
        self, kind: str, prefix: str, value, origin: tuple, context: tuple = ()
    ) -> str:  # pylint: disable=arguments-renamed
        """The name of ``value`` among the ``kind`` records, adding it as the lift of the native
        object ``origin`` reached in ``context``."""
        return super().name(kind, prefix, value, (origin, context))

    def by_kind(self) -> dict:
        """``{kind: {name: Origin}}``: the context is named only for a native object that lifts
        to more than one record."""
        out = {}
        for kind, seen in self.origins.items():
            copies = Counter(native for native, _ in seen.values())
            out[KINDS[kind]] = {
                name: Origin(native, context if copies[native] > 1 else ())
                for name, (native, context) in seen.items()
            }
        return out


@dataclass(frozen=True)
class Ref:
    """A phrase's value, equal to another's of the same value, with the native pattern it
    lifts and the context the walk reached it in."""

    value: tuple
    native: tuple = field(compare=False)
    context: tuple = field(compare=False)


@dataclass(frozen=True)
class Ctx:
    """A voice's state at a row: its instrument, the instrument its last trigger started (None
    before one), whether its vibrato slot is bound, the vibrato steps its cells hold, whether its
    control mask clears the gate, and whether it is still in the image init left."""

    ins: int
    trig: int | None
    vib: bool | None
    steps: tuple
    rest: bool
    boot: bool


def _expr(text: str) -> Expr:
    return Expr(canonical(text))


def _const(value: int) -> Expr:
    return Expr(f"{value} + 0")


class Lift:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """One core's document."""

    def __init__(self, core: Core):
        refuse(core)
        self.core, self.found, self.song = core, core.found, core.song
        self.names, self.cache = Names(), {}
        self.pokes, self.setters, self.sweepers = set(), set(), set()
        self.rowlen = core.cell("tempo_reload") + 1
        self.steady = all(
            e.control_byte() == self.rowlen - 1
            for o in self.song.orderlists
            for e in o.entries
            if self.found["order_tempo"] and e.pattern_id == MA.ORDER_TEMPO
        )
        self.skip = bool(self.found["pitch_skip"])
        self.always = bool(self.found["pulse_always"])
        self.first, self.walks, self.boots = {}, {}, {}
        self.ctl0 = [core.cell("note_ctl", x) for x in range(VOICES)]
        self.vibrates = any(self.ctl0[x] & MA.CTL_VIBARP for x in range(VOICES)) or any(
            isinstance(e, NoteEvent) and e.vibrato
            for pattern in self.song.patterns
            if pattern is not None
            for e in pattern.events()
        )

    # ---- instruments and programs -----------------------------------------------------------

    def record(self, k: int) -> ModelInstrument:
        """Instrument ``k``'s record: the bytes the player reads for it."""
        if k >= len(self.song.instruments):
            raise Refusal("instrument", f"{k} is not a record of the song")
        return self.song.instruments[k]

    @staticmethod
    def call(name: str, **params) -> Call:
        """The call of template ``name`` with ``params`` in its signature's order."""
        return Call(name, tuple(params[k] for k in asm.params(ROUTINES[name])))

    def steps(self, d: int, kind: str) -> int | None:
        """The ``*k`` of a row of ``kind`` and duration ``d``: it lasts ``d + 1`` clock rows, the
        build's duration cell that row stores to counting them down only where it is the
        voice's counter; None for one."""
        cell = {"rest": "dur_short", "hold": "dur_long", "note": "dur_note"}[kind]
        k = (d if self.found[cell] == self.found["dur"] else 0) + 1
        return k if k > 1 else None

    def program_of(self, flags: int):
        """The program an instrument with ``flags`` runs from its trigger, None where it has
        none."""
        return self.walk(flags & 0x0F) if flags & 0x0F else None

    def walk(self, slot: int) -> Walk:
        """The rows the player reads through program slot ``slot``'s pointer, whatever bytes
        they are: the cursor ``y`` reads a row at ``y``, ``y + 1``, ``y + 2`` and a marker at
        ``y + 3`` (a byte wrap included), which stops the program (``$FE``), restarts it
        (``$FF``), jumps (``$FD`` where the build binds it) or moves on to the next row. The
        slot's program is the model's; the memory is read where the cursor walks past it (a slot
        with no program, a jump to a row after its marker), bytes the model does not carry."""
        if slot in self.walks:
            return self.walks[slot]
        found, state = self.found, self.core.state
        base = state.cell("inner_lo", slot) | state.cell("inner_hi", slot) << 8
        program = self.song.inner_programs[slot]
        carried = program.to_bytes() if program is not None else b""

        def byte(y: int) -> int:
            y &= 255
            return carried[y] if y < len(carried) else state.window(base + y, 1)[0]

        y, rows, seen, loop = 0, [], {}, None
        while y not in seen:
            seen[y] = len(rows)
            rows.append(ArpeggioRow(byte(y), byte(y + 1), byte(y + 2)))
            marker = byte(y + 3)
            if marker == MA.PROGRAM_STOP:
                break
            if marker == MA.PROGRAM_JUMP and found["program_jump"]:
                if found["program_jump"] != found["instr_cursor"]:
                    raise Refusal("program jump", "bound apart from the cursor")
                target = byte(y + 4)
                y = 0 if target == 0 or target >= (y + 3) & 255 else (target * 3) & 255
            else:
                y = 0 if marker == MA.PROGRAM_RESTART else (y + 3) & 255
        else:
            loop = seen[y]
        self.walks[slot] = Walk(tuple(rows), loop)
        return self.walks[slot]

    def arpeggio(self, flags: int) -> str:
        """``arp`` where an instrument's frequency sweep runs from its trigger, ``after`` where
        it waits for a program that stops, else ''."""
        if not flags & 0x10:
            return ""
        prog = self.program_of(flags)
        if prog is None:
            return "arp"
        return "after" if prog.loop is None else ""

    def arp_args(self, k: int) -> dict:
        """The arpeggio template arguments of instrument ``k``: the phases that ascend, its
        step, its steps a phase and its delay."""
        rec = self.record(k)
        mask = sum(1 << j for j in range(4) if self.core.cell("arp", j))
        return {"m": mask, "s": rec.arp_step, "k": rec.arp_speed & 0x0F, "w": rec.arp_speed >> 3}

    def pitch(self, note: int) -> Expr:
        """The frequency a program row of pitch ``note`` binds."""
        if self.skip and note >= 0x80:
            return _expr(f"F({note & 0x7F})")
        return _expr("F(n)" if not note else f"F((n + {note}) & 127)")

    def program_row(self, row, x: int) -> Row:
        """The program row of an inner program row on voice ``x``."""
        keys = [("ctrl", row.wave), ("freq", self.pitch(row.note))]
        if row.filter:
            self.pokes.add(x)
            keys += [
                (f"q{x}.cut", Once(self.call("poke", u=row.filter))),
            ]
        return Row(keys)

    def stop(self, k: int, prog) -> Call:
        """The binding of the frequency sweep that follows the last row of ``prog``."""
        note = prog.rows[-1].note
        fixed = self.skip and note >= 0x80
        return self.call(
            "arpz", m=self.arp_args(k)["m"], p=note & (0x7F if fixed else 0xFF), b=int(fixed)
        )

    def trigger(self, k: int, x: int, mask: bool = True) -> str:
        """The name of the control program a note of instrument ``k`` on voice ``x`` runs: the
        trigger's row, then the instrument's inner program; with ``mask`` false the trigger
        keeps the rest's gate mask, which binds from the inner program's first row on."""
        rec = self.record(k)
        keys = [("ad", rec.attack_decay), ("sr", rec.sustain_release)]
        keys += [("m.ctrl", NONE)] * mask + [("ctrl", rec.waveform)]
        rows, loop = [Row(keys)], None
        prog = self.program_of(rec.porta)
        if prog is not None:
            rows += [self.program_row(r, x) for r in prog.rows]
            if prog.loop is not None:
                loop = 1 + prog.loop
            elif self.arpeggio(rec.porta) == "after":
                rows.append(Row({"freq": self.stop(k, prog)}))
        if not mask:
            masked = ("m.ctrl", _expr("v & 254"))
            if len(rows) > 1:
                rows[1] = Row(rows[1].keys + (masked,))
            else:
                rows.append(Row([masked]))
        return self.names.name("ctrl", "c", Program(rows, loop), ("instrument", k), (x, mask))

    def pulse(self, k: int):
        """The pulse binding a note of instrument ``k`` starts."""
        rec = self.record(k)
        init = (rec.pulse_init << 8 | rec.pulse_init) & 0xFFF
        if self.always or rec.porta & 0x40:
            return _expr(f"ite(phi == 0, {init}, v + {257 * rec.pulse_step})")
        if rec.porta & 0x20:
            return self.call("pong", p=rec.pulse_init, s=rec.pulse_step, k=rec.pulse_step & 0x0F)
        return init

    def instrument(self, k: int, x: int) -> str:
        """The name of instrument ``k`` as voice ``x`` starts it."""
        rec = self.record(k)
        args = self.arp_args(k)
        freq = (
            self.call("arp", w=args["w"], m=args["m"])
            if self.arpeggio(rec.porta)
            else _expr("F(n)")
        )
        fields = {"ctrl": self.trigger(k, x), "pw": self.pulse(k), "freq": freq}
        if self.arpeggio(rec.porta):
            fields["c.freq"] = Once(self.configured(k, False))
        return self.names.name("ins", "i", Instrument(fields), ("instrument", k), (x,))

    # ---- rows -------------------------------------------------------------------------------

    def rebinds(self, ctx: Ctx, new: int) -> dict:
        """The commands that bind the pulse sweep of the running instrument at the step of
        instrument ``new``, selected without a trigger."""
        old, rec = self.record(ctx.ins), self.record(new)
        if ctx.trig is None or rec.pulse_step == old.pulse_step:
            return {}
        flags = self.record(ctx.trig).porta
        if self.always or flags & 0x40:
            return {"pw": _expr(f"v + {257 * rec.pulse_step}")}
        if flags & 0x20:
            return {"pw": self.call("pongs", s=rec.pulse_step, k=rec.pulse_step & 0x0F)}
        return {}

    def configured(self, ins: int, vib: bool) -> Call:
        """The call that sets the arpeggio's step, steps a phase and vibrato flag as instrument
        ``ins`` has them, with the vibrato flag ``vib``."""
        rec = self.record(ins)
        return self.call("arpc", s=rec.arp_step, k=rec.arp_speed & 15, b=int(vib))

    def stepped(self, ins: int, trig) -> dict:
        """The command that sets the arpeggio's step and steps a phase as instrument ``ins`` has
        them, where a row selects it under the instrument ``trig`` last started."""
        if trig is None or not self.arpeggio(self.record(trig).porta):
            return {}
        rec = self.record(ins)
        return {"c.freq": Once(self.call("arpk", s=rec.arp_step, k=rec.arp_speed & 15))}

    @staticmethod
    def vibrato(steps: tuple) -> Expr:
        """The vibrato slot's binding for step bytes ``steps``: the word added a call, on every
        call or, with an odd low step, on every other."""
        word = steps[1] << 8 | steps[0]
        if steps[0] & 1:
            return _expr(f"ite(phi % 2 == 0, F(n) + (phi // 2 + 1) * {word}, v)")
        return _expr(f"F(n) + (phi + 1) * {word}")

    def running_vibrato(self, x: int) -> Expr | None:
        """The vibrato slot's binding for voice ``x``'s vibrato running from the image: the
        output cells' word stepped by the step bytes init left, on every call or, with an odd low
        step, on the calls where the toggle cell init left comes out zero (None where it never
        does, which leaves the word)."""
        core = self.core
        steps = (core.cell("vib_lo_step", x), core.cell("vib_hi_step", x))
        word, start = steps[1] << 8 | steps[0], core.cell("vib_out_hi", x) << 8
        start |= core.cell("vib_out_lo", x)
        count = f"{start} + (phi // 2 + 1) * {word}"
        if not steps[0] & 1:
            return _expr(f"{start} + (phi + 1) * {word}")
        toggle = core.cell("vib_toggle", x)
        if toggle not in (0, 0xFF):
            return None
        return _expr(f"ite(phi % 2 == {int(toggle == 0)}, {count}, v)")

    def sweep_commands(self, ev: NoteEvent, x: int) -> dict:
        """The commands of a note's filter command on voice ``x``."""
        cutoff, length = ev.args
        self.setters.add(x)
        self.sweepers.add(x)
        step = (((cutoff & 0x0F) << 1) - 0x10) & 0xFF
        preset = self.core.cell("res_presets", x) if length else 0xF0
        return {
            "res": preset >> 4,
            "route": preset & 0x0F,
            f"s{x}.cut": Once(self.call("fset", u=cutoff, c=length, s=step, o=x)),
            f"c{x}.cut": self.call("fsweep", t=x),
        }

    def image(self, x: int) -> dict:
        """The commands that hold the cells init left on voice ``x`` where its first row plays
        no note."""
        cell = self.core.cell
        word = cell("freq_hi_w", x) << 8 | cell("freq_lo_w", x)
        pw = (cell("pw_hi", x) & 0x0F) << 8 | cell("pw_lo", x)
        return {"freq": _const(word), "pw": pw}

    def start(self, x: int) -> Ctx:
        """The state of voice ``x`` before its first row."""
        steps = (self.core.cell("vib_lo_step", x), self.core.cell("vib_hi_step", x))
        return Ctx(self.core.cell("instr_x8", x) >> 3, None, None, steps, False, True)

    def note(self, ev: NoteEvent, sel, ctx: Ctx, x: int) -> tuple:
        """``(event, ctx)`` of a note row under instrument select ``sel``."""
        ins = ctx.ins if sel is None else sel
        cmds, length = {}, self.steps(ev.duration, "note")
        steps = ev.args if ev.vibrato and not ev.filter else ctx.steps
        if ev.filter:
            if ev.tied:
                raise Refusal("legato filter command")
            cmds |= self.sweep_commands(ev, x)
        if ev.vibrato:
            cmds["b.freq"] = self.vibrato(steps)
        elif ctx.vib is not False and self.vibrates:
            cmds["b.freq"] = NONE
        atom = note_name(ev.note)
        if not ev.tied:
            if ev.vibrato and self.arpeggio(self.record(ins).porta):
                cmds["c.freq"] = Once(self.configured(ins, True))
            return Event(atom, self.instrument(ins, x), cmds, steps=length), Ctx(
                ins, ins, ev.vibrato, steps, False, False
            )
        cmds["legato"] = True
        cmds["m.ctrl"] = NONE
        if ctx.boot:
            self.boots[x] = {"pw": self.image(x)["pw"], "m.ctrl": self.mask(x)}
        cmds["freq"] = _expr("F(n)")
        if ctx.trig is not None:
            cmds |= self.rebinds(ctx, ins)
            if self.arpeggio(self.record(ctx.trig).porta):
                cmds["c.freq"] = Once(self.configured(ins, ev.vibrato))
                cmds["freq"] = self.call("arpl", m=self.arp_args(ins)["m"])
        nxt = replace(ctx, ins=ins, vib=ev.vibrato, steps=steps, rest=False, boot=False)
        name = self.instrument(ins, x) if sel is not None else None
        return Event(atom, name, cmds, steps=length), nxt

    def mask(self, x: int) -> Expr:
        """The control mask voice ``x`` holds after init."""
        return _const(self.core.cell("ctrl", x))

    def booted(self, x: int, mask: bool = True) -> None:
        """Keep the commands that hold the cells init left on voice ``x`` for its init event,
        where its first row plays no note: its frequency and pulse width, the vibrato it runs
        and, under ``mask``, its control mask."""
        cmds = self.image(x)
        if self.ctl0[x] & MA.CTL_VIBARP and (running := self.running_vibrato(x)) is not None:
            cmds["b.freq"] = running
        if mask:
            cmds["m.ctrl"] = self.mask(x)
        self.boots[x] = cmds

    def primed(self, ins: int, x: int, rest: bool) -> dict:
        """The commands of a first row that plays no note on a voice whose note control init
        left without the trigger latch: the first output call triggers instrument ``ins`` at the
        note index and frequency init left."""
        flags = self.record(ins).porta
        if self.arpeggio(flags) or self.always:
            raise Refusal("init", "a trigger of the image with a sweep")
        return {
            "legato": True,
            "run": self.trigger(ins, x, not rest),
            "pw": self.pulse(ins),
        }

    def other(self, ev, sel, ctx: Ctx, x: int) -> tuple:
        """``(event, ctx)`` of a rest or a hold row under instrument select ``sel``."""
        rest = not ev.long
        ins = ctx.ins if sel is None else sel
        cmds, length = {}, self.steps(ev.duration, "rest" if rest else "hold")
        atom, trig = "-", ctx.trig
        primed = ctx.boot and not self.ctl0[x] & MA.CTL_TRIGGERED
        if ctx.boot:
            if self.always:
                raise Refusal("init", "a pulse sweep running from the image")
            self.booted(x, not (primed and rest))
        if primed:
            atom, trig = note_name(self.core.cell("note_index", x)), ins
            cmds |= self.primed(ins, x, rest)
        elif rest:
            cmds["m.ctrl"] = _expr("v & 254")
        if sel is not None and not ctx.boot:
            cmds |= self.rebinds(ctx, ins) | self.stepped(ins, ctx.trig)
        name = self.instrument(ins, x) if sel is not None or atom != "-" else None
        return Event(atom, name, cmds, steps=length), replace(
            ctx, ins=ins, trig=trig, rest=rest or ctx.rest, boot=False
        )

    def gate_start(self, age: int, rows: int) -> int:
        """The calls of a silence before the gate stays off under a stopping program of ``rows``
        rows that its trigger started ``age`` calls (parse calls aside) ago: each parse call
        clears the gate, and the program's next row call sets it again while a row is left."""
        passed, spent = 0, age
        while True:
            for row in range(PARSES):
                if spent > rows:
                    return passed
                span = self.rowlen - 1 + (PARSES + 1) * self.rowlen * (row == PARSES - 1)
                passed, spent = passed + span, spent + span

    def silence(self, ctx: Ctx, x: int, steps: int | None, age: int) -> Event:
        """The event of ``steps`` clock rows of a voice on the silence pattern, whose trigger
        ran ``age`` calls ago: the gate off at its row call."""
        cmds = {}
        prog = self.program_of(self.record(ctx.trig).porta) if ctx.trig is not None else None
        if ctx.boot:
            self.booted(x)
        if ctx.boot or ctx.rest:
            cmds["m.ctrl"] = _expr("v & 254")
        elif prog is None:
            cmds["m.ctrl"] = Once(_expr("v & 254"))
        elif prog.loop is None:
            if not self.steady:
                raise Refusal("silence", "a stopping program's calls across a tempo change")
            start = self.gate_start(age, len(prog.rows))
            cmds["m.ctrl"] = (
                Once(_expr("v & 254")) if not start else _expr(f"ite(phi >= {start}, v & 254, v)")
            )
        return Event("-", None, cmds, steps=steps)

    def events(self, pid: int) -> list:
        """The rows of pattern ``pid``."""
        if pid >= len(self.song.patterns) or self.song.patterns[pid] is None:
            raise Refusal("pattern", f"{pid} is not a pattern")
        return self.song.patterns[pid].events()

    def rows(self, pid: int) -> list:
        """The rows of pattern ``pid`` as ``(select, event)`` pairs."""
        out, sel = [], None
        for ev in self.events(pid):
            if isinstance(ev, InstrumentEvent):
                sel = ev.instrument
            else:
                out.append((sel, ev))
                sel = None
        return out

    def tailed(self, tail: tuple, event: Event, row: tuple) -> tuple:
        """``tail`` of a phrase after ``event``, the row ``ev`` taking ``before`` to ``after``:
        a trigger restarts the count of calls, any other row adds its own."""
        ev, before, after = row
        calls = (event.steps or 1) * self.rowlen - 1
        if (isinstance(ev, NoteEvent) and not ev.tied) or (
            before.trig is None and after.trig is not None
        ):
            return True, calls
        return tail[0], tail[1] + calls

    def phrase(self, pid: int, ctx: Ctx, x: int, tempo: int | None = None) -> tuple:
        """``(ref, ctx at the end, tail)`` of the phrase of pattern ``pid`` entered in ``ctx``,
        whose last row sets the tempo ``tempo`` (a ``$FC`` that follows its entry) if not None;
        ``tail`` is ``(triggered, calls)``: whether a row triggered an instrument and the calls
        (parse calls aside) from the last trigger, or all of them if none did."""
        key = (pid, ctx, x, tempo)
        if key not in self.cache:
            events, here, tail = self.built(pid, ctx, x, tempo)
            ref = Ref(tuple(events), ("pattern", pid), (x, *astuple(ctx), tempo))
            self.cache[key] = (ref, replace(here, vib=None), tail)
        return self.cache[key]

    def built(self, pid: int, ctx: Ctx, x: int, tempo: int | None) -> tuple:
        """``(events, ctx at the end, tail)`` of pattern ``pid`` entered in ``ctx``."""
        events, here, tail = [], ctx, (False, 0)
        rows = self.rows(pid)
        for sel, ev in rows:
            if not events and ctx.boot:
                self.first[x] = isinstance(ev, NoteEvent) and not ev.tied
            step = self.note if isinstance(ev, NoteEvent) else self.other
            event, after = step(ev, sel, here, x)
            tail = self.tailed(tail, event, (ev, here, after))
            if tempo is not None and len(events) == len(rows) - 1:
                event = replace(event, commands=(*event.commands, ("tempo", tempo + 1)))
            events.append(event)
            here = after
        return events, here, tail

    # ---- orderlists -------------------------------------------------------------------------

    def settle(self, order, real: list, at: int) -> tuple:
        """``(entry index, tempo)``: the entry the orderlist step reaches from ``at``, tempo
        commands passed (the last one's tempo, None without), None for the entry where it parks
        on the silence pattern. The restart after the end marker reads its entry as is."""
        tempo = None
        for _ in range(len(real) + 3):
            if at < len(real):
                if self.found["order_tempo"] and real[at].pattern_id == MA.ORDER_TEMPO:
                    tempo, at = real[at].control_byte(), at + 1
                    continue
                return at, tempo
            if order.terminator == MA.SILENCE_PATTERN:
                return None, tempo
            if order.terminator == MA.ORDER_JUMP:
                at = order.entries[-1].control_byte()
                continue
            at = self.found["order_restart"] // 2
            if at >= len(real):
                break
            if self.found["order_tempo"] and real[at].pattern_id == MA.ORDER_TEMPO:
                raise Refusal("orderlist", "a restart onto a tempo command")
            return at, tempo
        raise Refusal("orderlist", "does not reach a pattern")

    def voice(self, x: int) -> Voice:
        """Voice ``x``'s orderlist: its entries walked from the state init left until the walk
        returns to an entry in the state it was in, the visits then reduced to the shortest
        sequence that plays the same phrases."""
        order = self.song.orderlists[x]
        real = order.entries[: len(order.entries) - (order.terminator != MA.ORDER_END)]
        ctx, visits, seen, age, loop = self.start(x), [], {}, 0, None
        at, _ = self.settle(order, real, 0)
        if at and self.core.cell("pattern_id", x) != real[at].pattern_id:
            raise Refusal("orderlist", "a leading tempo command the init does not pass")
        while len(visits) < MAX_ITEMS:
            if at is None:
                break
            if (at, ctx) in seen:
                loop = seen[at, ctx]
                break
            seen[at, ctx] = len(visits)
            following, tempo = self.settle(order, real, at + 1)
            visit, ctx, age = self.visit(real[at], ctx, x, age, tempo)
            visits.append(visit)
            at = following
        else:
            raise Refusal("orderlist", "does not settle")
        if loop is not None:
            loop, visits = self.reduced(visits, loop)
        items = [self.resolved(item) for visit in visits for item in visit]
        if loop is None:
            return self.parked(items, ctx, x, age)
        return Voice(tuple(items), sum(len(v) for v in visits[:loop]))

    def visit(self, entry, ctx: Ctx, x: int, age: int, tempo: int | None) -> tuple:
        """``(items, ctx, age)`` after the voice ``x`` plays ``entry`` from ``ctx`` with its
        trigger ``age`` calls old, a ``$FC`` of ``tempo`` (if not None) following the entry."""
        plays = []
        for run in range(entry.repeat + 1):
            last = tempo if run == entry.repeat and not self.steady else None
            ref, ctx, (triggered, calls) = self.phrase(entry.pattern_id, ctx, x, last)
            age = min(calls if triggered else age + calls, MAX_AGE)
            plays.append(ref)
        return tuple(self.compressed(plays, entry.transpose)), ctx, age

    @staticmethod
    def reduced(visits: list, loop: int) -> tuple:
        """``(loop, visits)`` of the shortest sequence of visits that plays what ``visits`` do,
        the walk returning to visit ``loop``: the smallest period of the cycle, then the
        earliest start of the cycle."""
        cycle = visits[loop:]
        size = len(cycle)
        period = next(
            d
            for d in range(1, size + 1)
            if size % d == 0 and all(cycle[i] == cycle[i + d] for i in range(size - d))
        )
        visits = visits[: loop + period]
        while loop and visits[loop - 1] == visits[-1]:
            loop, visits = loop - 1, visits[:-1]
        return loop, visits

    def resolved(self, item):
        """``item`` with each phrase it plays named."""
        if isinstance(item, Group):
            return replace(item, body=tuple(self.resolved(b) for b in item.body))
        ref = item.phrase
        name = self.names.name("phrase", "p", ref.value, ref.native, ref.context)
        return replace(item, phrase=name)

    def parked(self, items: list, ctx: Ctx, x: int, age: int) -> Voice:
        """The voice ``x`` whose orderlist reaches the silence pattern after ``items``: its row
        counter, negative at the first parse and counting down, parses 128 row calls, then
        plays 128 and parses again."""
        found = self.found
        if found["dur_short"] != found["dur"] or found["dur_long"] != found["dur"]:
            raise Refusal("silence", "a build whose rows leave the row counter")
        native, context = ("pattern", MA.SILENCE_PATTERN), (x, *astuple(ctx), age)
        one = self.names.name(
            "phrase", "p", (self.silence(ctx, x, None, age),), native, (*context, "row")
        )
        last = self.names.name(
            "phrase", "p", (self.silence(ctx, x, PARSES + 1, age),), native, (*context, "last")
        )
        items += [Group((Play(one),), PARSES - 1), Play(last)]
        return Voice(tuple(items), len(items) - 2)

    @staticmethod
    def compressed(names: list, transpose: int) -> list:
        """The orderlist items that play ``names``, a run of one phrase a group."""
        items, shift = [], Signed(transpose) if transpose else None
        for name in names:
            if items and items[-1][0] == name:
                items[-1][1] += 1
            else:
                items.append([name, 1])
        return [Play(n, shift) if k == 1 else Group((Play(n, shift),), k) for n, k in items]

    # ---- the document -----------------------------------------------------------------------

    def init(self, x: int) -> Event:
        """Voice ``x``'s init event: the gate on, and the filter sweep the image runs if the
        voice owns it."""
        cmds, core = dict(self.boots.get(x, {})), self.core
        owner, acc = core.cell("filter_owner"), core.cell("fc_acc")
        ctr, count = core.cell("fc_ctr"), core.cell("trigger_ctr")
        if owner == x and (ctr or count or self.pokes):
            cmds["boot.cut"] = Once(
                self.call(
                    "fseti",
                    u=core.cell("trigger_acc"),
                    c=count,
                    s=core.cell("sweep_step"),
                    o=x,
                    a=acc,
                    r=ctr,
                    i=int(not self.first.get(x, False)),
                )
            )
            self.sweepers.add(x)
            cmds[f"c{x}.cut"] = self.call("fsweep", t=x)
        return Event("on", None, cmds)

    def header(self, used: list) -> Header:
        """The header: clock, tempo, slots, holds, state and the registers init left; the
        registers are not song data, so the memory gives them."""
        song = self.song
        named = {s for n in used for s in asm.symbols(ROUTINES[n])}
        state = tuple(
            (cell, keep)
            for cell, names in STATE.items()
            if (keep := tuple((k, n) for k, n in names if k in named))
        )
        cut = [Slot("boot")] if "fseti" in used else []
        for x in range(VOICES):
            cut += [Slot(f"s{x}")] if x in self.setters else []
            cut += [Slot(f"c{x}", hold=HELD, reset=("note",))] if x in self.sweepers else []
            cut += [Slot(f"q{x}")] if x in self.pokes else []
        slots = {
            "freq": [
                Slot("a", hold=HELD, reset=("legato",)),
                Slot("b", hold=HELD, reset=("note", "legato")),
                Slot("c"),
            ],
            "pw": [Slot("pw", hold=HELD)],
            "ctrl": [Slot("w", hold=HELD), Slot("m", hold=HELD)],
        }
        if cut:
            slots["cut"] = cut
        meta = (("title", song.name), ("author", song.author), ("released", song.released))
        return Header(
            tuple((k, meta_text(v)) for k, v in meta),
            clock=(CYCLES[self.core.clock], self.core.clock),
            slots=slots,
            holds={"ctrl": HELD},
            state=state or None,
            tempo=self.rowlen,
            regs=tuple(self.core.state.window(SID, 25)),
        )

    def document(self) -> Document:
        """The document of the subtune."""
        voices = [self.voice(x) for x in range(VOICES)]
        init = tuple(self.init(x) for x in range(VOICES))
        defs = [Section(k, tuple(self.names.section(k))) for k in ("ins", "ctrl", "phrase")]
        defs = [d for d in defs if d.records]
        doc = Document(Header(), (), (), defs, init, voices, None)
        used = list(dict.fromkeys(calls_of(doc)))
        mods = tuple((signature(n, asm.params(ROUTINES[n])), Code(ROUTINES[n])) for n in used)
        return Document(self.header(used), (), mods, defs, init, voices, None)


def native_counts(core: Core) -> dict:
    """The most song entries each voice's orderlist may lift to: each real entry and its repeats
    counted once, and the two a parked voice loops."""
    bounds = []
    for order in core.song.orderlists:
        count = 0
        for entry in order.entries:
            if entry.pattern_id == MA.SILENCE_PATTERN:
                count += 2
            elif entry.pattern_id < MA.ORDER_TEMPO:
                count += 1 + (entry.repeat > 0)
        bounds.append(count)
    return {"song": tuple(bounds)}


def document_bytes(core: Core) -> int:
    """The bytes of the song's data in the player's form: orderlists, patterns, instruments and
    inner programs."""
    song = core.song
    parts = [*song.orderlists, *song.patterns, *song.instruments, *song.inner_programs]
    return sum(len(p.to_bytes()) for p in parts if p is not None)


def build(core: Core) -> tuple:
    """``(text, origins)``: the document of ``core``, printed, and the native object each of its
    named objects stands for."""
    built = Lift(core)
    return printed(built.document()), built.names.by_kind()


def report(raw: bytes, subtune: int = 0) -> dict:
    """The size of a SID's document: per kind the ``(objects, native objects)`` it lifts (``song``:
    the most entries a voice against the most its orderlist may lift to), its ``fixed`` and
    ``song`` bytes, and the ``native_bytes`` of the song data."""
    core = core_of_sid(raw, subtune)
    text, origins = build(core)
    counts = invariants.provenance(text, origins)
    counts["song"] = (max(invariants.lifted(text)["song"]), max(native_counts(core)["song"]))
    return {"counts": counts, **invariants.sizes(text), "native_bytes": document_bytes(core)}


def document_text(core: Core) -> str:
    """The document of ``core``, printed, held to its provenance and the native entries."""
    text, origins = build(core)
    invariants.provenance(text, origins)
    invariants.check(text, native_counts(core))
    return text


# ---- the reference --------------------------------------------------------------------------


def player_of(core: Core, notes: bool = True) -> SpecPlayer:
    """The spec player of ``core`` with the standard note table (``notes``) or its own."""
    song, tune = core.song, Tune(core.song, core.subtune)
    binding = MUSIC_ASSEMBLER.spec(tune)
    head = song.container_header
    kwargs = {}
    if binding.sid is not None:
        ntsc = parse_sid_header(head).is_ntsc
        kwargs = {"sid": binding.sid, "clock": "NTSC" if ntsc else "PAL", "ntsc": ntsc}
    return SpecPlayer(
        core.spec,
        song.image,
        song.load_addr,
        song.init_addr,
        song.play_addr,
        core.subtune,
        bind=core.bind,
        entries=playspec.entries,
        psid=binding.psid,
        notes=core.clock if notes else None,
        **kwargs,
    )


def reference(raw: bytes, subtune: int = 0, frames: int = 20000, notes: bool = True):
    """The register grid of the spec player over ``frames`` frames."""
    fmt = find_format(raw)
    tune = fmt.parser.parse(raw, subtune)
    return np.asarray(
        fmt.spec_player(tune, notes=True if notes else None).render_grid(frames), dtype=np.uint8
    )


def compare(core: Core, frames: int, grid=None) -> tuple:
    """``(cells, text)``: the cells where the document of ``core`` differs from ``grid`` (the
    reference over ``frames`` frames; for a core, its player's)."""
    text = document_text(core)
    return cells_of(text, frames, grid, player_of(core).render_grid), text


def verify(raw: bytes, subtune: int = 0, frames: int = 20000) -> tuple:
    """``(cells, text)`` of a SID: the cells where its document differs from the reference."""
    text = document_text(core_of_sid(raw, subtune))
    return cells_of(text, frames, reference=lambda n: reference(raw, subtune, n)), text


def lift(raw: bytes, subtune: int = 0, frames: int | None = None) -> str:
    """The document of a SID's subtune, checked against the reference over ``frames`` frames
    (default :data:`CHECK`); a LiftError otherwise."""
    return lifted_text(verify, raw, subtune, frames, CHECK)


def main(argv=None) -> int:
    """Lift a SID, or verify SIDs against the spec player."""
    return cli.main(argv, __doc__.split("\n", 1)[0], lift, verify)


if __name__ == "__main__":
    sys.exit(main())
