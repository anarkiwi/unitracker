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

VERSION = "2.0"
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
CELL = {"vib": "freq", "pulse": "pw", "pong": "pw", "sweep": "cut"}


class TranscribeError(ValueError):
    """The tune uses something this transcription does not spell."""


@dataclass
class Voice:
    """A voice's cells as its tokens are written: its instrument, the flags and cursor of its
    inner program, its note index, the AD its last trigger wrote and whether its arpeggio runs."""

    ins: int
    flags: int = 0
    cursor: int = 0
    index: int = 0
    ad: int = 0
    arp: bool = False


class Transcriber:  # pylint: disable=too-many-instance-attributes
    """One subtune of a Music Assembler song as a generic document."""

    def __init__(self, song, subtune: int = 0):
        self.song, self.ref = song, Player(song, subtune)
        self.lay, self._rd = self.ref.layout, self.ref._rd  # pylint: disable=protected-access
        self.rowlen = self._rd(self.lay.tempo_reload) + 1
        self.ins, self.ctrl, self.phrases, self.used = {}, {}, {}, []
        self._check()

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
            val = rd(base + y)
            if (lay.order_tempo and val == MA.ORDER_TEMPO) or (
                lay.order_jump and val == MA.ORDER_JUMP
            ):
                raise TranscribeError(f"orderlist command ${val:02X}")
            if val == lay.order_end:
                y = lay.order_restart

    def _tokens(self, pid: int):
        """``(kind, duration, instrument, note)`` of each token of pattern ``pid``."""
        if pid == MA.SILENCE_PATTERN:
            raise TranscribeError("the silence pattern")
        rd = self._rd
        pat, y = self._ptr(self.lay.pattern_lo, self.lay.pattern_hi, pid), 0
        while True:
            val, ins = rd(pat + y), None
            if MA.INSTR_MIN <= val < MA.LONGDUR_MIN:
                ins, y = val & 0x1F, y + 1 & 0xFF
                val = rd(pat + y)
            if val >= MA.LONGDUR_MIN and ins is None:
                yield "-", val & MA.CTL_DUR_MASK, None, None
            elif val >= MA.DUR_MIN:
                yield "off", val & MA.CTL_DUR_MASK, ins, None
            else:
                y = y + 1 & 0xFF
                ctl = rd(pat + y)
                if ctl & ~MA.CTL_DUR_MASK:
                    raise TranscribeError(f"note control ${ctl:02X}")
                yield "note", ctl, ins, val
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

    def event(self, voice: Voice, token: tuple, trans: int) -> Event:
        """The event of ``token`` on ``voice`` under ``trans``: its ticks' control rows, the
        last its next parse's, which writes nothing and holds the sweeps."""
        kind, dur, ins, note = token
        length = (dur + 1) * self.rowlen
        if ins is not None and kind != "note" and ins != voice.ins:
            raise TranscribeError("an instrument selected without a note")
        voice.ins = voice.ins if ins is None else ins
        rows, name = [], None
        if kind == "note":
            voice.index = trans + note & 0xFF
            ad, _, wave, *_, flags = self._record(voice.ins)
            voice.flags, voice.cursor, voice.ad, voice.arp = flags, 0, ad, False
            rows.append({"ctrl": wave})
            name = self.instrument(voice.ins)
        rows += [self._step(voice) for _ in range(length - 1 - len(rows))]
        rows.append({"ad": voice.ad})
        atom = note_name(voice.index) if kind == "note" else kind
        return Event(atom, name, {"run": self._program(rows)}, steps=length)

    # ---- the song -----------------------------------------------------------------------

    def _ticks(self, pid: int) -> int:
        return sum((t[1] + 1) * self.rowlen for t in self._tokens(pid))

    def _period(self, v: int) -> tuple:
        """``(intro, loop)`` ticks of voice ``v``'s orderlist."""
        at, total = {}, 0
        for y, pid, transrep in self._entries(v):
            if y in at:
                return at[y], total - at[y]
            at[y] = total
            total += ((transrep & 0x0F) + 1) * self._ticks(pid)
        return None  # pragma: no cover

    def _starts(self, v: int, horizon: int) -> list:
        """``(tick, phrase)`` of each pattern voice ``v`` starts before ``horizon``."""
        voice, tick, out = Voice(self._rd(self.lay.instr_x8 + v) >> 3), 1, []
        for _, pid, transrep in self._entries(v):
            if tick >= horizon:
                break
            for _ in range((transrep & 0x0F) + 1):
                tick = self._play(voice, pid, transrep >> 4, tick, out)
        return out

    def _play(self, voice: Voice, pid: int, trans: int, tick: int, out: list) -> int:
        """Appends ``(tick, phrase)`` of pattern ``pid`` under ``trans`` from ``tick`` on
        ``voice`` to ``out``; the tick after it."""
        events = []
        for token in self._tokens(pid):
            if tick == 1 and not events and token[0] != "note":
                raise TranscribeError("a voice whose first row plays no note")
            events.append(self.event(voice, token, trans))
        out.append((tick, self.phrases.setdefault(tuple(events), f"p{len(self.phrases) + 1}")))
        return tick + sum(e.steps for e in events)

    def timeline(self) -> tuple:
        """``(rows, loop row)`` of the orderlists' pattern starts, after tick 0's parse."""
        periods = [self._period(v) for v in range(VOICES)]
        cycle = math.lcm(*(p for _, p in periods))
        if cycle > 1 << 20:
            raise TranscribeError("orderlists of incommensurate loops")
        horizon = 1 + max(i for i, _ in periods) + 4 * cycle
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
        events = [{"resfilt": (res_filt >> 4, res_filt & 0x0F), "mode": mode_vol & 0xF0}]
        events[0]["vol"] = mode_vol & 0x0F
        events += [{} for _ in range(VOICES - 1)]
        rd, lay = self._rd, self.lay
        owner, count = rd(lay.trigger_owner), rd(lay.trigger_ctr)
        if owner < VOICES and count:
            sweep = self.call("sweep", u=rd(lay.trigger_acc), c=count, s=rd(lay.sweep_step))
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
            VERSION,
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
        called = {b.name for b in bound if isinstance(b, Call)}
        used = [t for t in self.used if t in called]
        mods = [(signature(t, code.params(ROUTINES[t])), Code(ROUTINES[t])) for t in used]
        song = [SongRow(rows[0], [Section("phrase", phrases)])] + [SongRow(r) for r in rows[1:]]
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


def compare(song, text: str, frames: int = 20000, subtune: int = 0):
    """The ``(tick, register, ours, theirs)`` cells where document ``text``'s grid differs
    from the reference player's over ``frames`` ticks."""
    ours = play(text, ticks=frames)
    theirs = np.asarray(Player(song, subtune).render_grid(len(ours)), dtype=np.uint8)
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
