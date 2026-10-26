"""A SID-Wizard tune transcribed to the generic document from pysidwizard's playroutine.

Every tick is a step: a pattern row's TICK_0 and TICK_1 (the hard restart's) and TICK_2 (the row
applied) carry its events, its tempo the rests after them; the WF, pulse and filter tables are
programs, the vibrato, slides and WRPITCH code modulators (docs/direct/sidwizard.md).
"""

import sys

import numpy as np
from pysidtracker import SidImage, gridcmp
from pysidwizard import SWMPlayer, parse_sid_song
from pysidwizard.constants import INST_WF_TABLE_POS
from pysidwizard.player import DEFAULT_HR_ADSR
from unitracker_grammar import code
from unitracker_grammar.document import (
    CONT,
    END,
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
from unitracker.direct.goattracker2 import meta_text, note_name, repeat, rests
from unitracker.direct.swtables import (
    Tables,
    TranscribeError,
    _rows3,
    _swept_first,
    _unnoted,
)
from unitracker.direct.swcode import STATE, mirror_templates, templates, track_templates
from unitracker.direct.swtrace import CLOCK, LOOPS, VOICES, Trace, standard, trace, trapped

LEGATO, GATE_OFF, GATE_ON, NPORTAM = 0x3F, 0x7E, 0x7D, 0x78
DUMMY = 256


class Transcriber(Tables):  # pylint: disable=too-many-instance-attributes
    """The first subtune of a SID-Wizard module as a generic document."""

    def __init__(self, swm, meta: tuple = (), frames: int = 20000, check: bool = True):
        self.swm, self.ref, self.meta, self.frames = swm, standard(swm), meta, frames
        self.speed = swm.frame_speed
        self.feats = self.ref.features
        refused = self._unsupported() if check else []
        if refused:
            raise TranscribeError(f"{self.feats.name} build: {', '.join(refused)}")
        # A table walk can read any instrument byte as a track amount: write as the player.
        self.pwkb, self.fkb = self.feats.pwkeybtrack, self.feats.filtkbtrack
        # BIGFX0F writes CTFH under the filter walk: the walk keeps its cutoff in code.
        self.cutfx = any(r.fx == 0x0F for p in swm.patterns for r in p.rows)
        self.fkb = self.fkb or self.cutfx
        self.walk = "walk.cut" if self.cutfx else "cut"
        self._bind(self._held_rows())
        self.programs = {"ctrl": {}, "pw": {}, "filter": {}}
        self.ins, self.used, self.phrases, self.dummies = {}, [], {}, []

    def _bind(self, rows: bool) -> None:
        """The templates, with HARDRST's carry and the pulse mirror where WF rows hold
        (``rows``)."""
        feats = self.feats
        held = feats.arpspeedsupp and feats.detunesupport and rows
        self.hrc = held and not feats.vibslidealways
        self.mirror = held and (self.pwkb or any(i.pw_table for i in self.swm.instruments))
        self.code = templates(feats.calcvibrato, feats.vibratotypes, feats.portavibra, self.speed)
        if self.pwkb or self.fkb:
            self.code |= track_templates(feats.finefiltsweep, self.speed)
        if self.mirror:
            self.code |= mirror_templates(self.pwkb, self.speed)
        self.routines = {name: code.read(text) for name, text in self.code.items()}

    def _unsupported(self) -> list:
        """What the tune uses that this transcription refuses."""
        swm, feats, out = self.swm, self.feats, []
        if self.ref.image.play_gate:
            out.append("a play gate")
        if not swm.instruments or len(swm.sequences) < VOICES:
            out.append("no instruments, or fewer orderlists than voices")
        if feats.arpspeedsupp and not feats.multispeedsupp and self._held_rows():
            out.append("WF rows held over ticks without STRTSND's ARPSCNT reset")
        return out

    def _held_rows(self, header: bool = False) -> bool:
        """Whether a WF row lasts more than a tick: an arp speed or a hold row (with
        ``header``, also a header byte a walk from position 0 reads as one)."""
        image = self.ref._image  # pylint: disable=protected-access
        return any(
            i.arp_speed & 0x3F
            or any(r[0] < 0x10 for r in _rows3(i.wf_table))
            or header
            and any(r[0] < 0x10 for r in _rows3(image(i)[:INST_WF_TABLE_POS]))
            for i in self.swm.instruments
        )

    # ---- modulators ---------------------------------------------------------------------

    def amp(self, raw: int, zero: bool = True) -> dict:
        """SETFMOD's argument for raw amplitude ``raw`` (0 none where ``zero``): ``r`` one more
        than the half the build adds to the note, else ``g`` the pitch-independent FREQMOD."""
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

    def strtsnd(self, ins, voice: int) -> Call:
        """Instrument ``ins``'s STRTSND on ``voice``: SLIDEVIB, the vibrato, FREQHI's write."""
        kind, freq = ins.control & 0x30, ins.vibrato & 0x0F
        high, index = 0, 0
        if (
            self.feats.frame1switch
            and ins.control & 0x08
            and not self.ref.image.quirks.first_frame_fixed_waveform
        ):
            high = 1
            if self.ref.image.quirks.first_frame_freq_by_track:
                high, index = 2, (voice * 7 - 1) & 0xFF
        return self.call(
            "snd",
            c=kind,
            d=ins.vibrato_delay & 0xFF,
            i=self._counter(kind, freq),
            q=2 * freq,
            h=high,
            t=index,
            **self.amp((ins.vibrato >> 4) << 3),
        )

    def vibrato_byte(self, ins, byte: int) -> Call:
        """FORCVIB and SETVIBR of vibrato ``byte`` on instrument ``ins``."""
        kind, freq = ins.control & 0x30, byte & 0x0F
        return self.call(
            "vfx",
            c=kind,
            i=self._counter(kind, freq),
            q=2 * freq,
            **self.amp((byte >> 4) << 3),
        )

    # ---- instruments ---------------------------------------------------------------------

    def instrument(self, ins, number: int, voice: int) -> str:
        """The name of SID-Wizard instrument ``ins`` (number ``number``) on ``voice``."""
        name = f"i{number}"
        if self.ref.image.quirks.first_frame_freq_by_track and self.feats.frame1switch:
            name = f"i{number + 64 * (voice + 1)}"
        if name in self.ins:
            return name
        fields = {
            "ad": ins.attack << 4 | ins.decay,
            "sr": ins.sustain << 4 | ins.release,
            "ctrl": self.wave(ins),
        }
        if not _swept_first(ins.pw_table):
            fields["pw"] = self.pulse(ins)
        table = ins.filter_table
        if table and table[0] not in (0x00, 0xFF) and not _swept_first(table):  # claims
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

    def _hr_commands(self, ins) -> dict:
        """HRGTOFF's commands: the hard-restart envelope, $18 for a staccato instrument."""
        if self.feats.hardrestypes:
            out = {
                "ad": ins.hr_attack << 4 | ins.hr_decay,
                "sr": ins.hr_sustain << 4 | ins.hr_release,
            }
            return out | ({"ctrl": 0x18} if ins.control & 0x04 else {})
        return {"ad": DEFAULT_HR_ADSR >> 8, "sr": DEFAULT_HR_ADSR & 0xFF}

    def dummy(self, ins, first: int = 0) -> str:
        """The instrument whose WF program a voice's first HR ticks walk: the header of
        instrument 0 read as WF rows from DPITCH 0, gated as PTNGATE (0 after INITER) is. A
        multispeed tune's walks it from tick 0, MULCNTP on each MULPLY call to the first note's
        STRTSND (TICK_0 at ``first``, or none with no ``ins``), HRENDER on its TICK_0 and TICK_1."""
        # pylint: disable=too-many-locals
        n, held = self.speed, ins is not None
        staccato = held and self.feats.hardrestypes and ins.control & 0x04
        dummy = self.ref._dummy  # pylint: disable=protected-access
        image = self.ref._image(dummy)  # pylint: disable=protected-access
        setup = [first, first + n] if n > 1 else [0, 1]
        span = first + 2 * n if held else first
        walks = [t for t in range(span) if t % n != n - 1 or t in setup[: held * 2]]
        # HRGTOFF's PTNGATE $FE from the first setup tick it fires on.
        fired = [t for k, t in enumerate(setup if held else ()) if self.hr(ins, k)]
        masks = {t: 0xFE if fired and t >= fired[0] else 0 for t in walks}
        for k, t in enumerate(setup if held else ()):
            if staccato and self.hr(ins, k):
                # HRGTOFF's staccato exit walks no table on its tick.
                walks.remove(t)
        rows, _ = self.wave_rows(image, 0, dummy, len(walks), reload=0)
        ticks = []
        for row in rows:
            keys = dict(row.keys)
            ticks += [keys] + [{}] * (keys.pop("hold", 1) - 1)
        reads = dict(zip(walks, ticks))
        out = []
        for t in range(walks[-1] + 1 if walks else 0):
            keys = dict(reads.get(t, {}))
            if "ctrl" in keys:
                keys["ctrl"] &= masks[t]
            out.append(_unnoted(Row(keys)))
        first = next((k for k, r in enumerate(out) if r.keys), None)
        if first is None:
            raise TranscribeError("an idle walk that reads no row")
        out[first] = Row(dict(out[first].keys) | {"wave.freq": self.call("tone")})
        if n > 1 and dummy.arp_speed & 0xC0:
            # MULCNTP walks its pulse table too, from PWTPOS 0, on the MULPLY calls.
            steps = [t for t in range(span) if t % n != n - 1]
            out.insert(0, Row({"hold": 0, "run": self.pulse(dummy, 0, 0, steps=steps)}))
        if self.feats.vibslidealways:
            # The hard-restart ticks walk the pulse table too: the header's, from PWTPOS 0.
            at = min((t for t in walks if t in setup), default=0)
            out.insert(at, Row({"hold": 0, "run": self.pulse(dummy, 0, 0, lead=False)}))
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
                out["tick.freq"] = self.vibrato_byte(ins, low << 4 | ins.vibrato & 0x0F)
            elif high == 0x90:
                out["tick.freq"] = self.call("vfq", q=low << 1)
            elif high == 0xA0:
                out["vol"] = low
            elif high == 0xB0:
                out["mode"] = low << 4
            elif high == 0xD0:
                out |= self._dfx(low << 3)
            elif high == 0x40:
                out |= self._waveform(rec)
            elif high == 0xC0:
                out |= {"run": self._paced(rec, low)} if self.feats.arpspeedsupp else {}
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
            0x08: lambda: {"tick.freq": self.vibrato_byte(ins, value)} if ins else None,
            0x0D: lambda: self._dfx(value),
            0x16: lambda: {"tick.freq": self.call("kty", c=value & 0x30)},
            0x0F: lambda: {("fx" if rec.cutfirst else "late") + ".cut": self.call("fhi", c=value)},
            0x04: lambda: self._waveform(rec),
        }
        if fx == 0x0C:
            made[fx] = lambda: {"run": self._paced(rec, value)} if self.feats.arpspeedsupp else {}
        elif fx in (0x07, 0x09, 0x0A, 0x0B, 0x0E) or 0x17 <= fx <= 0x1C or fx == 0x1F:
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

    def _waveform(self, rec) -> dict:
        """A FX writing WFGHOST: the control byte it leaves, gated as the voice is."""
        value, gate = rec.ctrl
        if rec.newnote or value & 1 and not gate:
            raise TranscribeError("a waveform FX on a note or setting a gate the voice has off")
        return {"ctrl": value}

    def _paced(self, rec, speed: int) -> str:
        """An arp speed FX: the WF walk from where it is at its new pace."""
        if rec.newnote or rec.ins is None:
            raise TranscribeError("an arp speed FX on a note or before any instrument")
        return self.resume(rec.ins, rec.arp, skip=False, reload=speed & 0x3F)

    def _dfx(self, detune: int) -> dict:
        if not self.feats.detunesupport:
            return {}
        return {"dt.freq": self.call("dfx", d=detune & 0xFF)}

    def setup(self, rec, state: dict, tick: int) -> Event:
        """The event of a read row's TICK_0 or TICK_1 (``tick``): HARDRST's hard restart and
        the tables it holds, the dummy walk before a voice's first note, a staccato skip."""
        commands, atom = {}, "-"
        new = rec.newnote and not rec.dummy and not self.feats.vibslidealways
        if new and not tick:
            commands = {"pw": "hold", "cut": "hold"} | ({"pm.freq": "none"} if self.mirror else {})
            dropped, run = self.mulcntp(rec)
            commands = {k: v for k, v in commands.items() if k not in dropped}
            commands |= {"run": run} if run else {}
        if new and tick and self.fkb and rec.holder:
            # The cutoff is written every tick: its track follows the note STRTSND sets.
            commands = {self.walk: self.call("frs")}
        if rec.vol is not None and not tick:
            commands["vol"] = rec.vol
        if rec.newnote and rec.dummy and not tick and self.speed == 1:
            state["dummy"] = state["dummy"] or self.dummy(rec.hr)
            commands["run"] = self.ins[state["dummy"]].get("ctrl")
        if rec.newnote and not tick and self.hrc:
            fx = rec.row.instrument or 0
            carry = fx >= 0x3F if fx or not self.feats.hardrestypes else (rec.row.fx or 0) >= 3
            commands["hr.freq"] = self.call("hrc", c=int(carry))
        if rec.newnote and self.hr(rec.hr, tick):
            commands |= self._hr_commands(rec.hr)
            atom = "-" if rec.dummy else "off"
        if tick * self.speed in rec.skips and not rec.dummy:
            wave, ins, pulse, filt, gate = rec.skips[tick * self.speed]
            gate = gate if rec.unnoted else None
            if self.feats.vibslidealways:
                # The staccato tick walks no table and no vibrato, which this build would.
                commands["run"] = self.resume(ins, wave, pulse, filt, gate=gate)
                commands["tick.freq"] = self.call("wt")
            else:
                commands["run"] = self.resume(ins, wave, gate=gate)
        return Event(atom, commands=commands)

    def mulcntp(self, rec) -> tuple:
        """``(commands it drops, program it runs)`` of a new note's TICK_0 where the
        instrument's ARPSPED has MULCNTP walk the pulse (and the filter) on the MULPLY calls to
        STRTSND, which HARDRST's skip."""
        walker, pulse, swept, filt, count = rec.walking
        if self.speed == 1 or walker is None or not walker.arp_speed & 0xC0:
            return (), None
        steps = [t for t in range(2 * self.speed) if t % self.speed]
        out = {"pw": self.pulse(walker, pulse, swept, steps=steps)}
        if walker.arp_speed & 0x80 and rec.holder:
            out["cut"] = self.filter(walker, filt, count, steps=steps)
        if "cut" in out:
            # One run command: the pulse program starts the filter's.
            pw = self.programs["pw"][out["pw"]]
            rows = [Row({"hold": 0, "run": out["cut"]}), *pw.rows]
            loop = None if pw.loop is None else pw.loop + 1
            out = {"pw": self._named("pw", "pw", Program(rows, loop))}
        return ("pw", "pm.freq", "cut") if "cut" in out else ("pw", "pm.freq"), out["pw"]

    def events(self, rec, voice: int, state: dict, length: int) -> list:
        """The events of one read row on ``voice``, ``length`` ticks from its TICK_0: TICK_0,
        TICK_1 and TICK_2 on its first PLAYER calls, the rests between."""
        out = [self.setup(rec, state, 0), self.setup(rec, state, 1)]
        rebind = {}
        if rec.applied:
            if length < 2 * self.speed + 1:
                raise TranscribeError(f"a {length}-tick row")
            out.append(self.apply(rec, voice, state))
            if rec.switch is not None:
                out[2] = self.switch(rec, voice, state, out[2])
            # A binding the slot already holds would keep its phi: rebind it the tick before.
            applied, before = dict(out[2].commands), dict(out[1].commands)
            for key, value in (("tick.freq", self.call("stp")), ("fx.cut", "none")):
                if key in applied and key not in before:
                    rebind[key] = value
            if "late.cut" in applied:
                rebind["late.cut"] = "none"
        ticks = [Event("-")] * length
        for k, event in enumerate(out):
            if k * self.speed < length:
                ticks[k * self.speed] = event
        if rebind:
            k = 2 * self.speed - 1
            e = ticks[k]
            ticks[k] = Event(e.note, e.ins, dict(e.commands) | rebind, e.keep_pw, e.keep_filter)
        return ticks

    def switch(self, rec, voice: int, state: dict, event: Event) -> Event:
        """``event`` on a row that selects instrument ``rec.ins`` without STRTSND: its tables
        walk on from where they are, through the new instrument's bytes (a FX's walk as the
        FX left it)."""
        commands = dict(event.commands)
        wave, pulse, filt, reload, gate = rec.switch
        if rec.tick2:
            # A note without STRTSND: its walk resumed through the new instrument at the
            # ARPSPED STRTSND latched; the next STRTSND names the instrument.
            commands["run"] = self.resume(rec.ins, *rec.resume, reload=reload)
            return Event(event.note, event.ins, commands, event.keep_pw, event.keep_filter)
        gate = gate if rec.unnoted else None
        commands["run"] = self.resume(
            rec.ins, wave, pulse, filt, skip=False, reload=reload, gate=gate
        )
        name = self.instrument(rec.ins, rec.number, voice)
        state["ins"] = name
        return Event(event.note, name, commands, event.keep_pw, event.keep_filter, event.steps)

    def strtsnd_event(self, rec, voice: int, state: dict, commands: dict) -> Event:
        """The event of a note STRTSND starts: its instrument, the tables it keeps walking
        (reset switches, a filter it does not take) or the filter it takes over."""
        ins = rec.ins
        name = self.instrument(ins, rec.number, voice)
        given = name if state["ins"] != name else None
        state["ins"] = name
        if self.hrc:
            commands["hr.freq"] = "none"
        pulse, filt, routed = rec.kept
        start = INST_WF_TABLE_POS + len(ins.wf_table) + 1
        if pulse is None and _swept_first(ins.pw_table):
            pulse = (start, rec.counts[0])
        table = ins.filter_table
        if filt is None and _swept_first(table) and table[0] and not rec.keep_filter:
            filt, routed = (start + len(ins.pw_table) + 1, rec.counts[1]), True
        if (pulse, filt, routed) != rec.kept:
            rec.kept = (pulse, filt, routed)
        if rec.handover is not None:
            if "run" in commands or pulse is not None or filt is not None:
                raise TranscribeError("a filter handover on a note that runs another table")
            commands["run"] = self.filter(ins, handover=rec.handover)
        if pulse is not None or filt is not None:
            # A chord FX (its own run) picks the chord the kept walk plays.
            chord = rec.chord[2] if "run" in commands else None
            commands["run"] = self.wave(ins, chord, rec.kept)
        keep = rec.keep_filter or filt is not None
        return Event(self.pitch(rec), given, commands, keep_pw=pulse is not None, keep_filter=keep)

    def apply(self, rec, voice: int, state: dict) -> Event:
        """The TICK_2 event of an applied row."""
        # pylint: disable=too-many-branches,too-many-return-statements
        ins, note = rec.ins, rec.row.note
        commands = self.fx(rec)
        if rec.newnote and "dt.freq" in commands:
            raise TranscribeError("a detune FX on a note")
        if rec.newnote and ins is None:
            raise TranscribeError("a note before any instrument")
        if note is None or ins is None:
            return Event("-", commands=commands)
        if rec.newnote:
            return self.strtsnd_event(rec, voice, state, commands)
        if 0 < note < 0x60:
            return self.glide(rec, commands)
        if note == GATE_OFF and rec.gate is not None:
            if "run" in commands:
                raise TranscribeError("a gate-off pointer on a row that runs another table")
            wave, pulse, filt, jumped = rec.gate
            commands["run"] = self.resume(ins, wave, pulse, filt, skip=False)
            return Event("-" if jumped else "off", commands=commands)
        if note == GATE_OFF:
            return Event("off", commands=commands)
        if note == GATE_ON or 0x79 <= note <= 0x7C:
            # NGATEON, NSYNCON/OF, NRINGON/OF write WFGHOST's bit on the tick.
            commands |= self._waveform(rec)
            return Event("on" if note == GATE_ON else "-", commands=commands)
        if 0x60 <= note < NPORTAM:
            byte = (note & 0x0F) << 4 | ins.vibrato & 0x0F
            if "tick.freq" in commands:
                raise TranscribeError("two FX of one cell on a row")
            return Event("-", commands=commands | {"tick.freq": self.vibrato_byte(ins, byte)})
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
        if not -1 <= rec.note < 0xFF:
            raise TranscribeError(f"a note sounding at {rec.note}")
        return note_name(rec.note)

    # ---- the song -----------------------------------------------------------------------

    def occurrences(self, tr: Trace, voice: int) -> tuple:
        """``(plays, end, horizon)`` of ``voice``: ``(tick, phrase)`` of each pattern it plays
        whole, the tick it ends (None), the start of the pattern the trace cut."""
        reads, end = tr.reads[voice], tr.ended[voice]
        frames = [r.frame for r in reads] + ([end] if end is not None else [])
        state, plays, events, tick = {"ins": None, "dummy": None}, [], [], None
        if self.speed > 1:
            note = next((r for r in reads if r.newnote and r.dummy), None)
            hr, first = (note.hr, note.frame) if note else (None, tr.frames)
            state["dummy"] = self.dummy(hr, first)
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
            if plays and plays[0][0]:
                # A multispeed tune's MULPLY calls before the first PLAYER call.
                plays.insert(0, (0, self._phrase([Event("-")] * plays[0][0])))
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
        out = [Slot("ms")] if self.speed > 1 else []
        out += [
            Slot("snd"),
            Slot(
                "tick",
                hold=(
                    ("note",)
                    if self.feats.vibslidealways
                    else (f"note-{2 * self.speed}", f"note-{self.speed}", "note")
                ),
            ),
            Slot("wave", hold=held, reset=("row.note",)),
        ]
        if self.hrc:
            out.append(Slot("hr"))
        if self.mirror:
            out.append(Slot("pm"))
        if self.feats.detunesupport:
            out.append(Slot("dt", hold=held, reset=("row.freq",)))
        if self.cutfx:
            # BIGFX0F applies before the filter walk on a later voice, after it on an earlier.
            return {"freq": out, "cut": [Slot("fx"), Slot("walk"), Slot("late")]}
        return {"freq": out}

    def traced(self) -> tuple:
        """:meth:`timeline` of the reference player's run, run on (its tables' state can
        repeat later than its orderlists) until the timeline repeats."""
        for loops in (LOOPS, 4 * LOOPS, 16 * LOOPS):
            tr = trace(self.swm, loops=loops)
            if tr.refused:
                raise TranscribeError(tr.refused[0])
            if any(r.switch and r.unnoted for v in tr.reads for r in v):
                # An instrument selected before any note walks its header from position 0.
                self._bind(self._held_rows(header=True))
            self.phrases, self.programs = {}, {"ctrl": {}, "pw": {}, "filter": {}}
            self.ins, self.used, self.dummies = {}, [], []
            try:
                return self.timeline(tr)
            except TranscribeError as exc:
                if "does not repeat" not in str(exc) or loops == 16 * LOOPS:
                    raise
        raise AssertionError("unreachable")

    def document(self) -> str:
        """The document of the song to its loop or end; where the reference player's first tick
        differs from it, as after an init routine that wrote the SID, the registers it shows
        are the header's, held until the player writes them."""
        text = self.assemble()
        first = standard(self.swm).render_grid(1)[0]
        if play(text, ticks=1)[0].tolist() == list(first):
            return text
        return self.assemble(tuple(int(r) for r in first))

    def _cutoff(self) -> dict:
        """The cutoff the tune's init leaves in CTFH and CTFL, where the cutoff is code."""
        _, high, low, _ = self.ref.image.filter_init
        if not self.fkb or not high | low:
            return {}
        return {self.walk: self.call("fho", c=high, l=low & 7, j=0)}

    def assemble(self, start: tuple | None = None) -> str:
        """The document of the song to its loop or end, from registers ``start`` before tick 0."""
        rows, loop, dummies = self.traced()
        phrases = Section("phrase", [(name, list(events)) for events, name in self.phrases.items()])
        song = [SongRow(r) for r in rows]
        defs = [Section("ins", sorted(self.ins.items(), key=lambda kv: int(kv[0][1:])))]
        defs += [Section(kind, store) for kind, store in self.programs.items() if store]
        defs.append(phrases)
        # No HARDRST carry until a voice's first note sets one.
        carry = {"hr.freq": self.call("hrc", c=255)} if self.hrc else {}
        if self.speed > 1:
            # The PLAYER call's phase among the frame's calls.
            carry["ms.freq"] = self.call("ph")
        init = [Event("-", dummy, dict(carry)) for dummy in dummies]
        if self.speed > 1:
            init = [
                Event("-", d, carry | ({"run": self.ins[d].get("ctrl")} if d else {}))
                for d in dummies
            ]
        init[0] = Event("-", dummies[0], dict(init[0].commands) | {"vol": 15} | self._cutoff())
        named = {s for t in self.used for s in code.symbols(self.routines[t])}
        state = {
            cell: [(k, size) for k, size in names.items() if k in named]
            for cell, names in STATE.items()
        }
        header = Header(
            self.meta,
            clock=CLOCK,
            tempo=1,
            slots=self.slots(),
            state={cell: names for cell, names in state.items() if names} or None,
            regs=start,
        )
        mods = [
            (signature(name, code.params(self.routines[name])), Code(self.routines[name]))
            for name in self.used
        ]
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


def compare(swm, text: str, frames: int = 20000, native: bool = False):
    """The ``(tick, register, ours, theirs)`` cells where document ``text``'s grid differs
    from the reference player's at the standard note table (:func:`standard`), or ``native``
    at its own, over ``frames`` ticks."""
    ours = play(text, ticks=frames)
    ref = SWMPlayer(swm) if native else standard(swm)
    theirs = np.asarray(ref.render_grid(len(ours)), dtype=np.uint8)
    return gridcmp.cell_diff(ours, theirs)


def reference(raw: bytes, subtune: int = 0, ticks: int = 20000) -> tuple:
    """``(grid, traps)`` of one subtune of a SID: the reference player's at the standard note
    table, and the table loads its native table answers differently."""
    return trapped(parse_sid_song(raw, subtune + 1), ticks)


def verify(raw: bytes, subtune: int = 0, frames: int = 20000):
    """``(cells, document, traps)`` of one subtune of a SID: its transcription's grid against
    the reference player's (:func:`reference`)."""
    one = transcriber(raw, subtune, frames)
    text = one.document()
    ours = play(text, ticks=frames)
    theirs, found = trapped(one.swm, len(ours))
    return gridcmp.cell_diff(ours, theirs), text, found


def main(argv=None) -> int:
    """Transcribe a SID, or verify SIDs against pysidwizard."""
    return cli.main(argv, __doc__.split("\n", 1)[0], transcribe, verify)


if __name__ == "__main__":
    sys.exit(main())
