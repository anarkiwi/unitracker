"""A GoatTracker 2 tune transcribed to the generic document from pygoattracker's playroutine.

A pattern row's step begins on tick 1; the playroutine's skips are the header's holds, its
wave and filter tables programs, its frequency commands and pulse table code
modulators (:mod:`unitracker.direct.gt2code`, docs/direct/goattracker2.md).
"""

import re
import sys
from dataclasses import dataclass, field

import numpy as np
from pygoattracker import constants as C
from pygoattracker import decompile_sid
from pygoattracker.player import Player
from pysidtracker import gridcmp
from unitracker_grammar import code, frequency
from unitracker_grammar.document import (
    CONT,
    HOLD,
    NONE,
    RESET,
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
    SongRow,
    printed,
    signature,
)
from unitracker_grammar.expr import canonical
from unitracker_player.player import play

from unitracker.direct import cli, traps
from unitracker.direct.gt2code import CODE, STATE

CLOCK = {"cycles_per_tick": 19656, "clock_hz": 985000}
EVENT = "n"
TICK = "tick.freq"
CUT = "filter.cut"
START_PW = "set.pw"
NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
FREQ_CMDS = (C.CMD_PORTAUP, C.CMD_PORTADOWN, C.CMD_TONEPORTA, C.CMD_VIBRATO)
LEAD = 2
INTRO = 64  # the ticks the start registers are fitted over
ROUTINES = {name: code.read(text) for name, text in CODE.items()}
PHASES = ("events", "filter", "cut", "ctrl", "pulse", "freq", "pw", "read")
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
    frequency command ``(command, data)`` running into it (or None), its instrument, the speed
    entry command 0 has latched entering it, and each
    row's ``[vibrato delay left entering its tick 0, funk lengths its next row reads, (command,
    vibrato delay left) at its fetch]``."""

    tick: int
    pattern: int
    trans: int
    running: tuple | None
    instr: int
    latched: int = 0
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


def sequence(model, frames: int, loops: int = 3) -> Run:  # pylint: disable=too-many-locals
    """Each channel's pattern starts until every played channel has looped ``loops`` times, or
    over ``frames`` frames of the reference player."""
    ref = model.player()
    order = layout(ref)
    nchan = len(ref._channels)  # pylint: disable=protected-access
    run, fetched, frame = Run(frames, [[] for _ in range(nchan)]), [None] * nchan, [0]
    rows = []
    native = {
        name: getattr(ref, name) for name in ("_get_new_notes", "_play_channel", "_play_call")
    }

    def fetch(channel, chan):
        fetched[channel] = (
            chan.pattnum,
            chan.pattptr // 4,
            chan.trans,
            (chan.command, chan.vibdelay),
        )
        native["_get_new_notes"](channel, chan)

    def channel_tick(channel):
        chan = ref._channels[channel]  # pylint: disable=protected-access
        if (chan.tick - 1) & 0xFF == 0 and fetched[channel] is not None:
            pattern, row, trans, at = fetched[channel]
            if row == 0:
                trans = trans - 0x100 if trans >= 0x80 else trans
                running = (chan.command, chan.cmddata) if chan.command in FREQ_CMDS else None
                run.starts[channel].append(
                    Occurrence(
                        frame[0] + 1, pattern, trans, running, chan.instr & 0x3F, chan.cmddata
                    )
                )
            fetched[channel] = None
            if run.starts[channel]:
                rows.append([chan.vibdelay, None, at])
                run.starts[channel][-1].rows.append(rows[-1])
        native["_play_channel"](channel)

    def call():
        native["_play_call"]()
        funk = tuple(t + 1 for t in ref._funktable)  # pylint: disable=protected-access
        for row in rows:
            row[1] = funk
        rows.clear()
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
        self.cut = False
        self.phrases, self.entered, self.live = {}, set(), {}
        self.funk = tuple(t + 1 for t in self.ref._funktable)  # pylint: disable=protected-access

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

    def vibrato(self, idx: int, lag: int | None = 0) -> Call:
        """The call of the vibrato of speed entry ``idx`` after ``lag`` firings that
        change nothing, or (None) one step; its phase is the voice's state ``t``."""
        cmp, right = (self._lt(C.STBL, idx), self._rt(C.STBL, idx)) if idx else (0, 0)
        once = {} if lag is None else {"l": lag}
        suffix = "1" if lag is None else ""
        if cmp >= 0x80:
            return self.call("vibn" + suffix, k=right, c=cmp & 0x7F, **once)
        return self.call("vib" + suffix, s=right, c=cmp, **once)

    def command(self, cmd: int, data: int) -> Call:
        """The call of realtime command ``cmd`` with ``data``."""
        if cmd == C.CMD_VIBRATO:
            return self.vibrato(data)
        return self.portamento(cmd, data)

    def pulse(self, pos: int) -> Call:
        """The call of the pulse table from row ``pos``, read from ``PL``/``PR``, the
        player's own pulse table."""
        return self.call("ptabs" if self.ref.simplepulse else "ptab", b=pos)

    def pulse_start(self, pos: int) -> dict:
        """The row keys that start the pulse table at row ``pos``: its call, and a ``reset`` of
        the pulse slot, so a table already running restarts instead of keeping its phi."""
        return {"pw": self.pulse(pos), "pulse.pw": RESET}

    def _set_cut(self, value: int) -> Once:
        """The cutoff command slot's binding to ``value``, once: a filter table running on
        steps from it."""
        self.cut = True
        return Once(value)

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
        row's keys. A jump to a jump row plays that row (a program starting on one, none)."""
        rows, at = [], {}
        if self._lt(table, pos) == C.TABLEJUMP:
            pos = 0
        while pos and pos not in at:
            at[pos] = len(rows)
            rows.append(Row(row(pos)))
            pos = (pos + 1) & 0xFF
            if self._lt(table, pos) == C.TABLEJUMP:
                pos = self._rt(table, pos)
        return Program(rows, at.get(pos))

    def wave(self, pos: int, gated: bool = True, held: bool = False) -> str:
        """The ``ctrl`` name of the wavetable program at ``pos``; not ``gated``, it first turns
        the gate the note set on off again, as a note whose first wave leaves it off. Where a
        row binds the wave slot to a step, or the slot may hold another binding (``held``: an
        instrument's ``v``, or a step a command 8 program starts after), its note rows bind it
        to ``note``."""
        program = self._program(C.WTBL, pos, self._wave_row) if pos else Program()
        rows, loop = program.rows, program.loop
        if held or any(r.get("wave.freq") is not None for r in rows):
            note = self.call("note")
            rows = [
                Row(dict(r.keys) | {"wave.freq": note}) if r.get("note") is not None else r
                for r in rows
            ]
        if not gated:
            rows = (Row({"hold": 0, "gate": 0}),) + tuple(rows)
            loop = None if loop is None else loop + 1
        return self._named(self.ctrl, "c", Program(rows, loop))

    def sounds(self, n: int) -> bool:
        """Whether instrument ``n``'s wavetable plays a note on its first tick; else a note of
        it keeps the frequency until a wavetable note row sets it."""
        pos = self.ref._instr[n].wave_ptr  # pylint: disable=protected-access
        wave = self._lt(C.WTBL, pos)
        return (
            bool(pos)
            and self._rt(C.WTBL, pos) != 0x80
            and wave < C.WAVECMD
            and (wave == 0 or wave > C.WAVELASTDELAY)
        )

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
        elif wave != C.TABLEJUMP:
            return self._wave_command(wave & 0x0F, note)
        if note != 0x80:
            relative = note - 0x80 if note >= 0x40 else note
            keys["note"] = Signed(relative) if note < 0x80 else note_name(note & 0x7F)
        return keys

    def _wave_command(self, cmd: int, arg: int) -> dict:
        """The row keys of wavetable command ``cmd`` with ``arg``."""

        def step():
            return {"wave.freq": self.portamento(cmd, arg, once=True)}

        keys = {
            C.CMD_SETAD: lambda: {"ad": arg},
            C.CMD_SETSR: lambda: {"sr": arg},
            C.CMD_SETWAVE: lambda: {"ctrl": arg},
            C.CMD_SETPULSEPTR: lambda: self.pulse_start(arg) if arg else {"pw": HOLD},
            C.CMD_SETFILTERPTR: lambda: {"run": self.filt(arg)},
            C.CMD_SETFILTERCTRL: lambda: {"res": arg >> 4, "route": arg & 0x0F}
            | ({} if arg else {"run": self.filt(0)}),
            C.CMD_SETFILTERCUTOFF: lambda: {"cut": self._set_cut(arg << 3)},
            C.CMD_SETMASTERVOL: lambda: {"vol": arg} if arg < 0x10 else {},
            C.CMD_PORTAUP: step,
            C.CMD_PORTADOWN: step,
            C.CMD_TONEPORTA: step,
            C.CMD_VIBRATO: lambda: {"wave.freq": self.vibrato(arg, None)},
        }.get(cmd)
        if keys is None:
            raise TranscribeError(f"wavetable command {cmd:X}")
        return keys()

    def filt(self, pos: int) -> str:
        """The ``filter`` name of the filter program at ``pos``: one row per execution; at 0,
        the program that stops the running one, keeping the cutoff."""
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
                    CUT: Expr("v"),
                    "res": right >> 4,
                    "route": right & 0x0F,
                    "mode": left & 0x70,
                }
                if self._lt(C.FTBL, pos) == 0:
                    keys[CUT] = self._rt(C.FTBL, pos) << 3
                    pos = (pos + 1) & 0xFF
            elif left:
                keys = {
                    "hold": left,
                    CUT: Expr(canonical(f"v + {(right - 0x100 if right >= 0x80 else right) << 3}")),
                }
            else:
                keys = {CUT: right << 3}
            rows.append(Row(keys))
        if pos not in at:
            rows.append(Row({CUT: Expr("v")}))
        return self._named(self.filter, "f", Program(rows, at.get(pos)))

    # ---- instruments and phrases --------------------------------------------------------

    def instrument(self, n: int, live=None) -> str:
        """The name of GoatTracker instrument ``n``; with ``live``, a copy whose restart binds
        the tick slot to it from the fetch, as a live-vibrato build's vibrato reads the
        instrument fetched while command 0 runs (numbered from ``MAX_INSTR``)."""
        if live is not None:
            keys = dict(self.ins[self.instrument(n)].fields)
            first, *rest = keys["restart"].rows
            keys["restart"] = Program([Row(dict(first.keys) | {TICK: live}), *rest])
            name = self.live.setdefault((n, live), f"i{C.MAX_INSTR + len(self.live)}")
            self.ins[name] = Instrument(keys)
            return name
        name = f"i{n}"
        if name in self.ins:
            return name
        ins = self.ref._instr[n]  # pylint: disable=protected-access
        keys = {"ad": ins.ad, "sr": ins.sr}
        keys["ctrl"] = self.wave(ins.wave_ptr, self.gated(n), not self.sounds(n))
        if ins.pulse_ptr:
            keys["pw"] = self.pulse(ins.pulse_ptr)
        if ins.filter_ptr:
            keys["filter"] = self.filt(ins.filter_ptr)
        keys["wave.freq"] = self.call("note") if self.sounds(n) else Expr("v")
        keys[TICK] = self.tick(n)
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
        instrument's tick slot after a frequency command or where it latches another speed
        entry than the one bound (the one ``occ`` enters with, but none at a pattern's first
        such row where some occurrence enters it with a command running), after the vibrato
        delay it carries; a row the run did not reach, as entering with none and the start's
        funk lengths."""
        rows = self.ref._patterns[occ.pattern]  # pylint: disable=protected-access
        events, instr = [], occ.instr
        run = (
            occ.running if occ.pattern in self.entered else None,
            None if occ.pattern in self.entered else occ.latched,
            None,
        )
        for k, at in enumerate(range(0, len(rows) - 4, 4)):
            row = rows[at : at + 4]
            instr = row[1] or instr
            played = occ.rows[k] if k < len(occ.rows) else (0, self.funk, None)
            event, run = self._event(row, instr, played, run)
            events.append(event)
        return self.phrases.setdefault(tuple(rests(events)), f"p{len(self.phrases) + 1}")

    def _event(  # pylint: disable=too-many-locals
        self, row, instr: int, played, run: tuple
    ) -> tuple:
        """``(atom, (running command, the speed entry of the vibrato the tick slot is bound to,
        instrument named last))`` of one pattern row played by ``instr``, ``played`` its
        vibrato delay left entering its tick 0, where the slot fires, the funk lengths its next
        row reads and the command and delay left at its fetch."""
        note, _, cmd, data = row
        delay, funk, fetch = played
        cur, bound, last = run
        noted = note <= C.LASTNOTE
        commands = {}
        speed = self.ref._instr[instr].speed_ptr  # pylint: disable=protected-access
        if noted and cmd != C.CMD_TONEPORTA:
            cur, bound = None, speed
        if cmd in FREQ_CMDS:
            if cur != (cmd, data):
                commands[TICK] = self.command(cmd, data)
            cur, bound = (cmd, data), None
        elif cmd == C.CMD_DONOTHING:
            if bound is None or bound != speed and not self.ref.build.live_vibrato:
                commands[TICK] = self.tick(instr, delay)
            cur, bound = None, speed
        else:
            commands = self._command(cmd, data, instr, noted, funk)
        if noted and cmd == C.CMD_TONEPORTA:
            commands["legato"] = True
        name, last = self._named_instrument(row, instr, fetch, last)
        return Event(self._atom(note), name, commands), (cur, bound, last)

    def _named_instrument(self, row, instr: int, fetch, last) -> tuple:
        """``(instrument the row names or None, instrument named last)``. In a live-vibrato
        build a note names the instrument whose restart it plays where it is not the one named
        last: with an instrument fetched while command 0 runs, the copy whose restart switches
        the vibrato to it."""
        note, ins, cmd, _ = row
        name = self.instrument(ins) if ins else None
        if not self.ref.build.live_vibrato or note > C.LASTNOTE or cmd == C.CMD_TONEPORTA:
            return name, last
        switch = ins and fetch and fetch[0] == C.CMD_DONOTHING
        want = self.instrument(instr, self.tick(instr, fetch[1]) if switch else None)
        return want if ins or want != last else None, want

    @staticmethod
    def _atom(note: int) -> str:
        if note <= C.LASTNOTE:
            return note_name(note - C.FIRSTNOTE)
        return {C.KEYOFF: "off", C.KEYON: "on"}.get(note, "-")

    def _command(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, cmd: int, data: int, instr: int, noted: bool, funk: tuple
    ) -> dict:
        """The commands of one-shot command ``cmd`` with ``data`` on a row played by
        ``instr``, ``noted`` where it starts a note, ``funk`` the funk lengths after it: command
        E's, which every voice alternates from the first."""
        gated = not noted or self.gated(instr)
        commands = {
            C.CMD_SETAD: lambda: {"ad": data},
            C.CMD_SETSR: lambda: {"sr": data},
            C.CMD_SETWAVE: lambda: {"ctrl": data},
            C.CMD_SETWAVEPTR: lambda: {"run": self.wave(data, gated, True) if data else NONE},
            C.CMD_SETPULSEPTR: lambda: self.pulse_start(data) if data else {"pw": HOLD},
            C.CMD_SETFILTERPTR: lambda: {"run": self.filt(data)},
            C.CMD_SETFILTERCTRL: lambda: {"res": data >> 4, "route": data & 0x0F}
            | ({} if data else {"run": self.filt(0)}),
            C.CMD_SETFILTERCUTOFF: lambda: {"cut": self._set_cut(data << 3)},
            C.CMD_SETMASTERVOL: lambda: {"vol": data} if data < 0x10 else {},
            C.CMD_FUNKTEMPO: lambda: {"tempo": funk},
            C.CMD_SETTEMPO: lambda: self._tempo(data, funk),
        }.get(cmd)
        return commands() if commands else {}

    @staticmethod
    def _tempo(data: int, funk: tuple) -> dict:
        """Command F: ticks per row for every voice, or this voice's with bit 7; 0 and 1 are
        the ``funk`` lengths from that one."""
        ticks, key = data & 0x7F, "vtempo" if data >= 0x80 else "tempo"
        if ticks < 2:
            return {key: (funk[ticks], funk[1 - ticks])}
        return {key: max(ticks, 3)}

    # ---- the document -------------------------------------------------------------------

    def slots(self, start_pw: bool = False) -> dict:
        """The frequency's wavetable and realtime slots and the pulse slot, with the
        playroutine's per-tick skips as their holds, ``start_pw`` the pulse width's start slot
        after it; where the song sets the cutoff, its command and filter table slots."""
        build = self.ref.build
        tick = ("step-1" if build.optimize_realtime else "note-1", "row")
        pulse = ("read", "note-1", "last-1") if build.optimize_pulse else ("note-1",)
        return {
            "freq": (Slot("wave", reset=("row.note", "row.freq")), Slot("tick", hold=tick)),
            "pw": (Slot("pulse", hold=pulse),) + ((Slot("set"),) if start_pw else ()),
        } | ({"cut": (Slot("set"), Slot("filter"))} if self.cut or self.filter else {})

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

    def init(self, undo: tuple = (), start: tuple | None = None) -> tuple:
        """The init events: instrument 1 on every voice, its vibrato running from the intro after
        the channel's delay left. From registers ``start``, the lag's tick lasts a tick less,
        tick 0 is the first play call, the freq cell starts at the channel's frequency, and
        ``undo`` holds the other ``(voice, command, value)`` cells the start registers set that
        the first tick must leave as the document would. The freq cell's state ``t`` and ``m``
        are the vibrato phase and last note the channel starts with."""
        # pylint: disable=protected-access
        ins = self.instrument(1)
        speed = self.ref._instr[1].speed_ptr
        lag = start is not None
        out = []
        for v, c in enumerate(self.order):
            chan = self.ref._channels[c]
            delay = max(chan.vibdelay - 1, 0) + 1 - lag
            intro = {TICK: self.vibrato(speed, delay)} if speed else {}
            commands = dict(self._undo(k, n) for u, k, n in undo if u == v and k != "freq")
            at = v * C.VOICE_REG_SIZE
            commands |= self._start_freq(chan, lag and chan.freq != start[at] | start[at + 1] << 8)
            if not v:
                commands = {"vol": 15, "tempo": LEAD - lag} | commands
            out.append(Event("-", ins, commands | intro))
        return tuple(out)

    def _start_freq(self, chan, put: bool) -> dict:
        """The freq binding that starts ``chan``'s frequency where ``put``, and its state ``t``
        and ``m``, once; none where neither differs from the document's."""
        freq = {"l": chan.freq & 0xFF, "h": chan.freq >> 8} if put else {}
        seed = {"i": chan.vibtime, "j": chan.lastnote} if chan.vibtime or chan.lastnote else {}
        if not freq and not seed:
            return {}
        name = ("start" if seed else "put") if freq else "seed"
        return {"freq": Once(self.call(name, **freq, **seed))}

    def _undo(self, cell: str, value: int) -> tuple:
        """``(key, binding)`` setting ``cell`` to ``value`` on the first tick: once for a
        modulated cell, the pulse width's in its start slot, which no skip holds."""
        if cell == "pw":
            return START_PW, Once(value)
        return cell, self._set_cut(value) if cell == "cut" else value

    def document(self, frames: int = 200000) -> str:
        """The document of the song to its loop, sequenced over at most ``frames`` frames. Where
        the reference player's first ticks differ from it, the start registers are the header's,
        the output lagging the document a tick: the first tick's registers, then the init
        events setting back the cells the tick after them shows differently. A full-ghosting
        build lags a tick more, from its ghost image, which its init call must leave as is."""
        text = self.build(frames)
        state = self.model.info.state
        ghost = int(bool(state and state.ghost))
        theirs = np.asarray(standard(self.model).render_grid(INTRO), dtype=np.uint8)
        have = play(text, ticks=INTRO)
        if not ghost and not gridcmp.cell_diff(have[:2], theirs[:2]).size:
            return text
        if (theirs[ghost] != theirs[0]).any():
            raise TranscribeError("a ghost image the init call changes")
        start = tuple(int(r) for r in theirs[0])
        pairs = zip(start_cells(theirs[1 + ghost]), start_cells(start), start_cells(have[0]))
        undo = tuple(c for c, *others in pairs if any(c != o for o in others))
        return self.build(frames, start, undo, 1 + ghost)

    def build(  # pylint: disable=too-many-locals
        self, frames: int, start: tuple | None = None, undo: tuple = (), lag: int = 1
    ) -> str:
        """The document over ``frames`` frames, from registers ``start`` ``lag`` ticks before
        tick 0 where given, ``undo`` the cells its first tick sets back."""
        run = sequence(self.model, frames)
        rowlen = self.ref._default_tempo() + 1  # pylint: disable=protected-access
        if rowlen < 3:
            raise TranscribeError("funk default tempo")
        if (first := {run.starts[c][0].tick for c in self.order if run.starts[c]}) != {
            LEAD + rowlen
        }:
            raise TranscribeError(f"first patterns start at {sorted(first)}")
        rows, loop = self.timeline(run)
        init = self.init(undo, start)
        phrases = [("p0", (Event("-"), Event("-", commands={"tempo": rowlen})))]
        phrases += [(name, events) for events, name in self.phrases.items()]
        defs = [Section("ins", tuple(self.ins.items()))]
        defs += [Section("ctrl", tuple((n, p) for n, p in self.ctrl.items()))]
        if self.filter:
            defs.append(Section("filter", tuple(self.filter.items())))
        defs.append(Section("phrase", phrases))
        info = self.model.header
        meta = {k: meta_text(v) for k, v in (("title", info.name), ("author", info.author))}
        meta["released"] = meta_text(info.released)
        head = Header(
            meta,
            clock=(CLOCK["cycles_per_tick"], CLOCK["clock_hz"]),
            tempo=rowlen,
            slots=self.slots(any(k == "pw" for _, k, _ in undo)),
            order=PHASES,
            state=self.state(),
            regs=start,
            lag=lag if start else None,
            voices=len(self.order) if len(self.order) != C.MAX_CHN else None,
        )
        doc = Document(head, self.tables, (), defs, init, [SongRow(r) for r in rows], loop)
        return printed(replace_mods(doc, self.used))


def start_cells(regs) -> list:
    """``regs`` as ``(voice, command, value)`` cells: a voice's frequency, pulse width,
    control, attack/decay and sustain/release, voice 0's filter and mode."""
    regs, out = [int(r) for r in regs], []
    for v in range(C.MAX_CHN):
        b = v * C.VOICE_REG_SIZE
        out += [
            (v, "freq", regs[b] | regs[b + 1] << 8),
            (v, "pw", regs[b + 2] | regs[b + 3] << 8),
        ]
        out += [(v, "ctrl", regs[b + 4]), (v, "ad", regs[b + 5]), (v, "sr", regs[b + 6])]
    return out + [
        (0, "cut", regs[21] & 7 | regs[22] << 3),
        (0, "res", regs[23] >> 4),
        (0, "route", regs[23] & 15),
        (0, "mode", regs[24] & 0xF0),
        (0, "vol", regs[24] & 15),
    ]


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


def trapped(model, ticks: int = 20000) -> tuple:
    """``(grid, traps)``: ``ticks`` of :func:`standard`'s player, and the table reads whose
    word in the native table differs from ``F(k)``'s."""
    # pylint: disable=protected-access
    native, ref, log = model.player()._freq_table, standard(model), traps.Log()
    freq, play_channel = ref._freq, ref._play_channel
    voice = [0]

    def read(note):
        return log.read(voice[0], note & 0x7F, native[note & 0x7F], freq(note))

    def channel(index):
        voice[0] = ref._voices[index] // C.VOICE_REG_SIZE
        return play_channel(index)

    log.frames(ref, "play_frame")
    ref._freq, ref._play_channel = read, channel
    return np.asarray(ref.render_grid(ticks), dtype=np.uint8), log()


def reference(raw: bytes, subtune: int = 0, ticks: int = 20000) -> tuple:
    """:func:`trapped` of one subtune of a SID."""
    return trapped(decompile_sid(raw, subtune), ticks)


def verify(raw: bytes, subtune: int = 0, frames: int = 20000):
    """``(cells, document, traps)`` of one subtune of a SID: the document's grid against the
    reference player's with its frequency table trapped (:func:`reference`)."""
    model = decompile_sid(raw, subtune)
    text = Transcriber(model).document()
    ours = play(text, ticks=frames)
    theirs, found = trapped(model, len(ours))
    return gridcmp.cell_diff(ours, theirs), text, found


summary = cli.summary


def main(argv=None) -> int:
    """Transcribe a SID, or verify SIDs against pygoattracker."""
    return cli.main(
        argv, __doc__.split("\n", 1)[0], lambda raw, subtune: transcribe(raw, subtune)[0], verify
    )


if __name__ == "__main__":
    sys.exit(main())
