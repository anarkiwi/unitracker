"""A GoatTracker 2 tune transcribed to the generic document from pygoattracker's playroutine.

A pattern row's step begins on tick 1; the playroutine's skips are the header's holds, its
wave and filter tables programs, its pulse tables expressions (docs/direct/goattracker2.md).
"""

import argparse
import signal
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml
from pygoattracker import constants as C
from pygoattracker import decompile_sid
from pysidtracker import gridcmp
from tqdm import tqdm
from unitracker_grammar.expr import ExpressionError, canonical
from unitracker_player.player import play

from unitracker.document.yamlstyle import Atom, Dumper, Flow, FlowMap

VERSION = "1.22"
EVENT = "n"
TICK = "tick.freq"
WIDTH = 100
NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
PHI = "φ"
ROW_KEYS = "wait hold ctrl gate note ad sr pw cut res route mode freq run filter vol".split()
FREQ_CMDS = (C.CMD_PORTAUP, C.CMD_PORTADOWN, C.CMD_TONEPORTA, C.CMD_VIBRATO)
LEAD = 2
CALC = "((G[(N + 1) % 128] - G[N % 128]) % 65536) // d"
TO = f"G[{EVENT} % 128]"


def _toward(s: str) -> dict:
    """Toneporta by ``s``: the phase ``t`` restarts when it crosses its note."""
    reached = f"(v < {TO})*(v + {s} > {TO}) + (v > {TO})*(v - {s} < {TO})"
    out = f"(v < {TO})*min(v + {s}, {TO}) + (v > {TO})*max(v - {s}, {TO}) + (v == {TO})*v"
    return {"t": f"t*(1 - ({reached}))", "out": out}


def _vibrato(s: str) -> dict:
    """GoatTracker's vibrato on phase ``t``, depth ``c``, step ``s``, after ``l`` firings."""
    return {
        "w": f"({PHI} >= l)",
        "f": "(c < t)*(t < 128)",
        "u": "((t ^ (255*f)) + 2) & 255",
        "t": "t + w*(u - t)",
        "out": f"v + w*{s}*(1 - 2*(u & 1))",
    }


def _ptab(out: dict) -> dict:
    """The pulse table from row ``b``: row ``r`` and time ``q`` as state, a jump resolved,
    then a set row or one step of a modulation row."""
    return {
        "ra": f"({PHI} < 1)*b + ({PHI} >= 1)*r",
        "j": "(PL[ra] == 255)",
        "rb": "j*PR[ra] + (1 - j)*ra",
        "qa": f"({PHI} >= 1)*q",
        "go": "(rb != 0)",
        "st": "go*(qa == 0)*(PL[rb] >= 128)",
        "md": "go*(1 - st)",
        "qb": "((qa == 0)*PL[rb] + (qa != 0)*qa - 1) & 255",
        "q": "md*qb",
        "r": "(rb + st + md*(qb == 0)) & 255",
    } | out


TEMPLATES = {
    "note": {"t": f"t*({PHI} >= 1)", "out": f"({PHI} < 1)*G[N % 128] + v*({PHI} >= 1)"},
    "porta": {"t": 0, "out": "v + g*s"},
    "portan": {"t": 0, "out": f"v + g*({CALC})"},
    "tporta": _toward("s"),
    "tportan": _toward(f"({CALC})"),
    "snap": {"t": 0, "out": TO},
    "vib": _vibrato("s"),
    "vibn": _vibrato(f"({CALC})"),
    "ptab": _ptab(
        {
            "sp": "PR[rb] - 256*(PR[rb] >= 128)",
            "out": "st*(((PL[rb] & 15) << 8) | PR[rb]) + (1 - st)*(v + md*sp)",
        }
    ),
    "ptabs": _ptab(
        {
            "xb": "(v & 255) + PR[rb]",
            "zb": "st*PR[rb] + md*((xb + (xb >> 8)) & 255)",
            "out": "(st + md)*(((zb & 15) << 8) | zb) + (1 - st - md)*v",
        }
    ),
}
TEMPLATES.update(
    (f"{base}1", f"v + ({PHI} < 1)*({TEMPLATES[base]['out']} - v)")
    for base in ("porta", "portan", "tporta", "tportan", "snap")
)
STATE = "{freq: {t: 0}, pw: {r: 0, q: 0}}"
ORDER = "[events, ctrl, pulse, filter, freq, pw, cut, read]"
SLOTS = (
    "{freq: {wave: {reset: [row.note, row.freq]}, tick: {hold: [%s]}}, "
    "pw: {pulse: {hold: [%s]}}}"
)


class TranscribeError(ValueError):
    """The tune uses something this transcription does not spell."""


def note_name(k: int) -> str:
    """Generic note ``k`` (12 * octave + pitch class) by name."""
    return f"{NAMES[k % 12]}{k // 12}"


def _ordered(keys: dict) -> FlowMap:
    """A row's keys in the grammar's order, a slot's key ``slot.cell`` at its cell's."""
    order = {k: i for i, k in enumerate(ROW_KEYS)}
    return FlowMap(sorted(keys.items(), key=lambda kv: order[kv[0].rsplit(".", 1)[-1]]))


@dataclass
class Occurrence:
    """A pattern a voice starts: the generic tick of its first step, its transpose, the
    frequency command ``(command, data)`` running into it (or None), its instrument, and each
    row's vibrato ``(phase, delay)`` after its tick 0."""

    tick: int
    pattern: int
    trans: int
    running: tuple | None
    instr: int
    rows: list = field(default_factory=list)


@dataclass
class Run:
    """The reference player's sequencer over ``frames`` frames."""

    frames: int
    starts: list = field(default_factory=lambda: [[], [], []])


def sequence(model, frames: int, loops: int = 3) -> Run:
    """Each voice's pattern starts until every voice has looped ``loops`` times, or over
    ``frames`` frames of the reference player."""
    ref, run = model.player(), Run(frames)
    fetched, frame = [None] * C.MAX_CHN, [0]
    get_new_notes, play_channel, play_call = (
        ref._get_new_notes,  # pylint: disable=protected-access
        ref._play_channel,  # pylint: disable=protected-access
        ref._play_call,  # pylint: disable=protected-access
    )

    def fetch(channel, chan):
        fetched[channel] = (chan.pattnum, chan.pattptr // 4, chan.trans)
        get_new_notes(channel, chan)

    def channel_tick(channel):
        chan = ref._channels[channel]  # pylint: disable=protected-access
        row = None
        if (chan.tick - 1) & 0xFF == 0 and fetched[channel] is not None:
            pattern, row, trans = fetched[channel]
            if row == 0:
                trans = trans - 0x100 if trans >= 0x80 else trans
                running = (chan.command, chan.cmddata) if chan.command in FREQ_CMDS else None
                run.starts[channel].append(
                    Occurrence(frame[0] + 1, pattern, trans, running, chan.instr & 0x3F)
                )
            fetched[channel] = None
        play_channel(channel)
        if row is not None and run.starts[channel]:
            run.starts[channel][-1].rows.append((chan.vibtime, chan.vibdelay))

    def call():
        play_call()
        frame[0] += 1

    ref._get_new_notes = fetch  # pylint: disable=protected-access
    ref._play_channel = channel_tick  # pylint: disable=protected-access
    ref._play_call = call  # pylint: disable=protected-access
    while frame[0] < frames and min(ref.loops) < loops:
        ref.play_frame()
    return run


class Transcriber:  # pylint: disable=too-many-instance-attributes
    """One subtune of a decompiled GoatTracker 2 SID as a generic document."""

    def __init__(self, model):
        self.model = model
        self.ref = model.player()
        ref = self.ref
        self.tables = {
            "G": list(ref._freq_table[:128]),  # pylint: disable=protected-access
            "PL": _trimmed([0] + ref._ltable[C.PTBL][:255]),  # pylint: disable=protected-access
            "PR": _trimmed([0] + ref._rtable[C.PTBL][:255]),  # pylint: disable=protected-access
        }
        self.ctrl, self.filter, self.x, self.ins, self.used = {}, {}, {}, {}, set()
        self.phrases, self.entered = {}, set()
        self.funk = [t + 1 for t in self.ref._funktable]  # pylint: disable=protected-access

    def _lt(self, table, ptr):
        return self.ref._lt(table, ptr)  # pylint: disable=protected-access

    def _rt(self, table, ptr):
        return self.ref._rt(table, ptr)  # pylint: disable=protected-access

    # ---- modulators ---------------------------------------------------------------------

    def instance(self, template: str, **params) -> str:
        """The ``x`` name of ``template`` with ``params``, defined once."""
        self.used.add(template)
        bag = ", ".join(f"{k}: {v}" for k, v in params.items())
        return self._named(self.x, "x", Atom(f"{template} {{{bag}}}" if bag else template))

    def _speed(self, idx: int) -> tuple:
        """``(note-relative, params)`` of speed table entry ``idx``: a 16-bit step ``s``, or
        the note's semitone step divided by ``d``."""
        if not idx:
            return False, {"s": 0}
        left, right = self._lt(C.STBL, idx), self._rt(C.STBL, idx)
        if left >= 0x80:
            return True, {"d": 1 << right}
        return False, {"s": left << 8 | right}

    def portamento(self, cmd: int, data: int, once: bool = False) -> str:
        """The ``x`` name of portamento ``cmd`` with speed entry ``data``; ``once``, one step."""
        calc, params = self._speed(data)
        if cmd == C.CMD_TONEPORTA:
            template = "snap" if not data else "tportan" if calc else "tporta"
            params = params if data else {}
        else:
            template = "portan" if calc else "porta"
            params = {"g": 1 if cmd == C.CMD_PORTAUP else -1} | params
        return self.instance(template + ("1" if once else ""), **params)

    def vibrato(self, idx: int, lag: int = 0) -> str:
        """The ``x`` name of the vibrato of speed entry ``idx`` after ``lag`` firings that
        change nothing; its phase is the voice's state ``t``."""
        cmp, right = (self._lt(C.STBL, idx), self._rt(C.STBL, idx)) if idx else (0, 0)
        if cmp >= 0x80:
            return self.instance("vibn", d=1 << right, c=cmp & 0x7F, l=lag)
        return self.instance("vib", s=right, c=cmp, l=lag)

    def command(self, cmd: int, data: int) -> str:
        """The ``x`` name of realtime command ``cmd`` with ``data``."""
        if cmd == C.CMD_VIBRATO:
            return self.vibrato(data)
        return self.portamento(cmd, data)

    def pulse(self, pos: int) -> str:
        """The ``x`` name of the pulse table from row ``pos``, read from ``PL``/``PR``, the
        player's own pulse table."""
        return self.instance("ptabs" if self.ref.simplepulse else "ptab", b=pos)

    # ---- programs -----------------------------------------------------------------------

    @staticmethod
    def _named(store: dict, prefix: str, value) -> str:
        for name, held in store.items():
            if held == value:
                return name
        name = f"{prefix}{len(store) + 1}"
        store[name] = value
        return name

    def _program(self, table: int, pos: int, row) -> list:
        """Rows from table position ``pos`` to its end or its loop; ``row(pos)`` gives a
        row's keys."""
        rows, at = [], {}
        while pos and pos not in at and self._lt(table, pos) != C.TABLEJUMP:
            at[pos] = len(rows)
            rows.append(_ordered(row(pos)))
            pos = (pos + 1) & 0xFF
            if self._lt(table, pos) == C.TABLEJUMP:
                pos = self._rt(table, pos)
        return rows + [FlowMap(loop=at[pos]) if pos in at else Atom("end")]

    def wave(self, pos: int, gated: bool = True) -> str:
        """The ``ctrl`` name of the wavetable program at ``pos``; not ``gated``, it first turns
        the gate the note set on off again, as a note whose first wave leaves it off. Where a
        row binds the wave slot to a step, its note rows bind it back to ``note``."""
        rows = self._program(C.WTBL, pos, self._wave_row) if pos else [Atom("end")]
        if any(isinstance(r, dict) and "wave.freq" in r for r in rows):
            rows = [
                (
                    _ordered(r | {"wave.freq": Atom("note")})
                    if isinstance(r, dict) and "note" in r
                    else r
                )
                for r in rows
            ]
        if not gated:
            rows = [FlowMap(hold=0, gate=0)] + [
                FlowMap(loop=r["loop"] + 1) if isinstance(r, dict) and "loop" in r else r
                for r in rows
            ]
        return self._named(self.ctrl, "c", Flow(rows))

    def silent(self) -> str:
        """The ``ctrl`` name of the program of no rows, which stops the control slot."""
        return self.wave(0)

    def _wave_row(self, pos: int) -> dict:
        wave, note = self._lt(C.WTBL, pos), self._rt(C.WTBL, pos)
        keys = {}
        if wave <= C.WAVELASTDELAY:
            if wave:
                keys["wait"] = wave
        elif wave < C.WAVESILENT:
            keys["ctrl"] = wave
        elif wave <= C.WAVELASTSILENT:
            keys["ctrl"] = wave & 0x0F
        else:
            return self._wave_command(wave & 0x0F, note)
        if note != 0x80:
            keys["note"] = Atom(f"+{note}" if note < 0x80 else note_name(note & 0x7F))
        return keys

    def _wave_command(self, cmd: int, arg: int) -> dict:
        """The row keys of wavetable command ``cmd`` with ``arg``."""
        stop = Atom("hold")

        def step():
            return {"wave.freq": Atom(self.portamento(cmd, arg, once=True))}

        keys = {
            C.CMD_SETAD: lambda: {"ad": arg},
            C.CMD_SETSR: lambda: {"sr": arg},
            C.CMD_SETWAVE: lambda: {"ctrl": arg},
            C.CMD_SETPULSEPTR: lambda: {"pw": Atom(self.pulse(arg)) if arg else stop},
            C.CMD_SETFILTERPTR: lambda: {"run": Atom(self.filt(arg))} if arg else {"cut": stop},
            C.CMD_SETFILTERCTRL: lambda: {"res": arg >> 4, "route": arg & 0x0F}
            | ({} if arg else {"cut": stop}),
            C.CMD_SETFILTERCUTOFF: lambda: {"cut": arg << 3},
            C.CMD_SETMASTERVOL: lambda: {"vol": arg} if arg < 0x10 else {},
            C.CMD_PORTAUP: step,
            C.CMD_PORTADOWN: step,
            C.CMD_TONEPORTA: step,
        }.get(cmd)
        if keys is None:
            raise TranscribeError(f"wavetable command {cmd:X}")
        return keys()

    def filt(self, pos: int) -> str:
        """The ``filter`` name of the filter program at ``pos``: one row per execution."""
        rows, at = [], {}
        while pos and pos not in at:
            if self._lt(C.FTBL, pos) == C.TABLEJUMP:
                pos = self._rt(C.FTBL, pos)
                continue
            at[pos] = len(rows)
            left, right = self._lt(C.FTBL, pos), self._rt(C.FTBL, pos)
            pos = (pos + 1) & 0xFF
            if left >= 0x80:
                keys = {"cut": "v", "res": right >> 4, "route": right & 0x0F, "mode": left & 0x70}
                if self._lt(C.FTBL, pos) == 0:
                    keys["cut"] = self._rt(C.FTBL, pos) << 3
                    pos = (pos + 1) & 0xFF
            elif left:
                keys = {"hold": left, "cut": canonical(f"v + {right << 3}")}
            else:
                keys = {"cut": right << 3}
            rows.append(_ordered(keys))
        if pos in at:
            rows.append(FlowMap(loop=at[pos]))
        else:
            rows += [FlowMap(cut="v"), Atom("end")]
        return self._named(self.filter, "f", Flow(rows))

    # ---- instruments and phrases --------------------------------------------------------

    def instrument(self, n: int) -> str:
        """The name of GoatTracker instrument ``n``."""
        name = f"i{n}"
        if name in self.ins:
            return name
        ins = self.ref._instr[n]  # pylint: disable=protected-access
        keys = {"ad": ins.ad, "sr": ins.sr}
        keys["ctrl"] = Atom(self.wave(ins.wave_ptr, self.gated(n)))
        if ins.pulse_ptr:
            keys["pw"] = Atom(self.pulse(ins.pulse_ptr))
        if ins.filter_ptr:
            keys["filter"] = Atom(self.filt(ins.filter_ptr))
        keys["wave.freq"], keys[TICK] = Atom("note"), Atom(self.tick(n))
        keys["restart"] = Flow(self._restart(ins))
        self.ins[name] = FlowMap(keys)
        return name

    def tick(self, n: int, state: tuple | None = None) -> str:
        """Instrument ``n``'s binding of the tick slot: its vibrato after its delay, or from
        delay left in vibrato ``state`` ``(phase, delay)`` it carries; ``none`` without one."""
        ins = self.ref._instr[n]  # pylint: disable=protected-access
        if not ins.speed_ptr:
            return "none"
        delay = state[1] if state else ins.vibdelay
        return self.vibrato(ins.speed_ptr, max(delay - 1, 0))

    def gated(self, n: int) -> bool:
        """Whether a note of instrument ``n`` leaves the gate on: a first wave sets it on, or
        no first wave keeps it where a note without hard restart leaves it."""
        ins = self.ref._instr[n]  # pylint: disable=protected-access
        if ins.firstwave:
            return ins.firstwave != 0xFE
        return bool(ins.gatetimer & 0x40)

    def _restart(self, ins) -> list:
        timer = ins.gatetimer & 0x3F
        first = {"hold": timer} if timer != 1 else {}
        if not ins.gatetimer & 0x40:
            first["gate"] = 0
            if not ins.gatetimer & 0x80:
                adparam = self.ref.adparam
                first.update(ad=adparam >> 8, sr=adparam & 0xFF)
        init = {"ad": ins.ad, "sr": ins.sr, "run": Atom(self.silent())}
        if ins.firstwave >= 0xFE:
            init["gate"] = ins.firstwave & 1
        elif ins.firstwave:
            init.update(ctrl=ins.firstwave, gate=1)
        return [_ordered(first), _ordered(init), FlowMap(), Atom("end")]

    def phrase(self, occ: Occurrence) -> str:
        """The name of the phrase pattern occurrence ``occ`` plays. Command 0 binds the
        instrument's tick slot after a frequency command, and at a pattern's first such row
        where some occurrence enters it with one running, from the vibrato state it carries."""
        rows = self.ref._patterns[occ.pattern]  # pylint: disable=protected-access
        events, instr = [], occ.instr
        run = (
            occ.running if occ.pattern in self.entered else None,
            occ.pattern not in self.entered,
        )
        for k, at in enumerate(range(0, len(rows) - 4, 4)):
            row = rows[at : at + 4]
            instr = row[1] or instr
            event, run = self._event(row, instr, occ.rows[k] if k < len(occ.rows) else (0, 0), run)
            events.append(event)
        return self.phrases.setdefault(tuple(rests(events)), f"p{len(self.phrases) + 1}")

    def _event(self, row, instr: int, state: tuple, run: tuple) -> tuple:
        """``(atom, (running command, tick slot bound))`` of one pattern row played by
        ``instr`` with vibrato ``state`` after its tick 0."""
        note, ins, cmd, data = row
        cur, bound = run
        noted = note <= C.LASTNOTE
        text = ""
        if noted and cmd != C.CMD_TONEPORTA:
            cur, bound = None, True
        if cmd in FREQ_CMDS:
            if cur != (cmd, data):
                text = f"!{TICK}:{self.command(cmd, data)}"
            cur, bound = (cmd, data), False
        elif cmd == C.CMD_DONOTHING:
            if not bound:
                text = f"!{TICK}:{self.tick(instr, state if cur else None)}"
            cur, bound = None, True
        else:
            text = self._command(cmd, data, instr, noted)
        if noted and cmd == C.CMD_TONEPORTA:
            text += "!legato"
        return Atom(self._atom(note, ins) + text), (cur, bound)

    def _atom(self, note: int, ins: int) -> str:
        if note <= C.LASTNOTE:
            atom = note_name(note - C.FIRSTNOTE)
        else:
            atom = {C.KEYOFF: "off", C.KEYON: "on"}.get(note, "-")
        return atom + (f".{self.instrument(ins)}" if ins else "")

    def _command(self, cmd: int, data: int, instr: int, noted: bool) -> str:
        """The text of one-shot command ``cmd`` with ``data`` on a row played by ``instr``,
        ``noted`` where it starts a note."""
        gated = not noted or self.gated(instr)
        text = {
            C.CMD_SETAD: lambda: f"!ad:{data}",
            C.CMD_SETSR: lambda: f"!sr:{data}",
            C.CMD_SETWAVE: lambda: f"!ctrl:{data}",
            C.CMD_SETWAVEPTR: lambda: f"!run:{self.wave(data, gated)}" if data else "!run:none",
            C.CMD_SETPULSEPTR: lambda: f"!pw:{self.pulse(data) if data else 'hold'}",
            C.CMD_SETFILTERPTR: lambda: f"!run:{self.filt(data)}" if data else "!cut:hold",
            C.CMD_SETFILTERCTRL: lambda: (
                f"!resfilt:{data >> 4}/{data & 0x0F}" if data else "!resfilt:0/0!cut:hold"
            ),
            C.CMD_SETFILTERCUTOFF: lambda: f"!cut:{data << 3}",
            C.CMD_SETMASTERVOL: lambda: f"!vol:{data}" if data < 0x10 else "",
            C.CMD_FUNKTEMPO: lambda: self._funk(data),
            C.CMD_SETTEMPO: lambda: self._tempo(data),
        }.get(cmd)
        return text() if text else ""

    def _funk(self, data: int) -> str:
        """Command E: rows alternate the speed table entry's two lengths (the last ones for 0)
        on every voice, from the first."""
        if data:
            self.funk = [
                ((self._lt(C.STBL, data) - 1) & 0xFF) + 1,
                ((self._rt(C.STBL, data) - 1) & 0xFF) + 1,
            ]
        return f"!tempo:{self.funk[0]}/{self.funk[1]}"

    def _tempo(self, data: int) -> str:
        """Command F: ticks per row for every voice, or this voice's with bit 7; 0 and 1 are
        the funk lengths from that one."""
        ticks, which = data & 0x7F, "v" if data >= 0x80 else ""
        if ticks < 2:
            return f"!{which}tempo:{self.funk[ticks]}/{self.funk[1 - ticks]}"
        return f"!{which}tempo:{max(ticks, 3)}"

    # ---- the document -------------------------------------------------------------------

    def slots(self) -> str:
        """The frequency's wavetable and realtime slots and the pulse slot, with the
        playroutine's per-tick skips as their holds."""
        ref = self.ref
        tick = ["step-1" if ref.optimize_realtime else "note-1", "row"]
        pulse = ["read", "note-1", "last-1"] if ref.optimize_pulse else ["note-1"]
        return SLOTS % (", ".join(tick), ", ".join(pulse))

    def timeline(self, run: Run) -> tuple:
        """``(rows, loop row or None)`` of the reference player's pattern starts."""
        self.entered = {o.pattern for occs in run.starts for o in occs if o.running}
        plays = {}
        for v, occs in enumerate(run.starts):
            for occ in occs:
                entry = self.phrase(occ)
                entry += f"{occ.trans:+d}" if occ.trans else ""
                plays.setdefault(occ.tick, [None] * C.MAX_CHN)[v] = Atom(entry)
        ticks = sorted(plays)
        rows = [Flow([Atom("p0")] * C.MAX_CHN)] + [Flow(plays[t]) for t in ticks[:-1]]
        loop = repeat([tuple(r) for r in rows[1:]], [b - a for a, b in zip(ticks, ticks[1:])])
        if loop is None:
            return rows, None
        return rows[: loop[0] + loop[1] + 1], loop[0] + 1

    def init(self) -> Flow:
        """The init events: instrument 1 on every voice, its vibrato running from the intro."""
        first = f"-.{self.instrument(1)}"
        speed = self.ref._instr[1].speed_ptr  # pylint: disable=protected-access
        intro = f"!{TICK}:{self.vibrato(speed, 1)}" if speed else ""
        return Flow(
            [Atom(f"{first}!vol:15!tempo:{LEAD}{intro}")]
            + [Atom(f"{first}{intro}")] * (C.MAX_CHN - 1)
        )

    def _defs(self, rowlen: int) -> dict:
        """The definitions: the lead-in and the patterns' phrases, the instruments, the
        programs, the templates used and their instances."""
        phrases = {"p0": Flow([Atom("-"), Atom(f"-!tempo:{rowlen}")])}
        phrases.update((name, Flow(events)) for events, name in self.phrases.items())
        self.used.add("note")
        defs = {"phrase": phrases, "ins": self.ins, "ctrl": self.ctrl}
        if self.filter:
            defs["filter"] = self.filter
        defs["mod"] = {k: _template(t) for k, t in TEMPLATES.items() if k in self.used}
        if self.x:
            defs["x"] = self.x
        return defs

    def document(self, frames: int = 200000) -> str:
        """The document of the song to its loop, sequenced over at most ``frames`` frames."""
        run = sequence(self.model, frames)
        rowlen = self.ref._default_tempo() + 1  # pylint: disable=protected-access
        if rowlen < 3:
            raise TranscribeError("funk default tempo")
        first = {occ[0].tick for occ in run.starts if occ}
        if first != {LEAD + rowlen}:
            raise TranscribeError(f"first patterns start at {sorted(first)}")
        rows, loop = self.timeline(run)
        init = self.init()
        song = [{"def": self._defs(rowlen), "play": rows[0]}] + [{"play": r} for r in rows[1:]]
        song.append(Atom("end") if loop is None else FlowMap(loop=loop))
        header = self.model.header
        doc = {
            "unitracker": Atom(VERSION),
            "meta": FlowMap(title=header.name, author=header.author, released=header.released),
            "tempo": rowlen,
            "tables": {k: Flow(v) for k, v in self.tables.items()},
            "init": init,
            "song": song,
        }
        text = yaml.dump(doc, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=WIDTH)
        head, rest = text.split("tables:", 1)
        return f"{head}slots: {self.slots()}\nstate: {STATE}\norder: {ORDER}\ntables:{rest}"


def _trimmed(values: list) -> list:
    """``values`` without its trailing zeros, which a table reads past its end anyway."""
    end = len(values)
    while end > 1 and not values[end - 1]:
        end -= 1
    return values[:end]


def _template(template):
    """A template's text: its ordered entries as a flow map, or an expression in its one
    text where the expression language prints it."""
    return FlowMap(template) if isinstance(template, dict) else _printed(template)


def _printed(text):
    try:
        return canonical(str(text)) if isinstance(text, str) else text
    except (ExpressionError, KeyError):
        return text


def rests(events: list) -> list:
    """Consecutive bare rests as one ``-*k``."""
    out = []
    for event in events:
        if event == "-" and out and (out[-1] == "-" or out[-1].startswith("-*")):
            k = int(out[-1][2:]) if out[-1] != "-" else 1
            out[-1] = Atom(f"-*{k + 1}")
        else:
            out.append(event)
    return out


def repeat(rows: list, gaps: list):
    """``(k, period)``: the earliest row from which the rows and their lengths repeat with the
    shortest period, seen at least twice; None where they do not."""
    keyed = list(zip(rows, gaps))
    n = len(keyed)
    for k in range(n):
        for period in range(1, (n - k) // 2 + 1):
            if all(keyed[i] == keyed[i + period] for i in range(k, n - period)):
                return k, period
    return None


def transcribe(raw: bytes, subtune: int = 0) -> tuple:
    """``(document text, decompiled model)`` of one subtune."""
    model = decompile_sid(raw, subtune)
    return Transcriber(model).document(), model


def compare(model, frames: int = 20000):
    """``(cells, document)``: the ``(tick, register, ours, theirs)`` cells where the
    document's grid differs from the reference player's over ``frames`` ticks."""
    text = Transcriber(model).document()
    ours = play(text, ticks=frames)
    theirs = np.asarray(model.player().render_grid(len(ours)), dtype=np.uint8)
    return gridcmp.cell_diff(ours, theirs), text


def verify(raw: bytes, subtune: int = 0, frames: int = 20000):
    """:func:`compare` of one subtune of a SID."""
    return compare(decompile_sid(raw, subtune), frames)


def summary(cells, runs: int = 0) -> str:
    """``exact``, or the first differing cell and the count; with ``runs``, that many of each
    register's differing runs."""
    if not cells.size:
        return "exact"
    out = [f"first {tuple(int(c) for c in cells[0][:2])} cells {len(cells)}"]
    for reg in gridcmp.runs(cells) if runs else ():
        spans = ", ".join(f"{r['from']}-{r['to']} {r['values'][0][2:]}" for r in reg["runs"][:runs])
        out.append(f"  {reg['name']} {reg['cells']}: {spans}")
    return "\n".join(out)


def _expired(*_):
    raise TimeoutError("over the time limit")


def _check(job: tuple) -> tuple:
    path, subtune, frames, runs, limit = job
    signal.signal(signal.SIGALRM, _expired)
    signal.alarm(limit)
    try:
        cells = verify(path.read_bytes(), subtune, frames)[0]
        return path, not cells.size, summary(cells, runs)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return path, False, f"{type(exc).__name__}: {exc}"
    finally:
        signal.alarm(0)


def main(argv=None) -> int:
    """Transcribe a SID, or verify SIDs against pygoattracker."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("sids", nargs="+", type=Path)
    parser.add_argument("--subtune", type=int, default=0)
    parser.add_argument("--frames", type=int, default=20000)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--diff", type=int, default=0, help="differing runs per register")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=600, help="seconds per SID")
    args = parser.parse_args(argv)
    if not args.verify:
        sys.stdout.write(transcribe(args.sids[0].read_bytes(), args.subtune)[0])
        return 0
    jobs = [(path, args.subtune, args.frames, args.diff, args.timeout) for path in args.sids]
    exact = 0
    with ProcessPoolExecutor(args.jobs) as pool:
        futures = [pool.submit(_check, job) for job in jobs]
        progress = tqdm(as_completed(futures), total=len(jobs), unit="sid")
        for done in progress:
            path, ok, verdict = done.result()
            exact += ok
            print(f"{path}: {verdict}", flush=True)
            progress.set_postfix(exact=exact)
    return 0


if __name__ == "__main__":
    sys.exit(main())
