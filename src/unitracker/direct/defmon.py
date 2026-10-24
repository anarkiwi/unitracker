"""A defMON replay transcribed to the generic document from pydefmon's emulated replay.

A generic tick is a replay call's pattern, sidcall and pitch/pulse work (the next call's SID band
writes it) and that next call's ACID step; an event's two sidcall layers run as one control
program, the pitch, pulse and ACID steps as code modulators (:mod:`unitracker.direct.dmcode`).
"""

import re
import sys
from dataclasses import dataclass, field

import numpy as np
from pydefmon import DefmonPlayer
from pysidtracker import SidImage, gridcmp
from unitracker_grammar import code
from unitracker_grammar import document as d
from unitracker_player.player import play

from unitracker.direct import cli
from unitracker.direct.dmcode import CODE, STATE
from unitracker.direct.dmimage import LiftError, locate, row_fields

VERSION = "2.0"
PAL_HZ, NTSC_HZ = 985248, 1022727
NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
VOICES = (0x00, 0x31, 0x62)
COUNTER, POINTER = 0x114A, 0x1186  # voice 1's; each voice's 0x88 on
ARMED, ARR_Y, ARR_JR, ARR_JC = 0x10D9, 0x10EB, 0x14EC, 0x14ED
SIDTAB_LO, SIDTAB_HI, SIDTAB_DL = 0x1800, 0x1900, 0x1E00
GATE_A, GATE_B, GATE_N = 0x40, 0x20, 0x10
TABLES = {"G": (0x159C, 0x1638, 256), "P": (0x1583, 0x161F, 257), "R": (0x1578, 0x1614, 128)}
SLOTS = (
    ("freq", (d.Slot("n", reset=("note",)), d.Slot("p"))),
    (
        "pw",
        (
            d.Slot("pz", reset=("note",)),
            d.Slot("pm", reset=("row.freq",)),
            d.Slot("sw", reset=("row.pw",)),
        ),
    ),
    (
        "cut",
        tuple(d.Slot(f"k{v}{x}", reset=("row.cut",)) for x in "ab" for v in (1, 2, 3))
        + (d.Slot("st"),),
    ),
)
LAYER_B = {"cp": "bcp", "acid": "bacid", "re": "bre", "fv": "bfv"}
ACID = {"cp", "acid", "bcp", "bacid"}
MAX_CALLS, MAX_STEP = 1 << 20, 1 << 14
PHASE = 64
NOTE7 = "lda nn\nand #127\n"  # the absolute build's note wraps to 7 bits
ROUTINES = {name: code.read(text) for name, text in CODE.items()}
TEXT = re.compile(r"[^\s,\[\]{}'\"#&*!|>%@`][^,\[\]{}]*")


class TranscribeError(ValueError):
    """The tune uses something this transcription does not spell."""


def note_name(k: int) -> str:
    """Generic note ``k`` (12 * octave + pitch class) by name: defMON note index ``k``."""
    return f"{NAMES[k % 12]}{k // 12}"


def call(name: str, **params) -> d.Call:
    """The call of template ``name`` with ``params``, in its signature's order."""
    return d.Call(name, tuple(int(params[k]) for k in code.params(ROUTINES[name])))


BINDS = (("n.freq", call("nr")), ("p.freq", call("pitch")))  # the pitch step from call 0


@dataclass
class Step:
    """A song step: its first call, its arranger key, its length and per voice
    ``{offset: (a, b, n)}``."""

    call: int
    key: tuple
    events: list = field(default_factory=lambda: [{}, {}, {}])
    length: int = 0


def sequence(player, at: dict, calls: int = MAX_CALLS):
    """The song steps of the replay's calls, its build's ``$1000`` addresses placed by ``at``.
    A full call (it steps voice 1's counter or arms the arranger; a multispeed play routine's
    sub-tick calls do neither) runs the arranger where init or an ALT event armed it, else each
    voice whose counter has bit 7 runs the event at its pattern pointer. A step's key holds the
    full calls of the last :data:`PHASE` calls, the play routine's phase."""
    mem, step, full = player._mem, None, (1,) * PHASE  # pylint: disable=protected-access
    marks = [at[ARMED], at[COUNTER]]
    for c in range(calls):
        before = bytes(mem[a] for a in marks)
        armed = mem[at[ARMED]]
        key = tuple(mem[at[a]] if a in at else 0 for a in (ARR_Y, ARR_JR, ARR_JC)) + (armed, full)
        events = pending(mem, at)
        player.play_frame()
        ran = bytes(mem[a] for a in marks) != before
        full = full[1:] + (int(ran),)
        if step is not None and c - step.call >= MAX_STEP:
            raise TranscribeError(f"the song step from call {step.call} reaches no ALT event")
        if not ran:
            continue
        if armed & 0x80:
            if step is not None:
                step.length = c - step.call
                yield step
            step = Step(c, key)
            continue
        for v, ev in enumerate(events):
            if ev is not None:
                step.events[v][c - step.call] = ev
    raise TranscribeError(f"the song does not loop within {calls} calls")


def pending(mem, at: dict) -> list:
    """Each voice's pattern event the next full call runs, None where its counter runs."""
    return [
        (
            event_at(mem, mem[at[POINTER + 0x88 * v]] | mem[at[POINTER + 0x88 * v + 1]] << 8)
            if mem[at[COUNTER + 0x88 * v]] & 0x80
            else None
        )
        for v in range(3)
    ]


def event_at(mem, ptr: int) -> tuple:
    """``(layer A row, layer B row, note)`` of the pattern event at ``ptr``, each None absent."""
    flag, out, k = mem[ptr], [], 1
    for gate in (GATE_A, GATE_B, GATE_N):
        out.append(mem[(ptr + k) & 0xFFFF] if flag & gate else None)
        k += bool(flag & gate)
    return tuple(out)


class Transcriber:  # pylint: disable=too-many-instance-attributes
    """One subtune of a defMON replay, a build of the ``$1000`` build's code, as a document."""

    def __init__(self, raw: bytes, subtune: int = 0):
        self.raw, self.subtune = raw, subtune
        self.header = SidImage.from_bytes(raw).header
        self.player = DefmonPlayer(raw, subtune=subtune)
        self.mem = self.player._mem  # pylint: disable=protected-access
        try:
            self.at, variants = locate(self.mem)
        except LiftError as exc:
            raise TranscribeError(str(exc)) from exc
        self.absolute = "absolute" in variants
        self.check()
        self.joints, self.names, self.runs, self.lead, self.pulses = {}, {}, {}, 0, {}

    def __call__(self, addr: int, voice: int = 0) -> int:
        return self.mem[self.at[addr] + VOICES[voice]]

    def word(self, lo: int, hi: int, voice: int = 0) -> int:
        """The word of the bytes at ``lo`` and ``hi``."""
        return self(lo, voice) | self(hi, voice) << 8

    def check(self):
        """Refuses what the transcription does not spell: another build, a multispeed or
        multi-SID tune, an init that leaves state the generic init cannot write."""
        if self.header.second_sid:
            raise TranscribeError("a multi-SID tune")
        if self(0x10B8) != self(0x10BF) or self(0x10B8) not in (0x69, 0xE9):
            raise TranscribeError("ACID opcodes are not a matching SBC/ADC pair")
        if self(0x10D4) not in (0xEA, 0x0A):
            raise TranscribeError("the cutoff output is neither NOP nor ASL")
        for v in range(3):
            if (self(0x101F, v), self(0x1020, v), self(0x1021, v)) != (v, 1 << v, 0xFF ^ 1 << v):
                raise TranscribeError(f"voice {v + 1}'s detune and routing are not standard")

    # ---- sidTAB programs ----------------------------------------------------------------

    def resolve(self, row: int) -> int:
        """The row a fetch of ``row`` reads after its jump."""
        eff = row if self.sidtab(SIDTAB_HI, row) else self.sidtab(SIDTAB_LO, row)
        if not self.sidtab(SIDTAB_HI, eff):
            raise TranscribeError(f"sidTAB row {row} resolves to row {eff}, which has no body")
        return eff

    def columns(self, eff: int) -> dict:
        """The columns of sidTAB row ``eff``'s body, in the walk's ($16B0) order."""
        return row_fields(self.mem, self.sidtab(SIDTAB_LO, eff) | self.sidtab(SIDTAB_HI, eff) << 8)

    def sidtab(self, column: int, row: int) -> int:
        """sidTAB ``column``'s byte of ``row``."""
        return self.mem[self.at[column] + row]

    def tick(self, state: tuple) -> tuple:
        """``(state, columns)``: one call of a voice's sidcall cascade ($12DF) from ``state``,
        ``(row A, count A, row B, count B)``, and the columns of each row it fetches, layer A's
        first; a count of 0 fetches, $80 and over is frozen."""
        state, cols = list(state), []
        for k in (0, 2):
            row, cnt = state[k : k + 2]
            if not cnt:
                eff = self.resolve(row)
                state[k : k + 2] = (eff + 1) & 0xFF, self.sidtab(SIDTAB_DL, eff)
                cols.append(self.columns(eff))
                if k:  # layer B's ACID and filter columns apply after every voice's layer A
                    cols[-1] = {LAYER_B.get(c, c): x for c, x in cols[-1].items()}
            elif cnt < 0x80:
                state[k + 1] = cnt - 1
        return tuple(state), cols

    def joint(self, state: tuple, bits: tuple, fix: bool = False) -> tuple:
        """``(rows, loop)``: ``(ticks, columns)`` of each call that fetches from ``state`` on,
        to both layers frozen (loop None) or a state seen before. ``bits`` are bit 0 of
        ``wgh`` and ``wgl``, whose exclusive or is the gate each row that writes either sets
        (``_gate``); ``fix`` turns the gate off on the first call where no row sets it."""
        rows, seen, last = [[1, {"_gate": 0}]] if fix else [], {}, 0
        for t in range(MAX_CALLS):
            if state[1] >= 0x80 and state[3] >= 0x80:
                return rows, None
            after, cols = self.tick(state)
            if cols:
                if (state, bits) in seen:
                    rows[-1][0] = t - last
                    return rows, seen[(state, bits)]
                if not (fix and not t):
                    seen[(state, bits)] = len(rows)
                if rows:
                    rows[-1][0] = t - last
                for k, c in enumerate(merged(cols)):
                    bits, c = gated(bits, c)
                    if fix and not t and not k and rows:
                        rows.pop()
                        c = {"_gate": 0} | c
                    rows.append([0, c])
                rows[-1][0], last = 1, t
            state = after
        raise TranscribeError("a sidcall program runs without end")

    def row(self, hold: int, cols: dict, voice: int) -> d.Row:
        """A call's sidTAB columns on ``voice`` as a row lasting ``hold`` ticks."""
        keys = [("hold", hold)] if hold != 1 else []
        wgh, wgl = cols.get("wgh"), cols.get("wgl")
        if wgh is not None or wgl is not None:
            ctrl = "" if wgh is None else str(wgh | 1)
            keys.append(("ctrl", ctrl + ("" if wgl is None else f"^{wgl & 0xFE}")))
        if "_gate" in cols:
            keys.append(("gate", cols["_gate"]))
        if "_abs" in cols:
            keys.append(("note", note_name(cols["_abs"])))
        if "tr" in cols:
            tr = cols["tr"]
            absolute = self.absolute and tr & 0x80
            keys.append(("note", note_name(tr & 0x7F) if absolute else d.Signed(tr)))
        keys += [(k, cols[k]) for k in ("ad", "sr") if k in cols]
        if {"pw", "ps", "af"} & cols.keys():  # any pw binding resets the sweep slot's phi
            flags = ("pw" in cols) | ("ps" in cols) << 1
            keys.append(("sw.pw", call("pul", g=flags, p=cols.get("pw", 0), h=cols.get("ps", 0))))
        if ACID & cols.keys():
            for x, (cp, col) in zip("ab", (("cp", "acid"), ("bcp", "bacid"))):
                keys.append(
                    (f"k{voice + 1}{x}.cut", acid({"cp": cols.get(cp), "acid": cols.get(col)}))
                )
        keys += filtered(cols.get("re"), cols.get("fv"))
        if "bre" in cols or "bfv" in cols:
            keys.append(("run", self.pulse(filtered(cols.get("bre"), cols.get("bfv")))))
        if "af" in cols:
            keys += [("p.freq", pitch(cols["af"])), ("pm.pw", call("pmode", m=cols["af"]))]
        return d.Row(keys)

    def pulse(self, keys: list) -> str:
        """The name of the one-row pulse-slot program of layer B's filter ``keys``: the pulse
        phase runs after every voice's control program, as layer B runs after layer A."""
        program = d.Program([d.Row(keys)])
        if program not in self.pulses:
            self.pulses[program] = f"pw{len(self.pulses) + 1}"
        return self.pulses[program]

    def program(self, state: tuple, voice: int, bits: tuple, fix: bool) -> str:
        """The name of the control program both layers run from ``state`` and gate ``bits``
        on ``voice`` (see :meth:`joint`), the voice's own where it binds its ACID slot."""
        if (state, bits, fix) not in self.runs:
            self.runs[(state, bits, fix)] = self.joint(state, bits, fix)
        rows, loop = self.runs[(state, bits, fix)]
        cut = any(ACID & c.keys() for _, c in rows)
        key = (state, bits, fix, voice if cut else None)
        if key not in self.names:
            self.names[key] = f"c{len(self.names) + 1}"
            self.joints[self.names[key]] = (rows, loop, voice)
        return self.names[key]

    # ---- events and the document --------------------------------------------------------

    def song(self, calls: int = MAX_CALLS) -> tuple:
        """``(steps, loop step)``: each song step's length and per voice ``{offset: (event,
        program)}``, to the step whose arranger position and cascades recur. A note turns
        the gate on: where the call's rows leave it off, the note runs the voice's program
        from its state with the gate off."""
        states = [tuple(self(a, v) for a in (0x12EF, 0x12E0, 0x1382, 0x1373)) for v in range(3)]
        bits = [(0, 0)] * 3
        steps, seen = [], {}
        for step in sequence(DefmonPlayer(self.raw, subtune=self.subtune), self.at, calls):
            key = (step.key, tuple(states), tuple(bits))
            if key in seen:
                return steps, seen[key]
            seen[key] = len(steps)
            self.lead = self.lead if steps else step.call
            marks = [{}, {}, {}]
            for t in range(step.length):
                for v, events in enumerate(step.events):
                    states[v], bits[v], mark = self.call(v, states[v], bits[v], events.get(t))
                    if mark is not None:
                        marks[v][t] = mark
            steps.append((step.length, marks))
        raise TranscribeError("the song does not loop")  # pragma: no cover

    def call(self, voice: int, state: tuple, bits: tuple, ev: tuple | None) -> tuple:
        """``(state, bits, mark)``: a voice's cascades and gate bits after one call that runs
        pattern event ``ev`` (or None), and the event's ``(event, program)`` (or None)."""
        state = list(state)
        if ev is not None:
            state[0:2] = state[0:2] if ev[0] is None else (ev[0], 0)
            state[2:4] = state[2:4] if ev[1] is None else (ev[1], 0)
        after, cols, sets = *self.tick(tuple(state)), False
        out = bits
        for c in merged(cols) if cols else ():
            out, c = gated(out, c)
            sets |= "_gate" in c
        if ev is None:
            return after, out, None
        fix = ev[2] is not None and not sets and not out[0] ^ out[1]
        run = ev[0] is not None or ev[1] is not None or fix
        return after, out, (ev, self.program(tuple(state), voice, bits, fix) if run else None)

    def event(self, ev: tuple, program: str | None) -> d.Event:
        """The generic event of pattern event ``(a, b, n)`` starting ``program``."""
        commands = () if program is None else (("run", program),)
        n = ev[2]
        if n is None:
            return d.Event("-", commands=commands)
        if self.absolute and n >= 0x80:
            raise TranscribeError(f"note ${n:02X} past the note column")
        return d.Event(note_name(n), commands=commands)

    def phrase(self, length: int, marks: dict, first: bool) -> tuple:
        """A voice's events of a step of ``length`` ticks, one a tick, rests folded; the
        song's ``first`` binds the pitch step."""
        out = []
        for t in range(length):
            event = self.event(*marks[t]) if t in marks else d.Event("-")
            if first and not t:
                event = d.replace(event, commands=event.commands + BINDS)
            if out and event == d.Event("-") and out[-1].note == "-" and not out[-1].commands:
                out[-1] = d.replace(out[-1], steps=(out[-1].steps or 1) + 1)
            else:
                out.append(event)
        return tuple(out)

    def init(self) -> tuple:
        """Each voice's init: the instrument binding the pitch, the gate, the carried frequency
        and pulse cells, the carried note base as a legato note under the cleared note 0 (an
        absolute note relative rows replace); voice 1 also the volume and the ACID step's init."""
        out = []
        self.names["init"] = "c0"
        self.joints["c0"] = ([[1, {"_abs": 0}]], None, 0)
        for v in range(3):
            commands = [
                ("run", "c0"),
                (
                    "n.freq",
                    call(
                        "ninit",
                        k=v,
                        f=self.word(0x102D, 0x102F, v),
                        u=self.word(0x1019, 0x101A, v),
                    ),
                ),
                ("pz.pw", call("pzero")),
                (
                    "sw.pw",
                    call(
                        "pinit", k=v, f=self.word(0x1023, 0x1025, v), u=self.word(0x1019, 0x101A, v)
                    ),
                ),
                ("ctrl", 1),
            ]
            if not v:
                dbl = int(self(0x10D4) == 0x0A)
                commands += [
                    ("vol", 15),
                    ("st.cut", call("cinit", u=self(0x10B8) == 0xE9, f=self(0x10CE), k=dbl)),
                ]
            commands.append(("legato", True))
            out.append(d.Event(note_name(self(0x12ED, v)), "i1", commands))
        return tuple(out)

    def timeline(self, steps: list) -> list:
        """The lead-in row, then a row of each voice's phrase per song step."""
        lead = (d.Event("-"),)
        if self.lead:  # a multispeed play routine's sub-tick calls before the first full one
            lead += (d.Event("-", commands=BINDS, steps=self.lead),)
        phrases, rows = {}, [d.SongRow((d.Play("p0"),) * 3, (("phrase", {"p0": lead}),))]
        for k, (length, marks) in enumerate(steps):
            names, new = [], {}
            for v in range(3):
                events = self.phrase(length, marks[v], not (k or self.lead))
                if events not in phrases:
                    phrases[events] = new[events] = f"p{len(phrases) + 1}"
                names.append(d.Play(phrases[events]))
            rows.append(d.SongRow(tuple(names), (("phrase", {n: e for e, n in new.items()}),)))
        return [d.replace(r, defs=()) if not r.defs[0].records else r for r in rows]

    def head(self) -> d.Header:
        """The header: the SID's texts and clock, a step of one tick, the slots and state."""
        ntsc = (self.header.flags >> 2) & 3 == 2
        return d.Header(
            VERSION,
            meta=tuple((k, text(getattr(self.header, a))) for k, a in META),
            clock=(int(self.player.cycles_per_frame), NTSC_HZ if ntsc else PAL_HZ),
            tempo=1,
            slots=SLOTS,
            state=tuple((cell, tuple(names.items())) for cell, names in STATE.items()),
        )

    def document(self, calls: int = MAX_CALLS) -> str:
        """The document of the song to its loop."""
        steps, loop = self.song(calls)
        rows = self.timeline(steps)
        init = self.init()
        programs = {
            name: d.Program([self.row(h, c, v) for h, c in body], at)
            for name, (body, at, v) in self.joints.items()
        }
        defs = [("ins", {"i1": d.Instrument({"p.freq": call("pitch")})})]
        defs += [("ctrl", programs)] if programs else []
        defs += [("pw", {n: p for p, n in self.pulses.items()})] if self.pulses else []
        doc = d.Document(self.head(), self.tables(), (), defs, init, rows, loop + 1)
        return d.printed(d.replace(doc, mods=mods(d.printed(doc), self.absolute)))

    def tables(self) -> tuple:
        """The replay's note, portamento and rate tables as words, trailing zeros dropped."""
        out = []
        for name, (lo, hi, n) in TABLES.items():
            words = [self.mem[self.at[lo] + k] | self.mem[self.at[hi] + k] << 8 for k in range(n)]
            while len(words) > 1 and not words[-1]:
                words.pop()
            out.append((name, tuple(words)))
        return tuple(out)


META = (("title", "name"), ("author", "author"), ("released", "released"))


def text(value: str) -> str:
    """A meta text, quoted where the grammar's plain text cannot spell it."""
    return value if TEXT.fullmatch(value) else "'" + value.replace("'", "''") + "'"


def pitch(mode: int) -> d.Call:
    """The pitch step's call in pitch mode ``mode``."""
    if not mode:
        return call("pitch")
    return call("slide" if mode >= 0x80 else "por", m=mode)


def gated(bits: tuple, cols: dict) -> tuple:
    """``(bits, cols)``: bit 0 of ``wgh`` and ``wgl`` after a row's columns, and the columns
    with the gate it sets where it writes either."""
    if "wgh" not in cols and "wgl" not in cols:
        return bits, cols
    bits = (cols.get("wgh", bits[0]) & 1, cols.get("wgl", bits[1]) & 1)
    return bits, cols | {"_gate": bits[0] ^ bits[1]}


def merged(cols: list) -> list:
    """The columns both layers' rows of one call apply, layer B's replacing layer A's."""
    return [{k: v for c in cols for k, v in c.items()}]


def acid(cols: dict) -> d.Call:
    """The ACID call of a row's offset and ACID columns (None absent): ``g`` bit 0 sets the
    offset ``c``, bit 1 the accumulator ``j``, bit 2 an adding and bit 3 a subtracting step
    ``k``; a call setting nothing keeps the slot bound."""
    cp, col = cols.get("cp"), cols.get("acid")
    flags, value, step = int(cp is not None), 0, 0
    if col is not None and col < 0x8000:
        flags, value = flags | 2, col
    elif col is not None:
        flags, step = flags | (8 if col & 0x4000 else 4), col & 0x3FFF
    return call("acid", g=flags, c=cp or 0, j=value, k=step)


def filtered(re_: int | None, fv: int | None) -> list:
    """The keys of a row's ``$D417`` and ``$D418`` columns (None absent)."""
    return ([] if re_ is None else route(re_)) + ([] if fv is None else [("mode", fv & 0xF0)])


def route(re_: int) -> list:
    """The keys of a row's ``$D417`` column: ``0`` removes the voice, bit 3 adds it and the low
    nibble under the high nibble's resonance, any other byte sets both."""
    if not re_:
        return [("route", "'-'")]
    return [("res", re_ >> 4), ("route", f"+ {re_ & 0x0F}" if re_ & 0x08 else re_ & 0x0F)]


def mods(body: str, absolute: bool = True) -> tuple:
    """Each template ``body`` calls, keyed by its signature, in first-call order; a build of
    relative notes reads the note unwrapped."""
    first = {}
    for name in CODE:
        found = re.search(rf"(?<![a-z0-9_.]){name}\(", body)
        if found:
            first[name] = found.start()
    return tuple(
        (
            d.signature(name, code.params(ROUTINES[name])),
            d.Code.of(CODE[name] if absolute else CODE[name].replace(NOTE7, "lda nn\n")),
        )
        for name in sorted(first, key=first.get)
    )


def transcribe(raw: bytes, subtune: int = 0) -> str:
    """The document of one subtune of a defMON SID."""
    return Transcriber(raw, subtune).document()


def compare(raw: bytes, subtune: int = 0, frames: int = 20000) -> tuple:
    """``(cells, document)``: the ``(tick, register, ours, theirs)`` cells where the document's
    grid differs from the replay's over ``frames`` ticks."""
    text_ = transcribe(raw, subtune)
    ours = play(text_, ticks=frames)
    theirs = np.asarray(DefmonPlayer(raw, subtune=subtune).render_grid(len(ours)), dtype=np.uint8)
    return gridcmp.cell_diff(ours, theirs), text_


def main(argv=None) -> int:
    """Transcribe a SID, or verify SIDs against pydefmon's replay."""
    return cli.main(argv, __doc__.split("\n", 1)[0], transcribe, compare)


if __name__ == "__main__":
    sys.exit(main())
