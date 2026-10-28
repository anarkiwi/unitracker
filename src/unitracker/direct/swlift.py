"""A SID-Wizard tune transliterated to the generic document from pysidwizard's model.

Each native object is one document object: an instrument with its hard restart, the WF, pulse
and filter walks from each entry point as programs, a pattern as a phrase, an orderlist as a
voice's; the document starts at the first row's TICK_2 (docs/direct/swlift.md).
"""

import re
import sys
from dataclasses import dataclass, field

import numpy as np
from pysidtracker import SidImage, gridcmp
from pysidwizard import parse_sid_song
from pysidwizard.constants import INST_WF_TABLE_POS
from pysidwizard.model import End, Loop, PlayPattern, Transpose
from pysidwizard.player.tables import _parse_chord_starts
from unitracker_grammar import code
from unitracker_grammar.document import (
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
    Section,
    Signed,
    Slot,
    Voice,
    printed,
    signature,
)
from unitracker_player.player import play

from unitracker.direct import cli, swcode
from unitracker.direct.goattracker2 import meta_text, note_name, rests
from unitracker.direct.swtrace import CLOCK, VOICES, standard, trapped

LEGATO, NPORTAM, GATE_ON, GATE_OFF = 0x3F, 0x78, 0x7D, 0x7E
LEAD = 2
GATES = {"c1": Program([{"gate": 0}]), "c2": Program([{"gate": 1}])}
# WFARPTB's pitch, ARPSCNT in ac: m 0 RELPTCH (b the arp), 1 ABSPTCH, 2 NOP, 3 PLYCHRD, 4, 5 end.
_LOAD = "lda nn\nclc\nadc #1\nand #127\ntax\nlda F.lo-1,x\nsta z\nlda F.hi-1,x\nsta z+1\n"
_WAVE = (
    "lda phi\nora phi+1\nbne tk\nlda #h\nsec\nsbc #1\nsta ac\njmp rd\ntk:\ndec ac\nbpl sk\n"
    "lda #g\nsec\nsbc #1\nsta ac\nlda #m\ncmp #3\nbcs rd\nsk:\nclc\njmp sum\n"
    "rd:\nlda #f\nbeq kd\nlda #d\nsta dd\nkd:\nlda #m\nbeq rel\ncmp #1\nbeq abs\ncmp #3\n"
    "beq chd\ncmp #4\nbeq sk\nsec\njmp sum\nabs:\n"
    + _LOAD
    + "clc\njmp sum\nrel:\n"
    + _LOAD
    + "lda ne\nclc\nadc #1\nclc\nadc #b\njmp sum\n"
    "chd:\nldy cp\nlda C.lo,y\ncmp #126\nbne c1\nlda cs\nsta cp\ntay\nlda C.lo,y\n"
    "c1:\ncmp #127\nbne c2\nlda cs\nsta cp\ntay\nlda C.lo,y\nc2:\ninc cp\nsta tb\nlda ne\n"
    "clc\nadc #1\nclc\nadc tb\nphp\nand #127\ntax\nlda F.lo-1,x\nsta z\nlda F.hi-1,x\n"
    "sta z+1\nplp\n"
)
_SUM = "sum:\nlda z\nadc dd\nsta v\nlda z+1\nadc #0\nsta v+1\nrts\n"
_PLAIN = "sum:\nlda z\nsta v\nlda z+1\nsta v+1\nrts\n"
_FIRST = "lda phi\nora phi+1\nbne step\n"
SETUPS = ("vfx", "vfq", "kty", "sld", "por", "leg", "npn")
STATE = swcode.STATE["freq"] | {"tv": 1, "cs": 1, "cp": 1, "tb": 1, "ac": 1}


class LiftError(ValueError):
    """A tune this transliteration refuses."""


@dataclass
class Gaps:
    """What the grammar could not say one-to-one, by kind, with where."""

    seen: dict = field(default_factory=dict)

    def add(self, kind: str, where: str) -> None:
        """Record ``kind`` at ``where``."""
        self.seen.setdefault(kind, set()).add(where)


def _tracks(table: bytes) -> bool:
    """Whether a pulse or filter table's rows name a keyboard track."""
    return any(table[k + 2] for k in range(0, len(table) - 2, 3) if table[k] < 0xFE)


def _number(record: tuple) -> int:
    return int(record[0][1:])


def _signed(byte: int) -> int:
    return byte - 0x100 if byte & 0x80 else byte


def _operands(text: str) -> set:
    """The memory operands a template's code names."""
    out = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and not parts[1].startswith("#"):
            out.add(re.split(r"[+,-]", parts[1])[0])
    return out


class Lift:  # pylint: disable=too-many-instance-attributes
    """One subtune of a SID-Wizard module as a generic document."""

    def __init__(self, swm, meta: tuple = (), regs: tuple | None = None):
        self.swm, self.meta, self.regs = swm, meta, regs
        self.ref = standard(swm)
        self.feats = feats = self.ref.features
        if swm.frame_speed > 1:
            raise LiftError("multispeed")
        if self.ref.image.play_gate:
            raise LiftError("a play gate")
        if not swm.instruments or len(swm.sequences) < VOICES:
            raise LiftError("no instruments, or fewer orderlists than voices")
        self.code = swcode.templates(feats.calcvibrato, feats.vibratotypes, feats.portavibra)
        self.code["snd"] = self.code["snd"].replace("ldx #t\n", "ldx tv\n")
        for name in SETUPS:
            text = self.code[name][len(_FIRST) : self.code[name].index("step:\n")]
            self.code[name] = text if text.endswith("rts\n") else text + "rts\n"
        self.code["wv"] = _WAVE + (_SUM if feats.detunesupport else _PLAIN)
        self.code |= {
            "trk": "lda #t\nsta tv\nrts\n",
            "chs": "lda #o\nsta cs\nsta cp\nrts\n",
            "dset": "lda #d\nsta dd\nrts\n",
        }
        self.tracked = {
            "pw": feats.pwkeybtrack and any(_tracks(i.pw_table) for i in swm.instruments),
            "cut": feats.filtkbtrack and any(_tracks(i.filter_table) for i in swm.instruments),
        }
        if any(self.tracked.values()):
            self.code |= swcode.track_templates(feats.finefiltsweep)
            for name in ("fst", "fsw", "fid"):
                self.code[name] = self.code[name][: self.code[name].index("go:\n")] + "go:\nrts\n"
        self.routines = {k: code.read(v) for k, v in self.code.items()}
        self.used = []
        self.chords = _parse_chord_starts(swm.chord_table) if feats.chordsupport else []
        self.programs = {"ctrl": {}, "pw": {}, "filter": {}}
        self.restarts, self.ins, self.phrases = {}, {}, {}
        self.gaps = Gaps()

    # ---- calls ---------------------------------------------------------------------------

    def call(self, template: str, **params) -> Call:
        """The call of ``template``, its arguments in its signature's order."""
        if template not in self.used:
            self.used.append(template)
        return Call(template, tuple(params[k] for k in code.params(self.routines[template])))

    def _amp(self, raw: int, zero: bool = True) -> dict:
        """SETFMOD's argument for raw amplitude ``raw``: ``r`` (CALCVIBRATO) or ``g``."""
        if self.feats.calcvibrato:
            return {"r": ((raw + 1) >> 1) + 1 if raw or not zero else 0}
        if not raw:
            return {"g": 0}
        lo, hi = self.ref._freqmod(raw, 0)  # pylint: disable=protected-access
        return {"g": hi << 8 | lo}

    def _counter(self, kind: int, freq: int) -> int:
        """SETVIBR's first VIBRACNT for SLIDEVIB ``kind`` and frequency nibble ``freq``."""
        if not self.feats.vibratotypes or kind < 0x20:
            return freq >> 1
        return 0 if kind == 0x30 else freq

    def _vibrato(self, ins, byte: int, template: str = "vfx", **extra) -> Call:
        """SETVIBR of vibrato ``byte`` with FORCVIB's type from ``ins``' control byte."""
        kind, freq = ins.control & 0x30, byte & 0x0F
        params = {"c": kind, "i": self._counter(kind, freq), "q": 2 * freq} | extra
        return self.call(template, **params, **self._amp((byte >> 4) << 3))

    # ---- table walks ---------------------------------------------------------------------

    def _image(self, ins) -> bytes:
        """The bytes PLAYERZP reads for ``ins``."""
        return self.ref._image(ins)  # pylint: disable=protected-access

    def _program(self, kind: str, prefix: str, key, build, lead: dict = None) -> str:
        """The program template of ``key``, its rows after a lead row of ``l`` ticks."""
        store = self.programs[kind]
        if key not in store:
            rows, loop = build()
            loop = None if loop is None else loop + 1
            first = {"hold": "l"} | (lead or {})
            store[key] = (f"{prefix}{len(store) + 1}", Program([first, *rows], loop))
        return store[key][0]

    def wave(self, ins, pos: int, lead: int) -> Call:
        """The WF walk of ``ins`` from offset ``pos``, its first read ``lead`` ticks on."""
        name = self._program("ctrl", "wf", (id(ins), pos), lambda: self._wave(ins, pos))
        return Call(name, (lead,))

    def _wave(self, ins, pos: int) -> tuple:
        """WFARPTB's rows from ``pos``: each read in order, jumps followed, to a rest (its code
        reading it on) or to a row read again, the loop."""
        feats, image = self.feats, self._image(ins)
        pace = (ins.arp_speed & 0x3F) + 1 if feats.arpspeedsupp else 1
        if not ins.wf_table and pos >= INST_WF_TABLE_POS:
            return [self._wrow({}, 5, h=1, g=1)], None
        rows, seen, jumped = [], {}, False
        while (pos, jumped) not in seen:
            seen[(pos, jumped)] = len(rows)
            byte = image[pos] if pos < len(image) else 0xFF
            if not jumped and byte == 0xFF:
                rows.append(self._wrow({}, 5, h=pace, g=pace))
                return rows, None
            if not jumped and byte == 0xFE:
                raw = image[pos + 1] if pos + 1 < len(image) else 0
                if raw >= 0x80 or raw >= len(image):
                    rows.append(self._wrow({}, 5 if raw >= 0x80 else 4, h=pace, g=pace))
                    return rows, None
                pos, jumped = raw, True
                continue
            row, rest = self._read(ins, pos, jumped, pace)
            rows.append(row)
            if rest:
                return rows, None
            jumped = False
            pos += 3
        return rows, seen[(pos, jumped)]

    def _read(self, ins, pos: int, jumped: bool, pace: int) -> tuple:
        """One WF row's read: its waveform (none for a repeat row), pitch and detune, held for
        its ticks; and whether its chord holds it for good."""
        image, keys, wait = self._image(ins), {}, pace
        byte, arp, det = (image[p] if p < len(image) else None for p in range(pos, pos + 3))
        if not jumped and byte < 0x10 and self.feats.arpspeedsupp:
            wait = byte + 1
        else:
            keys["ctrl"] = byte
        det = 0xFF if det is None else det
        if arp == 0x7F and self.feats.chordsupport:
            hold = self._chord_hold(ins, wait, pace)
            return self._wrow(keys, 3, h=wait, g=pace, d=det, f=1, hold=hold), hold is None
        note, mode = self._pitch(arp)
        keep = {"d": 0, "f": 0} if det == 0xFF else {"d": det, "f": 1}
        return self._wrow(keys | note, mode, h=wait, g=pace, b=arp or 0, **keep), False

    def _pitch(self, arp: int | None) -> tuple:
        """``(note key, mode)`` of a WF row's arp byte: none (a NOP or no byte), absolute,
        relative."""
        if arp is None or arp == 0x80 and self.feats.wfarp_nop_supp:
            return {}, 4 if arp is None else 2
        if 0x80 <= arp < 0xE0:
            return {"note": note_name((arp & 0x7F) - 1)}, 1
        return {"note": Signed(_signed(arp))}, 0

    def _chord_hold(self, ins, wait: int, pace: int) -> int | None:
        """The ticks a chord row holds, its first read ``wait`` ticks then one each ``pace``:
        the instrument's default chord's reads before its $7E; None where it loops."""
        chord, table = ins.default_chord, b"\x00" + self.swm.chord_table
        if not 1 <= chord <= len(self.chords):
            self.gaps.add("chord out of range", f"instrument {ins.name_str()}")
            return None
        start = pos = self.chords[chord - 1]
        while pos < len(table) and table[pos] not in (0x7E, 0x7F):
            pos += 1
        if pos >= len(table) or table[pos] == 0x7F:
            return None
        self.gaps.add("chord returning to the WF table", f"instrument {ins.name_str()}")
        return wait + (pos - start - 1) * pace if pos > start else 0

    def _wrow(self, keys: dict, mode: int, **params) -> dict:
        """A WF row binding its pitch code, held ``hold`` ticks (its first read's wait ``h``
        unless given; None for good)."""
        hold = params.pop("hold", params["h"])
        args = {"m": mode, "f": 0, "d": 0, "b": 0} | params
        out = dict(keys) | {"wave.freq": self.call("wv", **args)}
        return out | ({"hold": hold} if hold not in (1, None) else {})

    def pulse(self, ins, pos: int, lead: int) -> Call:
        """The pulse walk of ``ins`` from offset ``pos``."""
        key = (id(ins), pos)
        name = self._program("pw", "pt", key, lambda: self._sweeps(ins, pos, "pw", 1))
        return Call(name, (lead,))

    def filter(self, ins, pos: int, lead: int) -> Call:
        """The filter walk of ``ins`` from offset ``pos``."""
        step = 1 if self.feats.finefiltsweep else 8
        key, out = (id(ins), pos), {"out.cut": self.call("frs")} if self.tracked["cut"] else {}
        name = self._program("filter", "ft", key, lambda: self._sweeps(ins, pos, "cut", step), out)
        return Call(name, (lead,))

    def _sweeps(self, ins, pos: int, cell: str, step: int) -> tuple:
        """SETPWID's or FILTPRG's rows from ``pos``: a set row a tick, a sweep row its adds
        then a tick, a jump to a sweep row a tick, to a set row none."""
        image, rows, seen, jumped = self._image(ins), [], {}, False
        if pos < len(image) and image[pos] < 0x80:
            self.gaps.add(
                f"a {cell} walk starting on a sweep: its count carries over", ins.name_str()
            )
        while (pos, jumped) not in seen:
            seen[(pos, jumped)] = len(rows)
            byte, value, track = (image[p] if p < len(image) else 0 for p in range(pos, pos + 3))
            byte = byte if pos < len(image) else 0xFF
            if not jumped and (
                byte == 0xFF or byte == 0xFE and value in (pos, *range(len(image), 256))
            ):
                return [*rows, self._swept(cell, "rest")], None
            if byte == 0xFE and not jumped:
                pos = value
                if image[pos] < 0x80:
                    rows.append(self._swept(cell, "rest"))
                jumped = cell == "pw" and image[pos] >= 0x80
                continue
            jumped = False
            if byte & 0x80:
                rows.append(self._swept(cell, "set", byte, value, track))
            else:
                if byte:
                    rows.append(
                        {"hold": byte} | self._swept(cell, "sweep", d=_signed(value) * step)
                    )
                rows.append(self._swept(cell, "advance", j=track))
            pos += 3
        return rows, seen[(pos, jumped)]

    def _swept(self, cell: str, kind: str, byte: int = 0, value: int = 0, j: int = 0, d: int = 0):
        """A pulse or filter row's keys: natively, or where the module tracks the keyboard by
        the code that keeps the walk's width or cutoff and its track amount."""
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        keys = {}
        if cell == "cut" and self.ref.image.quirks.kbtrack_fswitch and j >> 4 == 8:
            keys["route"], j = j & 0x0F, 0
        if cell == "cut" and kind == "set":
            keys |= {"res": byte & 0x0F, "mode": byte & 0x70}
        if not self.tracked[cell]:
            if j:
                self.gaps.add(f"{cell} keyboard track read past the tables", f"${j:02X}")
            native = {
                "set": ((byte & 0x7F) << 8 | value) & 0x0FFF if cell == "pw" else value << 3,
                "sweep": Expr(f"v + {d}" if d >= 0 else f"v - {-d}"),
            }
            return keys | {cell: native.get(kind, "none")}
        names = {"pw": ("pst", "psw", "pid", "pwr"), "cut": ("fst", "fsw", "fid", "none")}[cell]
        name = dict(zip(("set", "sweep", "advance", "rest"), names))[kind]
        params = {"w": (byte & 0x7F) << 8 | value, "c": value, "j": j, "d": d}
        return keys | {cell: name if name == "none" else self.call(name, **params)}

    # ---- instruments ---------------------------------------------------------------------

    def instrument(self, number: int) -> str:
        """The name of instrument ``number``, transliterated once."""
        name = f"i{number}"
        if name in self.ins:
            return name
        ins = self.swm.instruments[number - 1]
        base = INST_WF_TABLE_POS
        fields = {
            "ad": ins.attack << 4 | ins.decay,
            "sr": ins.sustain << 4 | ins.release,
            "ctrl": self.wave(ins, base, 1),
            "pw": self.pulse(ins, base + len(ins.wf_table) + 1, 1),
        }
        table = ins.filter_table
        if table and table[0] not in (0x00, 0xFF):
            start = self.ref._filt_table_start(ins)  # pylint: disable=protected-access
            fields["filter"] = self.filter(ins, start, 1)
        fields["snd.freq"] = self._strtsnd(ins)
        if self.chords:
            chord = ins.default_chord
            start = self.chords[chord - 1] if 1 <= chord <= len(self.chords) else 0
            fields["ch.freq"] = Once(self.call("chs", o=start))
        fields["tick.freq"] = self.call("stp")
        fields["restart"] = self._restart(ins)
        self.ins[name] = Instrument(fields)
        return name

    def _strtsnd(self, ins) -> Call:
        """STRTSND's SLIDEVIB and vibrato, and FREQHI's first-frame write."""
        high, quirks = 0, self.ref.image.quirks
        if self.feats.frame1switch and ins.control & 0x08:
            if not quirks.first_frame_fixed_waveform:
                high = 2 if quirks.first_frame_freq_by_track else 1
        extra = {"d": ins.vibrato_delay & 0xFF, "h": high}
        return self._vibrato(ins, ins.vibrato, "snd", **extra)

    def _restart(self, ins) -> Call:
        """HARDRST's gate off and ADHR on TICK_0 and TICK_1 where its timer fires, STRTSND's
        frame-1 waveform and SETFLTP's routing on the note."""
        if self.feats.hardrestypes:
            fires = (bool(ins.control & 2), bool(ins.control & 1) and not ins.control & 2)
            ad, sr = ins.hr_attack << 4 | ins.hr_decay, ins.hr_sustain << 4 | ins.hr_release
            staccato = bool(ins.control & 4)
            if staccato:
                self.gaps.add("staccato hard restart: its tick walks no table", ins.name_str())
        else:
            fires, ad, sr, staccato = (True, False), 0x0F, 0x00, False
        first = self._first(ins)
        table = ins.filter_table
        route = {"route_on" if table and table[0] != 0xFF else "route_off": 0}
        if "route_off" in route and self.ref.image.quirks.filter_stop_rewinds:
            route["cut"] = "hold"
        elif not table or table[0] in (0x00, 0xFF):
            self.gaps.add("the filter's voice walks on through a note's instrument", ins.name_str())
        shape = (fires, staccato, first is not None, tuple(route))
        if shape not in self.restarts:
            hr = {"gate": 0, "ad": "a", "sr": "s"} | ({"ctrl": 0x18} if staccato else {})
            rows = [
                {"hold": 1} | (hr if fires[0] else {}),
                {"hold": 1} | (hr if fires[1] else {}),
                {"hold": 0},
                route | ({"ctrl": "w"} if first is not None else {}),
            ]
            params = ("a", "s") * any(fires) + ("w",) * (first is not None)
            name = f"h{chr(ord('a') + len(self.restarts))}"
            self.restarts[shape] = (name, signature(name, params), Program(rows))
        args = (ad, sr) * any(fires) + (first,) * (first is not None)
        return Call(self.restarts[shape][0], args)

    def _first(self, ins) -> int | None:
        """STRTSND's first-frame waveform: $09 without FRAME1SWITCH or on a fixed-waveform
        driver, the instrument's where its control byte's bit 3 asks, else none."""
        quirks = self.ref.image.quirks
        if not self.feats.frame1switch or ins.control & 0x08 and quirks.first_frame_fixed_waveform:
            return 0x09
        if ins.control & 0x08:
            return ins.effective_first_waveform
        self.gaps.add("no frame-1 waveform: the gate waits for the WF row", ins.name_str())
        return None

    # ---- patterns ------------------------------------------------------------------------

    def phrase(self, number: int, state: dict) -> str:
        """The phrase of pattern ``number`` entered with voice ``state``, which it updates."""
        events = []
        for k, row in enumerate(self.swm.patterns[number - 1].rows):
            state["at"] = f"p{number}:{k}"
            events.append(self.event(row, state))
        events, name = tuple(rests(events)), f"p{number}"
        if self.phrases.setdefault(name, events) != events:
            self.gaps.add("pattern played from different voice states", name)
        return name

    def event(self, row, state: dict) -> Event:
        """One pattern row: its note column, instrument column and FX as an event, from voice
        ``state`` (its instrument, NPORTAM, the ADSR nibbles its small effects left)."""
        inst, fx = row.instrument, row.fx
        selected = inst is not None and 1 <= inst <= len(self.swm.instruments)
        if inst is not None and len(self.swm.instruments) < inst < LEGATO:
            self.gaps.add("instrument past the module's", state["at"])
        if selected:
            if not row.note or row.note >= 0x60:
                self.gaps.add("instrument selected without a note", state["at"])
            state |= {"ins": inst, "adsr": {}}
        ins = self.swm.instruments[state["ins"] - 1] if state.get("ins") else None
        atom, commands, keep, lead = "-", {}, (False, False), 0
        if row.note and ins is not None:
            if row.note < 0x60:
                atom, commands, keep, lead = self._note(row, ins, selected, state)
            else:
                commands = self._note_fx(row.note, ins, state)
        elif row.note and row.note < 0x60:
            self.gaps.add("note before any instrument", state["at"])
        for small in (inst if inst and inst > LEGATO else None, fx):
            if small is not None and small >= 0x20:
                commands |= self._small(small, ins, state)
        if fx is not None and fx < 0x20 and row.fx_value is not None and (fx, lead) != (3, 1):
            commands |= self._big(fx, row.fx_value, ins, int(lead > 0), state)
        state["rows"] = state.get("rows", 0) + 1
        name = self.instrument(inst) if selected else None
        pairs = [(k, x) for k, v in commands.items() for x in (v if isinstance(v, list) else [v])]
        return Event(atom, name, pairs, keep_pw=keep[0], keep_filter=keep[1])

    def _note(self, row, ins, selected: bool, state: dict) -> tuple:
        """CLEGATO's pitch, and STRTSND (lead 2: its walks start a tick on, the reset-off
        bits keeping them) or a glide (lead 1): ``(atom, commands, keep, lead)``."""
        octave = ins.octave_shift if self.feats.octaveshift else 0
        atom, at = note_name(row.note - 1 + octave), state["at"]
        glide = None
        if row.fx == 0x03:
            glide = self.call("por", m=0x83, **self._amp(row.fx_value or 0, False))
        elif state.get("portam") or row.instrument == LEGATO:
            glide = self.call("npn" if state.get("portam") else "leg")
        first = not state.get("noted") and state.get("rows")
        state |= {"portam": False, "noted": True}
        if glide is not None:
            self.gaps.add("a legato or portamento note: the walks pause", at)
            return atom, {"fx.freq": Once(glide), "legato": True}, (False, False), 1
        state["adsr"] = {}
        if first:
            self.gaps.add("a voice's first hard restart walks instrument 0", at)
        bits = ((self.feats.pwresetsw, 0x40, "pulse"), (self.feats.filtresetsw, 0x80, "filter"))
        keep = tuple(bool(on and not selected and ins.control & bit) for on, bit, _ in bits)
        for kept, (_, _, cell) in zip(keep, bits):
            if kept:
                self.gaps.add(f"a note keeping its {cell} walk, which pauses", at)
        return atom, {}, keep, 2

    def _note_fx(self, note: int, ins, state: dict) -> dict:
        """A note-column effect: gate off or on, NPORTAM, CHVIBFX."""
        if note == GATE_OFF:
            return self._gate_off(ins)
        if note == GATE_ON:
            return {"g.run": "c2"}
        if note == NPORTAM:
            state["portam"] = True
            return {"fx.freq": Once(self.call("sld", m=0xFF, **self._amp(110)))}
        if note < NPORTAM:
            return {"fx.freq": Once(self._vibrato(ins, (note & 0x0F) << 4 | ins.vibrato & 0x0F))}
        self.gaps.add(f"note effect ${note:02X}", state["at"])
        return {}

    def _gate_off(self, ins) -> dict:
        """NGATEOF: the gate off, or the WF walk from the gate-off pointer; the pulse and
        filter walks from theirs."""
        feats, out = self.feats, {}
        pointers = feats.gateoffptr
        if not pointers or not ins.gateoff_wf:
            out["g.run"] = "c1"
        runs = [self.wave(ins, ins.gateoff_wf, 0)] if pointers and ins.gateoff_wf else []
        if pointers and ins.gateoff_pw:
            runs.append(self.pulse(ins, ins.gateoff_pw, 0))
        if pointers and ins.gateoff_filt:
            self.gaps.add("filter gate-off pointer (the filter's voice decides)", ins.name_str())
        return out | ({"run": runs} if runs else {})

    def _small(self, byte: int, ins, state: dict) -> dict:
        """A small effect ($20-$FF) as commands."""
        # pylint: disable=too-many-return-statements
        high, low = byte & 0xF0, byte & 0x0F
        if high in (0x20, 0x30, 0x50, 0x60):
            if ins is None:
                self.gaps.add("ADSR small effect before any instrument", state["at"])
                return {}
            names = {0x20: "attack", 0x30: "decay", 0x50: "sustain", 0x60: "release"}
            nib = state.setdefault("adsr", {}) if self.feats.allghostregs else {}
            nib[names[high]] = low
            a, d, s, r = (nib.get(k, getattr(ins, k)) for k in names.values())
            return {"ad": a << 4 | d} if high < 0x40 else {"sr": s << 4 | r}
        if high == 0x70 and self.chords:
            return self._chord(low)
        if high == 0x80 and ins is not None:
            return {"fx.freq": Once(self._vibrato(ins, low << 4 | ins.vibrato & 0x0F))}
        if high == 0x90:
            return {"fx.freq": Once(self.call("vfq", q=low << 1))}
        if high == 0xA0:
            return {"vol": low}
        if high == 0xB0:
            return {"mode": low << 4}
        if high == 0xD0 and self.feats.detunesupport:
            return {"dset.freq": Once(self.call("dset", d=low << 3))}
        if high == 0xF0:
            return {"res": low}
        self.gaps.add(f"small effect ${high:02X}", state["at"])
        return {}

    def _chord(self, number: int) -> dict:
        """SMALFX7: CURCHORD and CHORDPOS."""
        if not 1 <= number <= len(self.chords):
            return {}
        return {"ch.freq": Once(self.call("chs", o=self.chords[number - 1]))}

    def _big(self, fx: int, value: int, ins, lead: int, state: dict) -> dict:
        """A big effect ($01-$1F) as commands."""
        # pylint: disable=too-many-return-statements,too-many-branches
        simple = {0x04: "ctrl", 0x05: "ad", 0x06: "sr"}
        if fx in simple:
            return {simple[fx]: value}
        if fx in (0x01, 0x02, 0x03):
            state["portam"] = False
            return {"fx.freq": Once(self.call("sld", m=0x80 | fx, **self._amp(value)))}
        if fx == 0x07:
            return self._chord(value) if self.chords else {}
        if fx == 0x08 and ins is not None:
            return {"fx.freq": Once(self._vibrato(ins, value))}
        if fx == 0x0D and self.feats.detunesupport:
            return {"dset.freq": Once(self.call("dset", d=value))}
        if fx in (0x10, 0x13):
            return {"tempo" if fx == 0x10 else "vtempo": self._tempo(value & 0x7F)}
        if fx in (0x11, 0x14):
            return {"tempo" if fx == 0x11 else "vtempo": self._tempo(value >> 4, value & 0x0F)}
        if fx == 0x16:
            return {"fx.freq": Once(self.call("kty", c=value & 0x30))}
        if ins is not None and fx in (0x09, 0x0A, 0x0B):
            return self._moved(fx, value, ins, lead)
        if fx == 0x1F and self.feats.filt_ctrl_fx:
            return {"res": value >> 4, "route": value & 0x0F}
        self.gaps.add(f"big effect ${fx:02X}", state["at"])
        return {}

    def _tempo(self, *ticks: int):
        """A single or funk tempo, a slot of 0 the player's 6; below 3 TICK_2 comes early."""
        ticks = tuple(t or 6 for t in ticks)
        if min(ticks) < 3:
            self.gaps.add("a tempo below 3: FASTSPEEDBIND moves TICK_2", f"{ticks}")
        return ticks[0] if len(ticks) == 1 else ticks

    def _moved(self, fx: int, value: int, ins, lead: int) -> dict:
        """BIGFX09, 0A, 0B: the walk from the row the value names."""
        wf = INST_WF_TABLE_POS
        tables = {
            0x09: (ins.wf_table, wf, self.wave),
            0x0A: (ins.pw_table, wf + len(ins.wf_table) + 1, self.pulse),
            0x0B: (ins.filter_table, wf + len(ins.wf_table) + len(ins.pw_table) + 2, self.filter),
        }
        table, base, walk = tables[fx]
        if value * 3 >= len(table):
            return {}
        if fx == 0x0B:
            self.gaps.add("filter position effect (the filter's voice decides)", f"${value:02X}")
        return {"run": [walk(ins, base + value * 3, lead)]}

    # ---- song ----------------------------------------------------------------------------

    def voice(self, v: int, sequence: list) -> Voice:
        """Voice ``v``'s orderlist: its patterns with the transposes in force, its loop."""
        state, plays, at, transpose, loop, target = {}, [], {}, 0, None, 0
        for k, cmd in enumerate(sequence):
            at[k] = len(plays)
            if isinstance(cmd, Transpose):
                transpose = cmd.semitones if self.feats.transposesupp else 0
            elif isinstance(cmd, PlayPattern) and 1 <= cmd.pattern <= len(self.swm.patterns):
                plays.append((cmd.pattern, transpose, k))
                self.phrase(cmd.pattern, state)
            elif isinstance(cmd, Loop):
                loop, target = at.get(cmd.position, 0), cmd.position
                break
            elif isinstance(cmd, End):
                break
            else:
                self.gaps.add("orderlist command", f"voice {v + 1}: {cmd}")
        if loop is not None:
            for number, was, k in plays[loop:]:
                self.phrase(number, state)
                if was != transpose and not any(
                    isinstance(c, Transpose) for c in sequence[target:k]
                ):
                    self.gaps.add("orderlist loop into another transpose", f"voice {v + 1}")
        out = [Play(f"p{n}", Signed(t) if t else None) for n, t, _ in plays]
        return Voice(tuple(out), loop)

    def header(self) -> Header:
        """The header: clock, tempo, slots, layers, state and the registers before tick 0."""
        left, right = (self.swm.subtune_tempos or [(6, 6)])[0]
        tempo = self._tempo(left & 0x7F, *(() if left & 0x80 else (right & 0x7F,)))
        before = () if self.feats.vibslidealways else ("note-2", "note-1")
        held = (*before, "note", "legato")
        slots = {
            "freq": (
                Slot("id"),
                Slot("snd"),
                Slot("ch"),
                Slot("fx"),
                Slot("dset"),
                Slot("tick", hold=(*before, "note", "legato")),
                Slot("wave", hold=("note", "legato"), reset=("row.freq",)),
            ),
            "pw": (Slot("pw", hold=held),),
            "cut": (Slot("cut", hold=held),) + (Slot("out"),) * self.tracked["cut"],
        }
        names = set().union(*(_operands(self.code[t]) for t in self.used))
        cells = {"freq": STATE, "pw": swcode.STATE["pw"], "cut": swcode.STATE["cut"]}
        state = tuple(
            (cell, tuple((k, n) for k, n in sizes.items() if k in names))
            for cell, sizes in cells.items()
            if names & set(sizes)
        )
        return Header(
            tuple((k, meta_text(v)) for k, v in self.meta),
            clock=CLOCK,
            tempo=tempo,
            slots=tuple(slots.items()),
            layers=(("ctrl", ("wf", "g")),),
            state=state,
            regs=self.regs,
        )

    def document(self) -> str:
        """The document text."""
        voices = [self.voice(v, s) for v, s in enumerate(self.swm.sequences[:VOICES])]
        dummy = self.instrument(1)
        init = tuple(
            Event("-", dummy, {"id.freq": Once(self.call("trk", t=(7 * v - 1) & 0xFF))})
            for v in range(VOICES)
        )
        defs = [
            ("restart", tuple((sig, p) for _, sig, p in self.restarts.values())),
            ("ins", tuple(sorted(self.ins.items(), key=_number))),
            ("ctrl", (*GATES.items(), *self._templates("ctrl"))),
            ("pw", self._templates("pw")),
            ("filter", self._templates("filter")),
            ("phrase", tuple(sorted(self.phrases.items(), key=_number))),
        ]
        mods = tuple(
            (signature(name, code.params(self.routines[name])), Code(self.routines[name]))
            for name in self.used
        )
        doc = Document(
            self.header(),
            tables=(("C", (0, *self.swm.chord_table)),) if "wv" in self.used else (),
            mods=mods,
            defs=[Section(k, v) for k, v in defs if v],
            init=init,
            rows=voices,
        )
        return printed(doc)

    def _templates(self, kind: str) -> tuple:
        return tuple((signature(n, ("l",)), p) for n, p in self.programs[kind].values())


def _meta(raw: bytes) -> tuple:
    psid = SidImage.from_bytes(raw).header
    return (("title", psid.name), ("author", psid.author), ("released", psid.released))


def start_regs(swm) -> tuple:
    """The registers the reference player holds before the first row's TICK_2."""
    grid = np.asarray(standard(swm).render_grid(LEAD), dtype=np.uint8)
    return tuple(int(x) for x in grid[LEAD - 1])


def lifter(raw: bytes, subtune: int = 0) -> Lift:
    """The lift of one subtune of a SID."""
    swm = parse_sid_song(raw, subtune + 1)
    return Lift(swm, _meta(raw), start_regs(swm))


def transcribe(raw: bytes, subtune: int = 0) -> str:
    """The document text of one subtune of a SID."""
    return lifter(raw, subtune).document()


def _positions(swm) -> set:
    """``(instrument, effect, value)`` of each table position effect a voice's orderlist plays
    with its instrument."""
    out = set()
    for sequence in swm.sequences[:VOICES]:
        number = None
        for cmd in sequence:
            if not isinstance(cmd, PlayPattern) or not 1 <= cmd.pattern <= len(swm.patterns):
                continue
            for row in swm.patterns[cmd.pattern - 1].rows:
                if row.instrument is not None and 1 <= row.instrument <= len(swm.instruments):
                    number = row.instrument
                if number and row.fx in (0x09, 0x0A, 0x0B) and row.fx_value is not None:
                    out.add((number, row.fx, row.fx_value))
    return out


def native_counts(raw: bytes, subtune: int = 0) -> dict:
    """The module's object counts (:func:`counts`)."""
    return counts(parse_sid_song(raw, subtune + 1))


def counts(swm) -> dict:
    """A module's object counts: instruments, the entry points of each instrument's WF,
    pulse and filter tables (its start, gate-off pointer and the positions its effects name;
    the WF's with the two gate assignments), patterns and each voice's orderlist entries."""
    ins, named = swm.instruments, _positions(swm)

    def entries(pointer: str, fx: int) -> int:
        return sum(len({0, getattr(i, pointer)}) for i in ins) + sum(f == fx for _, f, _ in named)

    return {
        "instruments": len(ins),
        "ctrl": entries("gateoff_wf", 0x09) + len(GATES),
        "pw": entries("gateoff_pw", 0x0A),
        "filter": entries("gateoff_filt", 0x0B),
        "phrases": len(swm.patterns),
        "song": tuple(len(s) for s in swm.sequences[:VOICES]),
    }


def compare(swm, text: str, frames: int):
    """The ``(tick, register, ours, theirs)`` cells where document ``text``'s grid differs from
    the reference player's from the first row's TICK_2, and its traps."""
    ours = play(text, ticks=frames)
    theirs, found = trapped(swm, len(ours) + LEAD)
    return gridcmp.cell_diff(ours, theirs[LEAD:]), found


def verify(raw: bytes, subtune: int = 0, frames: int = 20000):
    """``(cells, document, traps)`` of one subtune of a SID (:func:`compare`)."""
    one = lifter(raw, subtune)
    text = one.document()
    cells, found = compare(one.swm, text, frames)
    return cells, text, found


def main(argv=None) -> int:
    """Transliterate a SID, or verify SIDs against pysidwizard."""
    return cli.main(argv, __doc__.split("\n", 1)[0], transcribe, verify)


if __name__ == "__main__":
    sys.exit(main())
