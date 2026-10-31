"""A Music Assembler tune as a format 2.5 document, from pymusicassembler's song model and the
layout its playspec binding resolves; ``verify`` plays the document against the spec player with
the standard note table (docs/lift/musicassembler.md)."""

import sys
from dataclasses import dataclass, replace

import numpy as np
from pymusicassembler import constants as MA
from pymusicassembler import playspec
from pymusicassembler.formats import MUSIC_ASSEMBLER, Tune
from pymusicassembler.model import InstrumentEvent, NoteEvent, Song
from pymusicassembler.player import Player
from pysidtracker import NTSC_CLOCK_HZ, PAL_CLOCK_HZ, find_format, gridcmp, parse_sid_header
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
from unitracker_player.player import play

from unitracker.lift import cli
from unitracker.lift.macode import ROUTINES, STATE
from unitracker.lift.shared import meta_text, note_name

CYCLES = {PAL_CLOCK_HZ: 19656, NTSC_CLOCK_HZ: 17095}
CHECK = 4000
VOICES = MA.VOICES
SID = 0xD400
JMP_ABS = 0x4C
MAX_ITEMS = 512
HELD = ("step",)
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
    """What a lift needs of a tune: the song, its bound spec and layout, the memory the tune's
    init left, the machine clock and the 0-based subtune."""

    song: Song
    found: dict
    spec: object
    view: bytes
    clock: int
    subtune: int = 0
    bind: object = playspec.bind

    def cell(self, name: str, at: int = 0) -> int:
        """The byte of layout cell ``name`` (voice ``at``) after init."""
        return self.view[self.found[name] + at]


def _clock_of(song: Song) -> int:
    head = song.container_header
    ntsc = head[:4] in (b"PSID", b"RSID") and parse_sid_header(head).is_ntsc
    return NTSC_CLOCK_HZ if ntsc else PAL_CLOCK_HZ


def core_of_song(song: Song, subtune: int = 0, bind=playspec.bind, clock: int | None = None):
    """The core of ``song``'s 0-based ``subtune`` as the spec binder ``bind`` resolves it."""
    booted = Player(song, subtune, bind=bind)
    view = bytes(booted.view())
    spec, found = bind(view, booted.entry)
    return Core(song, dict(found), spec, view, clock or _clock_of(song), subtune, bind)


def core_of_sid(raw: bytes, subtune: int = 0) -> Core:
    """The core of a SID's subtune."""
    return core_of_song(MUSIC_ASSEMBLER.parser.parse(raw, subtune).song, subtune)


ALIASES = (
    ("ctrl", "gate_ctrl"),
    ("ctrl_mask", "gate_mask"),
    ("vib_freq_lo", "vib_out_lo"),
    ("vib_freq_hi", "vib_out_hi"),
    ("sweep_dec", "sweep_ctr"),
    ("sweep_ctr", "fc_ctr"),
    ("sweep_acc", "sweep_sta"),
    ("sweep_acc", "program_acc"),
    ("sweep_acc", "fc_acc"),
    ("trigger_owner", "filter_owner"),
    ("trigger_owner", "sweep_owner"),
    ("trigger_acc", "fc_acc_reset"),
    ("trigger_ctr", "fc_ctr_reset"),
    ("sweep_step", "fc_step"),
    ("transrep", "transrep_byte"),
    ("pw_substep", "pw_substep_cmp"),
)


def _arp_cells(found: dict) -> dict:
    """The cells the arpeggio code's operands name in a build that keeps them where the spec
    puts them."""
    return {
        ("arp_porta_dec", "arp_porta_inc"): found["porta_ctr"],
        ("arp_phase_rd", "arp_phase_inc"): found["arp_phase"],
        ("arp_substep_inc", "arp_substep_cmp", "arp_substep_clr"): found["arp_substep"],
        ("arp_down_src", "arp_down_dst", "arp_up_src", "arp_up_dst"): found["freq_lo_w"],
        ("arp_down_hi", "arp_up_hi"): found["freq_hi_w"],
        ("arp_down_step", "arp_up_step"): found["instr"] + MA.INSTR_ARP_STEP,
        ("arp_speed",): found["instr"] + MA.INSTR_ARP_SPEED,
    }


def _entered(core: Core) -> bool:
    """Whether the play address reaches the play entry through nothing but ``JMP abs``."""
    view, at, seen = core.view, core.song.play_addr, set()
    while view[at] == JMP_ABS and at not in seen:
        seen.add(at)
        at = view[at + 1] | view[at + 2] << 8
    return at == core.found["entry"]


def _refuse(core: Core) -> None:
    """Refuse what the document cannot state, before lifting."""
    found = core.found
    if core.song.play_addr == 0:
        raise Refusal("interrupt", "play address 0")
    if not _entered(core):
        raise Refusal("play routine", "the play address is not the play entry")
    if playspec.has_native(core.spec):
        raise Refusal("native", "the spec has native blocks")
    if len(playspec.entries(core.view)) > 1:
        raise Refusal("multi-SID", "several play entries")
    if (found["sid"], found["fc_sid"], found["res_sta"]) != (SID, SID, SID + 0x17):
        raise Refusal("SID mirror", hex(found["sid"]))
    if tuple(found["output_voices"]) != (0, 1, 2):
        raise Refusal("voice dispatch", str(found["output_voices"]))
    if tuple(core.cell("voice_reg", v) for v in range(VOICES)) != (0, 7, 14):
        raise Refusal("voice dispatch", "voice registers")
    for one, other in ALIASES:
        if found[one] != found[other]:
            raise Refusal("layout", f"{one} apart from {other}")
    if found["arp_down_cmp"] or found["pulse_cmp_adc"]:
        raise Refusal("arpeggio code", "a comparing variant")
    for names, cell in _arp_cells(found).items():
        if any(core.view[found[n]] | core.view[found[n] + 1] << 8 != cell for n in names):
            raise Refusal("relocated arpeggio operands", names[0])
    if core.cell("tempo") or any(core.cell("dur", v) for v in range(VOICES)):
        raise Refusal("init", "a first play call that reads no row")
    if found["order_restart"] % 2:
        raise Refusal("orderlist", "restart inside an entry")


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


@dataclass(frozen=True)
class Ctx:
    """A voice's state at a row: its instrument, the instrument its last trigger started (None
    before one), whether its vibrato slot is bound, the vibrato steps its cells hold, whether its
    control mask clears the gate, and whether it is still in the image init left."""

    ins: int
    trig: int | None
    vib: bool
    steps: tuple
    rest: bool
    boot: bool


def _expr(text: str) -> Expr:
    return Expr(canonical(text))


def _const(value: int) -> Expr:
    return Expr(f"{value} + 0")


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


class Lift:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """One core's document."""

    def __init__(self, core: Core):
        _refuse(core)
        self.core, self.found, self.song = core, core.found, core.song
        self.names, self.pokes, self.cache = Names(), set(), {}
        self.rowlen = core.cell("tempo_reload") + 1
        self.skip = bool(self.found["pitch_skip"])
        self.always = bool(self.found["pulse_always"])
        self.first = {}
        self.ctl0 = [core.cell("note_ctl", x) for x in range(VOICES)]

    # ---- instruments and programs -----------------------------------------------------------

    def record(self, k: int):
        """Instrument ``k``'s record."""
        if k >= len(self.song.instruments):
            raise Refusal("instrument", f"{k} outside the song's")
        return self.song.instruments[k]

    @staticmethod
    def call(name: str, **params) -> Call:
        """The call of template ``name`` with ``params`` in its signature's order."""
        return Call(name, tuple(params[k] for k in asm.params(ROUTINES[name])))

    def length(self, d: int, kind: str) -> int:
        """The calls of a row of ``kind`` and duration ``d``: the build's duration cell that
        row stores to counts it down only where it is the voice's counter."""
        cell = {"rest": "dur_short", "hold": "dur_long", "note": "dur_note"}[kind]
        return ((d if self.found[cell] == self.found["dur"] else 0) + 1) * self.rowlen

    def program_of(self, flags: int):
        """The inner program of an instrument's ``flags``, None where it has none."""
        if not flags & 0x0F:
            return None
        prog = self.song.inner_programs[flags & 0x0F]
        if prog is None or not prog.rows:
            raise Refusal("program", f"slot {flags & 0x0F} has no rows")
        return prog

    def arpeggio(self, flags: int) -> str:
        """``arp`` where an instrument's frequency sweep runs from its trigger, ``after`` where
        it waits for a program that stops, else ''."""
        if not flags & 0x10:
            return ""
        prog = self.program_of(flags)
        if prog is None:
            return "arp"
        return "after" if prog.terminator == MA.PROGRAM_STOP else ""

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
                (f"p{x}.cut", Once(self.call("pokea", t=x, u=row.filter))),
                (f"q{x}.cut", Once(self.call("pokeb", t=x, u=row.filter))),
            ]
        return Row(keys)

    def jump(self, prog) -> int:
        """The loop of a program ending in ``$FD r``."""
        found = self.found
        if not found["program_jump"] or found["program_jump"] != found["instr_cursor"]:
            raise Refusal("program jump", "bound apart from the cursor")
        target, n = prog.jump or 0, len(prog.rows)
        if target == 0 or target >= 3 * (n - 1) + 3:
            return 1
        if target >= n:
            raise Refusal("program jump", f"row {target} past the {n} rows")
        return 1 + target

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
            if prog.terminator == MA.PROGRAM_RESTART:
                loop = 1
            elif prog.terminator == MA.PROGRAM_JUMP:
                loop = self.jump(prog)
            elif self.arpeggio(rec.porta) == "after":
                rows.append(Row({"freq": self.call("arps", m=self.arp_args(k)["m"])}))
        if not mask:
            masked = ("m.ctrl", _expr("v & 254"))
            if len(rows) > 1:
                rows[1] = Row(rows[1].keys + (masked,))
            else:
                rows.append(Row([masked]))
        return self.names.name("ctrl", "c", Program(rows, loop))

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
        return self.names.name("ins", "i", Instrument(fields))

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

    def configure(self, ctx: Ctx, ins: int, trig, vib: bool) -> dict:
        """The command that sets the arpeggio's step, steps a phase and vibrato flag where a row
        changes them for the instrument ``trig`` last started."""
        if trig is None or not self.arpeggio(self.record(trig).porta):
            return {}
        rec, old = self.record(ins), self.record(ctx.ins)
        steps = (rec.arp_step, rec.arp_speed & 15)
        if trig == ctx.trig and (steps, vib) == ((old.arp_step, old.arp_speed & 15), ctx.vib):
            return {}
        return {"c.freq": Once(self.call("arpc", s=steps[0], k=steps[1], b=int(vib)))}

    @staticmethod
    def vibrato(steps: tuple) -> Expr:
        """The vibrato slot's binding for step bytes ``steps``: the word added a call, on every
        call or, with an odd low step, on every other."""
        word = steps[1] << 8 | steps[0]
        if steps[0] & 1:
            return _expr(f"ite(phi % 2 == 0, F(n) + (phi // 2 + 1) * {word}, v)")
        return _expr(f"F(n) + (phi + 1) * {word}")

    def sweep_commands(self, ev: NoteEvent, x: int) -> dict:
        """The commands of a note's filter command on voice ``x``."""
        cutoff, length = ev.args
        step = (((cutoff & 0x0F) << 1) - 0x10) & 0xFF
        preset = self.core.cell("res_presets", x) if length else 0xF0
        return {
            "res": preset >> 4,
            "route": preset & 0x0F,
            "set.cut": Once(self.call("fset", u=cutoff, c=length, s=step, o=x)),
            "cut.cut": self.call("fsweep"),
        }

    def image(self, x: int) -> dict:
        """The commands that hold the cells init left on voice ``x`` where its first row plays
        no note."""
        found, view = self.found, self.core.view
        word = view[found["freq_hi_w"] + x] << 8 | view[found["freq_lo_w"] + x]
        pw = (view[found["pw_hi"] + x] & 0x0F) << 8 | view[found["pw_lo"] + x]
        return {"freq": _const(word), "pw": pw}

    def start(self, x: int) -> Ctx:
        """The state of voice ``x`` before its first row."""
        steps = (self.core.cell("vib_lo_step", x), self.core.cell("vib_hi_step", x))
        return Ctx(self.core.cell("instr_x8", x) >> 3, None, False, steps, False, True)

    def note(self, ev: NoteEvent, sel, ctx: Ctx, x: int) -> tuple:
        """``(event, ctx)`` of a note row under instrument select ``sel``."""
        ins = ctx.ins if sel is None else sel
        cmds = {"vtempo": self.length(ev.duration, "note")}
        steps = ev.args if ev.vibrato and not ev.filter else ctx.steps
        if ev.filter:
            if ev.tied:
                raise Refusal("legato filter command")
            cmds |= self.sweep_commands(ev, x)
        if ev.vibrato:
            cmds["b.freq"] = self.vibrato(steps)
        elif ctx.vib:
            cmds["b.freq"] = NONE
        atom = note_name(ev.note)
        if not ev.tied:
            cmds |= self.configure(ctx, ins, ins, ev.vibrato)
            return Event(atom, self.instrument(ins, x), cmds), Ctx(
                ins, ins, ev.vibrato, steps, False, False
            )
        cmds["legato"] = True
        cmds["m.ctrl"] = _const(self.core.cell("ctrl", x)) if ctx.boot else NONE
        if ctx.boot:
            cmds["pw"] = self.image(x)["pw"]
        cmds["freq"] = _expr("F(n)")
        if ctx.trig is not None:
            cmds |= self.rebinds(ctx, ins) | self.configure(ctx, ins, ctx.trig, ev.vibrato)
            if self.arpeggio(self.record(ctx.trig).porta):
                cmds["freq"] = self.call("arpl", m=self.arp_args(ins)["m"])
        nxt = replace(ctx, ins=ins, vib=ev.vibrato, steps=steps, rest=False, boot=False)
        return Event(atom, self.instrument(ins, x) if sel is not None else None, cmds), nxt

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
        cmds = {"vtempo": self.length(ev.duration, "rest" if rest else "hold")}
        atom, trig = "-", ctx.trig
        if ctx.boot:
            if self.ctl0[x] & MA.CTL_VIBARP or self.always:
                raise Refusal("init", "a sweep running from the image")
            cmds |= self.image(x)
            cmds["m.ctrl"] = _const(self.core.cell("ctrl", x) & (0xFE if rest else 0xFF))
            if not self.ctl0[x] & MA.CTL_TRIGGERED:
                atom, trig = note_name(self.core.cell("note_index", x)), ins
                cmds |= self.primed(ins, x, rest)
                if rest:
                    del cmds["m.ctrl"]
        elif rest:
            cmds["m.ctrl"] = _expr("v & 254")
        if sel is not None and not ctx.boot:
            cmds |= self.rebinds(ctx, ins) | self.configure(ctx, ins, ctx.trig, ctx.vib)
        name = self.instrument(ins, x) if sel is not None or atom != "-" else None
        return Event(atom, name, cmds), replace(
            ctx, ins=ins, trig=trig, rest=rest or ctx.rest, boot=False
        )

    def silence(self, ctx: Ctx, x: int) -> Event:
        """The event of a voice parked on the silence pattern: the gate off on each row call."""
        cmds = {"vtempo": self.rowlen}
        prog = self.program_of(self.record(ctx.trig).porta) if ctx.trig is not None else None
        if ctx.boot:
            cmds |= self.image(x)
            cmds["m.ctrl"] = _const(self.core.cell("ctrl", x) & 0xFE)
        elif ctx.rest:
            cmds["m.ctrl"] = _expr("v & 254")
        elif prog is None:
            cmds["m.ctrl"] = Once(_expr("v & 254"))
        elif prog.terminator == MA.PROGRAM_STOP:
            raise Refusal("silence", "while a program may run")
        return Event("-", None, cmds)

    def rows(self, pid: int) -> list:
        """The rows of pattern ``pid`` as ``(select, event)`` pairs."""
        if pid >= len(self.song.patterns) or self.song.patterns[pid] is None:
            raise Refusal("pattern", f"{pid} is not a pattern")
        events, out, sel = self.song.patterns[pid].events(), [], None
        if not events:
            raise Refusal("pattern", f"{pid} is empty")
        for ev in events:
            if isinstance(ev, InstrumentEvent):
                sel = ev.instrument
            else:
                out.append((sel, ev))
                sel = None
        return out

    def phrase(self, pid: int, ctx: Ctx, x: int) -> tuple:
        """``(name, ctx at the end)`` of the phrase of pattern ``pid`` entered in ``ctx``."""
        key = (pid, ctx, x)
        if key not in self.cache:
            events, here = [], ctx
            for sel, ev in self.rows(pid):
                if not events and ctx.boot:
                    self.first[x] = isinstance(ev, NoteEvent) and not ev.tied
                step = self.note if isinstance(ev, NoteEvent) else self.other
                event, here = step(ev, sel, here, x)
                events.append(event)
            self.cache[key] = (self.names.name("phrase", "p", tuple(events)), here)
        return self.cache[key]

    # ---- orderlists -------------------------------------------------------------------------

    def settle(self, order, real: list, at: int):
        """The entry index the orderlist step reaches from ``at``, tempo commands passed, None
        where it parks on the silence pattern."""
        for _ in range(len(real) + 3):
            if at < len(real):
                if self.found["order_tempo"] and real[at].pattern_id == MA.ORDER_TEMPO:
                    if real[at].control_byte() != self.rowlen - 1:
                        raise Refusal("tempo change", f"to {real[at].control_byte()}")
                    at += 1
                    continue
                return at
            if order.terminator == MA.SILENCE_PATTERN:
                return None
            if order.terminator == MA.ORDER_JUMP:
                at = order.entries[-1].control_byte()
            else:
                at = self.found["order_restart"] // 2
        raise Refusal("orderlist", "does not reach a pattern")

    def voice(self, x: int) -> Voice:
        """Voice ``x``'s orderlist."""
        order = self.song.orderlists[x]
        real = order.entries[: len(order.entries) - (order.terminator != MA.ORDER_END)]
        ctx, items, seen = self.start(x), [], {}
        at = self.settle(order, real, 0)
        while len(items) < MAX_ITEMS:
            if at is None:
                items.append(Play(self.names.name("phrase", "p", (self.silence(ctx, x),))))
                return Voice(tuple(items), len(items) - 1)
            if (at, ctx) in seen:
                return Voice(tuple(items), seen[at, ctx])
            seen[at, ctx] = len(items)
            entry, plays = real[at], []
            for _ in range(entry.repeat + 1):
                name, ctx = self.phrase(entry.pattern_id, ctx, x)
                plays.append(name)
            items += self.compressed(plays, entry.transpose)
            at = self.settle(order, real, at + 1)
        raise Refusal("orderlist", "does not settle")

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
        cmds, core = {}, self.core
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
            cmds["cut.cut"] = self.call("fsweep")
        return Event("on", None, cmds)

    def header(self, used: list) -> Header:
        """The header: clock, slots, holds, state and the registers init left."""
        song, view = self.song, self.core.view
        named = {s for n in used for s in asm.symbols(ROUTINES[n])}
        state = tuple(
            (cell, keep)
            for cell, names in STATE.items()
            if (keep := tuple((k, n) for k, n in names if k in named))
        )
        cut = [Slot("boot")] if "fseti" in used else []
        cut += [Slot("set")] if "fset" in used else []
        cut += [Slot(f"p{x}") for x in sorted(self.pokes)]
        cut += [Slot("cut", hold=HELD, reset=("note",))] if "fsweep" in used else []
        cut += [Slot(f"q{x}") for x in sorted(self.pokes)]
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
            regs=tuple(view[SID : SID + 25]),
        )

    def document(self) -> Document:
        """The document of the subtune."""
        voices = [self.voice(x) for x in range(VOICES)]
        init = tuple(self.init(x) for x in range(VOICES))
        defs = [Section(k, tuple(self.names.section(k))) for k in ("ins", "ctrl", "phrase")]
        defs = [d for d in defs if d.records]
        doc = Document(Header(), (), (), defs, init, voices, None)
        used = list(dict.fromkeys(_calls(doc)))
        mods = tuple((signature(n, asm.params(ROUTINES[n])), Code(ROUTINES[n])) for n in used)
        return Document(self.header(used), (), mods, defs, init, voices, None)


def document_text(core: Core) -> str:
    """The document of ``core``, printed."""
    return printed(Lift(core).document())


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
    return np.asarray(fmt.spec_player(tune, notes=notes).render_grid(frames), dtype=np.uint8)


def compare(core: Core, frames: int, grid=None) -> tuple:
    """``(cells, text)``: the cells where the document of ``core`` differs from ``grid`` (the
    reference over ``frames`` frames; for a core, its player's)."""
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
    text = document_text(core_of_sid(raw, subtune))
    ours = np.asarray(play(text, frames), dtype=np.uint8)
    return gridcmp.cell_diff(ours, reference(raw, subtune, len(ours))), text


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
