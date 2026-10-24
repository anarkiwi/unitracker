"""A GoatTracker 2 tune transcribed to the generic document from pygoattracker's playroutine.

A pattern row's step begins on tick 1; the playroutine's skips are the header's holds, its
wave and filter tables programs, its frequency commands and pulse table code
modulators (:mod:`unitracker.direct.gt2code`, docs/direct/goattracker2.md).
"""

import argparse
import re
import signal
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from pygoattracker import constants as C
from pygoattracker import decompile_sid
from pygoattracker.player import Player
from pysidtracker import gridcmp
from tqdm import tqdm
from unitracker_grammar import code, frequency
from unitracker_grammar.document import (
    CONT,
    HOLD,
    NONE,
    Call,
    Code,
    Document,
    Event,
    Expr,
    Header,
    Instrument,
    Play,
    Program,
    Row,
    Section,
    Signed,
    Slot,
    SongRow,
    printed,
    signature,
)
from unitracker_grammar.expr import canonical
from unitracker_player.player import play

from unitracker.direct.gt2code import CODE, STATE

CLOCK = {"cycles_per_tick": 19656, "clock_hz": 985000}
EVENT = "n"
TICK = "tick.freq"
NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
FREQ_CMDS = (C.CMD_PORTAUP, C.CMD_PORTADOWN, C.CMD_TONEPORTA, C.CMD_VIBRATO)
LEAD = 2
ROUTINES = {name: code.read(text) for name, text in CODE.items()}
PHASES = ("events", "ctrl", "pulse", "filter", "freq", "pw", "cut", "read")
ORDER = f"[{', '.join(PHASES)}]"
_PLAIN = re.compile(r"[^\s,\[\]{}'\"#&*!|>%@`:?-][^,\[\]{}]*")
_YAML_WORDS = re.compile(r"(?i:y|n|yes|no|true|false|on|off|null|~)|[-+]?\d[\d_.:eE+-]*")


class TranscribeError(ValueError):
    """The tune uses something this transcription does not spell."""


def note_name(k: int) -> str:
    """Generic note ``k`` (12 * octave + pitch class) by name."""
    return f"{NAMES[k % 12]}{k // 12}"


def meta_text(value: str) -> str:
    """A meta value as the document writes it: plain where YAML reads it back as that text,
    else single-quoted."""
    plain = _PLAIN.fullmatch(value) and not _YAML_WORDS.fullmatch(value)
    if plain and ": " not in value and " #" not in value and value == value.strip():
        return value
    return "'" + value.replace("'", "''") + "'"


@dataclass
class Occurrence:
    """A pattern a voice starts: the generic tick of its first step, its transpose, the
    frequency command ``(command, data)`` running into it (or None), its instrument, and each
    row's vibrato delay left entering its tick 0."""

    tick: int
    pattern: int
    trans: int
    running: tuple | None
    instr: int
    rows: list = field(default_factory=list)


@dataclass
class Run:
    """The reference player's sequencer over ``frames`` frames: each channel's pattern starts."""

    frames: int
    starts: list


def layout(ref) -> tuple:
    """The channels of ``ref``'s build in the order of the SID voices they play, one voice from
    ``$D400`` each; a build whose played channels do not fill voices from the first one, that
    runs more than one player tick a frame, or that masks its control writes, is refused."""
    build, voices = ref.build, ref._voices  # pylint: disable=protected-access
    if build.calls != 1:
        raise TranscribeError(f"multispeed: {build.calls} player ticks a frame")
    if any(mask not in (None, (0xFF, 0)) for mask in build.overlay or ()):
        raise TranscribeError(f"control writes masked by an overlay {build.overlay}")
    played = {c: off for c, off in enumerate(voices) if c not in build.idle}
    if None in played.values():
        raise TranscribeError(f"voices {voices}: a played channel writes no SID voice")
    if len(played) > C.MAX_CHN or any(off >= 0x20 for off in played.values()):
        raise TranscribeError(f"voices {voices}: more than one SID")
    order = sorted(played, key=played.get)
    if [played[c] for c in order] != [v * C.VOICE_REG_SIZE for v in range(len(order))]:
        raise TranscribeError(f"voices {voices}: not the first voices")
    return tuple(order)


def sequence(model, frames: int, loops: int = 3) -> Run:
    """Each channel's pattern starts until every played channel has looped ``loops`` times, or
    over ``frames`` frames of the reference player."""
    ref = model.player()
    order = layout(ref)
    nchan = len(ref._channels)  # pylint: disable=protected-access
    run, fetched, frame = Run(frames, [[] for _ in range(nchan)]), [None] * nchan, [0]
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
        if (chan.tick - 1) & 0xFF == 0 and fetched[channel] is not None:
            pattern, row, trans = fetched[channel]
            if row == 0:
                trans = trans - 0x100 if trans >= 0x80 else trans
                running = (chan.command, chan.cmddata) if chan.command in FREQ_CMDS else None
                run.starts[channel].append(
                    Occurrence(frame[0] + 1, pattern, trans, running, chan.instr & 0x3F)
                )
            fetched[channel] = None
            if run.starts[channel]:
                run.starts[channel][-1].rows.append(chan.vibdelay)
        play_channel(channel)

    def call():
        play_call()
        frame[0] += 1

    ref._get_new_notes = fetch  # pylint: disable=protected-access
    ref._play_channel = channel_tick  # pylint: disable=protected-access
    ref._play_call = call  # pylint: disable=protected-access
    while frame[0] < frames and min(ref.loops[c] for c in order) < loops:
        ref.play_frame()
    return run


class Transcriber:  # pylint: disable=too-many-instance-attributes
    """One subtune of a decompiled GoatTracker 2 SID as a generic document."""

    def __init__(self, model):
        self.model = model
        self.ref = model.player()
        ref = self.ref
        self.order = layout(ref)
        self.tables = {
            "PL": _trimmed([0] + ref._ltable[C.PTBL][:255]),  # pylint: disable=protected-access
            "PR": _trimmed([0] + ref._rtable[C.PTBL][:255]),  # pylint: disable=protected-access
        }
        self.ctrl, self.filter, self.ins, self.used = {}, {}, {}, set()
        self.phrases, self.entered = {}, set()
        self.funk = [t + 1 for t in self.ref._funktable]  # pylint: disable=protected-access

    def _lt(self, table, ptr):
        return self.ref._lt(table, ptr)  # pylint: disable=protected-access

    def _rt(self, table, ptr):
        return self.ref._rt(table, ptr)  # pylint: disable=protected-access

    # ---- modulators ---------------------------------------------------------------------

    def call(self, template: str, **params) -> Call:
        """The call of ``template`` with ``params``, its arguments in its signature's order."""
        self.used.add(template)
        return Call(template, tuple(params[k] for k in code.params(ROUTINES[template])))

    def _speed(self, idx: int) -> tuple:
        """``(note-relative, params)`` of speed table entry ``idx``: a 16-bit step ``s``, or
        the note's semitone step shifted right by ``k``."""
        if not idx:
            return False, {"s": 0}
        left, right = self._lt(C.STBL, idx), self._rt(C.STBL, idx)
        if left >= 0x80:
            return True, {"k": right}
        return False, {"s": left << 8 | right}

    def portamento(self, cmd: int, data: int, once: bool = False) -> Call:
        """The call of portamento ``cmd`` with speed entry ``data``; ``once``, one step."""
        calc, params = self._speed(data)
        if cmd == C.CMD_TONEPORTA:
            template = "snap" if not data else "tportan" if calc else "tporta"
            params = params if data else {}
        else:
            template = "portan" if calc else "porta"
            params = {"g": 1 if cmd == C.CMD_PORTAUP else -1} | params
        return self.call(template + ("1" if once else ""), **params)

    def vibrato(self, idx: int, lag: int = 0) -> Call:
        """The call of the vibrato of speed entry ``idx`` after ``lag`` firings that
        change nothing; its phase is the voice's state ``t``."""
        cmp, right = (self._lt(C.STBL, idx), self._rt(C.STBL, idx)) if idx else (0, 0)
        if cmp >= 0x80:
            return self.call("vibn", k=right, c=cmp & 0x7F, l=lag)
        return self.call("vib", s=right, c=cmp, l=lag)

    def command(self, cmd: int, data: int) -> Call:
        """The call of realtime command ``cmd`` with ``data``."""
        if cmd == C.CMD_VIBRATO:
            return self.vibrato(data)
        return self.portamento(cmd, data)

    def pulse(self, pos: int) -> Call:
        """The call of the pulse table from row ``pos``, read from ``PL``/``PR``, the
        player's own pulse table."""
        return self.call("ptabs" if self.ref.simplepulse else "ptab", b=pos)

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
            rows.append(Row(row(pos)))
            pos = (pos + 1) & 0xFF
            if self._lt(table, pos) == C.TABLEJUMP:
                pos = self._rt(table, pos)
        return Program(rows, at.get(pos))

    def wave(self, pos: int, gated: bool = True) -> str:
        """The ``ctrl`` name of the wavetable program at ``pos``; not ``gated``, it first turns
        the gate the note set on off again, as a note whose first wave leaves it off. Where a
        row binds the wave slot to a step, its note rows bind it back to ``note``."""
        program = self._program(C.WTBL, pos, self._wave_row) if pos else Program()
        rows, loop = program.rows, program.loop
        if any(r.get("wave.freq") is not None for r in rows):
            note = self.call("note")
            rows = [
                Row(dict(r.keys) | {"wave.freq": note}) if r.get("note") is not None else r
                for r in rows
            ]
        if not gated:
            rows = (Row({"hold": 0, "gate": 0}),) + tuple(rows)
            loop = None if loop is None else loop + 1
        return self._named(self.ctrl, "c", Program(rows, loop))

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
            relative = note - 0x80 if note >= 0x40 else note
            keys["note"] = Signed(relative) if note < 0x80 else note_name(note & 0x7F)
        return keys

    def _wave_command(self, cmd: int, arg: int) -> dict:
        """The row keys of wavetable command ``cmd`` with ``arg``."""
        stop = HOLD

        def step():
            return {"wave.freq": self.portamento(cmd, arg, once=True)}

        keys = {
            C.CMD_SETAD: lambda: {"ad": arg},
            C.CMD_SETSR: lambda: {"sr": arg},
            C.CMD_SETWAVE: lambda: {"ctrl": arg},
            C.CMD_SETPULSEPTR: lambda: {"pw": self.pulse(arg) if arg else stop},
            C.CMD_SETFILTERPTR: lambda: {"run": self.filt(arg)} if arg else {"cut": stop},
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
                keys = {
                    "cut": Expr("v"),
                    "res": right >> 4,
                    "route": right & 0x0F,
                    "mode": left & 0x70,
                }
                if self._lt(C.FTBL, pos) == 0:
                    keys["cut"] = self._rt(C.FTBL, pos) << 3
                    pos = (pos + 1) & 0xFF
            elif left:
                keys = {
                    "hold": left,
                    "cut": Expr(
                        canonical(f"v + {(right - 0x100 if right >= 0x80 else right) << 3}")
                    ),
                }
            else:
                keys = {"cut": right << 3}
            rows.append(Row(keys))
        if pos not in at:
            rows.append(Row({"cut": Expr("v")}))
        return self._named(self.filter, "f", Program(rows, at.get(pos)))

    # ---- instruments and phrases --------------------------------------------------------

    def instrument(self, n: int) -> str:
        """The name of GoatTracker instrument ``n``."""
        name = f"i{n}"
        if name in self.ins:
            return name
        ins = self.ref._instr[n]  # pylint: disable=protected-access
        keys = {"ad": ins.ad, "sr": ins.sr}
        keys["ctrl"] = self.wave(ins.wave_ptr, self.gated(n))
        if ins.pulse_ptr:
            keys["pw"] = self.pulse(ins.pulse_ptr)
        if ins.filter_ptr:
            keys["filter"] = self.filt(ins.filter_ptr)
        keys["wave.freq"], keys[TICK] = self.call("note"), self.tick(n)
        keys["restart"] = self._restart(ins)
        self.ins[name] = Instrument(keys)
        return name

    def tick(self, n: int, delay: int | None = None) -> Call | str:
        """Instrument ``n``'s binding of the tick slot: its vibrato after its delay, or after
        the ``delay`` left that it carries; ``none`` without one. Its phase is the state ``t``."""
        ins = self.ref._instr[n]  # pylint: disable=protected-access
        if not ins.speed_ptr:
            return NONE
        delay = ins.vibdelay if delay is None else delay
        return self.vibrato(ins.speed_ptr, max(delay - 1, 0))

    def gated(self, n: int) -> bool:
        """Whether a note of instrument ``n`` leaves the gate on: a first wave sets it on, or
        no first wave keeps it where a note without hard restart leaves it."""
        ins = self.ref._instr[n]  # pylint: disable=protected-access
        if ins.firstwave:
            return ins.firstwave != 0xFE
        return bool(ins.gatetimer & 0x40)

    def _restart(self, ins) -> Program:
        timer = ins.gatetimer & 0x3F
        first = {"hold": timer} if timer != 1 else {}
        if not ins.gatetimer & 0x40:
            first["gate"] = 0
            if not ins.gatetimer & 0x80:
                adparam = self.ref.build.adparam
                first.update(ad=adparam >> 8, sr=adparam & 0xFF)
        init = {"ad": ins.ad, "sr": ins.sr, "run": self.silent()}
        if ins.firstwave >= 0xFE:
            init["gate"] = ins.firstwave & 1
        elif ins.firstwave:
            init.update(ctrl=ins.firstwave, gate=1)
        return Program([Row(first), Row(init), Row()])

    def phrase(self, occ: Occurrence) -> str:
        """The name of the phrase pattern occurrence ``occ`` plays. Command 0 binds the
        instrument's tick slot after a frequency command, and at a pattern's first such row
        where some occurrence enters it with one running, after the vibrato delay it carries."""
        rows = self.ref._patterns[occ.pattern]  # pylint: disable=protected-access
        events, instr = [], occ.instr
        run = (
            occ.running if occ.pattern in self.entered else None,
            occ.pattern not in self.entered,
        )
        for k, at in enumerate(range(0, len(rows) - 4, 4)):
            row = rows[at : at + 4]
            instr = row[1] or instr
            event, run = self._event(row, instr, occ.rows[k] if k < len(occ.rows) else 0, run)
            events.append(event)
        return self.phrases.setdefault(tuple(rests(events)), f"p{len(self.phrases) + 1}")

    def _event(self, row, instr: int, delay: int, run: tuple) -> tuple:
        """``(atom, (running command, tick slot bound))`` of one pattern row played by
        ``instr`` with vibrato ``delay`` left entering its tick 0, where the slot fires."""
        note, ins, cmd, data = row
        cur, bound = run
        noted = note <= C.LASTNOTE
        commands = {}
        if noted and cmd != C.CMD_TONEPORTA:
            cur, bound = None, True
        if cmd in FREQ_CMDS:
            if cur != (cmd, data):
                commands[TICK] = self.command(cmd, data)
            cur, bound = (cmd, data), False
        elif cmd == C.CMD_DONOTHING:
            if not bound:
                commands[TICK] = self.tick(instr, delay)
            cur, bound = None, True
        else:
            commands = self._command(cmd, data, instr, noted)
        if noted and cmd == C.CMD_TONEPORTA:
            commands["legato"] = True
        return Event(self._atom(note), self.instrument(ins) if ins else None, commands), (
            cur,
            bound,
        )

    @staticmethod
    def _atom(note: int) -> str:
        if note <= C.LASTNOTE:
            return note_name(note - C.FIRSTNOTE)
        return {C.KEYOFF: "off", C.KEYON: "on"}.get(note, "-")

    def _command(self, cmd: int, data: int, instr: int, noted: bool) -> dict:
        """The commands of one-shot command ``cmd`` with ``data`` on a row played by
        ``instr``, ``noted`` where it starts a note."""
        gated = not noted or self.gated(instr)
        commands = {
            C.CMD_SETAD: lambda: {"ad": data},
            C.CMD_SETSR: lambda: {"sr": data},
            C.CMD_SETWAVE: lambda: {"ctrl": data},
            C.CMD_SETWAVEPTR: lambda: {"run": self.wave(data, gated) if data else NONE},
            C.CMD_SETPULSEPTR: lambda: {"pw": self.pulse(data) if data else HOLD},
            C.CMD_SETFILTERPTR: lambda: {"run": self.filt(data)} if data else {"cut": HOLD},
            C.CMD_SETFILTERCTRL: lambda: {"res": data >> 4, "route": data & 0x0F}
            | ({} if data else {"cut": HOLD}),
            C.CMD_SETFILTERCUTOFF: lambda: {"cut": data << 3},
            C.CMD_SETMASTERVOL: lambda: {"vol": data} if data < 0x10 else {},
            C.CMD_FUNKTEMPO: lambda: self._funk(data),
            C.CMD_SETTEMPO: lambda: self._tempo(data),
        }.get(cmd)
        return commands() if commands else {}

    def _funk(self, data: int) -> dict:
        """Command E: rows alternate the speed table entry's two lengths (the last ones for 0)
        on every voice, from the first."""
        if data:
            self.funk = [
                ((self._lt(C.STBL, data) - 1) & 0xFF) + 1,
                ((self._rt(C.STBL, data) - 1) & 0xFF) + 1,
            ]
        return {"tempo": (self.funk[0], self.funk[1])}

    def _tempo(self, data: int) -> dict:
        """Command F: ticks per row for every voice, or this voice's with bit 7; 0 and 1 are
        the funk lengths from that one."""
        ticks, key = data & 0x7F, "vtempo" if data >= 0x80 else "tempo"
        if ticks < 2:
            return {key: (self.funk[ticks], self.funk[1 - ticks])}
        return {key: max(ticks, 3)}

    # ---- the document -------------------------------------------------------------------

    def slots(self) -> dict:
        """The frequency's wavetable and realtime slots and the pulse slot, with the
        playroutine's per-tick skips as their holds."""
        build = self.ref.build
        tick = ("step-1" if build.optimize_realtime else "note-1", "row")
        pulse = ("read", "note-1", "last-1") if build.optimize_pulse else ("note-1",)
        return {
            "freq": (Slot("wave", reset=("row.note", "row.freq")), Slot("tick", hold=tick)),
            "pw": (Slot("pulse", hold=pulse),),
        }

    def state(self) -> dict | None:
        """The state line: per cell, the variables the routines used name."""
        named = {s for t in self.used for s in code.symbols(ROUTINES[t])}
        cells = {
            cell: tuple((k, n) for k, n in names.items() if k in named)
            for cell, names in STATE.items()
        }
        return {cell: names for cell, names in cells.items() if names} or None

    def timeline(self, run: Run) -> tuple:
        """``(rows, loop row or None)`` of the reference player's pattern starts, a voice
        each played channel."""
        starts = [run.starts[c] for c in self.order]
        self.entered = {o.pattern for occs in starts for o in occs if o.running}
        plays, voices = {}, len(starts)
        for v, occs in enumerate(starts):
            for occ in occs:
                entry = Play(self.phrase(occ), Signed(occ.trans) if occ.trans else None)
                plays.setdefault(occ.tick, [CONT] * voices)[v] = entry
        ticks = sorted(plays)
        rows = [(Play("p0"),) * voices] + [tuple(plays[t]) for t in ticks[:-1]]
        loop = repeat(rows[1:], [b - a for a, b in zip(ticks, ticks[1:])])
        if loop is None:
            return rows, None
        return rows[: loop[0] + loop[1] + 1], loop[0] + 1

    def init(self) -> tuple:
        """The init events: instrument 1 on every voice, its vibrato running from the intro."""
        ins = self.instrument(1)
        speed = self.ref._instr[1].speed_ptr  # pylint: disable=protected-access
        intro = {TICK: self.vibrato(speed, 1)} if speed else {}
        first = Event("-", ins, {"vol": 15, "tempo": LEAD} | intro)
        return (first,) + (Event("-", ins, intro),) * (len(self.order) - 1)

    def document(self, frames: int = 200000) -> str:
        """The document of the song to its loop, sequenced over at most ``frames`` frames."""
        run = sequence(self.model, frames)
        rowlen = self.ref._default_tempo() + 1  # pylint: disable=protected-access
        if rowlen < 3:
            raise TranscribeError("funk default tempo")
        first = {run.starts[c][0].tick for c in self.order if run.starts[c]}
        if first != {LEAD + rowlen}:
            raise TranscribeError(f"first patterns start at {sorted(first)}")
        rows, loop = self.timeline(run)
        init = self.init()
        phrases = [("p0", (Event("-"), Event("-", commands={"tempo": rowlen})))]
        phrases += [(name, events) for events, name in self.phrases.items()]
        defs = [Section("ins", tuple(self.ins.items()))]
        defs += [Section("ctrl", tuple((n, p) for n, p in self.ctrl.items()))]
        if self.filter:
            defs.append(Section("filter", tuple(self.filter.items())))
        defs.append(Section("phrase", phrases))
        song = [SongRow(r) for r in rows]
        header = self.model.header
        meta = {k: meta_text(v) for k, v in (("title", header.name), ("author", header.author))}
        meta["released"] = meta_text(header.released)
        head = Header(
            meta,
            clock=(CLOCK["cycles_per_tick"], CLOCK["clock_hz"]),
            tempo=rowlen,
            slots=self.slots(),
            order=PHASES,
            state=self.state(),
            voices=len(self.order) if len(self.order) != C.MAX_CHN else None,
        )
        doc = Document(head, self.tables, (), defs, init, song, loop)
        return printed(replace_mods(doc, self.used))


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


def replace_mods(doc: Document, used: set) -> Document:
    """``doc`` with its mod section: each template used, by its signature, in first-call order."""
    order = [n for n in dict.fromkeys(_calls(doc)) if n in used]
    mods = tuple((signature(n, code.params(ROUTINES[n])), Code(ROUTINES[n])) for n in order)
    return Document(doc.header, doc.tables, mods, doc.defs, doc.init, doc.rows, doc.loop)


def _trimmed(values: list) -> list:
    """``values`` without its trailing zeros, which a table reads past its end anyway."""
    end = len(values)
    while end > 1 and not values[end - 1]:
        end -= 1
    return values[:end]


REST = Event("-")


def rests(events: list) -> list:
    """Consecutive bare rests as one ``-*k``."""
    out = []
    for event in events:
        last = out[-1] if out else None
        if event == REST and last is not None and Event("-", steps=last.steps) == last:
            out[-1] = Event("-", steps=(last.steps or 1) + 1)
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


def standard(model) -> Player:
    """The reference player of ``model`` with the document's note table: every index ``k`` the
    player reads its table at sounds ``F(k)`` at :data:`CLOCK`."""
    table = [frequency(k, CLOCK["clock_hz"]) for k in range(len(C.FREQ_TABLE))]
    info = model.info
    return Player(
        model.typed,
        subtune=model.subtune,
        build=info.playback,
        state=info.state,
        freq_table=table,
    )


def compare(model, frames: int = 20000, native: bool = False):
    """``(cells, document)``: the ``(tick, register, ours, theirs)`` cells where the
    document's grid differs from the reference player's at the standard note table
    (:func:`standard`), or ``native`` at its own, over ``frames`` ticks."""
    text = Transcriber(model).document()
    ours = play(text, ticks=frames)
    ref = model.player() if native else standard(model)
    theirs = np.asarray(ref.render_grid(len(ours)), dtype=np.uint8)
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
