"""A SID-Wizard tune transcribed to the generic document from pysidwizard's playroutine.

A pattern row is two steps: its TICK_0 and TICK_1, the hard restart's, then its TICK_2 onward,
when the row applies; the WF, pulse and filter tables are programs, the vibrato and the frequency
compose format 1.23 code modulators (:mod:`unitracker.direct.swcode`, docs/direct/sidwizard.md).
"""

import math
import sys
from dataclasses import dataclass

import numpy as np
from pysidtracker import SidImage, gridcmp
from pysidwizard import Loop, PlayPattern, SWMPlayer, parse_sid_song
from pysidwizard.constants import INST_WF_TABLE_POS
from pysidwizard.player import DEFAULT_HR_ADSR, PAL_CYCLES_PER_FRAME
from unitracker_grammar import code, frequency
from unitracker_grammar.document import (
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
from unitracker.direct.swcode import CODE, STATE

# The clock whose note table F is nearest FREQTBL/FREQTBH at note k + 1: no clock is exact.
CLOCK = (PAL_CYCLES_PER_FRAME, 985256)
ROUTINES = {name: code.read(text) for name, text in CODE.items()}
SLOTS = {
    "freq": [
        Slot("tick", hold=("note-1", "note-2")),
        Slot("wave", hold=("legato",), reset=("row.note",)),
    ]
}
VOICES = 3
HR = 2
GATE_OFF, LEGATO, VIBFX = 0x7E, 0x3F, range(0x60, 0x78)
REFUSED = (
    "arpspeedsupp calcvibrato chordsupport detunesupport hardrestypes frame1switch octaveshift"
    " vibratotypes portavibra gateoffptr pwresetsw filtresetsw pwkeybtrack filtkbtrack"
    " fastspeedbind allghostregs vibslidealways"
).split()


class TranscribeError(ValueError):
    """The tune uses something this transcription does not spell."""


@dataclass
class Voice:
    """A voice's context as its rows are written: its instrument, whether it played a note, and
    the ticks a lower voice's STRTSND takes the filter on after its own."""

    ins: int = 0
    noted: bool = False
    shadow: frozenset = frozenset()


def _col(image: bytes, pos: int, past=0):
    """Byte ``pos`` of ``image``, ``past`` beyond it."""
    return image[pos] if pos < len(image) else past


def _signed(byte: int) -> int:
    return byte - 0x100 if byte >= 0x80 else byte


class Transcriber:  # pylint: disable=too-many-instance-attributes
    """The first subtune of a SID-Wizard module as a generic document."""

    def __init__(self, swm, meta: tuple = (), frames: int = 20000):
        self.swm, self.ref, self.meta, self.frames = swm, SWMPlayer(swm), meta, frames
        feats = self.ref.features
        refused = [name for name in REFUSED if getattr(feats, name)]
        if refused:
            raise TranscribeError(f"{feats.name} build: {', '.join(refused)}")
        if swm.frame_speed != 1 or swm.play_gate or any(swm.filter_init):
            raise TranscribeError("multispeed, a play gate or an initial filter")
        if not swm.instruments or len(swm.sequences) < VOICES:
            raise TranscribeError("no instruments, or fewer orderlists than voices")
        left, _ = swm.subtune_tempos[0] if swm.subtune_tempos else (0x86, 0x86)
        self.tempo = (left & 0x7F) or 6
        if not left & 0x80 or self.tempo < 3:
            raise TranscribeError(f"tempo ${left:02X}")
        self.programs = {"ctrl": {}, "pw": {}, "filter": {}}
        self.ins, self.used, self.phrases = {}, [], {}

    # ---- modulators ---------------------------------------------------------------------

    def call(self, template: str, **params) -> Call:
        """The call of ``template`` with ``params``, its arguments in its signature's order."""
        if template not in self.used:
            self.used.append(template)
        return Call(template, tuple(params[k] for k in code.params(ROUTINES[template])))

    def _amplitude(self, amp: int) -> int:
        """SETFMOD's FREQMOD of an amplitude nibble, which this build reads pitch-independent."""
        if not amp:
            return 0
        lo, hi = self.ref._freqmod(amp << 3, 0)  # pylint: disable=protected-access
        return hi << 8 | lo

    def vibrato(self, n: int) -> Call:
        """Instrument ``n``'s STRTSND vibrato: its delay, counter, amplitude and period."""
        ins = self.swm.instruments[n - 1]
        freq = ins.vibrato & 0x0F
        return self.call(
            "vib",
            d=ins.vibrato_delay & 0xFF,
            k=freq >> 1,
            f=self._amplitude(ins.vibrato >> 4),
            p=2 * freq,
        )

    def vibfx(self, n: int, note: int) -> Call:
        """Note-column vibrato ``note`` on instrument ``n``: the note's amplitude, its period."""
        freq = self.swm.instruments[n - 1].vibrato & 0x0F
        return self.call("vibfx", k=freq >> 1, f=self._amplitude(note & 0x0F), p=2 * freq)

    # ---- tables -------------------------------------------------------------------------

    def _image(self, n: int) -> bytes:
        """The bytes PLAYERZP reads for instrument ``n``."""
        return self.ref._image(self.swm.instruments[n - 1])  # pylint: disable=protected-access

    def _named(self, kind: str, prefix: str, program: Program) -> str:
        store = self.programs[kind]
        for name, held in store.items():
            if held == program:
                return name
        name = f"{prefix}{len(store)}"
        store[name] = program
        return name

    def _wave_rows(self, image: bytes, pos: int, limit: int = 256) -> tuple:
        """``(rows, loop)`` of WFARPTB from ``pos``: a row per tick, jumps taken on the tick."""
        nop = self.ref.features.wfarp_nop_supp
        rows, at = [], {}
        while len(rows) < limit:
            for _ in range(4):
                if pos >= len(image) or image[pos] == 0xFF:
                    return rows, None
                if image[pos] != 0xFE:
                    break
                target = _col(image, pos + 1)
                if target >= 0x80 or target >= len(image) or target == pos:
                    return rows, None
                pos = target
            else:
                raise TranscribeError("a WF jump chain")
            if pos in at:
                return rows, at[pos]
            at[pos] = len(rows)
            keys = {"ctrl": image[pos]}
            arp = _col(image, pos + 1, None)
            if arp is not None and not (arp == 0x80 and nop):
                if arp >= 0xE0:
                    keys["note"] = Signed(arp - 0x100)
                elif arp >= 0x80:
                    keys["note"] = note_name((arp & 0x7F) - 1)
                else:
                    keys["note"] = Signed(arp)
                keys["wave.freq"] = self.call("tone")
            rows.append(Row(keys))
            pos += 3
        return rows, None

    def wave(self, n: int) -> str:
        """The ``ctrl`` name of instrument ``n``'s WF program, after STRTSND's tick: control
        $09 and SETFLTP's routing."""
        ins = self.swm.instruments[n - 1]
        rows, loop = (
            self._wave_rows(self._image(n), INST_WF_TABLE_POS) if ins.wf_table else ([], None)
        )
        first = Row({"ctrl": 9, "route_on" if self._filtered(n) else "route_off": 0})
        return self._named("ctrl", "c", Program([first, *rows], None if loop is None else loop + 1))

    def idle(self) -> str:
        """The ``ctrl`` name of the dummy instrument's WF walk on a voice's first HR ticks."""
        dummy = self.ref._dummy  # pylint: disable=protected-access
        image = self.ref._image(dummy)  # pylint: disable=protected-access
        rows, _ = self._wave_rows(image, 0, HR)
        if not rows:
            raise TranscribeError("an idle walk that ends at once")
        rows[0] = Row(dict(rows[0].keys) | {"gate": 0, "wave.freq": self.call("tone")})
        return self._named("ctrl", "c", Program(rows))

    def _filtered(self, n: int) -> bool:
        """Whether SETFLTP routes instrument ``n``'s voice through the filter."""
        table = self.swm.instruments[n - 1].filter_table
        return bool(table) and table[0] != 0xFF

    def _swept(self, image: bytes, pos: int, row, cell: str, step) -> tuple:
        """``(rows, loop)`` of a pulse or filter program from ``pos``: ``row(pos)`` gives a set
        row's keys, ``step(byte)`` a sweep's per-tick expression."""
        rows, at = [Row({})], {}
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
            at[pos] = len(rows)
            byte = image[pos]
            if byte & 0x80:
                rows.append(Row(row(pos)))
            elif first:
                raise TranscribeError(f"a {cell} table that starts with a sweep")
            else:
                if byte:
                    rows.append(Row({"hold": byte, cell: step(_col(image, pos + 1))}))
                    rows.append(Row({cell: "none"}))
                else:
                    rows.append(Row({}))
            first = False
            pos += 3

    def pulse(self, n: int) -> str:
        """The ``pw`` name of instrument ``n``'s pulse program (SETPWID), from the tick after
        STRTSND's."""
        ins = self.swm.instruments[n - 1]
        image = self._image(n)
        rows, loop = self._swept(
            image,
            INST_WF_TABLE_POS + len(ins.wf_table) + 1,
            lambda p: {"pw": ((image[p] & 0x7F) << 8 | _col(image, p + 1)) & 0xFFF},
            "pw",
            lambda d: Expr(canonical(f"v + {_signed(d)}")),
        )
        return self._named("pw", "pw", Program(rows, loop))

    def filter(self, n: int) -> str:
        """The ``filter`` name of instrument ``n``'s filter program (FILTPRG)."""
        ins = self.swm.instruments[n - 1]
        image = self._image(n)
        scale = 1 if self.ref.features.finefiltsweep else 8

        def row(p):
            return {"res": image[p] & 0x0F, "mode": image[p] & 0x70, "cut": _col(image, p + 1) << 3}

        start = INST_WF_TABLE_POS + len(ins.wf_table) + 1 + len(ins.pw_table) + 1
        rows, loop = self._swept(
            image, start, row, "cut", lambda d: Expr(canonical(f"v + {scale * _signed(d)}"))
        )
        return self._named("filter", "f", Program(rows, loop))

    # ---- instruments and phrases --------------------------------------------------------

    def instrument(self, n: int) -> str:
        """The name of SID-Wizard instrument ``n``."""
        name = f"i{n}"
        if name in self.ins:
            return name
        if not 1 <= n <= len(self.swm.instruments):
            raise TranscribeError(f"instrument {n} past the instruments")
        ins = self.swm.instruments[n - 1]
        fields = {
            "ad": ins.attack << 4 | ins.decay,
            "sr": ins.sustain << 4 | ins.release,
            "ctrl": self.wave(n),
            "pw": self.pulse(n),
        }
        table = ins.filter_table
        if table and table[0] not in (0x00, 0xFF):
            fields["filter"] = self.filter(n)
        fields["tick.freq"] = self.vibrato(n)
        self.ins[name] = Instrument(fields)
        return name

    def dummy(self) -> str:
        """The name of the instrument a voice's first HR ticks walk: the header of instrument 0."""
        if "i0" not in self.ins:
            hr = DEFAULT_HR_ADSR
            self.ins["i0"] = Instrument({"ad": hr >> 8, "sr": hr & 0xFF, "ctrl": self.idle()})
        return "i0"

    def _small_fx(self, fx: int, n: int) -> dict:
        """The commands of instrument-column small FX ``fx`` on instrument ``n``."""
        high, low = fx & 0xF0, fx & 0x0F
        if high not in (0x50, 0x60) or not n:
            raise TranscribeError(f"small FX ${fx:02X}")
        ins = self.swm.instruments[n - 1]
        if high == 0x50:
            return {"sr": low << 4 | ins.release}
        return {"sr": ins.sustain << 4 | low}

    def events(self, row, voice: Voice, tick: int) -> list:
        """The two events of one pattern row on ``voice`` from ``tick``: its HR ticks' and its
        TICK_2's."""
        note, ins, fx = row.note, row.instrument, row.fx
        if fx is not None:
            raise TranscribeError(f"FX ${fx:02X}")
        commands = {}
        if ins is not None and 1 <= ins < LEGATO:
            if note is None or note >= 0x60:
                if ins != voice.ins:
                    raise TranscribeError("an instrument selected without a note")
            else:
                voice.ins = ins
        elif ins is not None and ins > LEGATO:
            commands = self._small_fx(ins, voice.ins)
        if note is None:
            return [Event("-"), Event("-", commands=commands)]
        if not voice.ins:
            raise TranscribeError("a note column before any instrument")
        if note == GATE_OFF:
            return [Event("-"), Event("off", commands=commands)]
        if note in VIBFX:
            return [
                Event("-"),
                Event("-", commands=commands | {"tick.freq": self.vibfx(voice.ins, note)}),
            ]
        if not 1 <= note < 0x60:
            raise TranscribeError(f"note column ${note:02X}")
        return self._pitched(note, ins == LEGATO, voice, tick, commands)

    def _pitched(self, note: int, legato: bool, voice: Voice, tick: int, commands: dict) -> list:
        """The two events of a row that plays ``note`` (1-95) on ``voice`` from ``tick``."""
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        name = note_name(note - 1)
        if legato:
            commands |= {"tick.freq": self.call("leg"), "legato": True}
            return [Event("-"), Event(name, commands=commands)]
        if voice.noted:
            first = Event("off", commands={"pw": "hold", "cut": "hold", "ad": 15, "sr": 0})
        else:
            first = Event("B-1", self.dummy())
        voice.noted = True
        keep = tick + HR in voice.shadow
        return [first, Event(name, self.instrument(voice.ins), commands, keep_filter=keep)]

    def phrase(self, pattern: int, voice: Voice, tick: int) -> str:
        """The name of the phrase one occurrence of ``pattern`` from ``tick`` plays on ``voice``."""
        events = []
        for k, row in enumerate(self.swm.patterns[pattern - 1].rows):
            events += self.events(row, voice, tick + k * self.tempo)
        return self.phrases.setdefault(tuple(rests(events)), f"p{len(self.phrases) + 1}")

    def _legatos(self, frames: int) -> None:
        """Refuses a legato note whose TICK_2, which walks no table, the reference player
        reaches with its WF, pulse or (held) filter table still walking, over ``frames``."""
        ref = SWMPlayer(self.swm)
        apply_row = ref._apply_row  # pylint: disable=protected-access

        def ended(image: bytes, pos: int, wave: bool) -> bool:
            byte = image[pos] if pos < len(image) else 0xFF
            target = image[pos + 1] if byte == 0xFE and pos + 1 < len(image) else 0
            jumped = byte == 0xFE and (
                target in (pos,) or target >= len(image) or wave and target >= 0x80
            )
            return byte == 0xFF or jumped

        def check(v, row):
            if row.instrument == LEGATO and row.note and row.note < 0x60 and v.instrument:
                image = ref._image(v.instrument)  # pylint: disable=protected-access
                walking = not ended(image, v.wf_pos, True) or not ended(image, v.pw_pos, False)
                if ref.filter_controller_voice is v and not ended(image, ref.filt_pos, False):
                    walking = True
                if walking:
                    raise TranscribeError(
                        f"a legato note at frame {ref.frame_idx} over walking tables"
                    )
            apply_row(v, row)

        ref._apply_row = check  # pylint: disable=protected-access
        ref.render_grid(frames)

    # ---- the song -----------------------------------------------------------------------

    def _starts(self, voice: int, horizon: int) -> list:
        """``(tick, pattern)`` of each pattern ``voice``'s orderlist starts before ``horizon``."""
        seq, pos, tick, out = self.swm.sequences[voice], 0, 0, []
        while tick < horizon:
            if pos >= len(seq):
                raise TranscribeError("an orderlist that ends")
            cmd = seq[pos]
            pos += 1
            if isinstance(cmd, Loop):
                pos = cmd.position
            elif isinstance(cmd, PlayPattern):
                if not 1 <= cmd.pattern <= len(self.swm.patterns):
                    raise TranscribeError(f"pattern {cmd.pattern}")
                out.append((tick, cmd.pattern))
                tick += len(self.swm.patterns[cmd.pattern - 1].rows) * self.tempo
            else:
                raise TranscribeError(f"orderlist {type(cmd).__name__}")
        return out

    def _period(self, voice: int) -> tuple:
        """``(intro, loop)`` ticks of ``voice``'s orderlist."""
        seq, rows = self.swm.sequences[voice], []
        for cmd in seq:
            if isinstance(cmd, PlayPattern) and 1 <= cmd.pattern <= len(self.swm.patterns):
                rows.append(len(self.swm.patterns[cmd.pattern - 1].rows) * self.tempo)
            else:
                rows.append(0)
        loop = next((c.position for c in seq if isinstance(c, Loop)), None)
        if loop is None:
            raise TranscribeError("an orderlist that ends")
        return sum(rows[:loop]), sum(rows[loop:])

    def _strtsnd(self, starts: list) -> list:
        """``(tick, voice, first filter table byte)`` of every STRTSND, in the player's order."""
        out = []
        for v, voice_starts in enumerate(starts):
            ins = 0
            for tick, pattern in voice_starts:
                for k, row in enumerate(self.swm.patterns[pattern - 1].rows):
                    note = row.note
                    pitched = note is not None and 1 <= note < 0x60
                    if pitched and row.instrument is not None and 1 <= row.instrument < LEGATO:
                        ins = row.instrument
                    if (
                        pitched
                        and row.instrument != LEGATO
                        and 1 <= ins <= len(self.swm.instruments)
                    ):
                        table = self.swm.instruments[ins - 1].filter_table
                        out.append((tick + k * self.tempo + HR, -v, table[0] if table else 0xFF))
        return sorted(out)

    def _shadows(self, starts: list) -> list:
        """Per voice, the ticks a lower voice's STRTSND takes the filter after its own in the
        same tick, as the player runs the voices last to first. Refuses a note of the voice
        that holds the filter that leaves the filter walking another instrument's table."""
        out, holder, ticks = [set() for _ in range(VOICES)], None, {}
        for tick, neg, first in self._strtsnd(starts):
            ticks.setdefault(tick, []).append((-neg, first))
        for tick, notes in ticks.items():
            claims = [voice for voice, first in notes if first not in (0x00, 0xFF)]
            if claims:
                holder = claims[-1]
                for voice in claims[:-1]:
                    out[voice].add(tick)
            elif any(
                v == holder and not (f == 0xFF and self.swm.filter_stop_rewinds) for v, f in notes
            ):
                raise TranscribeError("a note of the filter's voice that does not take it")
        return [frozenset(s) for s in out]

    def _horizon(self) -> int:
        """The ticks over which the orderlists, intros played, loop together four times."""
        periods = [self._period(v) for v in range(VOICES)]
        if not all(p for _, p in periods):
            raise TranscribeError("an orderlist that loops no pattern")
        cycle = math.lcm(*(p for _, p in periods))
        if cycle > 1 << 20:
            raise TranscribeError("orderlists of incommensurate loops")
        return max(i for i, _ in periods) + 4 * cycle

    def timeline(self) -> tuple:
        """``(rows, loop row)`` of the orderlists' pattern starts."""
        horizon = self._horizon()
        starts = [self._starts(v, horizon) for v in range(VOICES)]
        shadows = self._shadows(starts)
        plays = {}
        for v, voice_starts in enumerate(starts):
            voice = Voice(shadow=shadows[v])
            for tick, pattern in voice_starts:
                entry = Play(self.phrase(pattern, voice, tick))
                plays.setdefault(tick, [None] * VOICES)[v] = entry
        ticks = sorted(plays)
        rows = [tuple(p or "~" for p in plays[t]) for t in ticks[:-1]]
        loop = repeat(rows, [b - a for a, b in zip(ticks, ticks[1:])])
        if loop is None:
            raise TranscribeError("a timeline that does not repeat")
        return rows[: loop[0] + loop[1]], loop[0]

    def _header(self, named: set) -> Header:
        """The header: the build's tempo as its HR steps and the rest, the state ``named``."""
        state = {
            cell: [(k, size) for k, size in names.items() if k in named]
            for cell, names in STATE.items()
        }
        return Header(
            self.meta,
            clock=CLOCK,
            tempo=(HR, self.tempo - HR),
            slots=SLOTS,
            state={c: n for c, n in state.items() if n} or None,
        )

    def document(self) -> str:
        """The document of the song to its loop."""
        rows, loop = self.timeline()
        if "leg" in self.used:
            self._legatos(self.frames)
        phrases = Section("phrase", [(name, list(events)) for events, name in self.phrases.items()])
        song = [SongRow(r) for r in rows]
        defs = [Section("ins", sorted(self.ins.items(), key=lambda kv: int(kv[0][1:])))]
        defs += [Section(kind, store) for kind, store in self.programs.items() if store]
        defs.append(phrases)
        named = {s for t in self.used for s in code.symbols(ROUTINES[t])}
        mods = [
            (signature(name, code.params(ROUTINES[name])), Code(ROUTINES[name]))
            for name in self.used
        ]
        doc = Document(
            self._header(named),
            mods=mods,
            defs=defs,
            init=[Event("-", commands={"vol": 15})] + [Event("-")] * (VOICES - 1),
            rows=song,
            loop=loop,
        )
        return printed(doc)


def transcriber(raw: bytes, subtune: int = 0, frames: int = 20000) -> Transcriber:
    """The transcriber of one subtune of a SID, its legatos checked over ``frames``."""
    psid = SidImage.from_bytes(raw).header
    meta = (("title", psid.name), ("author", psid.author), ("released", psid.released))
    swm = parse_sid_song(raw, subtune + 1)
    return Transcriber(swm, tuple((k, meta_text(v)) for k, v in meta), frames)


def transcribe(raw: bytes, subtune: int = 0) -> str:
    """The document text of one subtune of a SID."""
    return transcriber(raw, subtune).document()


def standard(swm) -> SWMPlayer:
    """The reference player of ``swm`` with the document's note table: note k + 1 sounds
    ``F(k)`` at :data:`CLOCK`; SETFMOD's amplitudes still read the native table."""
    ref = SWMPlayer(swm)
    native = ref.freq_lo, ref.freq_hi
    words = [frequency(k - 1, CLOCK[1]) for k in range(256)]
    table = bytes(w & 0xFF for w in words), bytes(w >> 8 for w in words)
    ref.freq_lo, ref.freq_hi = table
    freqmod = ref._freqmod  # pylint: disable=protected-access

    def native_freqmod(raw: int, dpitch: int) -> tuple:
        ref.freq_lo, ref.freq_hi = native
        try:
            return freqmod(raw, dpitch)
        finally:
            ref.freq_lo, ref.freq_hi = table

    ref._freqmod = native_freqmod  # pylint: disable=protected-access
    return ref


def compare(swm, text: str, frames: int = 20000):
    """The ``(tick, register, ours, theirs)`` cells where document ``text``'s grid differs
    from the reference player's over ``frames`` ticks."""
    ours = play(text, ticks=frames)
    theirs = np.asarray(SWMPlayer(swm).render_grid(len(ours)), dtype=np.uint8)
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
