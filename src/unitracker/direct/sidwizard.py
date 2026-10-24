"""A SID-Wizard tune transcribed to the generic document from pysidwizard's playroutine.

Every tick is a step: a pattern row's TICK_0 and TICK_1 (the hard restart's) and TICK_2 (the row
applied) carry its events, its tempo the rests after them; the WF, pulse and filter tables are
programs, the vibrato, slides and WRPITCH code modulators (docs/direct/sidwizard.md).
"""

import math
import sys
from dataclasses import dataclass, field

import numpy as np
from pysidtracker import SidImage, gridcmp
from pysidwizard import SWMPlayer, parse_sid_song
from pysidwizard.constants import INST_WF_TABLE_POS
from pysidwizard.player import DEFAULT_HR_ADSR, PAL_CYCLES_PER_FRAME
from unitracker_grammar import code, frequency
from unitracker_grammar.document import (
    CONT,
    END,
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

from unitracker.direct import cli
from unitracker.direct.goattracker2 import meta_text, note_name, repeat, rests
from unitracker.direct.swcode import STATE, templates

VERSION = "1.23"
# The clock whose note table F is nearest FREQTBL/FREQTBH at note k + 1: no clock is exact.
CLOCK = (PAL_CYCLES_PER_FRAME, 985256)
VOICES = 3
LEGATO, GATE_OFF, GATE_ON, NPORTAM = 0x3F, 0x7E, 0x7D, 0x78
MAX_FRAMES = 200000
DUMMY = 256
LOOPS = 4
# Build flags this transcription refuses whenever the tune uses what they change.
USED = {
    "pwkeybtrack": "pulse keyboard tracking",
    "filtkbtrack": "filter keyboard tracking",
    "pwresetsw": "the pulse reset switch",
    "filtresetsw": "the filter reset switch",
    "gateoffptr": "gate-off table pointers",
    "portavibra": "vibrato portamento",
    "allghostregs": "all-ghost-register small FX",
}


class TranscribeError(ValueError):
    """The tune uses something this transcription does not spell."""


def _col(image: bytes, pos: int, past=0):
    """Byte ``pos`` of ``image``, ``past`` beyond it."""
    return image[pos] if pos < len(image) else past


def _signed(byte: int) -> int:
    return byte - 0x100 if byte >= 0x80 else byte


def _rows3(table: bytes):
    return (table[i : i + 3] for i in range(0, len(table), 3))


@dataclass
class Read:  # pylint: disable=too-many-instance-attributes
    """A pattern row as the reference player reads it at TICK_0 and applies it at TICK_2."""

    frame: int
    row: object
    start: bool
    newnote: bool
    hr: object
    dummy: bool
    applied: bool = False
    ins: object = None
    number: int = 0
    note: int = 0
    slide: int = 0
    tick2: bool = False
    adsr: tuple = ()
    keep_filter: bool = False
    resume: tuple = ()
    chord: tuple = None
    skips: dict = field(default_factory=dict)


@dataclass
class Trace:
    """The reference player's reads per voice, the frame each voice ended, refusals seen."""

    reads: list = field(default_factory=lambda: [[] for _ in range(VOICES)])
    ended: list = field(default_factory=lambda: [None] * VOICES)
    starts: list = field(default_factory=lambda: [{} for _ in range(VOICES)])
    refused: list = field(default_factory=list)
    frames: int = 0


def standard(swm) -> SWMPlayer:
    """The reference player of ``swm`` with the document's note table: note k + 1 sounds
    ``F(k)`` at :data:`CLOCK`, and SETFMOD's EXPTABH is built on it."""
    ref = SWMPlayer(swm)
    words = [frequency(k - 1, CLOCK[1]) for k in range(256)]
    ref.freq_lo, ref.freq_hi = bytes(w & 0xFF for w in words), bytes(w >> 8 for w in words)
    ref.exptabh = bytes(11) + ref.freq_hi
    return ref


def _ended(image: bytes, pos: int, wave: bool) -> bool:
    """Whether a table walk at ``pos`` rests: an end, a jump nowhere or to itself."""
    byte = _col(image, pos, 0xFF)
    target = _col(image, pos + 1) if byte == 0xFE else 0
    jumped = byte == 0xFE and (target == pos or target >= len(image) or wave and target >= 0x80)
    return byte == 0xFF or jumped


def trace(swm, frames: int = MAX_FRAMES) -> Trace:
    """The reference player's reads until every voice ended or looped :data:`LOOPS` times."""
    # pylint: disable=protected-access,too-many-statements,too-many-locals
    ref, out = standard(swm), Trace()
    voices = {id(v): i for i, v in enumerate(ref.voices)}
    advance_row, apply_row = ref._advance_row, ref._apply_row
    deferred, setfltp, staccato = ref._run_deferred_fx, ref._setfltp, ref._is_staccato_hr_tick
    claims = []

    def read(v):
        advance_row(v)
        if v.pending_row is not None:
            i = voices[id(v)]
            start = v.pattern_row == 1
            if start:
                out.starts[i].setdefault((id(v.sequence), v.seq_pos), []).append(ref.frame_idx)
            hr = ref._upcoming_hr_instrument(v, v.pending_row)
            new = ref._pending_row_has_new_note(v)
            dummy = v.instrument is None
            out.reads[i].append(Read(ref.frame_idx, v.pending_row, start, new, hr, dummy))

    def applied(v, row):
        rec = out.reads[voices[id(v)]][-1]
        note = row.note
        rec.slide = v.slide_vib
        rec.tick2 = (
            note is not None
            and 0 < note < 0x60
            and (row.fx == 0x03 or row.instrument == LEGATO or v.slide_vib == 0xFF)
        )
        if rec.tick2:
            held = ref.filter_controller_voice is v
            filt = (ref.filt_pos, ref.filter_sweep_count) if held else None
            wave = (v.wf_pos, v.arp_speed_counter, v.current_chord, v.chord_pos)
            rec.resume = (wave, (v.pw_pos, v.pw_sweep_count), filt)
        before = v.instrument
        apply_row(v, row)
        rec.applied, rec.ins, rec.number = True, v.instrument, v.instrument_idx
        if v.instrument is not before and not rec.newnote:
            out.refused.append(f"an instrument selected without a note, frame {ref.frame_idx}")
        rec.note = v.note + v.octave_shift + v.transpose - 1
        rec.adsr = (v.sid_ad, v.sid_sr)
        rec.chord = (v.wf_pos, v.arp_speed_counter, v.current_chord, v.instrument)

    def run_deferred(v):
        pending = v.deferred_fx is not None
        deferred(v)
        if pending:
            rec = out.reads[voices[id(v)]][-1]
            rec.adsr = (v.sid_ad, v.sid_sr)
            rec.chord = (v.wf_pos, v.arp_speed_counter, v.current_chord, v.instrument)

    def claim(v):
        setfltp(v)
        claims.append(v)

    def hard(v, pre, apply_sc=2):
        fires = staccato(v, pre, apply_sc)
        if fires:
            rec = out.reads[voices[id(v)]][-1]
            wave = (v.wf_pos, v.arp_speed_counter, v.current_chord, v.chord_pos)
            rec.skips[ref.frame_idx - rec.frame] = (wave, v.instrument)
        return fires

    ref._advance_row, ref._apply_row, ref._run_deferred_fx = read, applied, run_deferred
    ref._setfltp, ref._is_staccato_hr_tick = claim, hard
    for frame in range(frames):
        claims.clear()
        ref.play_frame()
        _claims(ref, claims, out)
        for i, v in enumerate(ref.voices):
            if v.sequence_ended and out.ended[i] is None:
                out.ended[i] = frame
        if out.refused or _looped(out, frame):
            out.frames = frame + 1
            return out
    raise TranscribeError(f"no loop within {frames} frames")


def _looped(out: Trace, frame: int) -> bool:
    """Whether every voice ended, or its pattern starts repeat and all of them together have
    repeated :data:`LOOPS` times by ``frame``."""
    periods, first = [], 0
    for ended, starts in zip(out.ended, out.starts):
        if ended is not None:
            continue
        seen = max(starts.values(), key=len, default=[])
        if len(seen) < 3:
            return False
        periods.append(seen[-1] - seen[-2])
        first = max(first, seen[0])
    return not periods or frame >= first + LOOPS * math.lcm(*periods)


def _claims(ref, claims: list, out: Trace) -> None:
    """Per frame: a STRTSND another voice's claim overrides keeps the filter; a note of the
    filter's voice that leaves it walking another instrument's table is refused."""
    holder = ref.filter_controller_voice
    voices = {id(v): i for i, v in enumerate(ref.voices)}
    for v in claims:
        rec = out.reads[voices[id(v)]][-1]
        first = (rec.ins.filter_table or b"\xff")[0] if rec.ins is not None else 0xFF
        took = first not in (0x00, 0xFF)
        if took and holder is not v:
            rec.keep_filter = True
        elif not took and holder is v and not (first == 0xFF and ref.swm.filter_stop_rewinds):
            out.refused.append(
                f"a note of the filter's voice that does not take it, frame {ref.frame_idx}"
            )


class Transcriber:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """The first subtune of a SID-Wizard module as a generic document."""

    def __init__(self, swm, meta: tuple = (), frames: int = 20000, check: bool = True):
        self.swm, self.ref, self.meta, self.frames = swm, standard(swm), meta, frames
        self.feats = self.ref.features
        refused = self._unsupported() if check else []
        if refused:
            raise TranscribeError(f"{self.feats.name} build: {', '.join(refused)}")
        self.code = templates(self.feats.calcvibrato, self.feats.vibratotypes)
        self.routines = {name: code.read(text) for name, text in self.code.items()}
        self.programs = {"ctrl": {}, "pw": {}, "filter": {}}
        self.ins, self.used, self.phrases, self.dummies = {}, [], {}, []
        feats = self.feats
        self.hrc = feats.arpspeedsupp and feats.detunesupport and self._held_rows()

    def _unsupported(self) -> list:
        """What the tune uses that this transcription refuses."""
        swm, feats, out = self.swm, self.feats, []
        if swm.frame_speed != 1 or swm.play_gate or any(swm.filter_init):
            out.append("multispeed, a play gate or an initial filter")
        if not swm.instruments or len(swm.sequences) < VOICES:
            out.append("no instruments, or fewer orderlists than voices")
        rows = [r for p in swm.patterns for r in p.rows]
        uses = {
            "pwkeybtrack": any(
                len(r) > 2 and r[2] and r[0] < 0xFE
                for i in swm.instruments
                for r in _rows3(i.pw_table)
            ),
            "filtkbtrack": any(
                len(r) > 2 and r[2] and r[0] < 0xFE
                for i in swm.instruments
                for r in _rows3(i.filter_table)
            ),
            "pwresetsw": any(i.control & 0x40 for i in swm.instruments),
            "filtresetsw": any(i.control & 0x80 for i in swm.instruments),
            "gateoffptr": any(
                i.gateoff_wf or i.gateoff_pw or i.gateoff_filt for i in swm.instruments
            ),
            "portavibra": any(
                r.instrument == LEGATO or r.fx == 0x03 or r.note == NPORTAM for r in rows
            ),
            "allghostregs": any(
                r.instrument is not None
                and 0x50 <= r.instrument < 0x70
                or r.fx is not None
                and 0x20 <= r.fx < 0x70
                for r in rows
            ),
        }
        out += [USED[k] for k, used in uses.items() if used and getattr(feats, k)]
        if feats.arpspeedsupp and not feats.multispeedsupp and self._held_rows():
            out.append("WF rows held over ticks without STRTSND's ARPSCNT reset")
        if feats.fastspeedbind or feats.vibslidealways:
            out.append("fast speed binding or tables walking on hard-restart ticks")
        return out

    def reload(self, ins) -> int:
        """ARPSPED's reload of instrument ``ins``: the ticks after its first a WF row lasts."""
        return ins.arp_speed & 0x3F if self.feats.arpspeedsupp else 0

    def _held_rows(self) -> bool:
        """Whether a WF row lasts more than a tick: an arp speed or a hold row."""
        return any(
            i.arp_speed & 0x3F or any(r[0] < 0x10 for r in _rows3(i.wf_table))
            for i in self.swm.instruments
        )

    # ---- modulators ---------------------------------------------------------------------

    def call(self, template: str, **params) -> Call:
        """The call of ``template`` with ``params``, its arguments in its signature's order."""
        if template not in self.used:
            self.used.append(template)
        return Call(template, tuple(params[k] for k in code.params(self.routines[template])))

    def amp(self, raw: int, zero: bool = True) -> dict:
        """SETFMOD's argument for raw amplitude ``raw`` (0 none where ``zero``): ``r`` one more
        than the half the build adds to the note, else ``g`` the pitch-independent FREQMOD."""
        if self.feats.calcvibrato:
            return {"r": ((raw + 1) >> 1) + 1 if raw or not zero else 0}
        if not raw:
            return {"g": 0}
        lo, hi = self.ref._freqmod(raw, 0)  # pylint: disable=protected-access
        return {"g": hi << 8 | lo}

    def counter(self, kind: int, freq: int) -> int:
        """SETVIBR's first VIBRACNT for SLIDEVIB ``kind`` and frequency nibble ``freq``."""
        if not self.feats.vibratotypes or kind < 0x20:
            return freq >> 1
        return 0 if kind == 0x30 else freq

    def strtsnd(self, ins, voice: int) -> Call:
        """Instrument ``ins``'s STRTSND on ``voice``: SLIDEVIB, the vibrato, FREQHI's write."""
        kind, freq = ins.control & 0x30, ins.vibrato & 0x0F
        high, index = 0, 0
        if (
            self.feats.frame1switch
            and ins.control & 0x08
            and not self.swm.first_frame_fixed_waveform
        ):
            high = 1
            if self.swm.first_frame_freq_by_track:
                high, index = 2, (voice * 7 - 1) & 0xFF
        return self.call(
            "snd",
            c=kind,
            d=ins.vibrato_delay & 0xFF,
            i=self.counter(kind, freq),
            q=2 * freq,
            h=high,
            t=index,
            **self.amp((ins.vibrato >> 4) << 3),
        )

    def vibrato_byte(self, ins, byte: int, clear: bool) -> Call:
        """FORCVIB and SETVIBR of vibrato ``byte`` on instrument ``ins``."""
        kind, freq = ins.control & 0x30, byte & 0x0F
        return self.call(
            "vfx",
            c=kind,
            w=int(clear),
            i=self.counter(kind, freq),
            q=2 * freq,
            **self.amp((byte >> 4) << 3),
        )

    # ---- tables -------------------------------------------------------------------------

    def _named(self, kind: str, prefix: str, program: Program) -> str:
        store = self.programs[kind]
        for name, held in store.items():
            if held == program:
                return name
        name = f"{prefix}{len(store)}"
        store[name] = program
        return name

    def _det(self, keys: dict, detune, carry: int, arp: int = 0) -> dict:
        """``keys`` with the row's WRPITCH binding on a detune build."""
        if not self.feats.detunesupport:
            return keys
        keep = detune is None
        call = self.call("det", f=int(not keep), d=0 if keep else detune, c=carry, b=arp)
        return keys | {"dt.freq": call}

    def skipped(self, ins, ticks: int) -> list:
        """The rows of ``ticks`` WF ticks ARPSCNT skips, their carry the pulse walk's."""
        if not ticks:
            return []
        if not self.feats.detunesupport:
            return [Row({"hold": ticks})]
        if self.feats.pwkeybtrack:
            carry = 0
        elif not ins.pw_table:
            carry = 1
        else:
            raise TranscribeError("WF rows held over ticks with a pulse table, its carry")
        return [Row(self._det({"hold": ticks} if ticks > 1 else {}, None, 3, carry))]

    def _rest(self, rows: list, ins, reload: int, carry: int) -> tuple:
        """``rows`` and the WF walk resting where it ends: re-read at its pace, each read's
        carry ``carry``, ARPSCNT's skipped ticks between."""
        if not self.feats.detunesupport:
            return rows, None
        rest = [Row(self._det({}, None, carry))] + self.skipped(ins, reload)
        return rows + rest, len(rows) if reload else None

    def wave_rows(self, image: bytes, pos: int, ins, limit: int = 1 << 12, **at_start) -> tuple:
        """``(rows, loop)`` of instrument ``ins``'s WFARPTB from ``pos``, row by row as the
        player reads them: each lasts its arp speed or hold, jumps taken on the tick; a chord
        row plays its chord from its start (or ``chord`` at first)."""
        # pylint: disable=too-many-locals
        reload = at_start.get("reload", self.reload(ins))
        number, cpos = at_start.get("chord", (ins.default_chord, None))
        rows, at, ticks = [], {}, 0
        while ticks < limit:
            for _ in range(4):
                byte = _col(image, pos, 0xFF)
                if byte == 0xFF:
                    return self._rest(rows, ins, reload, 1)
                if byte != 0xFE:
                    break
                target = _col(image, pos + 1)
                if target >= 0x80 or target == pos:
                    return self._rest(rows, ins, reload, 1)
                if target >= len(image):
                    return self._rest(rows, ins, reload, 0)
                pos = target
            else:
                raise TranscribeError("a WF jump chain")
            if pos in at:
                return rows, at[pos]
            at[pos] = len(rows)
            keys, hold = self._wave_row(image, pos, reload)
            first, cpos = cpos, None
            if self.feats.chordsupport and _col(image, pos + 1) == 0x7F:
                played, looped = self._chord(ins, keys, hold, number, first)
                if looped is not None:
                    return rows + played, len(rows) + looped
                rows += played
                ticks += hold * len(played)
                pos += 3
                continue
            rows += [Row(keys)] + self.skipped(ins, hold - 1)
            ticks += hold
            pos += 3
        return rows, None

    def _chord(self, ins, keys: dict, hold: int, number: int, pos=None) -> tuple:
        """``(rows, loop)`` of a chord row playing chord ``number``: a WF read per pitch with
        the row's waveform, from ``pos`` (else the chord's start) to its $7E (on to the next
        row in the tick) or its $7F (its first pitch again: ``loop`` the row it loops to)."""
        # pylint: disable=protected-access
        starts, table = self.ref._chord_starts, self.ref._chord_table
        if not 1 <= number <= len(starts) or _col(table, starts[number - 1], 0x7F) == 0x7F:
            raise TranscribeError(f"chord {number}, past the chord table or empty")
        start = starts[number - 1]
        base = {k: v for k, v in keys.items() if k == "ctrl"}

        def played(at: int) -> tuple:
            rows = []
            while at < len(table) and table[at] not in (0x7E, 0x7F):
                pitch = base | {"note": Signed(table[at]), "wave.freq": self.call("tone")}
                rows += [Row(self._det(pitch, None, 0))] + self.skipped(ins, hold - 1)
                at += 1
            return rows, at < len(table) and table[at] == 0x7F

        rows, looped = played(start if pos is None or pos >= len(table) else pos)
        if not looped:
            return rows, None
        if pos is None or pos == start:
            return rows, 0
        cycle, _ = played(start)
        return rows + cycle, len(rows)

    def _wave_row(self, image: bytes, pos: int, reload: int) -> tuple:
        """``(keys, ticks)`` of the WF row at ``pos``: its waveform (or hold), its arp as the
        note, its detune and WRPITCH's carry."""
        feats, byte = self.feats, image[pos]
        keys, hold = {}, reload + 1
        if byte < 0x10 and feats.arpspeedsupp:
            hold = byte + 1
        else:
            keys["ctrl"] = byte
        arp, carry = _col(image, pos + 1, None), 0
        if arp is not None and not (arp == 0x80 and feats.wfarp_nop_supp):
            if arp >= 0xE0:
                keys["note"], carry = Signed(arp - 0x100), 2
            elif arp >= 0x80:
                keys["note"] = note_name((arp & 0x7F) - 1)
            else:
                keys["note"] = Signed(arp)
            keys["wave.freq"] = self.call("tone")
        elif arp is not None:
            carry = 1
        detune = _col(image, pos + 2, 0xFF)
        return self._det(keys, None if detune == 0xFF else detune, carry, arp or 0), hold

    def wave(self, ins, chord=None) -> str:
        """The ``ctrl`` name of instrument ``ins``'s WF program from STRTSND's tick: its first
        waveform (control $09 without the first-frame switch), SETFLTP's routing."""
        feats = self.feats
        table = ins.filter_table
        first = {"route": "+" if table and table[0] != 0xFF else "'-'"}
        lead = []
        if not feats.frame1switch or ins.control & 0x08 and self.swm.first_frame_fixed_waveform:
            first["ctrl"] = 9
        elif ins.control & 0x08:
            first["ctrl"] = ins.effective_first_waveform
        elif feats.hardrestypes and not ins.control & 0x03:
            raise TranscribeError("a first frame that keeps a waveform no hard restart gated off")
        else:
            first["gate"] = 0
            lead = [Row({"hold": 0, "gate": 1})]
        if ins.wf_table:
            image = self.ref._image(ins)  # pylint: disable=protected-access
            number = ins.default_chord if chord is None else chord
            rows, loop = self.wave_rows(image, INST_WF_TABLE_POS, ins, chord=(number, None))
            if lead and rows[0].get("ctrl") is None:
                raise TranscribeError("a first frame that keeps a waveform before a hold row")
        else:
            (rows, loop), lead = self._rest([], ins, 0, 1), []
        skip = 1 + len(lead)
        program = Program([Row(first), *lead, *rows], None if loop is None else loop + skip)
        return self._named("ctrl", "c", program)

    def _swept(self, image: bytes, pos: int, row, cell: str, step, count=None) -> tuple:
        """``(rows, loop)`` of a pulse or filter program from ``pos``: ``row(pos)`` gives a set
        row's keys, ``step(byte)`` a sweep's per-tick expression; resumed with sweep ``count``
        from STRTSND's tick, which it leaves to the voice's bindings."""
        # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-branches
        rows, at = [Row({} if count is None else {cell: "none"})], {}
        first = True
        while True:
            if pos >= len(image) or image[pos] == 0xFF:
                return rows, None
            if image[pos] == 0xFE:
                target = _col(image, pos + 1)
                if target == pos or target >= len(image):
                    return rows, None
                if image[target] in (0xFE, 0xFF):
                    raise TranscribeError(f"a {cell} jump to a jump or an end")
                if image[target] < 0x80:
                    rows.append(Row({}))
                pos = target
            if pos in at:
                return rows, at[pos]
            byte = image[pos]
            if first and count is not None and not byte & 0x80:
                left = byte - count
                if left > 0:
                    rows.append(Row({"hold": left, cell: step(_col(image, pos + 1))}))
                rows.append(Row({cell: "none"} if left > 0 else {}))
                first = False
                pos += 3
                continue
            at[pos] = len(rows)
            if byte & 0x80:
                rows.append(Row(row(pos)))
            elif first:
                raise TranscribeError(f"a {cell} table that starts with a sweep")
            elif byte:
                rows.append(Row({"hold": byte, cell: step(_col(image, pos + 1))}))
                rows.append(Row({cell: "none"}))
            else:
                rows.append(Row({}))
            first = False
            pos += 3

    def pulse(self, ins, pos=None, count=None) -> str:
        """The ``pw`` name of instrument ``ins``'s pulse program, from the tick after
        STRTSND's, or resumed from ``pos`` at sweep ``count``."""
        image = self.ref._image(ins)  # pylint: disable=protected-access
        rows, loop = self._swept(
            image,
            INST_WF_TABLE_POS + len(ins.wf_table) + 1 if pos is None else pos,
            lambda p: {"pw": ((image[p] & 0x7F) << 8 | _col(image, p + 1)) & 0xFFF},
            "pw",
            lambda d: Expr(canonical(f"v + {_signed(d)}")),
            count,
        )
        return self._named("pw", "pw", Program(rows, loop))

    def filter(self, ins, pos=None, count=None) -> str:
        """The ``filter`` name of instrument ``ins``'s filter program, or resumed from ``pos``
        at sweep ``count``."""
        image = self.ref._image(ins)  # pylint: disable=protected-access
        scale = 1 if self.feats.finefiltsweep else 8

        def row(p):
            return {"res": image[p] & 0x0F, "mode": image[p] & 0x70, "cut": _col(image, p + 1) << 3}

        if pos is None:
            pos = INST_WF_TABLE_POS + len(ins.wf_table) + 1 + len(ins.pw_table) + 1
        rows, loop = self._swept(
            image, pos, row, "cut", lambda d: Expr(canonical(f"v + {scale * _signed(d)}")), count
        )
        return self._named("filter", "f", Program(rows, loop))

    def resume(self, ins, wave: tuple, pulse=None, filt=None, skip: bool = True) -> str:
        """The ``ctrl`` name of a program that skips a tick of the tables (a legato's TICK_2, a
        staccato hard restart; not ``skip``: a chord FX) and resumes them where the player left
        them: ``wave`` the WF position, ARPSCNT, chord and its position, ``pulse`` and ``filt``
        positions and sweep counts."""
        # pylint: disable=too-many-locals
        lead = []
        if pulse is not None:
            lead.append(Row({"hold": 0, "run": self.pulse(ins, *pulse)}))
        if filt is not None:
            lead.append(Row({"hold": 0, "run": self.filter(ins, *filt)}))
        pos, counter, chord, cpos = wave
        held = counter if self.feats.arpspeedsupp and counter < 0x80 else 0
        lead += ([Row({})] if skip else []) + self.skipped(ins, held)
        image = self.ref._image(ins)  # pylint: disable=protected-access
        rows, loop = self.wave_rows(image, pos, ins, chord=(chord, cpos))
        program = Program(lead + rows, None if loop is None else loop + len(lead))
        return self._named("ctrl", "c", program)

    def rechord(self, rec) -> str:
        """The ``ctrl`` name of the WF walk a chord FX leaves: from STRTSND on a note (the FX
        deferred past it), else from where it is, the chord its own."""
        if rec.chord is None:
            raise TranscribeError("a chord FX before any instrument")
        pos, counter, chord, ins = rec.chord
        if rec.newnote:
            return self.wave(ins, chord)
        return self.resume(ins, (pos, counter, chord, None), skip=False)

    # ---- instruments ---------------------------------------------------------------------

    def instrument(self, ins, number: int, voice: int) -> str:
        """The name of SID-Wizard instrument ``ins`` (number ``number``) on ``voice``."""
        name = f"i{number}"
        if self.swm.first_frame_freq_by_track and self.feats.frame1switch:
            name = f"i{number + 64 * (voice + 1)}"
        if name in self.ins:
            return name
        fields = {
            "ad": ins.attack << 4 | ins.decay,
            "sr": ins.sustain << 4 | ins.release,
            "ctrl": self.wave(ins),
            "pw": self.pulse(ins),
        }
        table = ins.filter_table
        if table and table[0] not in (0x00, 0xFF):
            fields["filter"] = self.filter(ins)
        fields["snd.freq"] = self.strtsnd(ins, voice)
        fields["tick.freq"] = self.call("stp")
        self.ins[name] = Instrument(fields)
        return name

    def hr(self, ins, tick: int) -> bool:
        """Whether HRGTOFF fires on TICK_0 or TICK_1 (``tick``) for upcoming instrument
        ``ins``."""
        if not self.feats.hardrestypes:
            return True
        return bool(ins.control & (0x02 if tick == 0 else 0x01))

    def hr_commands(self, ins) -> dict:
        """HRGTOFF's commands: the hard-restart envelope, $18 for a staccato instrument."""
        if self.feats.hardrestypes:
            out = {
                "ad": ins.hr_attack << 4 | ins.hr_decay,
                "sr": ins.hr_sustain << 4 | ins.hr_release,
            }
            return out | ({"ctrl": 0x18} if ins.control & 0x04 else {})
        return {"ad": DEFAULT_HR_ADSR >> 8, "sr": DEFAULT_HR_ADSR & 0xFF}

    def dummy(self, ins) -> str:
        """The instrument whose WF program a voice's first HR ticks walk: the header of
        instrument 0 read as WF rows from DPITCH 0, gated as PTNGATE (0 after INITER) is."""
        if self.feats.hardrestypes and ins.control & 0x04:
            raise TranscribeError("a staccato first note")
        dummy = self.ref._dummy  # pylint: disable=protected-access
        image = self.ref._image(dummy)  # pylint: disable=protected-access
        rows, _ = self.wave_rows(image, 0, dummy, 2, reload=0)
        masks = [0xFE if self.hr(ins, 0) else 0]
        masks.append(0xFE if self.hr(ins, 1) or masks[0] else 0)
        ticks = []
        for row in rows:
            keys = dict(row.keys)
            ticks += [keys] + [{}] * (keys.pop("hold", 1) - 1)
        out = []
        for k, keys in enumerate(ticks[:2]):
            if "ctrl" in keys:
                keys["ctrl"] &= masks[k]
            if isinstance(keys.get("note"), Signed):
                keys["note"] = note_name((keys["note"] & 0x7F) - 1)
            if "dt.freq" in keys and keys["dt.freq"].args[2] == 2:
                keys["dt.freq"] = Call("det", keys["dt.freq"].args[:2] + (0, 0))
            out.append(Row(keys))
        if not out:
            raise TranscribeError("an idle walk that ends at once")
        out[0] = Row(dict(out[0].keys) | {"wave.freq": self.call("tone")})
        instrument = Instrument({"ctrl": self._named("ctrl", "c", Program(out))})
        for name, held in self.ins.items():
            if held == instrument:
                return name
        name = f"i{DUMMY + len(self.dummies)}"
        self.dummies.append(name)
        self.ins[name] = instrument
        return name

    # ---- events -------------------------------------------------------------------------

    def fx(self, rec) -> dict:
        """The commands of a row's instrument-column small FX and FX column."""
        # pylint: disable=too-many-branches
        row, ins, out = rec.row, rec.ins, {}
        codes = []
        if row.instrument is not None and row.instrument > LEGATO:
            codes.append(row.instrument)
        if row.fx is not None and row.fx >= 0x20:
            codes.append(row.fx)
        for value in codes:
            high, low = value & 0xF0, value & 0x0F
            if high in (0x20, 0x30, 0x50, 0x60):
                out["ad"], out["sr"] = rec.adsr
            elif high == 0x70 and self.feats.chordsupport:
                out["run"] = self.rechord(rec)
            elif high in (0x70, 0xE0):
                pass
            elif high == 0x80 and ins is not None:
                out["tick.freq"] = self.vibrato_byte(ins, low << 4 | ins.vibrato & 0x0F, False)
            elif high == 0x90:
                out["tick.freq"] = self.call("vfq", q=low << 1)
            elif high == 0xA0:
                out["vol"] = low
            elif high == 0xB0:
                out["mode"] = low << 4
            elif high == 0xD0:
                out |= self._dfx(low << 3)
            else:
                raise TranscribeError(f"small FX ${value:02X}")
        fx, value = row.fx, row.fx_value
        if fx is None or fx >= 0x20 or value is None:
            return out
        made = {
            0x01: lambda: {"tick.freq": self.call("sld", m=0x81, **self.amp(value))},
            0x02: lambda: {"tick.freq": self.call("sld", m=0x82, **self.amp(value))},
            0x03: lambda: (
                {}
                if rec.tick2
                else {"tick.freq": self.call("sld", m=0x83, **self.amp(value, False))}
            ),
            0x05: lambda: {"ad": value},
            0x06: lambda: {"sr": value},
            0x08: lambda: {"tick.freq": self.vibrato_byte(ins, value, False)} if ins else None,
            0x0D: lambda: self._dfx(value),
            0x16: lambda: {"tick.freq": self.call("kty", c=value & 0x30)},
        }
        if (
            fx in (0x04, 0x07, 0x09, 0x0A, 0x0B, 0x0C, 0x0E, 0x0F)
            or 0x17 <= fx <= 0x1C
            or fx == 0x1F
        ):
            if fx != 0x07:
                raise TranscribeError(f"FX ${fx:02X}")
        if fx == 0x07 and self.feats.chordsupport:
            made[fx] = lambda: {"run": self.rechord(rec)}
        commands = made[fx]() if fx in made else {}
        if commands is None:
            raise TranscribeError(f"FX ${fx:02X} before any instrument")
        if set(commands) & set(out) - {"ad", "sr", "run"}:
            raise TranscribeError("two FX of one cell on a row")
        return out | commands

    def _dfx(self, detune: int) -> dict:
        if not self.feats.detunesupport:
            return {}
        return {"dt.freq": self.call("dfx", d=detune & 0xFF)}

    def events(self, rec, voice: int, state: dict, length: int) -> list:
        """The events of one read row on ``voice``, ``length`` ticks from its TICK_0."""
        out = []
        for tick in (0, 1):
            commands, atom = {}, "-"
            if rec.newnote and not tick and not rec.dummy:
                commands = {"pw": "hold", "cut": "hold"}
            if rec.newnote and rec.dummy and not tick:
                state["dummy"] = state["dummy"] or self.dummy(rec.hr)
                commands["run"] = self.ins[state["dummy"]].get("ctrl")
            if rec.newnote and not tick and self.hrc:
                fx = rec.row.instrument or 0
                carry = fx >= 0x3F if fx or not self.feats.hardrestypes else (rec.row.fx or 0) >= 3
                commands["hr.freq"] = self.call("hrc", c=int(carry))
            if rec.newnote and self.hr(rec.hr, tick):
                commands |= self.hr_commands(rec.hr)
                atom = "-" if rec.dummy else "off"
            if tick in rec.skips:
                commands["run"] = self.resume(rec.skips[tick][1], rec.skips[tick][0])
            out.append(Event(atom, commands=commands))
        if rec.applied:
            out.append(self.apply(rec, voice, state))
            if any(key == "tick.freq" for key, _ in out[2].commands):
                # A binding the slot already holds would keep its phi: rebind it first.
                out[1] = Event(
                    out[1].note, commands=dict(out[1].commands) | {"tick.freq": self.call("stp")}
                )
        if length < len(out):
            if rec.applied:
                raise TranscribeError(f"a {length}-tick row")
            out = out[:length]
        out += [Event("-")] * (length - len(out))
        return out

    def apply(self, rec, voice: int, state: dict) -> Event:
        """The TICK_2 event of an applied row."""
        ins, note = rec.ins, rec.row.note
        commands = self.fx(rec)
        if rec.newnote and "dt.freq" in commands:
            raise TranscribeError("a detune FX on a note")
        if rec.newnote and ins is None:
            raise TranscribeError("a note before any instrument")
        if note is None or ins is None:
            return Event("-", commands=commands)
        if rec.newnote:
            name = self.instrument(ins, rec.number, voice)
            given = name if state["ins"] != name else None
            state["ins"] = name
            if self.hrc:
                commands["hr.freq"] = "none"
            return Event(self.pitch(rec), given, commands, keep_filter=rec.keep_filter)
        if 0 < note < 0x60:
            return self.glide(rec, commands)
        if note in (GATE_OFF, GATE_ON):
            return Event("off" if note == GATE_OFF else "on", commands=commands)
        if 0x60 <= note < NPORTAM:
            byte = (note & 0x0F) << 4 | ins.vibrato & 0x0F
            if "tick.freq" in commands:
                raise TranscribeError("two FX of one cell on a row")
            return Event("-", commands=commands | {"tick.freq": self.vibrato_byte(ins, byte, True)})
        if note == NPORTAM:
            return Event(
                "-",
                commands=commands | {"tick.freq": self.call("sld", m=0xFF, **self.amp(110, False))},
            )
        raise TranscribeError(f"note column ${note:02X}")

    def glide(self, rec, commands: dict) -> Event:
        """A note without STRTSND (legato, BIGFX03, after NPORTAM): toward its word, the
        tables resumed past TICK_2."""
        fx, value = rec.row.fx, rec.row.fx_value or 0
        slid = commands.pop("tick.freq", None)
        if fx == 0x03:
            call = self.call("por", m=0x83, **self.amp(value, False))
        elif rec.row.instrument == LEGATO and fx in (0x01, 0x02):
            call = self.call("por", m=0x80 + fx, **self.amp(value))
        elif slid is not None:
            raise TranscribeError("a legato note with a vibrato FX")
        elif rec.row.instrument == LEGATO:
            call = self.call("leg")
        else:
            call = self.call("npn")
        commands |= {"tick.freq": call, "legato": True}
        wave, pulse, filt = rec.resume
        commands["run"] = self.resume(rec.ins, wave, pulse, filt)
        return Event(self.pitch(rec), commands=commands)

    def pitch(self, rec) -> str:
        """The note name of a row's pitch with its octave shift and transpose."""
        if not -1 <= rec.note < 0x80:
            raise TranscribeError(f"a note sounding at {rec.note}")
        return note_name(rec.note)

    # ---- the song -----------------------------------------------------------------------

    def occurrences(self, tr: Trace, voice: int) -> tuple:
        """``(plays, end, horizon)`` of ``voice``: ``(tick, phrase)`` of each pattern it plays
        whole, the tick it ends (None), the start of the pattern the trace cut."""
        reads, end = tr.reads[voice], tr.ended[voice]
        frames = [r.frame for r in reads] + ([end] if end is not None else [])
        state, plays, events, tick = {"ins": None, "dummy": None}, [], [], None
        for k, rec in enumerate(reads):
            if rec.start and tick is not None:
                plays.append((tick, self._phrase(events)))
                events = []
            if rec.start:
                tick = rec.frame
            if k + 1 >= len(frames):
                return plays, None, tick, state["dummy"]
            events += self.events(rec, voice, state, frames[k + 1] - rec.frame)
        if tick is not None:
            plays.append((tick, self._phrase(events)))
        return plays, end, None, state["dummy"]

    def _phrase(self, events: list) -> str:
        return self.phrases.setdefault(tuple(rests(events)), f"p{len(self.phrases) + 1}")

    def timeline(self, tr: Trace) -> tuple:
        """``(rows, loop row or None, each voice's dummy instrument)``."""
        entries, horizon, dummies = {}, None, []
        for v in range(VOICES):
            plays, end, cut, dummy = self.occurrences(tr, v)
            dummies.append(dummy)
            for tick, phrase in plays:
                entries.setdefault(tick, [CONT] * VOICES)[v] = Play(phrase)
            if end is not None:
                entries.setdefault(end, [CONT] * VOICES)[v] = END
            if cut is not None:
                horizon = cut if horizon is None else min(horizon, cut)
        ticks = sorted(t for t in entries if horizon is None or t < horizon)
        rows = [tuple(entries[t]) for t in ticks]
        if horizon is None:
            return rows, None, dummies
        loop = repeat(rows, [b - a for a, b in zip(ticks, ticks[1:] + [horizon])])
        if loop is None:
            raise TranscribeError("a timeline that does not repeat")
        return rows[: loop[0] + loop[1]], loop[0], dummies

    def slots(self) -> dict:
        """The frequency's slots in the player's order: VIBSLIDE, WFARPTB, WRPITCH."""
        held = ("note", "legato")
        out = [
            Slot("snd"),
            Slot("tick", hold=("note-2", "note-1", "note")),
            Slot("wave", hold=held, reset=("row.note",)),
        ]
        if self.hrc:
            out.append(Slot("hr"))
        if self.feats.detunesupport:
            out.append(Slot("dt", hold=held, reset=("row.freq",)))
        return {"freq": out}

    def document(self) -> str:
        """The document of the song to its loop or end."""
        tr = trace(self.swm)
        if tr.refused:
            raise TranscribeError(tr.refused[0])
        rows, loop, dummies = self.timeline(tr)
        phrases = Section("phrase", [(name, list(events)) for events, name in self.phrases.items()])
        song = [SongRow(rows[0], [phrases])] + [SongRow(r) for r in rows[1:]]
        defs = [Section("ins", sorted(self.ins.items(), key=lambda kv: int(kv[0][1:])))]
        defs += [Section(kind, store) for kind, store in self.programs.items() if store]
        named = {s for t in self.used for s in code.symbols(self.routines[t])}
        state = [(k, size) for k, size in STATE["freq"].items() if k in named]
        header = Header(
            VERSION,
            self.meta,
            clock=CLOCK,
            tempo=1,
            slots=self.slots(),
            state={"freq": state} if state else None,
        )
        mods = [
            (signature(name, code.params(self.routines[name])), Code(self.routines[name]))
            for name in self.used
        ]
        init = [Event("-", dummy) for dummy in dummies]
        init[0] = Event("-", dummies[0], {"vol": 15})
        doc = Document(header, mods=mods, defs=defs, init=init, rows=song, loop=loop)
        return printed(doc)


def transcriber(raw: bytes, subtune: int = 0, frames: int = 20000) -> Transcriber:
    """The transcriber of one subtune of a SID."""
    psid = SidImage.from_bytes(raw).header
    meta = (("title", psid.name), ("author", psid.author), ("released", psid.released))
    swm = parse_sid_song(raw, subtune + 1)
    return Transcriber(swm, tuple((k, meta_text(v)) for k, v in meta), frames)


def transcribe(raw: bytes, subtune: int = 0) -> str:
    """The document text of one subtune of a SID."""
    return transcriber(raw, subtune).document()


def compare(swm, text: str, frames: int = 20000):
    """The ``(tick, register, ours, theirs)`` cells where document ``text``'s grid differs
    from the reference player's at the standard note table (:func:`standard`) over ``frames``
    ticks."""
    ours = play(text, ticks=frames)
    theirs = np.asarray(standard(swm).render_grid(len(ours)), dtype=np.uint8)
    return gridcmp.cell_diff(ours, theirs)


def verify(raw: bytes, subtune: int = 0, frames: int = 20000):
    """``(cells, document)`` of one subtune of a SID: :func:`compare` of its transcription."""
    one = transcriber(raw, subtune, frames)
    text = one.document()
    return compare(one.swm, text, frames), text


def main(argv=None) -> int:
    """Transcribe a SID, or verify SIDs against pysidwizard."""
    return cli.main(argv, __doc__.split("\n", 1)[0], transcribe, verify)


if __name__ == "__main__":
    sys.exit(main())
