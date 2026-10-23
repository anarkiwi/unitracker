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
from pygoattracker.player import _Channel
from pysidtracker import gridcmp
from tqdm import tqdm
from unitracker_grammar.expr import canonical
from unitracker_player.player import play

from unitracker.document.yamlstyle import Atom, Dumper, Flow, FlowMap

VERSION = "1.20"
WIDTH = 100
NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
PHI = "φ"
ROW_KEYS = "wait hold ctrl gate note ad sr pw cut res route mode freq run filter vol".split()
FREQ_CMDS = (C.CMD_PORTAUP, C.CMD_PORTADOWN, C.CMD_TONEPORTA, C.CMD_VIBRATO)
LEAD = 2


class TranscribeError(ValueError):
    """The tune uses something this transcription does not spell."""


def note_name(k: int) -> str:
    """Generic note ``k`` (12 * octave + pitch class) by name."""
    return f"{NAMES[k % 12]}{k // 12}"


def letters(i: int) -> str:
    """``i`` as lowercase letters, a table name's suffix."""
    out = ""
    while True:
        out = chr(ord("a") + i % 26) + out
        i = i // 26 - 1
        if i < 0:
            return out


def orbit(step, state):
    """``(outputs, prefix, period)`` of ``state -> (state', out)`` iterated from ``state``
    until a state recurs: output ``i`` is the ``i``-th step's."""
    seen, outs = {}, []
    while state not in seen:
        seen[state] = len(outs)
        state, out = step(state)
        outs.append(out)
    return outs, seen[state], len(outs) - seen[state]


def index(prefix: int, period: int, phi: str = PHI) -> str:
    """The orbit index of firing ``phi``: ``phi`` in the prefix, then around the cycle."""
    if period == 1:
        return f"min({phi}, {prefix})"
    if not prefix:
        return f"{phi} % {period}"
    return f"min({phi}, {prefix}) + ({phi} >= {prefix})*(({phi} - {prefix}) % {period})"


def _ordered(keys: dict) -> FlowMap:
    return FlowMap((k, keys[k]) for k in ROW_KEYS if k in keys)


@dataclass
class Occurrence:
    """A pattern a voice starts: the generic tick of its first step, its transpose, the
    frequency command ``(command, data)`` running into it (or None) and its instrument."""

    tick: int
    pattern: int
    trans: int
    running: tuple | None
    instr: int


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
        self.tables = {"G": list(self.ref._freq_table)[:128]}  # pylint: disable=protected-access
        self.ctrl, self.pw, self.filter, self.x, self.ins = {}, {}, {}, {}, {}
        self.phrases = {}

    # ---- tables -------------------------------------------------------------------------

    def _lt(self, table, ptr):
        return self.ref._lt(table, ptr)  # pylint: disable=protected-access

    def _rt(self, table, ptr):
        return self.ref._rt(table, ptr)  # pylint: disable=protected-access

    def _table(self, prefix: str, values: list) -> str:
        """The name of a table holding ``values``, defined once."""
        for name, held in self.tables.items():
            if name.startswith(prefix) and held == values:
                return name
        name = prefix + letters(sum(n.startswith(prefix) for n in self.tables))
        self.tables[name] = values
        return name

    def _speed(self, idx: int) -> str:
        """Speed table entry ``idx`` as a 16-bit step, or the note-relative calculated one."""
        if not idx:
            return "0"
        left, right = self._lt(C.STBL, idx), self._rt(C.STBL, idx)
        if left >= 0x80:
            return f"((G[N + 1] - G[N]) % 65536) // {1 << right}"
        return str(left << 8 | right)

    def _vibrato(self, idx: int):
        """``(signs table, prefix, period, speed)`` of vibrato speed entry ``idx``: the sign
        bit of each firing from a vibrato phase of 0."""
        cmp = self._lt(C.STBL, idx) if idx else 0
        speed = str(self._rt(C.STBL, idx)) if idx else "0"
        if cmp >= 0x80:
            cmp &= 0x7F
            speed = f"((G[N + 1] - G[N]) % 65536) // {1 << self._rt(C.STBL, idx)}"

        def fire(vt):
            if cmp < vt < 0x80:
                vt ^= 0xFF
            vt = (vt + 2) & 0xFF
            return vt, vt & 1

        bits, prefix, period = orbit(fire, 0)
        return self._table("V", bits), prefix, period, speed

    # ---- modulators ---------------------------------------------------------------------

    def command(self, cmd: int, data: int, noted: bool = True, lag: int = 0) -> str:
        """The ``x`` name of realtime command ``cmd`` with ``data``: ``noted``, the note's
        frequency until its first firing; its first ``lag`` firings change nothing."""
        if cmd == C.CMD_PORTAUP:
            text = f"v + {self._speed(data)}"
        elif cmd == C.CMD_PORTADOWN:
            text = f"v - ({self._speed(data)})"
        elif cmd == C.CMD_TONEPORTA:
            s = self._speed(data)
            text = (
                "G[N]"
                if not data
                else f"(v < G[N])*min(v + {s}, G[N]) + (v > G[N])*max(v - ({s}), G[N])"
                " + (v == G[N])*v"
            )
        else:
            name, prefix, period, s = self._vibrato(data)
            at = index(prefix, period, f"({PHI} - {lag})" if lag else PHI)
            text = f"v + ({s}) - 2*({s})*{name}[{at}]"
        if lag:
            text = f"v + ({PHI} >= {lag})*({text} - v)"
        if noted:
            text = f"G[N]*({PHI} < 0) + ({PHI} >= 0)*({text})"
        return self._named(self.x, "x", canonical(text))

    def instrument_freq(self, speed_ptr: int, delay: int) -> str:
        """The instrument's frequency: its note, with its vibrato after ``delay`` firings."""
        if not speed_ptr:
            return "G[N]"
        bits, prefix, period, s = self._vibrato(speed_ptr)
        name, low, drift = self._cumulative(self.tables[bits], prefix)
        lag = max(delay - 1, 0)
        psi = f"({PHI} - {lag})" if lag else PHI
        at = f"{name}[{index(prefix, period, psi)}] - {-low}"
        if drift:
            at += f" + {drift}*(({psi} >= {prefix})*(({psi} - {prefix}) // {period}))"
        gate = f"({PHI} >= {lag})*" if lag else ""
        return canonical(f"G[N] + {gate}({s})*({at})")

    def _cumulative(self, bits: list, prefix: int) -> tuple:
        """``(table, offset, drift)``: the running sum of the vibrato's signs, offset to be
        non-negative, and what one cycle adds."""
        signs = [1 - 2 * b for b in bits]
        cum = np.cumsum(signs).tolist()
        low = min(0, *cum)
        return self._table("W", [c - low for c in cum]), low, sum(signs[prefix:])

    def _pulse_step(self, state: tuple, start: int) -> tuple:
        """``(state', width)`` of one pulse table execution from ``state`` and width ``start``."""
        chan = _Channel(pulse_table_ptr=state[0], pulsetime=state[1], pulse=start)
        if chan.pulse_table_ptr:
            self.ref._pulse_exec(chan)  # pylint: disable=protected-access
        return (chan.pulse_table_ptr, chan.pulsetime), chan.pulse

    def pulse(self, pos: int) -> str:
        """The ``pw`` name of the pulse program at table position ``pos``: per firing a set
        width or an added step, from the reference player's own pulse execution."""

        a, b = 0x123, 0x9AB

        def fire(state):
            (s1, p1), (s2, p2) = self._pulse_step(state, a), self._pulse_step(state, b)
            if s1 != s2:
                raise TranscribeError(f"pulse table {pos}: the step depends on the width")
            if p1 == p2:
                return s1, (1, p1)
            d1, d2 = (p1 - a) & 0xFFF, (p2 - b) & 0xFFF
            if d1 != d2:
                raise TranscribeError(f"pulse table {pos}: neither a set nor a step")
            return s1, (0, d1)

        outs, prefix, period = orbit(fire, (pos, 0))
        sets, values = [k for k, _ in outs], [v for _, v in outs]
        at = index(prefix, period)
        name = self._table("P", values)
        if not any(sets):
            text = f"v + {name}[{at}]"
        elif all(sets):
            text = f"{name}[{at}]"
        else:
            text = f"v*(1 - {self._table('K', sets)}[{at}]) + {name}[{at}]"
        return self._named(self.pw, "pw", canonical(text))

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
        the gate the note set on off again, as a note whose first wave leaves it off."""
        rows = self._program(C.WTBL, pos, self._wave_row) if pos else [Atom("end")]
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
            if note < 0x80:
                keys["note"] = Atom(f"{note - 0x80 if note >= 0x40 else note:+d}")
            else:
                keys["note"] = Atom(note_name(note & 0x7F))
        return keys

    def _wave_command(self, cmd: int, arg: int) -> dict:
        """The row keys of wavetable command ``cmd`` with ``arg``."""
        none = Atom("none")
        keys = {
            C.CMD_SETAD: lambda: {"ad": arg},
            C.CMD_SETSR: lambda: {"sr": arg},
            C.CMD_SETWAVE: lambda: {"ctrl": arg},
            C.CMD_SETPULSEPTR: lambda: {"run": Atom(self.pulse(arg))} if arg else {"pw": none},
            C.CMD_SETFILTERPTR: lambda: {"run": Atom(self.filt(arg))} if arg else {"filter": none},
            C.CMD_SETFILTERCTRL: lambda: {"res": arg >> 4, "route": arg & 0x0F}
            | ({} if arg else {"filter": none}),
            C.CMD_SETFILTERCUTOFF: lambda: {"cut": arg << 3},
            C.CMD_SETMASTERVOL: lambda: {"vol": arg} if arg < 0x10 else {},
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
        keys["freq"] = self.instrument_freq(ins.speed_ptr, ins.vibdelay)
        keys["restart"] = Flow(self._restart(ins))
        self.ins[name] = FlowMap(keys)
        return name

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

    def phrase(self, pattern: int, running: tuple | None, instr: int) -> str:
        """The name of pattern ``pattern`` entered with frequency command ``running`` and
        instrument ``instr``."""
        key = (pattern, running, instr)
        if key not in self.phrases:
            rows = self.ref._patterns[pattern]  # pylint: disable=protected-access
            events, cur = [], running
            for at in range(0, len(rows) - 4, 4):
                instr = rows[at + 1] or instr
                event, cur = self._event(rows[at : at + 4], cur, instr)
                events.append(event)
            self.phrases[key] = (f"p{len(self.phrases) + 1}", rests(events))
        return self.phrases[key][0]

    def _command(self, cmd: int, data: int, gated: bool) -> str:
        """The text of one-shot command ``cmd`` with ``data``; a wavetable it starts on a note
        that leaves the gate off is not ``gated``."""
        text = {
            C.CMD_SETAD: lambda: f"!ad:{data}",
            C.CMD_SETSR: lambda: f"!sr:{data}",
            C.CMD_SETWAVE: lambda: f"!ctrl:{data}",
            C.CMD_SETWAVEPTR: lambda: f"!run:{self.wave(data, gated)}" if data else "!run:none",
            C.CMD_SETPULSEPTR: lambda: f"!run:{self.pulse(data)}" if data else "!pw:none",
            C.CMD_SETFILTERPTR: lambda: f"!run:{self.filt(data)}" if data else "!filter:none",
            C.CMD_SETFILTERCTRL: lambda: (
                f"!resfilt:{data >> 4}/{data & 0x0F}" if data else "!filter:none!resfilt:0/0"
            ),
            C.CMD_SETFILTERCUTOFF: lambda: f"!cut:{data << 3}",
            C.CMD_SETMASTERVOL: lambda: f"!vol:{data}" if data < 0x10 else "",
            C.CMD_SETTEMPO: lambda: _tempo(data),
        }.get(cmd)
        if text is None:
            raise TranscribeError("command E (funk tempo)")
        return text()

    def _event(self, row, cur, instr):
        """``(atom, running frequency command)`` of one pattern row played by ``instr``."""
        note, ins, cmd, data = row
        if note <= C.LASTNOTE:
            atom = note_name(note - C.FIRSTNOTE)
        else:
            atom = {C.KEYOFF: "off", C.KEYON: "on"}.get(note, "-")
        if ins:
            atom += "." + self.instrument(ins)
        if note <= C.LASTNOTE and cmd != C.CMD_TONEPORTA:
            cur = None
        text = ""
        if cmd in FREQ_CMDS:
            if cur != (cmd, data):
                text = f"!freq:{self.command(cmd, data)}"
            cur = (cmd, data)
        elif cmd == C.CMD_DONOTHING:
            text = "!freq:hold" if cur is not None else ""
            cur = None
        else:
            noted = note <= C.LASTNOTE and cmd != C.CMD_TONEPORTA
            text = self._command(cmd, data, not noted or self.gated(instr))
        if note <= C.LASTNOTE and cmd == C.CMD_TONEPORTA:
            text += "!legato"
        return Atom(atom + text), cur

    # ---- the document -------------------------------------------------------------------

    def holds(self) -> FlowMap:
        """The playroutine's per-tick skips as hold events."""
        ref = self.ref
        freq = ["step-1" if ref.optimize_realtime else "note-1", "row"]
        pw = ["read", "note-1", "last-1"] if ref.optimize_pulse else ["note-1"]
        return FlowMap(freq=Flow(map(Atom, freq)), pw=Flow(map(Atom, pw)))

    def timeline(self, run: Run) -> tuple:
        """``(rows, loop row or None)`` of the reference player's pattern starts."""
        plays = {}
        for v, occs in enumerate(run.starts):
            for occ in occs:
                entry = self.phrase(occ.pattern, occ.running, occ.instr)
                entry += f"{occ.trans:+d}" if occ.trans else ""
                plays.setdefault(occ.tick, ["~"] * C.MAX_CHN)[v] = entry
        ticks = sorted(plays)
        rows = [Flow([Atom("p0")] * C.MAX_CHN)] + [Flow(map(Atom, plays[t])) for t in ticks[:-1]]
        loop = repeat([tuple(r) for r in rows[1:]], [b - a for a, b in zip(ticks, ticks[1:])])
        if loop is None:
            return rows, None
        return rows[: loop[0] + loop[1] + 1], loop[0] + 1

    def init(self) -> Flow:
        """The init events: instrument 1 on every voice, its vibrato running from the intro."""
        first = f"-.{self.instrument(1)}"
        speed = self.ref._instr[1].speed_ptr  # pylint: disable=protected-access
        intro = f"!freq:{self.command(C.CMD_VIBRATO, speed, False, 1)}" if speed else ""
        return Flow(
            [Atom(f"{first}!vol:15!tempo:{LEAD}{intro}")]
            + [Atom(f"{first}{intro}")] * (C.MAX_CHN - 1)
        )

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
        phrases = {"p0": Flow([Atom("-"), Atom(f"-!tempo:{rowlen}")])}
        phrases.update((name, Flow(events)) for name, events in self.phrases.values())
        defs = {"phrase": phrases, "ins": self.ins}
        for key, store in (("ctrl", self.ctrl), ("pw", self.pw), ("filter", self.filter)):
            if store:
                defs[key] = dict(store)
        if self.x:
            defs["x"] = dict(self.x)
        song = [{"def": defs, "play": rows[0]}] + [{"play": r} for r in rows[1:]]
        song.append(Atom("end") if loop is None else FlowMap(loop=loop))
        header = self.model.header
        doc = {
            "unitracker": Atom(VERSION),
            "meta": FlowMap(title=header.name, author=header.author, released=header.released),
            "tempo": rowlen,
            "hold": self.holds(),
            "tables": {k: Flow(v) for k, v in self.tables.items()},
            "init": init,
            "song": song,
        }
        return yaml.dump(doc, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=WIDTH)


def _tempo(data: int) -> str:
    """Command F: ticks per row for every voice, or this voice's with bit 7."""
    ticks = data & 0x7F
    if ticks < 2:
        raise TranscribeError("funk tempo from command F")
    return f"!{'v' if data >= 0x80 else ''}tempo:{max(ticks, 3)}"


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
