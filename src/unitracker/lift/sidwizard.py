"""A SID-Wizard tune as a format 2.5 document, from pysidwizard's typed module and the layout of its
bound playroutine spec; ``verify`` plays the document against the spec player with the standard
note table (docs/lift/sidwizard.md)."""

import sys
from dataclasses import dataclass

import numpy as np
from pysidtracker import NTSC_CLOCK_HZ, PAL_CLOCK_HZ, SidImage, find_format, parse_sid_header
from pysidtracker.errors import SidParseError
from pysidwizard import End, Loop, PlayPattern, RawSequenceByte, Transpose
from pysidwizard import typed as T
from pysidwizard.playspec import binding
from unitracker_grammar import code as asm
from unitracker_grammar.expr import canonical
from unitracker_player.compiler import load
from unitracker_player.player import Player
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

from unitracker.lift import cli, invariants, swcode
from unitracker.lift.shared import Names, Reference, Refusal, cells_of, lifted_text
from unitracker.lift.shared import meta_text, note_name, rests, spec_played
from pysidtracker.notes import frequency

CYCLES = {PAL_CLOCK_HZ: 19656, NTSC_CLOCK_HZ: 17095}
PRE = 2
CHECK = 20000
LIMIT = 1 << 17
VOICES = 3
STRIDE = 7
summary = cli.summary


@dataclass(frozen=True)
class Core:  # pylint: disable=too-many-instance-attributes
    """What a lift needs of a tune: the typed module, the bound spec layout, the memory and
    registers its init left, the clock and the title lines."""

    swm: object
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
    return out


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


def _plus(step: int) -> Expr:
    return Expr(canonical(f"v + {step}"))


def plays(core: Core, voice: int) -> tuple:
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
        self.firsts, self.booted = firsts or {}, set()
        self.core, self.lay, self.swm = core, core.lay, core.swm
        self.names = Names()
        self.entered, self.defined, self.takes, self.rows = {}, {}, set(), []
        self.tempos = [cadence(row_lengths(core, v, 16)) for v in range(VOICES)]
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

    def chord(self, ins, number: int):
        """The chord ``number`` of the module, a Refusal where there is none."""
        chords = self.swm.chords
        if not 0 < number <= len(chords):
            raise Refusal("chord", f"chord {number} of {len(chords)}")
        return chords[number - 1]

    def wave_rows(self, ins, start: int = 0, number: int = 0) -> tuple:
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
            keys = {} if wave is None else {"wf.ctrl": wave}
            hold = row.count + 1 if repeat and not jumped else speed
            hold = {"hold": hold} if hold != 1 else {}
            if isinstance(row.pitch, T.ChordPitch):
                if row.detune != 0:
                    raise Refusal("detune", "a chord row loads a detune")
                chord = self.chord(ins, number or ins.default_chord)
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
        """``(rows, loop)`` of a program that applies its first row a call after it starts: the
        row's own wait, or where the loop returns to it a copy of it ends the rows."""
        if not out:
            return out, loop
        first = Row({**dict(out[0].keys), "wait": 1})
        if loop != 0:
            return [first, *out[1:]], loop
        return [first, *out[1:], out[0]], 1

    def wave(self, n: int, start: int = 0, number: int = 0) -> str:
        """The ``ctrl`` program name of instrument ``n``'s waveform table from row ``start`` with
        chord ``number`` (the instrument's by default)."""
        ins = self.ins(n)
        out, loop = self.wave_rows(ins, start, number)
        out = [Row({**dict(out[0].keys), "gate": 1}), *out[1:]] if out else [Row({"gate": 1})]
        out, loop = self.entered_at(out, loop)
        origin = invariants.Origin(("wave", n, start, number))
        return self.names.name("ctrl", "c", Program(out, loop), origin)

    def pulse_rows(self, ins, start: int = 0) -> tuple:
        """``(program rows, loop)`` of the pulse table of ``ins`` from row ``start``."""
        rows = ins.pw_rows
        base = 0x10 + len(ins.wf_table) + 1
        out, seen, i, entry = [], {}, start, True
        while i < len(rows):
            if i in seen:
                return out, seen[i]
            row = rows[i]
            if isinstance(row, T.TableJump):
                j = self.row_of(rows, row.target, base, i)
                if j == i:
                    break
                if j >= len(rows):
                    break
                if isinstance(rows[j], T.PulseSweep):
                    out.append(Row({}))
                i, entry = j, False
                continue
            if row.keytrack:
                raise Refusal("pulse keytrack", "a pulse table row tracks the keyboard")
            seen[i] = len(out)
            if isinstance(row, T.PulseSet):
                out.append(Row({"pw": (row.high << 8 | row.low) & 0xFFF}))
            else:
                if entry:
                    raise Refusal("pulse sweep", "a pulse table starts on a sweep")
                if row.frames:
                    out.append(Row({"hold": row.frames, "pw": _plus(row.step)}))
                out.append(Row({"pw": NONE}))
            entry = False
            i += 1
        return out, None

    def pulse(self, n: int) -> str:
        """The ``pw`` program name of instrument ``n``'s pulse table."""
        ins = self.ins(n)
        out, loop = self.pulse_rows(ins)
        if not out or "pw" not in dict(out[0].keys):
            out = [Row({"pw": NONE}), *out[1:]] if out else [Row({"pw": NONE})]
        return self.names.name("pw", "pw", Program(out, loop), invariants.Origin(("pulse", n)))

    def filter_rows(self, ins, start: int = 0) -> tuple:
        """``(program rows, loop)`` of the filter table of ``ins`` from row ``start``."""
        rows = ins.filter_rows
        base = 0x10 + len(ins.wf_table) + len(ins.pw_table) + 2
        out, seen, i, entry = [], {}, start, True
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

    def filter(self, n: int) -> str:
        """The ``filter`` program name of instrument ``n``'s filter table."""
        self.filtered = True
        ins = self.ins(n)
        out, loop = self.filter_rows(ins)
        if not out or "cut" not in dict(out[0].keys):
            out = [Row({"cut": NONE}), *out[1:]] if out else [Row({"cut": NONE})]
        return self.names.name("filter", "f", Program(out, loop), invariants.Origin(("filter", n)))

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

    def restart(self, n: int, boot: tuple | None = None) -> str:
        """The ``restart`` program name of instrument ``n``: the hard restart on the ticks its
        control byte selects, then the note's first tick; ``boot`` for a voice's first note, whose
        two restart ticks also run the player's empty instrument."""
        ins = self.ins(n)
        ctl = ins.control
        hard = {"gate": 0, "ad": ins.hr_attack << 4 | ins.hr_decay}
        hard["sr"] = ins.hr_sustain << 4 | ins.hr_release
        first = {"ad": ins.attack << 4 | ins.decay, "sr": ins.sustain << 4 | ins.release}
        if ctl & 4:
            hard["wf.ctrl"] = 0x18
        if ctl & 8:
            first["wf.ctrl"] = ins.first_waveform
            first["first.freq"] = Once(
                self.call("fixb" if self.lay["first_frame_freq_by_track"] else "fix")
            )
        first |= self.routing(ins)
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
        return self.names.name("restart", "r", program, invariants.Origin(("ins", n), boot or ()))

    def routing(self, ins) -> dict:
        """The row keys routing the voice through the filter, or not, as the instrument's filter
        table does: any first row but the end routes it, the end also stops the filter program
        the voice controls in a build that rewinds it."""
        if ins.control & 0x80:
            raise Refusal("tied filter", "an instrument keeps the filter table across notes")
        if ins.filter_rows:
            return {"route_on": 0}
        return {"route_off": 0} | ({"cut": HOLD} if self.lay["filter_stop_rewinds"] else {})

    def controls(self, ins) -> bool:
        """Whether the instrument's note starts the filter program of its table."""
        rows = ins.filter_rows
        return bool(rows) and not (isinstance(rows[0], T.FilterSweep) and rows[0].frames == 0)

    def define(self, n: int, boot: tuple | None = None) -> str:
        """The name of instrument ``n``, defined on first use; ``boot`` for the copy a voice's
        first note starts, whose restart runs the player's empty instrument too."""
        if (n, boot) in self.defined:
            return self.defined[n, boot]
        ins = self.ins(n)
        fields = {"ctrl": self.wave(n), "restart": self.restart(n, boot)}
        fields["set.freq"] = Once(self.vibrato(ins, ins.vibrato & 15, ins.vibrato >> 4, True))
        fields["pw"] = self.pulse(n)
        if self.controls(ins):
            fields["filter"] = self.filter(n)
        origin = invariants.Origin(("ins", n), boot or ())
        name = self.names.name("ins", "i", Instrument(fields), origin)
        self.defined[n, boot] = name
        return name

    # ---- rows -------------------------------------------------------------------------------

    def gate(self, on: int) -> str:
        """The ``ctrl`` program setting the gate state ``on`` (0 or 1)."""
        origin = invariants.Origin(("gate", on))
        return self.names.name("ctrl", "c", Program([Row({"gate": on})]), origin)

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

    @staticmethod
    def big(n: int, value: int, cmds: dict) -> None:
        """Adds the commands of big effect ``n`` of ``value`` to ``cmds``."""
        if n in (1, 2, 3, 8):
            return
        if n == 7:
            cmds["_chord"] = value
        elif n in (5, 6):
            cmds["ad" if n == 5 else "sr"] = value
        elif n == 0x10 and value & 0x7F >= 3:
            cmds["tempo"] = value & 0x7F
        elif n == 0x11 and min(value >> 4, value & 15) >= 3:
            cmds["tempo"] = (value >> 4, value & 15)
        else:
            raise Refusal("pattern effect", f"big effect {n:#x}")

    def effects(self, row, note, ins) -> tuple:
        """``(commands, frequency call)`` of a row's effects, the note column's first."""
        column, fx, value = row.instrument, row.fx, row.fx_value or 0
        cmds, calls = {}, []
        if isinstance(note, T.VibratoAmplitude):
            calls.append(self.vibrato(ins, ins.vibrato & 15, note.amplitude))
        if isinstance(column, T.SmallEffect):
            self.small(ins, column, cmds)
        if isinstance(fx, T.SmallEffect):
            self.small(ins, fx, cmds)
            if fx.effect == 8:
                calls.append(self.vibrato(ins, ins.vibrato & 15, fx.value))
        if isinstance(fx, T.BigEffect):
            self.big(int(fx), value, cmds)
            if fx in (1, 2, 3):
                calls.append(self.slide(0x80 + int(fx), value))
            if fx == 8:
                calls.append(self.vibrato(ins, value & 15, value >> 4))
        if note.__class__.__name__ in WAVE_FX:
            cmds["ctrl"] = Once(Expr(WAVE_FX[note.__class__.__name__]))
        if len(calls) > 1:
            raise Refusal("pattern effect", "two effects on the frequency in one row")
        return cmds, (calls or [None])[0]

    def event(self, row, ctx: tuple) -> tuple:
        """``(event, context after)`` of a pattern row played in ``ctx``: the instrument and
        whether a portamento note effect awaits its note."""
        cur0, pend, restore, rows, begun = ctx
        column, fx = row.instrument, row.fx
        select = int(column) if isinstance(column, T.SelectInstrument) else 0
        cur = select or cur0
        ins = self.ins(cur) if cur else None
        note = row.note if ins is not None or isinstance(row.note, T.Note) else None
        started = isinstance(note, T.Note)
        if started and ins is None:
            raise Refusal("note without instrument", "a note before any instrument")
        cmds, sets = self.effects(row, note, ins)
        porta = isinstance(note, T.Portamento) or type(note) is T.NoteEffect
        tone = isinstance(fx, T.BigEffect) and fx == 3
        legato = started and (tone or pend or isinstance(column, T.Legato))
        first = started and not legato and not begun
        boot = (self.voice_now, self.firsts.get(self.voice_now, 0)) if first else None
        if first:
            self.booted.add(self.voice_now)
        name = self.define(cur, boot) if select or first else None
        if select and self.controls(self.ins(select)):
            self.takes.add(self.voice_now)
        if restore and not name:
            name = self.define(restore)
        restore = cur if first else 0
        if legato and sets is None:
            sets = self.call("tone" if pend or not isinstance(column, T.Legato) else "leg")
        elif legato and isinstance(column, T.Legato) and not (tone or pend):
            raise Refusal("pattern effect", "an effect on the frequency of a legato note")
        if porta:
            sets = self.slide(0xFF, 110)
        pend = porta or (pend and sets is None and not started)
        keep, atom = {}, "-"
        if started:
            atom = note_name(int(note) - 1 + ins.octave_shift)
            if not (legato or select):
                keep = {
                    "keep_pw": bool(ins.control & 0x40),
                    "keep_filter": bool(ins.control & 0x80),
                }
        elif isinstance(note, T.GateOff | T.GateOn):
            if ins is not None and any((ins.gateoff_wf, ins.gateoff_pw, ins.gateoff_filt)):
                raise Refusal("gate off pointer", "a gate off table position")
            on = isinstance(note, T.GateOn)
            cmds["ctrl"] = Once(Expr("v | 1" if on else "v & 254"))
            cmds["b.run"] = self.gate(int(on))
        elif note is not None and not isinstance(note, T.VibratoAmplitude) and not porta:
            if note.__class__.__name__ not in WAVE_FX:
                raise Refusal("note column", type(note).__name__)
        if "_chord" in cmds:
            if not started or legato:
                raise Refusal("pattern effect", "a chord change but at a note start")
            cmds["run"] = self.wave(cur, 0, cmds.pop("_chord"))
        if sets is not None:
            self.call("tick")
            cmds["set.freq"] = Once(sets)
        if legato:
            cmds["legato"] = True
        return Event(atom, name, cmds, **keep), (cur, pend, restore, rows + 1, begun or started)

    def events(self, rows, ctx: tuple) -> tuple:
        """``(events, context after)`` of pattern ``rows`` played in ``ctx``."""
        events = []
        for row in rows:
            event, ctx = self.event(row, ctx)
            events.append(event)
        return tuple(events), ctx

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
        walks, ctx = self.segments(units, (0, False, 0, 0, False))
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
            self.entered.setdefault(name, (num, ctx + ("tempo",) * (index == 0 and v == 0)))
            items.append(Play(name, Signed(trans) if trans else None))
        return Voice(tuple(items), None if loop is None else starts[loop])

    def timed(self, event: Event) -> Event:
        """``event`` with the tempo of the tune, unless it sets its own."""
        commands = {"tempo": self.tempos[0]} | dict(event.commands)
        return Event(event.note, event.ins, commands, event.keep_pw, event.keep_filter)

    # ---- the document -----------------------------------------------------------------------

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
        freq = [Slot("boot"), Slot("set"), Slot("first")]
        freq += [Slot("un", Call("un"), hold=quiet)] if self.carry else []
        freq += [Slot("tick", Call("tick"), hold=NOTE_HOLDS)] if "tick" in used else []
        freq += [Slot("wave")]
        freq += [Slot("out", Call("out"), hold=quiet)] if self.carry else []
        slots = {"pw": (Slot("pw", hold=NOTE_HOLDS),), "cut": (Slot("cut", hold=NOTE_HOLDS),)}
        slots["ctrl"] = (Slot("fx"), Slot("wf"))
        names = {x for n in used for x in asm.symbols(self.code[n])}
        state = tuple((k, n) for k, n in swcode.STATE if k in names)
        return Header(
            (("title", name), ("author", author), ("released", released)),
            clock=(CYCLES[core.clock], core.clock),
            slots=slots | {"freq": tuple(freq)},
            layers={"ctrl": ("a", "b")},
            holds={"pw": NOTE_HOLDS, "filter": NOTE_HOLDS, "ctrl": ("legato",)},
            state=(("freq", state),) if state else None,
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
        if len(self.takes) > 1:
            raise Refusal("filter order", "several voices take over the filter")
        intro = self.names.name("phrase", "p", (Event("-"),))
        voices = [Voice((Play(intro), *v.play[1:]), v.loop) for v in voices]
        kinds = ("restart", "ins", "ctrl", "pw", "filter", "phrase")
        defs = [Section(k, tuple(self.names.section(k))) for k in kinds]
        init = [
            Event("-", None, ({"tempo": PRE} if v == 0 else {}) | self.seeds(v))
            for v in range(VOICES)
        ]
        for name in ("un", "out") * self.carry:
            self.call(name)
        used = tuple(n for n in self.code if n in self.calls)
        mods = tuple((signature(n, asm.params(self.code[n])), Code(self.code[n])) for n in used)
        tables = (
            (("V", self.vtable()),)
            if any("V.lo" in asm.symbols(self.code[n]) for n in used)
            else ()
        )
        defs = [d for d in defs if d.records]
        return Document(self.header(used), tables, mods, defs, tuple(init), tuple(voices), None)

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

    player = Player(load(text), observe)
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
    ``frames`` frames."""
    core = core_of_sid(raw, subtune)
    text, built = document_of(core)
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
    return cli.main(argv, __doc__.split("\n", 1)[0], lambda raw, st: lift(raw, st), verify)


if __name__ == "__main__":
    sys.exit(main())
