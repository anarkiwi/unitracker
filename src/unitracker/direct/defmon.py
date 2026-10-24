"""A defMON replay transcribed to the generic document from pydefmon's emulated replay.

A generic tick is a replay call's pattern, sidcall and pitch/pulse work (the next call's SID band
writes it) and that next call's ACID step; an event's two sidcall layers run as one control
program, the pitch, pulse and ACID steps as code modulators (:mod:`unitracker.direct.dmcode`).
"""

import argparse
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from pydefmon import DefmonPlayer
from pysidtracker import SidImage, gridcmp
from tqdm import tqdm
from unitracker_grammar import code
from unitracker_grammar import document as d
from unitracker_player.player import play

from unitracker.direct.dmcode import CODE, STATE
from unitracker.lift.base import LiftError
from unitracker.lift.defmon import locate

VERSION = "1.23"
PAL_HZ, NTSC_HZ = 985248, 1022727
NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
VOICES = (0x00, 0x31, 0x62)
COUNTER, POINTER = 0x114A, 0x1186  # voice 1's; each voice's 0x88 on
ARMED, ARR_Y, ARR_JR, ARR_JC = 0x10D9, 0x10EB, 0x14EC, 0x14ED
SIDTAB_LO, SIDTAB_HI, SIDTAB_DL = 0x1800, 0x1900, 0x1E00
GATE_A, GATE_B, GATE_N = 0x40, 0x20, 0x10
TABLES = {"G": (0x159C, 0x1638, 256), "P": (0x1583, 0x161F, 257), "R": (0x1578, 0x1614, 128)}
COLUMNS = (  # the sidTAB walk's ($16B0) columns: (bitmap byte, bit, size, name)
    (0, 0x40, 1, "wgh"),
    (0, 0x80, 1, "wgl"),
    (0, 0x20, 1, "ad"),
    (0, 0x10, 1, "sr"),
    (0, 0x08, 1, "tr"),
    (0, 0x04, 1, "af"),
    (0, 0x02, 1, "pw"),
    (1, 0x80, 1, "ps"),
    (1, 0x40, 1, "re"),
    (1, 0x20, 1, "fv"),
    (1, 0x10, 1, "cp"),
    (1, 0x08, 2, "acid"),
)
SLOTS = (
    ("freq", (d.Slot("n", reset=("note",)), d.Slot("p"))),
    ("pw", (d.Slot("pw", reset=("row.pw",)),)),
    ("cut", tuple(d.Slot(f"k{v}", reset=("row.cut",)) for v in (1, 2, 3)) + (d.Slot("st"),)),
)
MAX_CALLS = 1 << 17
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


@dataclass
class Step:
    """A song step: its first call, its arranger key, its length and per voice
    ``{offset: (a, b, n)}``."""

    call: int
    key: tuple
    events: list = field(default_factory=lambda: [{}, {}, {}])
    length: int = 0


def sequence(player, calls: int = MAX_CALLS):
    """The song steps of the replay's calls: a call runs the arranger where init or an ALT
    event armed it, else each voice whose counter has bit 7 runs the event at its pattern
    pointer."""
    mem, step = player._mem, None  # pylint: disable=protected-access
    for c in range(calls):
        if mem[ARMED] & 0x80:
            if step is not None:
                step.length = c - step.call
                yield step
            step = Step(c, (mem[ARR_Y], mem[ARR_JR], mem[ARR_JC], mem[ARMED] & 0x0F))
        else:
            for v in range(3):
                if mem[COUNTER + 0x88 * v] & 0x80:
                    ptr = mem[POINTER + 0x88 * v] | mem[POINTER + 0x88 * v + 1] << 8
                    step.events[v][c - step.call] = event_at(mem, ptr)
        player.play_frame()
    raise TranscribeError(f"the song does not loop within {calls} calls")


def event_at(mem, ptr: int) -> tuple:
    """``(layer A row, layer B row, note)`` of the pattern event at ``ptr``, each None absent."""
    flag, out, k = mem[ptr], [], 1
    for gate in (GATE_A, GATE_B, GATE_N):
        out.append(mem[(ptr + k) & 0xFFFF] if flag & gate else None)
        k += bool(flag & gate)
    return tuple(out)


class Transcriber:  # pylint: disable=too-many-instance-attributes
    """One subtune of a defMON replay of the ``$1000`` build as a generic document."""

    def __init__(self, raw: bytes, subtune: int = 0):
        self.raw, self.subtune = raw, subtune
        self.header = SidImage.from_bytes(raw).header
        self.player = DefmonPlayer(raw, subtune=subtune)
        self.mem = self.player._mem  # pylint: disable=protected-access
        self.check()
        self.joints, self.names, self.runs, self.gated = {}, {}, {}, False

    def __call__(self, addr: int, voice: int = 0) -> int:
        return self.mem[addr + VOICES[voice]]

    def word(self, lo: int, hi: int, voice: int = 0) -> int:
        """The word of the bytes at ``lo`` and ``hi``."""
        return self(lo, voice) | self(hi, voice) << 8

    def check(self):
        """Refuses what the transcription does not spell: another build, a multispeed or
        multi-SID tune, an init that leaves state the generic init cannot write."""
        if self.header.second_sid:
            raise TranscribeError("a multi-SID tune")
        try:
            at, variants = locate(self.mem)
        except LiftError as exc:
            raise TranscribeError(str(exc)) from exc
        if any(k != a for k, a in at.items()) or variants != {"counted", "absolute"}:
            raise TranscribeError("not the $1000 build of the counted arranger, absolute notes")
        if self.header.play_address != 0x1003:
            raise TranscribeError(f"a play routine at ${self.header.play_address:04X}")
        if self(0x10B8) != self(0x10BF) or self(0x10B8) not in (0x69, 0xE9):
            raise TranscribeError("ACID opcodes are not a matching SBC/ADC pair")
        if self(0x10D4) not in (0xEA, 0x0A):
            raise TranscribeError("the cutoff output is neither NOP nor ASL")
        for v in range(3):
            if (self(0x101F, v), self(0x1020, v), self(0x1021, v)) != (v, 1 << v, 0xFF ^ 1 << v):
                raise TranscribeError(f"voice {v + 1}'s detune and routing are not standard")
            if self.word(0x1019, 0x101A, v):
                raise TranscribeError(f"init leaves voice {v + 1}'s slide accumulator set")

    # ---- sidTAB programs ----------------------------------------------------------------

    def resolve(self, row: int) -> int:
        """The row a fetch of ``row`` reads after its jump."""
        eff = row if self.mem[SIDTAB_HI + row] else self.mem[SIDTAB_LO + row]
        if not self.mem[SIDTAB_HI + eff]:
            raise TranscribeError(f"sidTAB row {row} resolves to row {eff}, which has no body")
        return eff

    def columns(self, eff: int) -> dict:
        """The columns of sidTAB row ``eff``'s body, in the walk's order."""
        pos = self.mem[SIDTAB_LO + eff] | self.mem[SIDTAB_HI + eff] << 8
        out, half, bitmap = {}, 0, self.mem[pos]
        for byte, bit, size, name in COLUMNS:
            if byte != half:
                pos, half = pos + 1, byte
                bitmap = self.mem[pos]
            if bitmap & bit:
                out[name] = int.from_bytes(bytes(self.mem[pos + 1 : pos + 1 + size]), "little")
                pos += size
        return out

    def tick(self, state: tuple) -> tuple:
        """``(state, columns)``: one call of a voice's sidcall cascade ($12DF) from ``state``,
        ``(row A, count A, row B, count B)``, and the columns its fetches apply, layer B's
        last; a count of 0 fetches, $80 and over is frozen."""
        state, cols = list(state), []
        for k in (0, 2):
            row, cnt = state[k : k + 2]
            if not cnt:
                eff = self.resolve(row)
                state[k : k + 2] = (eff + 1) & 0xFF, self.mem[SIDTAB_DL + eff]
                cols.append(self.columns(eff))
            elif cnt < 0x80:
                state[k + 1] = cnt - 1
        if len(cols) == 2 and {"re", "acid"} & cols[0].keys() & cols[1].keys():
            raise TranscribeError("both layers' rows of one call write $D417 or the ACID unit")
        return tuple(state), {k: v for c in cols for k, v in c.items()} if cols else None

    def joint(self, state: tuple) -> tuple:
        """``(rows, loop)``: ``(ticks, columns)`` of each call that fetches from ``state`` on,
        to both layers frozen (loop None) or a state seen before."""
        rows, seen, last = [], {}, 0
        for t in range(MAX_CALLS):
            if state[1] >= 0x80 and state[3] >= 0x80:
                return rows, None
            after, cols = self.tick(state)
            if cols is not None:
                if state in seen:
                    rows[-1][0] = t - last
                    return rows, seen[state]
                seen[state] = len(rows)
                if rows:
                    rows[-1][0] = t - last
                rows.append([1, cols])
                last = t
            state = after
        raise TranscribeError("a sidcall program runs without end")

    def row(self, hold: int, cols: dict, voice: int) -> d.Row:
        """A call's sidTAB columns on ``voice`` as a row lasting ``hold`` ticks."""
        keys = [("hold", hold)] if hold != 1 else []
        wgh, wgl = cols.get("wgh"), cols.get("wgl")
        if wgh is not None or wgl is not None:
            ctrl = "" if wgh is None else str(wgh if self.gated else wgh | 1)
            keys.append(("ctrl", ctrl + ("" if wgl is None else f"^{wgl & 0xFE}")))
            if wgl is not None and not self.gated:
                keys.append(("gate", wgl & 1))
        if "tr" in cols:
            tr = cols["tr"]
            keys.append(("note", note_name(tr & 0x7F) if tr & 0x80 else d.Signed(tr)))
        keys += [(k, cols[k]) for k in ("ad", "sr") if k in cols]
        if "pw" in cols or "ps" in cols:
            flags = ("pw" in cols) | ("ps" in cols) << 1
            keys.append(("pw", call("pul", g=flags, p=cols.get("pw", 0), h=cols.get("ps", 0))))
        if "cp" in cols or "acid" in cols:
            keys.append((f"k{voice + 1}.cut", acid(cols)))
        if "re" in cols:
            keys += route(cols["re"])
        if "fv" in cols:
            keys.append(("mode", cols["fv"] & 0xF0))
        if "af" in cols:
            keys.append(("p.freq", pitch(cols["af"])))
        return d.Row(keys)

    def program(self, state: tuple, voice: int) -> str:
        """The name of the control program both layers run from ``state`` on ``voice``, the
        voice's own where it binds its ACID slot."""
        if state not in self.runs:
            self.runs[state] = self.joint(state)
        rows, loop = self.runs[state]
        key = (state, voice if any({"cp", "acid"} & c.keys() for _, c in rows) else None)
        if key not in self.names:
            self.names[key] = f"c{len(self.names) + 1}"
            self.joints[self.names[key]] = (rows, loop, voice)
        return self.names[key]

    def gate(self):
        """Settles the gate: with no odd ``wgl`` it is always on and ``ctrl`` is ``wgh``; with
        no odd ``wgh`` it is ``wgl``'s bit 0 and ``ctrl`` is ``wgh | 1``."""
        cols = [c for rows, _, _ in self.joints.values() for _, c in rows]
        odd = {k for c in cols for k in ("wgh", "wgl") if c.get(k, 0) & 1}
        if len(odd) == 2:
            raise TranscribeError("sidTAB rows set bit 0 of both wgh and wgl")
        self.gated = "wgl" not in odd
        if any(c.get("af") for c in cols) and any(c.get("ps") for c in cols):
            raise TranscribeError("a pulse sweep beside a pitch mode")

    # ---- events and the document --------------------------------------------------------

    def song(self, calls: int = MAX_CALLS) -> tuple:
        """``(steps, loop step)``: each song step's length and per voice ``{offset: (event,
        program)}``, to the step whose arranger position and cascades recur."""
        states = [tuple(self(a, v) for a in (0x12EF, 0x12E0, 0x1382, 0x1373)) for v in range(3)]
        steps, seen = [], {}
        for step in sequence(DefmonPlayer(self.raw, subtune=self.subtune), calls):
            key = (step.key, tuple(states))
            if key in seen:
                return steps, seen[key]
            seen[key] = len(steps)
            marks = [{}, {}, {}]
            for t in range(step.length):
                for v, events in enumerate(step.events):
                    state = list(states[v])
                    if t in events:
                        a, b, _ = events[t]
                        state[0:2] = state[0:2] if a is None else (a, 0)
                        state[2:4] = state[2:4] if b is None else (b, 0)
                        armed = a is not None or b is not None
                        marks[v][t] = (events[t], self.program(tuple(state), v) if armed else None)
                    states[v] = self.tick(tuple(state))[0]
            steps.append((step.length, marks))
        raise TranscribeError("the song does not loop")  # pragma: no cover

    def event(self, ev: tuple, program: str | None) -> d.Event:
        """The generic event of pattern event ``(a, b, n)`` starting ``program``."""
        commands = () if program is None else (("run", program),)
        n = ev[2]
        if n is None:
            return d.Event("-", commands=commands)
        if n >= 0x80:
            raise TranscribeError(f"note ${n:02X} past the note column")
        if not self.gated and (program is None or "wgl" not in self.joints[program][0][0][1]):
            raise TranscribeError("a note whose first row does not set the gate")
        return d.Event(note_name(n), commands=commands)

    def phrase(self, length: int, marks: dict, first: bool) -> tuple:
        """A voice's events of a step of ``length`` ticks, one a tick, rests folded; the
        song's ``first`` binds the pitch step."""
        out = []
        for t in range(length):
            event = self.event(*marks[t]) if t in marks else d.Event("-")
            if first and not t:
                event = d.replace(event, commands=event.commands + (("p.freq", call("pitch")),))
            if out and event == d.Event("-") and out[-1].note == "-" and not out[-1].commands:
                out[-1] = d.replace(out[-1], steps=(out[-1].steps or 1) + 1)
            else:
                out.append(event)
        return tuple(out)

    def init(self) -> tuple:
        """Each voice's init: the instrument binding the pitch, the gate, the carried frequency
        and pulse cells; voice 1 also the volume and the ACID step's init."""
        out = []
        for v in range(3):
            commands = [
                ("n.freq", call("nr", k=v, f=self.word(0x102D, 0x102F, v))),
                ("pw", call("pinit", k=v, f=self.word(0x1023, 0x1025, v))),
            ]
            if not self.gated:
                commands.append(("ctrl", 1))
            if not v:
                dbl = int(self(0x10D4) == 0x0A)
                commands += [
                    ("vol", 15),
                    ("st.cut", call("cinit", u=self(0x10B8) == 0xE9, f=self(0x10CE), k=dbl)),
                ]
            out.append(d.Event("on" if self.gated else "-", "i1", commands))
        return tuple(out)

    def timeline(self, steps: list) -> list:
        """The lead-in row, then a row of each voice's phrase per song step."""
        phrases, rows = {}, [d.SongRow((d.Play("p0"),) * 3, (("phrase", {"p0": (d.Event("-"),)}),))]
        for k, (length, marks) in enumerate(steps):
            names, new = [], {}
            for v in range(3):
                events = self.phrase(length, marks[v], not k)
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
        self.gate()
        rows = self.timeline(steps)
        programs = {
            name: d.Program([self.row(h, c, v) for h, c in body], at)
            for name, (body, at, v) in self.joints.items()
        }
        defs = [("ins", {"i1": d.Instrument({"p.freq": call("pitch")})})]
        defs += [("ctrl", programs)] if programs else []
        doc = d.Document(self.head(), self.tables(), (), defs, self.init(), rows, loop + 1)
        return d.printed(d.replace(doc, mods=mods(d.printed(doc))))

    def tables(self) -> tuple:
        """The replay's note, portamento and rate tables as words, trailing zeros dropped."""
        out = []
        for name, (lo, hi, n) in TABLES.items():
            words = [self.mem[lo + k] | self.mem[hi + k] << 8 for k in range(n)]
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


def acid(cols: dict) -> d.Call:
    """The ACID call of a row's offset and ACID columns."""
    flags, value = int("cp" in cols), cols.get("acid", 0)
    if "acid" in cols:
        flags |= 2 if value < 0x8000 else 8 if value & 0x4000 else 4
        value &= 0x7FFF if value < 0x8000 else 0x3FFF
    return call("acid", g=flags, c=cols.get("cp", 0), j=value)


def route(re_: int) -> list:
    """The keys of a row's ``$D417`` column: ``0`` removes the voice, bit 3 adds it and the low
    nibble under the high nibble's resonance, any other byte sets both."""
    if not re_:
        return [("route", "'-'")]
    return [("res", re_ >> 4), ("route", f"+ {re_ & 0x0F}" if re_ & 0x08 else re_ & 0x0F)]


def mods(body: str) -> tuple:
    """Each template ``body`` calls, keyed by its signature, in first-call order."""
    first = {}
    for name in CODE:
        found = re.search(rf"(?<![a-z0-9_.]){name}\(", body)
        if found:
            first[name] = found.start()
    return tuple(
        (d.signature(name, code.params(ROUTINES[name])), d.Code(ROUTINES[name]))
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


def _check(job: tuple) -> tuple:
    path, subtune, frames, runs = job
    try:
        cells = compare(path.read_bytes(), subtune, frames)[0]
        return path, not cells.size, summary(cells, runs)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return path, False, f"{type(exc).__name__}: {exc}"


def main(argv=None) -> int:
    """Transcribe a SID, or verify SIDs against pydefmon's replay."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("sids", nargs="+", type=Path)
    parser.add_argument("--subtune", type=int, default=0)
    parser.add_argument("--frames", type=int, default=20000)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--diff", type=int, default=0, help="differing runs per register")
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args(argv)
    if not args.verify:
        sys.stdout.write(transcribe(args.sids[0].read_bytes(), args.subtune))
        return 0
    jobs = [(path, args.subtune, args.frames, args.diff) for path in args.sids]
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
