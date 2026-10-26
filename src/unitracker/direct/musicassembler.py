"""A Music Assembler tune transcribed to the generic document from pymusicassembler's player.

A pattern token is an event from the play call after its parse to its voice's next parse, a
tick the voice writes nothing; each event's control program walks the inner program, and the
pulse and filter sweeps are code modulators (docs/direct/musicassembler.md).
"""

import math
import sys
from dataclasses import dataclass, replace

import numpy as np
import pymusicassembler
from pymusicassembler import constants as MA
from pymusicassembler.player import Player
from pysidtracker import gridcmp
from pysidtracker.registers import PAL_CYCLES_PER_FRAME
from unitracker_grammar import code, frequency
from unitracker_grammar.document import (
    CONT,
    NONE,
    Call,
    Code,
    Document,
    Event,
    Header,
    Instrument,
    Play,
    Program,
    Row,
    Section,
    Slot,
    SongRow,
    printed,
    signature,
)
from unitracker_player.player import play

from unitracker.direct import cli
from unitracker.direct.goattracker2 import meta_text, note_name, repeat
from unitracker.direct.macode import CODE, STATE

# The clock whose F is nearest the player's note table: 53 of its notes 0-94 differ by one.
CLOCK = (PAL_CYCLES_PER_FRAME, 985056)
VOICES = MA.VOICES
FROZEN = "row.ad"
ROUTINES = {name: code.read(text) for name, text in CODE.items()}
SLOTS = {
    "freq": [Slot("freq", hold=(FROZEN,))],
    "pw": [Slot("pw", hold=(FROZEN,))],
    "cut": [Slot("cut", hold=(FROZEN,), reset=("note",))],
}
CELL = {"vib": "freq", "pulse": "pw", "pong": "pw", "csweep": "cut"}


class TranscribeError(ValueError):
    """The tune uses something this transcription does not spell."""


@dataclass
class Voice:  # pylint: disable=too-many-instance-attributes
    """A voice's cells as its tokens are written: its instrument, the flags and cursor of its
    inner program, its note index, the AD its last trigger wrote and whether its arpeggio runs,
    and whether a trigger has run (a legato note before it never sounds)."""

    ins: int
    v: int = 0
    flags: int = 0
    cursor: int = 0
    index: int = 0
    ad: int = 0
    arp: bool = False
    cut: Call | None = None
    live: bool = False


class Transcriber:  # pylint: disable=too-many-instance-attributes
    """One subtune of a Music Assembler song as a generic document."""

    def __init__(self, song, subtune: int = 0):
        self.song, self.ref = song, Player(song, subtune)
        self.lay, self._rd = self.ref.layout, self.ref._rd  # pylint: disable=protected-access
        self.rowlen = self._rd(self.lay.tempo_reload) + 1
        self.ins, self.ctrl, self.phrases, self.used = {}, {}, {}, []
        self.sweeps, self.start = [], (VOICES, 0, 0, 0)
        self._check()
        lay = self.lay
        cells = (lay.trigger_owner, lay.trigger_acc, lay.trigger_ctr, lay.sweep_step)
        self.start = tuple(self._rd(c) for c in cells)

    def _check(self) -> None:
        lay, rd = self.lay, self._rd
        if self.song.play_addr != lay.entry:
            raise TranscribeError("a play routine that is not the player's entry")
        offsets = tuple(rd(lay.voice_reg + v) for v in range(VOICES))
        if lay.output_voices != (0, 1, 2) or offsets != MA.SID_VOICE_OFFSET[:VOICES]:
            raise TranscribeError("a voice dispatch other than 1, 2, 3")
        if rd(lay.tempo) or any(rd(lay.dur + v) for v in range(VOICES)):
            raise TranscribeError("a first play call that reads no row")
        if self.rowlen < 2 or self.rowlen > 0x80:
            raise TranscribeError(f"tempo {self.rowlen - 1}")
        if lay.transrep != lay.transrep_byte:
            raise TranscribeError("a transpose cell apart from the orderlist byte")
        if any(self.ref.snapshot()[:23]):
            raise TranscribeError("an init that writes a voice or the cutoff")

    # ---- memory -------------------------------------------------------------------------

    def _ptr(self, lo: int, hi: int, index: int) -> int:
        return self._rd(lo + index) | self._rd(hi + index) << 8

    def _record(self, ins: int) -> list:
        """Instrument ``ins``'s 8-byte record."""
        at = self.lay.instr + (ins << 3 & 0xFF)
        return [self._rd(at + k) for k in range(MA.INSTR_RECORD_SIZE)]

    def _entries(self, v: int):
        """``(position, pattern, transrep)`` of each orderlist entry voice ``v`` reaches."""
        lay, rd = self.lay, self._rd
        base, y = self._ptr(lay.order_lo, lay.order_hi, v), 0
        start = (rd(base), rd(base + 1), rd(base + 1) & 0x0F, 0, 0)
        cells = (lay.pattern_id, lay.transrep, lay.repeat, lay.order_cursor, lay.pat_cursor)
        if tuple(rd(c + v) for c in cells) != start:
            raise TranscribeError("an init that does not start the orderlists")
        while True:
            yield y, rd(base + y), rd(base + (y + 1 & 0xFF))
            y = y + 2 & 0xFF
            val, seen = rd(base + y), set()
            while True:
                if lay.order_tempo and val == MA.ORDER_TEMPO:
                    raise TranscribeError(f"orderlist command ${val:02X}")
                if not (lay.order_jump and val == MA.ORDER_JUMP):
                    break
                if y in seen:
                    raise TranscribeError("an orderlist jump to itself")
                seen.add(y)
                y = rd(base + (y + 1 & 0xFF)) << 1 & 0xFF
                val = rd(base + y)
            if val == lay.order_end:
                y = lay.order_restart

    def _tokens(self, pid: int):
        """``(kind, duration, instrument, note, filter, legato)`` of each token of pattern ``pid``,
        the filter command's two bytes if its note has one, and whether its note is legato."""
        rd = self._rd
        pat, y = self._ptr(self.lay.pattern_lo, self.lay.pattern_hi, pid), 0
        while True:
            val, ins = rd(pat + y), None
            if MA.INSTR_MIN <= val < MA.LONGDUR_MIN:
                ins, y = val & 0x1F, y + 1 & 0xFF
                val = rd(pat + y)
            if val >= MA.LONGDUR_MIN and ins is None:
                yield "-", val & MA.CTL_DUR_MASK, None, None, None, False
            elif val >= MA.DUR_MIN:
                yield "off", val & MA.CTL_DUR_MASK, ins, None, None, False
            else:
                y = y + 1 & 0xFF
                ctl, sweep = rd(pat + y), None
                if ctl & MA.CTL_FILTER:
                    sweep = (rd(pat + (y + 1 & 0xFF)), rd(pat + (y + 2 & 0xFF)))
                    y = y + 2 & 0xFF
                if ctl & ~(MA.CTL_DUR_MASK | MA.CTL_FILTER | MA.CTL_TRIGGERED):
                    raise TranscribeError(f"note control ${ctl:02X}")
                yield "note", ctl & MA.CTL_DUR_MASK, ins, val, sweep, bool(ctl & MA.CTL_TRIGGERED)
            y = y + 1 & 0xFF
            if rd(pat + y) == MA.PATTERN_END:
                return
            if not y:
                raise TranscribeError("a pattern of no end")

    # ---- definitions --------------------------------------------------------------------

    def call(self, template: str, **params) -> Call:
        """The call of ``template`` with ``params``, its arguments in its signature's order."""
        if template not in self.used:
            self.used.append(template)
        return Call(template, tuple(params[k] for k in code.params(ROUTINES[template])))

    def instrument(self, n: int) -> str:
        """The name of instrument ``n``: its envelope and its trigger's pulse and sweep."""
        name = f"i{n}"
        if name not in self.ins:
            ad, sr, _, p, s, *_, flags = self._record(n)
            if flags & 0x40 or self.lay.pulse_always:
                pw = self.call("pulse", p=p, s=s)
            elif flags & 0x20:
                pw = self.call("pong", p=p, s=s, k=s & 0x0F)
            else:
                pw = (p << 8 | p) & 0xFFF
            self.ins[name] = Instrument({"ad": ad, "sr": sr, "pw": pw, "freq": NONE})
        return name

    def arpeggio(self, n: int) -> Call:
        """Instrument ``n``'s arpeggio: its delay, step, phase length and ascending phases."""
        lay, rd = self.lay, self._rd
        cells = {
            ("porta_dec", "porta_inc"): lay.porta_ctr,
            ("phase_rd", "phase_inc"): lay.arp_phase,
            ("substep_inc", "substep_cmp", "substep_clr"): lay.arp_substep,
            ("down_src", "down_dst", "up_src", "up_dst"): lay.freq_lo_w,
            ("down_hi", "up_hi"): lay.freq_hi_w,
            ("down_step", "up_step"): lay.instr + MA.INSTR_ARP_STEP,
            ("speed",): lay.instr + MA.INSTR_ARP_SPEED,
        }

        def operand(at: int) -> int:
            return rd(at) | rd(at + 1) << 8

        moved = any(operand(getattr(lay, "arp_" + k)) != c for ks, c in cells.items() for k in ks)
        if lay.arp_down_cmp or moved:
            raise TranscribeError("an arpeggio on other cells")
        _, _, _, _, _, speed, step, _ = self._record(n)
        mask = sum(1 << j for j in range(4) if rd(lay.arp + j))
        return self.call("vib", w=speed >> 3, s=step, k=speed & 0x0F, m=mask)

    def _step(self, voice: Voice) -> dict:
        """The row of one sustain tick of ``voice``: its inner program's step, if running."""
        prog = voice.flags & 0x0F
        if not prog:
            if not voice.flags & 0x10 or voice.arp:
                return {}
            voice.arp = True
            return {"freq": self.arpeggio(voice.ins)}
        rd, lay = self._rd, self.lay
        base, cur = self._ptr(lay.inner_lo, lay.inner_hi, prog), voice.cursor
        wave, pitch = rd(base + cur), rd(base + (cur + 1 & 0xFF))
        if rd(base + (cur + 2 & 0xFF)):
            raise TranscribeError("an inner program's filter byte")
        nxt = cur + 3 & 0xFF
        marker = rd(base + nxt)
        if lay.program_jump and marker == MA.PROGRAM_JUMP:
            raise TranscribeError("an inner program jump")
        if marker == MA.PROGRAM_STOP:
            voice.flags &= 0xF0
        elif marker == MA.PROGRAM_RESTART:
            nxt = 0
        voice.cursor = nxt
        if pitch < 0x80 or not lay.pitch_skip:
            pitch += voice.index
        return {"ctrl": wave, "note": note_name(pitch & 0x7F)}

    def _program(self, rows: list) -> str:
        """The name of the control program of per-tick ``rows``, empty ones as holds."""
        merged = []
        for keys in rows:
            if merged and not keys:
                merged[-1][1] += 1
            else:
                merged.append([keys, 1])
        program = Program([Row(k | ({"hold": h} if h != 1 else {})) for k, h in merged])
        return self.ctrl.setdefault(program, f"c{len(self.ctrl) + 1}")

    def sweep(self, tick: int, v: int) -> tuple:
        """``(owner, u, c, s)``, the filter sweep a note of voice ``v`` at ``tick`` triggers:
        the init's, then each filter command parsed up to it, the voices in order."""
        state = self.start
        for at, owner, u, c, s in self.sweeps:
            if at > (tick, v):
                break
            state = (owner, u, c if c else state[2], s)
        return state

    def image(self, v: int) -> dict:
        """The row of voice ``v``'s image cells: the frequency, pulse width and control the
        player outputs until the voice's first note."""
        rd, lay = self._rd, self.lay
        return {
            "freq": rd(lay.freq_hi_w + v) << 8 | rd(lay.freq_lo_w + v),
            "pw": (rd(lay.pw_hi + v) & 0x0F) << 8 | rd(lay.pw_lo + v),
            "ctrl": rd(lay.ctrl + v),
        }

    def event(
        self, voice: Voice, token: tuple, trans: int, tick: int = 1, first: bool = False
    ) -> Event:
        """The event of ``token`` on ``voice`` under ``trans`` from ``tick``:
        its ticks' control rows, the last its next parse's, which writes nothing and holds the
        sweeps; a ``first`` non-note writes the voice's image cells instead, no instrument
        running yet."""
        kind, dur, ins, note = token[:4]
        legato = token[5]
        length = (dur + 1) * self.rowlen
        if ins is not None and kind != "note" and ins != voice.ins:
            raise TranscribeError("an instrument selected without a note")
        voice.ins = voice.ins if ins is None else ins
        rows, name = [], None
        if legato and not voice.live:
            raise TranscribeError("a legato note before the voice's first trigger")
        if legato and ins is not None and ins != voice.ins:
            raise TranscribeError("an instrument selected on a legato note")
        if legato and voice.arp:
            raise TranscribeError("a legato note over a running arpeggio")
        if kind == "note" and legato:
            voice.index = trans + note & 0xFF
        elif kind == "note":
            rows, name = [self._trigger(voice, trans + note & 0xFF)], self.instrument(voice.ins)
        rows += [self._step(voice) for _ in range(length - 1 - len(rows))]
        if legato:
            rows[0] = {"note": note_name(voice.index)} | rows[0]
        rows.append({"ad": voice.ad})
        cmds = self.image(voice.v) if first and kind != "note" else {"run": self._program(rows)}
        if kind == "note":
            cmds |= self._filter(voice, token[4], tick)
        if legato:
            cmds["legato"] = True
        return Event(note_name(voice.index) if kind == "note" else kind, name, cmds, steps=length)

    def _trigger(self, voice: Voice, index: int) -> dict:
        """The row of ``voice``'s note trigger at ``index``: its cells take the instrument's."""
        ad, _, wave, *_, flags = self._record(voice.ins)
        voice.index = index
        voice.flags, voice.cursor, voice.ad, voice.arp = flags, 0, ad, False
        voice.live = True
        return {"ctrl": wave}

    def _filter(self, voice: Voice, filt, tick: int) -> dict:
        """The commands of a note of ``voice`` at ``tick``: a filter command's resonance and
        routing, and the cutoff sweep its trigger restarts if ``voice`` owns it, bound again
        when the song's sweep changed since ``voice`` bound it."""
        v, cmds = voice.v, {}
        if filt is not None:
            preset = self._rd(self.lay.res_presets + v) if filt[1] else 0xF0
            cmds |= {"res": preset >> 4, "route": preset & 0x0F}
        owner, u, c, s = self.sweep(tick, v)
        if owner == v and c:
            want = self.call("csweep", u=u, c=c, s=s)
            if want != voice.cut:
                cmds["cut"], voice.cut = want, want
        return cmds

    # ---- the song -----------------------------------------------------------------------

    def _ticks(self, pid: int) -> int:
        return sum((t[1] + 1) * self.rowlen for t in self._tokens(pid))

    def _period(self, v: int) -> tuple:
        """``(intro, loop)`` ticks of voice ``v``'s orderlist; a voice parked on the silence
        pattern has a loop of 0, a constant tail."""
        at, total = {}, 0
        for y, pid, transrep in self._entries(v):
            if pid == MA.SILENCE_PATTERN:
                return total, 0
            if y in at:
                return at[y], total - at[y]
            at[y] = total
            total += ((transrep & 0x0F) + 1) * self._ticks(pid)
        return None  # pragma: no cover

    def _walk(self, v: int, horizon: int):
        """``(tick, pid, trans)`` of each pattern voice ``v`` starts before ``horizon``; the
        silence pattern, which the player never leaves, is the last."""
        tick = 1
        for _, pid, transrep in self._entries(v):
            if tick >= horizon:
                break
            if pid == MA.SILENCE_PATTERN:
                yield tick, pid, 0
                return
            for _ in range((transrep & 0x0F) + 1):
                yield tick, pid, transrep >> 4
                tick += self._ticks(pid)

    def _filters(self, horizon: int) -> None:
        """The song's filter commands before ``horizon``, in the order the player parses them:
        ``self.sweeps`` is ``((tick, voice), voice, u, c, s)`` of each."""
        found = []
        for v in range(VOICES):
            for tick, pid, _ in self._walk(v, horizon):
                if pid == MA.SILENCE_PATTERN:
                    continue
                for token in self._tokens(pid):
                    if token[4] is not None:
                        u, c = token[4]
                        found.append(((tick, v), v, u, c, ((u & 0x0F) << 1) - 0x10 & 0xFF))
                    tick += (token[1] + 1) * self.rowlen
        owners = {f[1] for f in found} | ({self.start[0]} if self.start[2] else set())
        if len(owners) > 1:
            raise TranscribeError("a filter sweep owned by more than one voice")
        self.sweeps = sorted(found)

    def _starts(self, v: int, horizon: int) -> list:
        """``(tick, phrase)`` of each pattern voice ``v`` starts before ``horizon``."""
        voice, out = Voice(self._rd(self.lay.instr_x8 + v) >> 3, v), []
        if v == self.start[0] and self.start[2]:
            voice.cut = self.call("csweep", u=self.start[1], c=self.start[2], s=self.start[3])
        for tick, pid, trans in self._walk(v, horizon):
            if pid == MA.SILENCE_PATTERN:
                self._silence(voice, tick, horizon, out)
            else:
                self._play(voice, pid, trans, tick, out)
        return out

    def _silence(self, voice: Voice, tick: int, horizon: int, out: list) -> None:
        """Appends ``(tick, phrase)`` of ``voice`` parked on the silence pattern from ``tick``:
        the gate off, its modulators running, held to ``horizon``."""
        dur = max((horizon - tick) // self.rowlen - 1, 0)
        event = self.event(voice, ("off", dur, None, None, None, False), 0, tick, tick == 1)
        out.append((tick, self.phrases.setdefault((event,), f"p{len(self.phrases) + 1}")))

    def _play(self, voice: Voice, pid: int, trans: int, tick: int, out: list) -> None:
        """Appends ``(tick, phrase)`` of pattern ``pid`` under ``trans`` from ``tick`` on
        ``voice`` to ``out``."""
        events, at = [], tick
        for token in self._tokens(pid):
            if (
                tick == 1
                and not events
                and token[0] != "note"
                and self._rd(self.lay.ctrl + voice.v) & 1
            ):
                raise TranscribeError("a voice whose first row plays no note and whose image gates")
            events.append(self.event(voice, token, trans, at, tick == 1 and not events))
            at += events[-1].steps
        out.append((tick, self.phrases.setdefault(tuple(events), f"p{len(self.phrases) + 1}")))

    def timeline(self) -> tuple:
        """``(rows, loop row)`` of the orderlists' pattern starts, after tick 0's parse."""
        periods = [self._period(v) for v in range(VOICES)]
        cycle = math.lcm(*(p for _, p in periods if p))
        if cycle > 1 << 20:
            raise TranscribeError("orderlists of incommensurate loops")
        horizon = 1 + max(i for i, _ in periods) + 4 * cycle
        self._filters(horizon)
        plays = {}
        for v in range(VOICES):
            for tick, name in self._starts(v, horizon):
                plays.setdefault(tick, [CONT] * VOICES)[v] = Play(name)
        ticks = sorted(plays)
        rows = [tuple(plays[t]) for t in ticks[:-1]]
        loop = repeat(rows, [b - a for a, b in zip(ticks, ticks[1:])])
        if loop is None:
            raise TranscribeError("a timeline that does not repeat")
        return [(Play("p0"),) * VOICES] + rows[: loop[0] + loop[1]], loop[0] + 1

    def init(self) -> list:
        """The init events: the volume and filter init wrote, and the owner's cutoff sweep."""
        regs = self.ref.snapshot()
        res_filt, mode_vol = regs[MA.RES_FILT_REG], regs[MA.MODE_VOL_REG]
        events = [{"res": res_filt >> 4, "route": res_filt & 0x0F, "mode": mode_vol & 0xF0}]
        events[0]["vol"] = mode_vol & 0x0F
        events += [{} for _ in range(VOICES - 1)]
        rd, lay = self._rd, self.lay
        owner, count = rd(lay.trigger_owner), rd(lay.trigger_ctr)
        if owner < VOICES and count:
            sweep = self.call("csweep", u=rd(lay.trigger_acc), c=count, s=rd(lay.sweep_step))
            events[owner]["cut"] = sweep
        return [Event("-", commands=c) for c in events]

    def _defs(self, phrases: list) -> list:
        """The instruments and control programs ``phrases`` play."""
        runs = {e.commands[0][1] for _, events in phrases for e in events if e.commands}
        kits = {e.ins for _, events in phrases for e in events}
        kept = sorted(
            ((k, v) for k, v in self.ins.items() if k in kits), key=lambda kv: int(kv[0][1:])
        )
        programs = [(n, p) for p, n in self.ctrl.items() if n in runs]
        return [Section("ins", kept), Section("ctrl", programs)]

    def _header(self, used: list) -> Header:
        """The header: the clock, a tick a step, and the slots and state of the ``used``."""
        cells = {CELL[t] for t in used}
        named = {s for t in used for s in code.symbols(ROUTINES[t])}
        state = {c: [(k, n) for k, n in names.items() if k in named] for c, names in STATE.items()}
        return Header(
            tuple((k, meta_text(v)) for k, v in self.meta()),
            clock=CLOCK,
            tempo=1,
            slots={c: s for c, s in SLOTS.items() if c in cells} or None,
            state={c: n for c, n in state.items() if n} or None,
        )

    def document(self) -> str:
        """The document of the song to its loop."""
        init = self.init()
        rows, loop = self.timeline()
        named = {p.phrase for r in rows for p in r if isinstance(p, Play)}
        phrases = [("p0", [Event("-")])]
        phrases += [(n, list(e)) for e, n in self.phrases.items() if n in named]
        defs = self._defs(phrases)
        bound = [v for s in defs for _, d in s.records for v in _values(d)]
        bound += [v for e in init for _, v in e.commands]
        bound += [v for _, events in phrases for e in events for _, v in e.commands]
        called = {b.name for b in bound if isinstance(b, Call)}
        used = [t for t in self.used if t in called]
        mods = [(signature(t, code.params(ROUTINES[t])), Code(ROUTINES[t])) for t in used]
        song, defs = [SongRow(r) for r in rows], defs + [Section("phrase", phrases)]
        doc = Document(self._header(used), mods=mods, defs=defs, init=init, rows=song, loop=loop)
        return printed(doc)

    def meta(self) -> tuple:
        """The song's title, author and release."""
        song = self.song
        return (("title", song.name), ("author", song.author), ("released", song.released))


def _values(record) -> list:
    """The values of an instrument's fields or a program's rows."""
    if isinstance(record, Instrument):
        return [v for _, v in record.fields]
    return [v for row in record.rows for _, v in row.keys]


def transcriber(raw: bytes, subtune: int = 0) -> Transcriber:
    """The transcriber of one subtune of a SID."""
    return Transcriber(pymusicassembler.read(raw), subtune)


def transcribe(raw: bytes, subtune: int = 0) -> str:
    """The document text of one subtune of a SID."""
    return transcriber(raw, subtune).document()


def standard(song, subtune: int = 0) -> Player:
    """The reference player of ``song`` with the document's note table: every index k the
    player reads its table at sounds ``F(k)`` at :data:`CLOCK`."""
    # pylint: disable=protected-access
    ref = Player(song, subtune)
    words = [frequency(k, CLOCK[1]) for k in range(0x100)]
    table = bytes(w & 0xFF for w in words) + bytes(w >> 8 for w in words)
    bus = ref._bus
    ref.layout = ref._lay = replace(ref.layout, freq_lo=0x10000, freq_hi=0x10100)
    ref._rd = lambda addr: table[addr - 0x10000] if addr >> 16 else bus[addr & 0xFFFF]
    return ref


def compare(song, text: str, frames: int = 20000, subtune: int = 0, native: bool = False):
    """The ``(tick, register, ours, theirs)`` cells where document ``text``'s grid differs
    from the reference player's at the standard note table (:func:`standard`), or ``native``
    at its own, over ``frames`` ticks."""
    ours = play(text, ticks=frames)
    ref = Player(song, subtune) if native else standard(song, subtune)
    theirs = np.asarray(ref.render_grid(len(ours)), dtype=np.uint8)
    return gridcmp.cell_diff(ours, theirs)


def verify(raw: bytes, subtune: int = 0, frames: int = 20000):
    """``(cells, document)`` of one subtune of a SID: :func:`compare` of its transcription."""
    one = transcriber(raw, subtune)
    text = one.document()
    return compare(one.song, text, frames, subtune), text


def main(argv=None) -> int:
    """Transcribe a SID, or verify SIDs against pymusicassembler."""
    return cli.main(argv, __doc__.split("\n", 1)[0], transcribe, verify)


if __name__ == "__main__":
    sys.exit(main())
