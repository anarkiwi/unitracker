# pylint: disable=too-many-lines
"""A SID-Wizard tune as a format 2.5 document, from pysidwizard's typed module and the layout of its
bound playroutine spec; ``verify`` plays the document against the spec player with the standard
note table (docs/lift/sidwizard.md)."""

import sys
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np
from pysidtracker import NTSC_CLOCK_HZ, PAL_CLOCK_HZ, SidImage, find_format, parse_sid_header
from pysidtracker.cadence import cadence_info
from pysidtracker.errors import SidParseError
from pysidtracker.notes import frequency
from pysidwizard import End, Loop, PlayPattern, RawSequenceByte, SWMFile, Transpose
from pysidwizard import typed as T
from pysidwizard.playspec import binding
from unitracker_grammar import code as asm
from unitracker_grammar.document import (
    HOLD,
    NONE,
    Call,
    Code,
    Document,
    Event,
    Expr,
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
from unitracker_player.compiler import load as load_song
from unitracker_player.player import Player

from unitracker.lift import cli, invariants, swcode
from unitracker.lift.shared import Names, Reference, Refusal, cells_of, lifted_text
from unitracker.lift.shared import meta_text, note_name, rests, spec_played

CYCLES = {PAL_CLOCK_HZ: 19656, NTSC_CLOCK_HZ: 17095}
PRE = 2
CHECK = 20000
LIMIT = 1 << 17
TRIGGERS = ("pal_video", "ntsc_video", "cia_timer")
VOICES = 3
STRIDE = 7
summary = cli.summary


@dataclass(frozen=True)
class Core:  # pylint: disable=too-many-instance-attributes
    """What a lift needs of a tune: the typed module, the bound spec layout, the memory and
    registers its init left, the clock and the title lines."""

    swm: SWMFile
    lay: dict
    mem: bytes
    regs: tuple
    clock: int
    head: tuple
    subtune: int = 0
    raw: bytes = b""

    def grid(self, frames: int) -> list:
        """The first ``frames`` register rows the spec player writes."""
        fmt = find_format(self.raw)
        return np.asarray(fmt.spec_player(self.swm, notes=True).render_grid(frames), int).tolist()

    def var(self, name: str, voice: int = 0) -> int:
        """The byte of voice ``voice``'s player variable ``name`` after init."""
        return self.mem[self.lay[name] + STRIDE * voice]


def core_of_sid(raw: bytes, subtune: int = 0) -> Core:
    """The core of a SID's subtune; a Refusal for a tune the playspec does not state."""
    head = parse_sid_header(raw)
    if head.version >= 3 and head.second_sid or head.version >= 4 and head.third_sid:
        raise Refusal("multi-SID", "a second or third SID")
    if not head.play_address:
        raise Refusal("interrupt", "play address 0")
    if (trigger := cadence_info(raw, subtune + 1)["trigger"]) not in TRIGGERS:
        raise Refusal("digi", f"the play source is {trigger}")
    fmt = find_format(raw)
    swm = fmt.load(raw, subtune)
    if swm.sid_amount != 1:
        raise Refusal("multi-SID", f"{swm.sid_amount} SIDs")
    try:
        player = fmt.spec_player(swm, notes=True)
        lay = binding(player.view(), player.entry)
    except (ValueError, SidParseError) as exc:
        kind = "partly relocated" if "relocated" in str(exc) else "player"
        raise Refusal(kind, str(exc)) from exc
    clock = NTSC_CLOCK_HZ if head.is_ntsc else PAL_CLOCK_HZ
    names = (head.name, head.author, head.released)
    regs = tuple(player.regs)
    return Core(swm, lay, player.memory, regs, clock, names, subtune, raw)


def row_lengths(core: Core, voice: int, rows: int) -> list:
    """The first ``rows`` row lengths of voice ``voice`` in calls: DOTRACK's clock stepped over the
    tempo program its ``tmppos`` selects."""
    table = core.mem[core.lay["tempotbl"] :][:256]
    count, pos, base = (core.var(n, voice) for n in ("spdcnt", "tmppos", "tmpbase"))
    out, run = [], 0
    while len(out) < rows + 1 and run < 1 << 16:
        nxt = (pos + 1) & 255
        diff = (count - table[nxt]) & 255
        if diff == 0:
            count, pos = 0, nxt
        elif (count ^ table[nxt]) & (count ^ diff) & 128:
            count, pos = 0, base
        if count == 0 and run:
            out.append(run)
            run = 0
        count, run = (count + 1) & 255, run + 1
    return out[:rows]


def cadence(lengths: list) -> int | tuple:
    """The step length of a voice's rows: one length, or the two alternating; a Refusal for a
    longer tempo program."""
    for period in (1, 2):
        if all(lengths[i] == lengths[i % period] for i in range(len(lengths))):
            return lengths[0] if period == 1 or lengths[0] == lengths[1] else tuple(lengths[:2])
    raise Refusal("tempo program", f"row lengths {lengths[:6]}")


UNSUPPORTED = (
    "allghostregs fastspeedbind hrender_inline mulply sfx swp play_gate echo_support "
    "restart_trdelay subtune_restarts bigfx_v10 vibslidealways filteralways pulsealways "
    "sound_tables_first vibamfx_amp_only first_frame_fixed_waveform portavibra"
).split()
REQUIRED = "hardrestypes frame1switch tempoprgsupp arpspeedsupp multispeedsupp vibratotypes".split()


def refuse_build(lay: dict) -> None:
    """A Refusal naming the first release difference of the build this lift does not state."""
    for name in UNSUPPORTED:
        if lay.get(name):
            raise Refusal("build", name)
    for name in REQUIRED:
        if not lay.get(name):
            raise Refusal("build", f"no {name}")


class Ctx(NamedTuple):
    """What a voice carries between rows: its instrument, whether a portamento note effect awaits
    its note, the instrument its next event restores after a first note, and whether a note has
    started."""

    cur: int = 0
    pend: bool = False
    restore: int = 0
    begun: bool = False


class Step(NamedTuple):
    """What a row does to its voice: the instrument it selects and plays, its note column, and
    whether the note starts, slides to its pitch, is the voice's first or keeps the filter."""

    select: int
    cur: int
    ins: object
    note: object
    started: bool
    legato: bool
    first: bool
    tied: bool


def _plus(step: int) -> Expr:
    return Expr(canonical(f"v + {step}"))


def plays(core: Core, voice: int) -> tuple:  # pylint: disable=too-many-branches
    """``(units, loop)`` of a voice's orderlist: the ``(pattern, transpose)`` each step starts,
    and the unit index the orderlist loops to (None where it ends). A Refusal for an entry this
    lift does not state."""
    entries = core.swm.sequences[voice]
    at = {}
    offset = 0
    for index, entry in enumerate(entries):
        at[offset] = index
        offset += len(entry.encode())
    units, seen, pos, trans = [], {}, 0, 0
    while len(units) < 4096:
        state = (pos, trans)
        if state in seen:
            return units, seen[state]
        seen[state] = len(units)
        if pos >= len(entries):
            raise Refusal("orderlist", "runs past its end")
        entry = entries[pos]
        pos += 1
        if isinstance(entry, PlayPattern):
            if not 0 < entry.pattern <= len(core.swm.patterns):
                raise Refusal("orderlist", f"pattern {entry.pattern}")
            units.append((entry.pattern, trans))
        elif isinstance(entry, Transpose):
            trans = entry.semitones
        elif isinstance(entry, RawSequenceByte) and core.lay["seq_tempo_capped"]:
            if not 0xF0 <= entry.byte <= 0xFD:
                raise Refusal("orderlist", f"byte {entry.byte:#x}")
        elif isinstance(entry, End):
            return units, None
        elif isinstance(entry, Loop):
            if pos > 1 and not isinstance(entries[pos - 2], (PlayPattern, Loop)):
                raise Refusal("orderlist", "an effect before a loop")
            if entry.position not in at:
                raise Refusal("orderlist", f"loop to byte {entry.position}")
            pos = at[entry.position]
        else:
            raise Refusal("orderlist", type(entry).__name__)
    raise Refusal("orderlist", "does not loop")


NOTE_HOLDS = ("note-2", "note-1", "note", "legato")
WAVE_FX = {
    "SyncOn": "v | 2",
    "SyncOff": "v & 253",
    "RingOn": "v | 4",
    "RingOff": "v & 251",
}
GATE_OFF, GATE_ON = "gate off", "gate on"


class Lift:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """One core's document."""

    def __init__(self, core: Core, firsts: dict | None = None):
        refuse_build(core.lay)
        self.firsts, self.booted, self.voice_now = firsts or {}, set(), 0
        self.core, self.lay, self.swm = core, core.lay, core.swm
        self.names = Names()
        self.entered, self.defined, self.rows = {}, {}, []
        self.tempos = [cadence(row_lengths(core, v, 16)) for v in range(VOICES)]
        self.pwkt = any(
            r.keytrack
            for i in core.swm.instruments
            for r in i.pw_rows
            if not isinstance(r, T.TableJump)
        )
        self.pwkey = "walk.pw" if self.pwkt else "pw"
        self.slowest = max(max(t) if isinstance(t, tuple) else t for t in self.tempos)
        if len(set(self.tempos)) > 1:
            raise Refusal("tempo program", "the voices run different tempos")
        self.filtered = False
        self.calls = {}
        self.code = swcode.assembled(core.lay["exp_end"])
        self.carry = bool(core.lay["detunesupport"])
        self.slides = False

    def call(self, name: str, *args) -> Call:
        """The call of template ``name`` with ``args`` in its signature's order."""
        self.calls[name] = None
        return Call(name, tuple(args))

    # ---- instruments ------------------------------------------------------------------------

    def ins(self, n: int):
        """The typed instrument ``n``, a Refusal where the module has no such one."""
        if not 0 < n <= len(self.swm.instruments):
            raise Refusal("instrument", f"{n} of {len(self.swm.instruments)}")
        return self.swm.instruments[n - 1]

    @staticmethod
    def row_of(rows: list, target: int, base: int, own: int = -1):
        """The index of the table row at instrument offset ``target`` of rows starting at
        ``base``; a Refusal where it falls between rows or on a jump row but ``own``."""
        index, rest = divmod(target - base, 3)
        if rest or index < 0:
            raise Refusal("table jump", f"offset {target} is no row")
        if index != own and index < len(rows) and isinstance(rows[index], T.TableJump):
            raise Refusal("table jump", "onto a jump row")
        return index

    def chord(self, number: int):
        """The chord ``number`` of the module, a Refusal where there is none."""
        chords = self.swm.chords
        if not 0 < number <= len(chords):
            raise Refusal("chord", f"chord {number} of {len(chords)}")
        return chords[number - 1]

    def wave_rows(  # pylint: disable=too-many-locals
        self, ins, start: int = 0, number: int = 0
    ) -> tuple:
        """``(program rows, loop)`` of the waveform table of ``ins`` from row ``start`` with
        chord ``number`` (the instrument's by default): a row lasts the arpeggio speed's calls, a
        repeat row its count's, and a chord row one call for each interval."""
        rows, speed = ins.wf_rows, (ins.arp_speed & 63) + 1
        out, seen, i, jumped = [], {}, start, False
        while i < len(rows):
            if (i, jumped) in seen:
                return out, seen[i, jumped]
            row = rows[i]
            if isinstance(row, T.TableJump):
                if row.target & 128:
                    break
                i, jumped = self.row_of(rows, row.target, 0x10), True
                continue
            seen[i, jumped] = len(out)
            repeat = isinstance(row, T.RepeatRow)
            wave = row.waveform if not repeat else row.count if jumped else None
            keys = {} if wave is None else {"wf.ctrl": Once(self.call("wv", wave))}
            hold = row.count + 1 if repeat and not jumped else speed
            hold = {"hold": hold} if hold != 1 else {}
            if isinstance(row.pitch, T.ChordPitch):
                if row.detune != 0:
                    raise Refusal("detune", "a chord row loads a detune")
                chord = self.chord(number or ins.default_chord)
                for interval in chord.intervals:
                    shift = T.RelativePitch(interval & 255)
                    out.append(Row(keys | self.pitch(shift) | hold))
                if chord.loop:
                    return out, seen[i, jumped]
            else:
                if row.detune:
                    raise Refusal("detune", "a waveform table row sets a detune")
                out.append(Row(keys | self.pitch(row.pitch) | hold))
            i, jumped = i + 1, False
        if not self.carry:
            return out, None
        out.append(self.stopped(speed))
        return out, len(out) - 1

    def pitch(self, pitch) -> dict:
        """The row keys of a waveform table row's pitch column."""
        match pitch:
            case T.RelativePitch():
                load = self.call("ldr", pitch & 255) if self.carry else self.call("ld", 0)
                return {"note": Signed(pitch.semitones), "wave.freq": Once(load)}
            case T.AbsolutePitch():
                return {"note": note_name(pitch.note - 1), "wave.freq": Once(self.call("ld", 0))}
            case T.KeepPitch():
                return self.carried()
        raise Refusal("chord", "a waveform table row plays a chord")

    def carried(self) -> dict:
        """The row keys of a step that leaves the pitch with the carry set."""
        return {"wave.freq": Once(self.call("kp"))} if self.carry else {}

    def stopped(self, speed: int) -> Row:
        """The row a stopped waveform table repeats, a step every ``speed`` calls."""
        return Row(self.carried() | ({"hold": speed} if speed != 1 else {}))

    @staticmethod
    def entered_at(out: list, loop) -> tuple:
        """``(rows, loop)`` of a program that applies its first row a call after it starts: its
        own wait, or where the loop returns to it a copy of the row without it ends the rows."""
        if not out:
            return out, loop
        first = Row({**dict(out[0].keys), "wait": 1})
        if loop != 0:
            return [first, *out[1:]], loop
        return [first, *out[1:], out[0]], 1

    def wave(self, n: int, start: int = 0, number: int = 0, wait: int = 1) -> str:
        """The ``ctrl`` program name of instrument ``n``'s waveform table from row ``start`` with
        chord ``number`` (the instrument's by default), whose first row applies ``wait`` calls
        after it starts."""
        ins = self.ins(n)
        out, loop = self.wave_rows(ins, start, number)
        out, loop = self.entered_at(out, loop) if wait else (out, loop)
        origin = invariants.Origin(("wave", n, start, number, wait))
        return self.names.name("ctrl", "c", Program(out, loop), origin)

    def pulse_rows(self, ins, start: int = 0, entry: bool = True) -> tuple:
        """``(program rows, loop)`` of the pulse table of ``ins`` from row ``start``; ``entry``
        where the cursor comes to it with the sweep count the last table left, which a sweep may
        not use."""
        rows, key = ins.pw_rows, self.pwkey
        base = 0x10 + len(ins.wf_table) + 1
        out, seen, i = [], {}, start
        while i < len(rows):
            if i in seen:
                return out, seen[i]
            row = rows[i]
            if isinstance(row, T.TableJump):
                j = self.row_of(rows, row.target, base, i)
                if j == i or j >= len(rows):
                    break
                if isinstance(rows[j], T.PulseSweep):
                    out.append(Row({}))
                i, entry = j, False
                continue
            seen[i] = len(out)
            track = self.pulse_track(row.keytrack)
            if isinstance(row, T.PulseSet):
                out.append(Row({key: (row.high << 8 | row.low) & 0xFFF} | track))
            else:
                if entry:
                    raise Refusal("pulse sweep", "a pulse table starts on a sweep")
                if row.frames:
                    out.append(Row({"hold": row.frames, key: _plus(row.step)}))
                out.append(Row({key: NONE} | track))
            entry = False
            i += 1
        return out, None

    def pulse_track(self, column: int) -> dict:
        """The row key setting the keyboard track of a pulse table row's column."""
        if not self.pwkt:
            return {}
        return {"set.pw": Once(self.call("pkset", column))}

    def pulse(self, n: int, start: int = 0, fresh: bool = False) -> str:
        """The ``pw`` program name of instrument ``n``'s pulse table from row ``start``; ``fresh``
        where the table position is set with the sweep count reset, as a note's start does."""
        ins = self.ins(n)
        out, loop = self.pulse_rows(ins, start, not fresh)
        if not out or self.pwkey not in dict(out[0].keys):
            out = [Row({self.pwkey: NONE}), *out[1:]] if out else [Row({self.pwkey: NONE})]
        origin = invariants.Origin(("pulse", n, start, fresh))
        return self.names.name("pw", "pw", Program(out, loop), origin)

    def filter_rows(self, ins, start: int = 0, entry: bool = True) -> tuple:
        """``(program rows, loop)`` of the filter table of ``ins`` from row ``start``; ``entry``
        where the cursor comes to it with the sweep count the last table left."""
        rows = ins.filter_rows
        base = 0x10 + len(ins.wf_table) + len(ins.pw_table) + 2
        out, seen, i = [], {}, start
        while i < len(rows):
            if i in seen:
                return out, seen[i]
            row = rows[i]
            if isinstance(row, T.TableJump):
                j = self.row_of(rows, row.target, base, i)
                if j == i or j >= len(rows):
                    break
                if isinstance(rows[j], T.FilterSweep):
                    out.append(Row({}))
                i, entry = j, False
                continue
            route = self.track(row.keytrack)
            seen[i] = len(out)
            if isinstance(row, T.FilterSet):
                keys = {"mode": row.band, "res": row.resonance, "cut": row.cutoff << 3} | route
                out.append(Row(keys))
            else:
                if entry:
                    raise Refusal("filter sweep", "a filter table starts on a sweep")
                if row.frames:
                    out.append(Row({"hold": row.frames, "cut": _plus(row.step)}))
                out.append(Row({"cut": NONE} | route))
            entry = False
            i += 1
        return out, None

    def track(self, column: int) -> dict:
        """The row keys of a filter row's keyboard track column: the route switches of ``$80``
        to ``$8F``, nothing for 0; a Refusal for a track."""
        if column == 0:
            return {}
        if self.lay["kbtrack_fswitch"] and 0x80 <= column < 0x90:
            return {"route": column & 15}
        raise Refusal("filter keytrack", "a filter table row tracks the keyboard")

    def filter(self, n: int, start: int = 0, fresh: bool = False) -> str:
        """The ``filter`` program name of instrument ``n``'s filter table from row ``start``;
        ``fresh`` where the table position is set with the sweep count reset."""
        ins = self.ins(n)
        out, loop = self.filter_rows(ins, start, not fresh)
        if not out or "cut" not in dict(out[0].keys):
            out = [Row({"cut": NONE}), *out[1:]] if out else [Row({"cut": NONE})]
        origin = invariants.Origin(("filter", n, start, fresh))
        return self.names.name("filter", "f", Program(out, loop), origin)

    def garbage(self, voice: int, tick: int) -> tuple:
        """``(before, [(freq, pw, ctrl)] at the two restart ticks)`` of ``voice`` whose first note
        starts at call ``tick``: the player acts on its empty instrument there, whose table the
        hard restart ticks walk."""
        if not tick:
            return (0, 0, 0), [(0, 0, 0)] * 2
        if len(self.rows) < tick + 1:
            self.rows = self.core.grid(max(tick + 1, 2 * len(self.rows), 64))
        out = []
        for call in range(tick - 3, tick):
            r = self.rows[call][STRIDE * voice :] if call >= 0 else [0] * 25
            out.append((r[0] | r[1] << 8, r[2] | (r[3] & 15) << 8, r[4]))
        if any(c[2] & 1 for c in out[1:]):
            raise Refusal("start state", "the first note gates the voice on within its restart")
        return out[0], out[1:]

    def restart(  # pylint: disable=too-many-locals
        self, n: int, boot: tuple | None = None, tied: bool = False
    ) -> str:
        """The ``restart`` program name of instrument ``n``: the hard restart on the ticks its
        control byte selects, then the note's first tick; ``boot`` for a voice's first note, whose
        two restart ticks also run the player's empty instrument."""
        ins = self.ins(n)
        ctl = ins.control
        hard = {"ctrl": Once(self.call("goff")), "ad": ins.hr_attack << 4 | ins.hr_decay}
        hard["sr"] = ins.hr_sustain << 4 | ins.hr_release
        first = {"ctrl": Once(self.call("arm"))}
        first |= {"ad": ins.attack << 4 | ins.decay, "sr": ins.sustain << 4 | ins.release}
        if ctl & 4:
            hard["wf.ctrl"] = 0x18
        if ctl & 8:
            first["wf.ctrl"] = ins.first_waveform
            first["first.freq"] = Once(
                self.call("fixb" if self.lay["first_frame_freq_by_track"] else "fix")
            )
        first |= {} if tied else self.routing(ins)
        rows = []
        have, ticks = self.garbage(*boot) if boot else ((0, 0, 0), [])
        for tick in (0, 1):
            keys = dict(hard) if ctl & (2 >> tick) else {}
            if boot and not (keys and ctl & 4):
                freq, pw, ctrl = ticks[tick]
                keys |= {"boot.freq": Once(_plus(freq - have[0]))} if freq != have[0] else {}
                keys |= {"pw": pw} if pw != have[1] else {}
                keys |= {"wf.ctrl": ctrl} if ctrl != have[2] else {}
                have = (freq, pw, ctrl)
            rows.append(Row(keys))
        program = Program([*rows, Row({"hold": 0}), Row(first)])
        origin = invariants.Origin(("ins", n), (boot or ()) + ("tied",) * tied)
        return self.names.name("restart", "r", program, origin)

    def routing(self, ins) -> dict:
        """The row keys routing the voice through the filter, or not, as the instrument's filter
        table does: any first row but the end routes it, the end also stops the filter program
        the voice controls in a build that rewinds it."""
        if ins.filter_rows:
            return {"route_on": 0}
        return {"route_off": 0} | ({"cut": HOLD} if self.lay["filter_stop_rewinds"] else {})

    @staticmethod
    def controls(ins) -> bool:
        """Whether the instrument's note starts the filter program of its table. A table that
        filters the voice without control leaves the program running on the tables of this
        instrument, which the lift does not state."""
        rows = ins.filter_rows
        if rows and isinstance(rows[0], T.FilterSweep) and rows[0].frames == 0:
            raise Refusal("filter without control", "a filter table that takes no control")
        return bool(rows)

    def vibrato(self, ins, freq: int, amp: int, delay: bool = False) -> Call:
        """The call setting ``ins``'s vibrato of period ``freq`` and amplitude ``amp``: its
        delay too where ``delay``, a note's start."""
        kind = ins.control & 0x30
        count = freq if kind >= 0x20 else freq >> 1
        args = (kind, freq << 1, 0 if kind == 0x30 else count, amp * 4)
        self.call("tick")
        if delay:
            return self.call("vset", ins.vibrato_delay, *args)
        return self.call("vibr", *args)

    def slide(self, kind: int, value: int) -> Call:
        """The call starting the slide or portamento ``kind`` of the row's ``value``."""
        self.call("tick")
        return self.call("sld", kind, (value >> 1) + (value & 1))

    @staticmethod
    def small(ins, effect, cmds: dict) -> None:
        """Adds the commands of small effect ``effect`` of instrument ``ins`` to ``cmds``; the
        nibble effects set a nibble of the instrument's byte."""
        n, v = effect.effect, effect.value
        if ins is None and n != 0xA:
            raise Refusal("pattern effect", "a small effect before any instrument")
        ad, sr = ins.attack << 4 | ins.decay, ins.sustain << 4 | ins.release
        match n:
            case 2:
                cmds["ad"] = v << 4 | ad & 15
            case 3:
                cmds["ad"] = ad & 0xF0 | v
            case 5:
                cmds["sr"] = v << 4 | sr & 15
            case 6:
                cmds["sr"] = sr & 0xF0 | v
            case 7:
                cmds["_chord"] = v
            case 0xA:
                cmds["vol"] = v
            case 8:
                pass
            case _:
                raise Refusal("pattern effect", f"small effect {int(effect):#x}")

    def big(self, n: int, value: int, cmds: dict) -> None:
        """Adds the commands of big effect ``n`` of ``value`` to ``cmds``."""
        if n in (1, 2, 3, 8, 9, 0xA, 0xB):
            return
        if n == 7:
            cmds["_chord"] = value
        elif n in (5, 6):
            cmds["ad" if n == 5 else "sr"] = value
        elif n == 0x10 and value & 0x7F >= 3:
            cmds["tempo"] = value & 0x7F
            self.slowest = max(self.slowest, value & 0x7F)
        elif n == 0x11 and min(value >> 4, value & 15) >= 3:
            cmds["tempo"] = (value >> 4, value & 15)
            self.slowest = max(self.slowest, value >> 4, value & 15)
        else:
            raise Refusal("pattern effect", f"big effect {n:#x}")

    def step(self, row, ctx: Ctx) -> Step:
        """The :class:`Step` of ``row`` in ``ctx``; a Refusal for a row that selects an instrument
        but starts no note, which leaves running tables to read the new instrument's."""
        column, fx = row.instrument, row.fx
        select = int(column) if isinstance(column, T.SelectInstrument) else 0
        cur = select or ctx.cur
        ins = self.ins(cur) if cur else None
        note = row.note if ins is not None or isinstance(row.note, T.Note) else None
        started = isinstance(note, T.Note)
        if started and ins is None:
            raise Refusal("note without instrument", "a note before any instrument")
        tone = isinstance(fx, T.BigEffect) and fx == 3
        legato = started and (tone or ctx.pend or isinstance(column, T.Legato))
        if select and ctx.begun and not (started and not legato):
            raise Refusal("instrument change", "an instrument selected without a note start")
        first = started and not legato and not ctx.begun
        tied = started and not (legato or select) and bool(ins.control & 0x80)
        return Step(select, cur, ins, note, started, legato, first, tied)

    def effects(self, row, k: Step) -> tuple:
        """``(commands, frequency call)`` of a row's effects, the note column's first."""
        column, fx, value = row.instrument, row.fx, row.fx_value or 0
        cmds, calls = {}, []
        if isinstance(k.note, T.VibratoAmplitude):
            calls.append(self.vibrato(k.ins, k.ins.vibrato & 15, k.note.amplitude))
        if isinstance(column, T.SmallEffect):
            self.small(k.ins, column, cmds)
        if isinstance(fx, T.SmallEffect):
            self.small(k.ins, fx, cmds)
            if fx.effect == 8:
                calls.append(self.vibrato(k.ins, k.ins.vibrato & 15, fx.value))
        if isinstance(fx, T.BigEffect):
            self.big(int(fx), value, cmds)
            self.jump(k, row, cmds)
            if fx in (1, 2, 3):
                calls.append(self.slide(0x80 + int(fx), value))
            if fx == 8:
                calls.append(self.vibrato(k.ins, value & 15, value >> 4))
        if k.note.__class__.__name__ in WAVE_FX:
            cmds["ctrl"] = Once(Expr(WAVE_FX[k.note.__class__.__name__]))
        if len(calls) > 1:
            raise Refusal("pattern effect", "two effects on the frequency in one row")
        return cmds, (calls or [None])[0]

    def position(self, ins, offset: int, base: int, rows: int) -> int:
        """The row of a table of ``rows`` rows from instrument offset ``base`` at ``offset``; a
        Refusal where the cursor can land on no row or the arpeggio speed puts the next step off
        the next call."""
        if ins.arp_speed & 63:
            raise Refusal("table position", "a table position set between steps of an arpeggio")
        index, rest = divmod(offset - base, 3)
        if rest or not 0 <= index < rows:
            raise Refusal("table position", f"offset {offset} is no row")
        return index

    def jump(self, k: Step, row, cmds: dict) -> None:
        """Adds to ``cmds`` the program starts of the table positions a row sets."""
        fx, ins = row.fx, k.ins
        if not (isinstance(fx, T.BigEffect) and fx in (9, 0xA, 0xB)):
            return
        if ins is None or fx == 9 and self.lay["bigfx_v10"]:
            raise Refusal("table position", "a table position set before any instrument")
        runs, value, wf, pw = (
            cmds.setdefault("runs", []),
            row.fx_value or 0,
            ins.wf_table,
            ins.pw_table,
        )
        if fx == 9:
            at = self.position(ins, 0x10 + 3 * value, 0x10, len(ins.wf_rows))
            runs.append(self.wave(k.cur, at, 0, int(k.note is not None)))
            return
        if not self.lay["table_fx_resets"]:
            raise Refusal("table position", "a table position set without its sweep count")
        base = 0x10 + len(wf) + 1 + (len(pw) + 1) * (fx == 0x0B)
        rows = ins.pw_rows if fx == 0x0A else ins.filter_rows
        at = self.position(ins, base + 3 * value, base, len(rows))
        runs.append(self.pulse(k.cur, at, True) if fx == 0x0A else self.filter(k.cur, at, True))

    def gate(self, k: Step, cmds: dict) -> None:
        """Adds to ``cmds`` what a gate note effect does: the gate, or the instrument's gate off
        table positions where it has them."""
        runs, ins = cmds.setdefault("runs", []), k.ins
        if isinstance(k.note, T.GateOn):
            cmds["ctrl"] = Once(self.call("gon"))
            return
        if ins is None:
            return
        pointers = self.lay["gateoffptr"]
        if ins.gateoff_filt and pointers:
            raise Refusal("gate off pointer", "a gate off position of the filter table")
        if ins.gateoff_wf and pointers:
            at = self.position(ins, ins.gateoff_wf, 0x10, len(ins.wf_rows))
            runs.append(self.wave(k.cur, at, 0, 0))
        else:
            cmds["ctrl"] = Once(self.call("goff"))
        if ins.gateoff_pw and pointers:
            base = 0x10 + len(ins.wf_table) + 1
            at = self.position(ins, ins.gateoff_pw, base, len(ins.pw_rows))
            runs.append(self.pulse(k.cur, at))

    def names_of(self, k: Step, ctx: Ctx) -> str | None:
        """The name of the instrument an event plays, None where the voice's last one stands: a
        selected one, a voice's first note's copy and a note that keeps the filter table's."""
        boot = (self.voice_now, self.firsts.get(self.voice_now, 0)) if k.first else None
        self.booted |= {self.voice_now} if k.first else set()
        if k.select or k.first or k.tied:
            return self.define(k.cur, boot, k.tied)
        return self.define(ctx.restore) if ctx.restore else None

    def event(self, row, ctx: Ctx) -> tuple:
        """``(event, context after)`` of a pattern row played in ``ctx``."""
        k = self.step(row, ctx)
        cmds, sets = self.effects(row, k)
        porta = isinstance(k.note, T.Portamento) or k.note.__class__ is T.NoteEffect
        if k.legato and sets is None:
            sets = self.call(
                "leg" if isinstance(row.instrument, T.Legato) and not ctx.pend else "tone"
            )
        if porta:
            sets = self.slide(0xFF, 110)
        keep, atom = {}, "-"
        if k.started:
            atom = note_name(int(k.note) - 1 + k.ins.octave_shift)
            if not (k.legato or k.select):
                keep = {
                    "keep_pw": bool(k.ins.control & 0x40),
                    "keep_filter": bool(k.ins.control & 0x80),
                }
        elif isinstance(k.note, T.GateOff | T.GateOn):
            self.gate(k, cmds)
        elif k.note is not None and not (porta or isinstance(k.note, T.VibratoAmplitude)):
            if k.note.__class__.__name__ not in WAVE_FX:
                raise Refusal("note column", type(k.note).__name__)
        if "_chord" in cmds:
            if not k.started or k.legato:
                raise Refusal("pattern effect", "a chord change but at a note start")
            cmds.setdefault("runs", []).append(self.wave(k.cur, 0, cmds.pop("_chord")))
        if sets is not None:
            self.call("tick")
            cmds["eff.freq"] = Once(sets)
        cmds |= {"legato": True} if k.legato else {}
        name = self.names_of(k, ctx)
        runs = cmds.pop("runs", [])
        event = Event(atom, name, [*cmds.items(), *[("run", r) for r in runs]], **keep)
        pend = porta or (ctx.pend and sets is None and not k.started)
        return event, Ctx(k.cur, pend, k.cur if k.first else 0, ctx.begun or k.started)

    def events(self, rows, ctx: Ctx) -> tuple:
        """``(events, context after)`` of pattern ``rows`` played in ``ctx``."""
        events = []
        for row in rows:
            event, ctx = self.event(row, ctx)
            events.append(event)
        return tuple(events), ctx

    def define(self, n: int, boot: tuple | None = None, tied: bool = False) -> str:
        """The name of instrument ``n``, defined on first use; ``boot`` for the copy a voice's
        first note starts, whose restart runs the player's empty instrument too."""
        if (n, boot, tied) in self.defined:
            return self.defined[n, boot, tied]
        ins = self.ins(n)
        fields = {"ctrl": self.wave(n), "restart": self.restart(n, boot, tied)}
        fields["set.freq"] = Once(self.vibrato(ins, ins.vibrato & 15, ins.vibrato >> 4, True))
        fields["pw"] = self.pulse(n)
        if self.controls(ins):
            fields["filter"] = self.filter(n)
        origin = invariants.Origin(("ins", n), (boot or ()) + ("tied",) * tied)
        name = self.names.name("ins", "i", Instrument(fields), origin)
        self.defined[n, boot, tied] = name
        return name

    # ---- orderlists -------------------------------------------------------------------------

    def segments(self, units: list, ctx: tuple) -> tuple:
        """``([(unit, events, context entered in)], context after)`` of ``units`` played from
        ``ctx``."""
        out = []
        for num, trans in units:
            events, after = self.events(self.swm.patterns[num - 1].rows, ctx)
            out.append(((num, trans), events, ctx))
            ctx = after
        return out, ctx

    def voice(self, v: int) -> Voice:
        """Voice ``v``'s orderlist: its phrases in order, the loop body played a second time when
        entering it in another context changes what it plays."""
        units, loop = plays(self.core, v)
        self.voice_now = v
        walks, ctx = self.segments(units, Ctx())
        if loop is not None:
            again, _ = self.segments(units[loop:], ctx)
            if [w[1] for w in again] != [w[1] for w in walks[loop:]]:
                units, walks, loop = units + units[loop:], walks + again, len(units)
        items = [Play("")]
        starts = []
        for index, ((num, trans), events, ctx) in enumerate(walks):
            starts.append(len(items))
            if index == 0 and v == 0:
                events = (self.timed(events[0]), *events[1:])
            name = self.names.name("phrase", "p", tuple(rests(list(events))))
            self.entered.setdefault(name, (num, (*ctx, *(["tempo"] * (index == 0 and v == 0)))))
            items.append(Play(name, Signed(trans) if trans else None))
        return Voice(tuple(items), None if loop is None else starts[loop])

    def timed(self, event: Event) -> Event:
        """``event`` with the tempo of the tune, unless it sets its own."""
        commands = {"tempo": self.tempos[0]} | dict(event.commands)
        return Event(event.note, event.ins, commands, event.keep_pw, event.keep_filter)

    # ---- the document -----------------------------------------------------------------------

    def tables(self, used: tuple) -> tuple:
        """The tables the routines in ``used`` read: the vibrato steps ``V``, the keyboard track
        tables ``E`` and ``M``."""
        mem, lay = self.core.mem, self.lay
        want = {x for n in used for x in asm.symbols(self.code[n])}
        found = {"V": self.vtable}
        found["E"] = lambda: tuple(mem[lay["exptabh"] : lay["exptabh"] + 256])
        found["M"] = lambda: tuple(mem[lay["exptabh_m1"] : lay["exptabh_m1"] + 256])
        return tuple((k, f()) for k, f in found.items() if f"{k}.lo" in want)

    def vtable(self) -> tuple:
        """The vibrato step table ``V``: the player's fine steps, then its note steps."""
        lay, mem = self.lay, self.core.mem
        top, low = lay["exp_end"], lay["exp_threshold"]
        steps = [mem[lay["exptbase"] + y] if y < low else 0 for y in range(top + 1)]
        for y in range(low, top + 1):
            steps[y] = mem[lay["vib_hi"] + y] << 8 | mem[lay["vib_lo"] + y]
        return tuple(steps)

    def header(self, used: tuple) -> Header:
        """The header lines given the templates ``used``."""
        core, regs = self.core, self.header_regs()
        name, author, released = (meta_text(s) for s in core.head)
        quiet = ("note", "legato")
        freq = [Slot("boot"), Slot("set"), Slot("first"), Slot("eff")]
        freq += [Slot("un", Call("un"), hold=quiet)] if self.carry else []
        freq += [Slot("tick", Call("tick"), hold=NOTE_HOLDS)] if "tick" in used else []
        freq += [Slot("wave")]
        freq += [Slot("out", Call("out"), hold=quiet)] if self.carry else []
        slots = {"pw": (Slot("pw", hold=NOTE_HOLDS),), "cut": (Slot("cut", hold=NOTE_HOLDS),)}
        if self.pwkt:
            slots["pw"] = (
                Slot("un", Call("pun"), hold=NOTE_HOLDS),
                Slot("walk", hold=NOTE_HOLDS),
                Slot("set"),
                Slot("out", Call("pout"), hold=NOTE_HOLDS),
            )
        slots["ctrl"] = (Slot("fx"), Slot("wf"))
        states = []
        for cell, group, variables in (
            ("freq", swcode.routines(0), swcode.STATE),
            ("pw", swcode.PW, swcode.PW_STATE),
            ("ctrl", swcode.CTRL, swcode.CTRL_STATE),
        ):
            names = {x for n in used if n in group for x in asm.symbols(self.code[n])}
            states += [(cell, tuple((k, n) for k, n in variables if k in names))] * bool(names)
        return Header(
            (("title", name), ("author", author), ("released", released)),
            clock=(CYCLES[core.clock], core.clock),
            slots=slots | {"freq": tuple(freq)},
            holds={"pw": NOTE_HOLDS, "filter": NOTE_HOLDS, "ctrl": ("legato",)},
            state=tuple(states) or None,
            regs=tuple(regs) if any(regs) else None,
        )

    def header_regs(self) -> tuple:
        """The registers before the first call, without the bits the SID lacks."""
        regs = list(self.core.regs)
        for reg in (3, 10, 17):
            regs[reg] &= 15
        regs[21] &= 7
        return tuple(regs)

    def seeds(self, voice: int) -> dict:
        """The init commands loading the high byte a first frame writes where the build reads it
        by voice."""
        if not self.lay["first_frame_freq_by_track"]:
            return {}
        high = frequency(STRIDE * voice - 1, self.core.clock) >> 8
        return {"set.freq": Once(self.call("seed", high))}

    def document(self) -> Document:
        """The document of the subtune."""
        voices = [self.voice(v) for v in range(VOICES)]
        intro = self.names.name("phrase", "p", (Event("-"),))
        voices = [Voice((Play(intro), *v.play[1:]), v.loop) for v in voices]
        kinds = ("restart", "ins", "ctrl", "pw", "filter", "phrase")
        defs = [Section(k, tuple(self.names.section(k))) for k in kinds]
        init = [
            Event("-", None, ({"tempo": PRE} if v == 0 else {}) | self.seeds(v))
            for v in range(VOICES)
        ]
        for name in ("un", "out") * self.carry + ("pun", "pout") * self.pwkt:
            self.call(name)
        used = tuple(n for n in self.code if n in self.calls)
        mods = tuple((signature(n, asm.params(self.code[n])), Code(self.code[n])) for n in used)
        defs = [d for d in defs if d.records]
        return Document(
            self.header(used), self.tables(used), mods, defs, tuple(init), tuple(voices), None
        )

    def origins(self) -> dict:
        """Each named object's :class:`~unitracker.lift.invariants.Origin`."""
        out = {k: dict(v) for k, v in self.names.origins.items() if k != "phrase"}
        out["instruments"] = out.pop("ins", {})
        phrases = self.names.kinds.get("phrase", {})
        natives = {}
        out["phrases"] = {}
        for name in phrases:
            if name in self.entered:
                natives.setdefault(self.entered[name][0], []).append(name)
            else:
                out["phrases"][name] = invariants.Origin(("intro",))
        for num, names in natives.items():
            for name in names:
                context = (self.entered[name][1],) if len(names) > 1 else ()
                out["phrases"][name] = invariants.Origin(("pattern", num), context)
        return out

    def span(self) -> int:
        """The calls after which every voice's orderlist has played through and looped once
        more, at the slowest tempo of the tune."""
        rows = 0
        for v in range(VOICES):
            units, loop = plays(self.core, v)
            count = [len(self.swm.patterns[n - 1].rows) for n, _ in units]
            rows = max(rows, sum(count) + sum(count[loop or 0 :]) * (loop is not None))
        return rows * self.slowest

    def song(self) -> tuple:
        """The play entries each voice's orderlist may hold: its steps, the loop body once more
        and the intro."""
        out = []
        for v in range(VOICES):
            units, loop = plays(self.core, v)
            out.append(len(units) + (len(units) - loop if loop is not None else 0) + 1)
        return tuple(out)


def first_notes(text: str, voices: set) -> dict:
    """``{voice: call}`` of the first note each of ``voices`` starts in the document ``text``."""
    found = {}

    def observe(kind, *args):
        if kind == "event" and args[0] in voices and isinstance(args[1].note, int):
            found.setdefault(args[0], player.call)

    player = Player(load_song(text), observe)
    while len(found) < len(voices) and player.call < LIMIT and player.step() is not None:
        pass
    return found


def document_of(core: Core) -> tuple:
    """``(text, lift)``: the document of ``core``, printed, and the lift that built it. The
    player's empty instrument acts on each voice's first note, so the document is built twice:
    once to find when those notes start, once with what the player does then."""
    built = Lift(core)
    text = printed(built.document())
    if built.booted:
        built = Lift(core, first_notes(text, built.booted))
        text = printed(built.document())
    return text, built


def held(text: str, built: Lift) -> dict:
    """The objects of ``text`` per kind, held to their origins and to the native song entries."""
    objects = invariants.provenance(text, built.origins())
    invariants.check(text, {"song": built.song()})
    return objects


def reference(
    raw: bytes, subtune: int = 0, frames: int = CHECK, notes: bool = True, both: bool = False
):
    """The register grid of the spec player over ``frames`` frames; with ``both``, ``(grid,
    edges)`` (:func:`~unitracker.lift.shared.spec_played`)."""
    fmt = find_format(raw)
    player = fmt.spec_player(fmt.load(raw, subtune), notes=notes or None)
    return spec_played(player, frames) if both else np.asarray(player.render_grid(frames), np.uint8)


def verify(raw: bytes, subtune: int = 0, frames: int = CHECK) -> tuple:
    """``(cells, text)`` of a SID: the cells where its document differs from the reference over
    ``frames`` frames, or the song and its loop once more where that is longer."""
    core = core_of_sid(raw, subtune)
    text, built = document_of(core)
    frames = max(frames, built.span())
    job = Reference(lambda n: reference(raw, subtune, n, both=True), frames)
    try:
        held(text, built)
        return cells_of(text, frames, reference=job), text
    finally:
        job.close()


def report(raw: bytes, subtune: int = 0) -> dict:
    """The size of a SID's document: per kind the ``(objects, native objects)`` it lifts, its
    ``fixed`` and ``song`` bytes, and the ``native_bytes`` of the tune's data."""
    text, built = document_of(core_of_sid(raw, subtune))
    return {
        "counts": held(text, built),
        **invariants.sizes(text),
        "native_bytes": len(SidImage.from_sid(raw).image),
    }


def lift(raw: bytes, subtune: int = 0, frames: int | None = None) -> str:
    """The document of a SID's subtune, checked against the reference (see :func:`verify`); a
    LiftError otherwise."""
    return lifted_text(verify, raw, subtune, frames, CHECK)


def main(argv=None) -> int:
    """Lift a SID, or verify SIDs against the spec player."""
    argv = sys.argv[1:] if argv is None else argv
    return cli.main(argv, __doc__.split("\n", 1)[0], lift, verify)


if __name__ == "__main__":
    sys.exit(main())
