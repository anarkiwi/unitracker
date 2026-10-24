"""The reference player's run of a SID-Wizard tune, as :mod:`unitracker.direct.sidwizard`
reads it: each voice's pattern rows as the player reads and applies them, at the standard note
table, until the orderlists loop together."""

import math
from dataclasses import dataclass, field

from pysidwizard import SWMPlayer
from pysidwizard.player import PAL_CYCLES_PER_FRAME
from unitracker_grammar import frequency

# The clock whose note table F is nearest FREQTBL/FREQTBH at note k + 1: no clock is exact.
CLOCK = (PAL_CYCLES_PER_FRAME, 985256)
VOICES = 3
LEGATO = 0x3F
MAX_FRAMES = 200000
LOOPS = 4


class TraceError(ValueError):
    """The run does not loop within its frames."""


def _col(image: bytes, pos: int, past=0):
    """Byte ``pos`` of ``image``, ``past`` beyond it."""
    return image[pos] if pos < len(image) else past


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
    holder: bool = False
    kept: tuple = (None, None, False)
    handover: tuple = None
    gate: tuple = None
    switch: tuple = None
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
            out.reads[i][-1].holder = ref.filter_controller_voice is v

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
        before, octave = v.instrument, v.octave_shift
        if rec.newnote and v.instrument is not None and not 1 <= (row.instrument or 0) < LEGATO:
            control, feats = v.instrument.control, ref.features
            held = ref.filter_controller_voice is v
            pulse = (v.pw_pos, v.pw_sweep_count) if feats.pwresetsw and control & 0x40 else None
            filt = None
            if feats.filtresetsw and control & 0x80:
                filt = (ref.filt_pos, ref.filter_sweep_count) if held else ()
            rec.kept = (pulse, filt, False)
        apply_row(v, row)
        rec.applied, rec.ins, rec.number = True, v.instrument, v.instrument_idx
        if v.instrument is not before and not rec.newnote:
            frame = ref.frame_idx
            if before is None:
                out.refused.append(f"an instrument selected before any note, frame {frame}")
            elif v.octave_shift != octave:
                out.refused.append(
                    f"an instrument of another octave selected without a note, frame {frame}"
                )
            held = ref.filter_controller_voice is v
            rec.switch = (
                (v.wf_pos, v.arp_speed_counter, v.current_chord, v.chord_pos),
                (v.pw_pos, v.pw_sweep_count),
                (ref.filt_pos, ref.filter_sweep_count) if held else None,
                v.arp_speed_reload & 0x3F if ref.features.arpspeedsupp else 0,
            )
        rec.note = v.note + v.octave_shift + v.transpose - 1
        rec.adsr = (v.sid_ad, v.sid_sr)
        rec.chord = (v.wf_pos, v.arp_speed_counter, v.current_chord, v.instrument)
        ins = v.instrument
        if row.note == 0x7E and ref.features.gateoffptr and ins is not None:
            held = ref.filter_controller_voice is v
            wave = (v.wf_pos, v.arp_speed_counter, v.current_chord, v.chord_pos)
            pulse = (v.pw_pos, v.pw_sweep_count) if ins.gateoff_pw else None
            filt = (ref.filt_pos, ref.filter_sweep_count) if ins.gateoff_filt and held else None
            if ins.gateoff_wf or pulse or filt:
                rec.gate = (wave, pulse, filt, bool(ins.gateoff_wf))

    def run_deferred(v):
        pending = v.deferred_fx is not None
        deferred(v)
        if pending:
            rec = out.reads[voices[id(v)]][-1]
            rec.adsr = (v.sid_ad, v.sid_sr)
            rec.chord = (v.wf_pos, v.arp_speed_counter, v.current_chord, v.instrument)

    def filter_state():
        return (
            ref.filter_cutoff_hi,
            ref.filter_cutoff_lo,
            ref.filter_resonance >> 4,
            ref.filter_mode_vol & 0x70,
            ref.filter_kbd_track,
        )

    def claim(v):
        holder = ref.filter_controller_voice
        first = (v.instrument.filter_table or b"\xff")[0]
        claiming = first not in (0x00, 0xFF)
        if claiming and holder is not None and holder is not v and filter_state() != claims[0]:
            rec = out.reads[voices[id(v)]][-1]
            rec.handover = filter_state()
        setfltp(v)
        claims.append(v)

    def hard(v, pre, apply_sc=2):
        fires = staccato(v, pre, apply_sc)
        if fires:
            rec = out.reads[voices[id(v)]][-1]
            wave = (v.wf_pos, v.arp_speed_counter, v.current_chord, v.chord_pos)
            held = ref.filter_controller_voice is v
            filt = (ref.filt_pos, ref.filter_sweep_count) if held else None
            pulse = (v.pw_pos, v.pw_sweep_count)
            rec.skips[ref.frame_idx - rec.frame] = (wave, v.instrument, pulse, filt)
        return fires

    ref._advance_row, ref._apply_row, ref._run_deferred_fx = read, applied, run_deferred
    ref._setfltp, ref._is_staccato_hr_tick = claim, hard
    for frame in range(frames):
        claims[:] = [filter_state()]
        ref.play_frame()
        _claims(ref, claims[1:], out)
        for i, v in enumerate(ref.voices):
            if v.sequence_ended and out.ended[i] is None:
                out.ended[i] = frame
        if out.refused or _looped(out, frame):
            out.frames = frame + 1
            return out
    raise TraceError(f"no loop within {frames} frames")


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
    filter's voice that does not take it leaves it walking, through the new instrument."""
    holder = ref.filter_controller_voice
    voices = {id(v): i for i, v in enumerate(ref.voices)}
    for v in claims:
        rec = out.reads[voices[id(v)]][-1]
        first = (rec.ins.filter_table or b"\xff")[0] if rec.ins is not None else 0xFF
        took = first not in (0x00, 0xFF)
        if took and holder is not v:
            rec.keep_filter = True
        elif not took and holder is v:
            # The walk goes on from where it is (or its rewound end), through the new bytes.
            rec.kept = (rec.kept[0], (ref.filt_pos, ref.filter_sweep_count), True)
