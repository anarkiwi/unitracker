"""A Music Assembler tune as a format 2.5 document, from pymusicassembler's song model and the
layout its playspec binding resolves; ``verify`` plays the document against the spec player with
the standard note table (docs/lift/musicassembler.md)."""

import sys
from dataclasses import dataclass, replace
from types import SimpleNamespace

import numpy as np
from pymusicassembler import constants as MA
from pymusicassembler import playspec
from pymusicassembler.formats import MUSIC_ASSEMBLER, Tune
from pymusicassembler.errors import MusicAssemblerError
from pymusicassembler.grammar import GRAMMAR, PATTERN, orderlist_rule
from pymusicassembler.reader import parse_orderlist, parse_pattern
from pymusicassembler.model import ArpeggioRow, DurationEvent
from pymusicassembler.model import Instrument as ModelInstrument
from pymusicassembler.model import InstrumentEvent, NoteEvent, Song
from pymusicassembler.player import Player
from pysidtracker import (
    NTSC_CLOCK_HZ,
    PAL_CLOCK_HZ,
    SidImage,
    find_format,
    gridcmp,
    parse_sid_header,
)
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

from unitracker.lift import cli, invariants
from unitracker.lift.macode import ROUTINES, STATE
from unitracker.lift.shared import meta_text, note_name

CYCLES = {PAL_CLOCK_HZ: 19656, NTSC_CLOCK_HZ: 17095}
CHECK = 4000
VOICES = MA.VOICES
SID = 0xD400
JMP_ABS = 0x4C
MAX_ITEMS = 512
PARSES = 128
MAX_AGE = 1 << 12
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
class Walk:
    """An instrument program as the player reads it: its rows in the order the cursor visits
    them, and the row it continues at after the last (None where it stops)."""

    rows: tuple
    loop: int | None


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
    song = replace(song, **_lists(view, dict(found)))
    return Core(song, dict(found), spec, view, clock or _clock_of(song), subtune, bind)


def _lists(view: bytes, found: dict) -> dict:
    """The orderlists and patterns the memory init left reads through the pointer tables: a
    subtune's init may point the voices at other lists than the file's."""
    img, lay = SidImage.from_prg(b"\0\0" + view), SimpleNamespace(**found)
    rule = orderlist_rule(lay)
    orders = [parse_orderlist(img, lay, voice, rule) for voice in range(VOICES)]
    reached = {e.pattern_id for o in orders for e in o.entries if e.pattern_id < MA.ORDER_TEMPO}
    patterns = [None] * (max(reached, default=-1) + 1)
    for pid in reached:
        try:
            patterns[pid] = parse_pattern(img, lay, pid)
        except MusicAssemblerError:
            continue
    return {"orderlists": orders, "patterns": patterns}


def core_of_sid(raw: bytes, subtune: int = 0) -> Core:
    """The core of a SID's subtune."""
    return core_of_song(MUSIC_ASSEMBLER.parser.parse(raw, subtune=subtune).song, subtune)


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


def _layout(core: Core) -> None:
    """Refuse the layouts whose cells the document's model does not keep apart or where the
    spec puts them."""
    found = core.found
    for one, other in ALIASES:
        if found[one] != found[other]:
            raise Refusal("layout", f"{one} apart from {other}")
    if found["arp_down_cmp"] or found["pulse_cmp_adc"]:
        raise Refusal("arpeggio code", "a comparing variant")
    for names, cell in _arp_cells(found).items():
        if any(core.view[found[n]] | core.view[found[n] + 1] << 8 != cell for n in names):
            raise Refusal("relocated arpeggio operands", names[0])
    if found["order_restart"] % 2:
        raise Refusal("orderlist", "restart inside an entry")


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
    _layout(core)
    if core.cell("tempo") or any(core.cell("dur", v) for v in range(VOICES)):
        raise Refusal("init", "a first play call that reads no row")


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
        self.names, self.cache = Names(), {}
        self.pokes, self.setters, self.sweepers = set(), set(), set()
        self.rowlen = core.cell("tempo_reload") + 1
        self.skip = bool(self.found["pitch_skip"])
        self.always = bool(self.found["pulse_always"])
        self.first, self.walks = {}, {}
        self.ctl0 = [core.cell("note_ctl", x) for x in range(VOICES)]

    # ---- instruments and programs -----------------------------------------------------------

    def record(self, k: int) -> Instrument:
        """Instrument ``k``'s record as the memory init left it, where the player reads it."""
        at = self.found["instr"] + (k << 3)
        return ModelInstrument.from_bytes(self.core.view[at : at + MA.INSTR_RECORD_SIZE])

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
        """The program an instrument with ``flags`` runs from its trigger, None where it has
        none."""
        return self.walk(flags & 0x0F) if flags & 0x0F else None

    def walk(self, slot: int) -> Walk:
        """The rows the player reads through program slot ``slot``'s pointer, whatever bytes
        they are: the cursor ``y`` reads a row at ``y``, ``y + 1``, ``y + 2`` and a marker at
        ``y + 3`` (a byte wrap included), which stops the program (``$FE``), restarts it
        (``$FF``), jumps (``$FD`` where the build binds it) or moves on to the next row."""
        if slot in self.walks:
            return self.walks[slot]
        found, view = self.found, self.core.view
        base = view[found["inner_lo"] + slot] | view[found["inner_hi"] + slot] << 8

        def byte(y: int) -> int:
            return view[(base + (y & 255)) & 0xFFFF]

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
            if self.always:
                raise Refusal("init", "a pulse sweep running from the image")
            cmds |= self.image(x)
            if self.ctl0[x] & MA.CTL_VIBARP and (running := self.running_vibrato(x)) is not None:
                cmds["b.freq"] = running
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
        vib = ctx.vib or bool(ctx.boot and self.ctl0[x] & MA.CTL_VIBARP)
        return Event(atom, name, cmds), replace(
            ctx, ins=ins, trig=trig, vib=vib, rest=rest or ctx.rest, boot=False
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

    def silence(self, ctx: Ctx, x: int, length: int, age: int) -> Event:
        """The event of ``length`` calls of a voice on the silence pattern, whose trigger ran
        ``age`` calls ago: the gate off at its row call."""
        cmds = {"vtempo": length}
        prog = self.program_of(self.record(ctx.trig).porta) if ctx.trig is not None else None
        if ctx.boot:
            cmds |= self.image(x)
            cmds["m.ctrl"] = _const(self.core.cell("ctrl", x) & 0xFE)
        elif ctx.rest:
            cmds["m.ctrl"] = _expr("v & 254")
        elif prog is None:
            cmds["m.ctrl"] = Once(_expr("v & 254"))
        elif prog.loop is None:
            start = self.gate_start(age, len(prog.rows))
            cmds["m.ctrl"] = (
                Once(_expr("v & 254")) if not start else _expr(f"ite(phi >= {start}, v & 254, v)")
            )
        return Event("-", None, cmds)

    def events(self, pid: int) -> list:
        """The rows of pattern ``pid``; an empty one plays its lone ``$FF`` as a hold and reads on
        into the bytes after it."""
        if pid >= len(self.song.patterns) or self.song.patterns[pid] is None:
            raise Refusal("pattern", f"{pid} is not a pattern")
        events = self.song.patterns[pid].events()
        if events:
            return events
        found, view = self.found, self.core.view
        at = view[found["pattern_lo"] + pid] | view[found["pattern_hi"] + pid] << 8
        try:
            rest = GRAMMAR.parse_prefix(bytes(view[at + 1 : at + 257]), PATTERN).model
        except MusicAssemblerError as exc:
            raise Refusal("pattern", f"{pid} is empty and no pattern follows") from exc
        return [DurationEvent(MA.CTL_DUR_MASK, long=True)] + rest

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

    @staticmethod
    def tailed(tail: tuple, event: Event, row: tuple) -> tuple:
        """``tail`` of a phrase after ``event``, the row ``ev`` taking ``before`` to ``after``:
        a trigger restarts the count of calls, any other row adds its own."""
        ev, before, after = row
        calls = dict(event.commands)["vtempo"] - 1
        if (isinstance(ev, NoteEvent) and not ev.tied) or (
            before.trig is None and after.trig is not None
        ):
            return True, calls
        return tail[0], tail[1] + calls

    def phrase(self, pid: int, ctx: Ctx, x: int) -> tuple:
        """``(name, ctx at the end, tail)`` of the phrase of pattern ``pid`` entered in ``ctx``;
        ``tail`` is ``(triggered, calls)``: whether a row triggered an instrument and the calls
        (parse calls aside) from the last trigger, or all of them if none did."""
        key = (pid, ctx, x)
        if key not in self.cache:
            events, here, tail = [], ctx, (False, 0)
            for sel, ev in self.rows(pid):
                if not events and ctx.boot:
                    self.first[x] = isinstance(ev, NoteEvent) and not ev.tied
                step = self.note if isinstance(ev, NoteEvent) else self.other
                event, after = step(ev, sel, here, x)
                events.append(event)
                tail = self.tailed(tail, event, (ev, here, after))
                here = after
            name = self.names.name("phrase", "p", tuple(events))
            self.cache[key] = (name, here, tail)
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
        ctx, items, seen, age = self.start(x), [], {}, 0
        at = self.settle(order, real, 0)
        while len(items) < MAX_ITEMS:
            if at is None:
                return self.parked(items, ctx, x, age)
            if (at, ctx) in seen:
                return Voice(tuple(items), seen[at, ctx])
            seen[at, ctx] = len(items)
            entry, plays = real[at], []
            for _ in range(entry.repeat + 1):
                name, ctx, (triggered, calls) = self.phrase(entry.pattern_id, ctx, x)
                age = min(calls if triggered else age + calls, MAX_AGE)
                plays.append(name)
            items += self.compressed(plays, entry.transpose)
            at = self.settle(order, real, at + 1)
        raise Refusal("orderlist", "does not settle")

    def parked(self, items: list, ctx: Ctx, x: int, age: int) -> Voice:
        """The voice ``x`` whose orderlist reaches the silence pattern after ``items``: its row
        counter, negative at the first parse and counting down, parses 128 row calls, then
        plays 128 and parses again."""
        found = self.found
        if found["dur_short"] != found["dur"] or found["dur_long"] != found["dur"]:
            raise Refusal("silence", "a build whose rows leave the row counter")
        one = self.names.name("phrase", "p", (self.silence(ctx, x, self.rowlen, age),))
        last = self.names.name(
            "phrase", "p", (self.silence(ctx, x, (PARSES + 1) * self.rowlen, age),)
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
            self.sweepers.add(x)
            cmds[f"c{x}.cut"] = self.call("fsweep", t=x)
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


def native_counts(core: Core) -> dict:
    """The most objects of each kind the document of ``core`` may hold, from the song alone.

    A voice's carried state (instrument, last trigger, vibrato, rest mask, image) changes
    through a pattern by what its last row sets, so applying a pattern twice is applying it once:
    a voice's orderlist is lifted at most twice, from the state init left and then from the
    state its own list leaves, and an entry with a repeat plays its first run from the entering
    state and every later one from the state the first left. So per voice:

    * ``phrases``: at most 2 passes x (1 + 1 if the entry repeats) phrases for each real entry,
      and the two phrases a parked voice loops (its row and its last row);
    * ``song``: at most the entries of those two passes, 2 per entry (a run of one phrase is one
      item), and 2 for a parked voice;
    * ``instruments`` and ``ctrl``: an instrument is one trigger program, which differs per voice
      only where its inner program pokes the filter (the poke slot is the voice's), plus one
      trigger a voice that starts without a note runs from the image;
    * ``pw``: one binding per instrument.

    Instruments are those a reached pattern selects or a voice starts on.
    """
    song, built = core.song, Lift(core)
    real = [
        [
            e
            for e in o.entries
            if e.pattern_id < MA.ORDER_TEMPO or e.pattern_id == MA.SILENCE_PATTERN
        ]
        for o in song.orderlists
    ]
    reached = {e.pattern_id for entries in real for e in entries}
    chosen = {built.start(x).ins for x in range(VOICES)}
    for pid in reached:
        if pid < len(song.patterns) and song.patterns[pid] is not None:
            events = song.patterns[pid].events()
            chosen |= {e.instrument for e in events if isinstance(e, InstrumentEvent)}
    objects = VOICES

    def pokes(k: int) -> bool:
        prog = built.program_of(built.record(k).porta)
        return prog is not None and any(r.filter for r in prog.rows)

    objects += sum(VOICES if pokes(k) else 1 for k in chosen)
    parked = [
        o.terminator == MA.SILENCE_PATTERN or any(e.pattern_id == MA.SILENCE_PATTERN for e in r)
        for o, r in zip(song.orderlists, real)
    ]
    runs = [sum(1 + (e.repeat > 0) for e in r if e.pattern_id != MA.SILENCE_PATTERN) for r in real]
    return {
        "instruments": objects,
        "ctrl": objects,
        "pw": len(chosen),
        "phrases": sum(2 * n for n in runs) + 2 * sum(parked),
        "song": tuple(4 * n + 2 * p for n, p in zip(runs, parked)),
    }


def document_bytes(core: Core) -> int:
    """The bytes of the song's data in the player's form: orderlists, patterns, instruments and
    inner programs."""
    song = core.song
    parts = [*song.orderlists, *song.patterns, *song.instruments, *song.inner_programs]
    return sum(len(p.to_bytes()) for p in parts if p is not None)


def measure(raw: bytes, subtune: int, text: str) -> dict:
    """The lifted and native object counts of a SID's document ``text`` and its bytes against the
    song data's."""
    core = core_of_sid(raw, subtune)
    return {
        "lifted": invariants.lifted(text),
        "native": native_counts(core),
        "bytes": len(text.encode()),
        "native_bytes": document_bytes(core),
    }


def document_text(core: Core) -> str:
    """The document of ``core``, printed, with its objects counted against the song's."""
    text = printed(Lift(core).document())
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
