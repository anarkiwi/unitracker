"""A defMON replay's song data transliterated to the generic document (format 2.4).

Each native object is one document object: a pattern a phrase, an arranger step an orderlist
entry per voice, a sidTAB entry a pattern event arms a control program on that event's layer
(``a`` or ``b``), a sidTAB row a program row (docs/direct/dmlift.md).
"""

import sys
from functools import cached_property

import numpy as np
from pydefmon import DefmonPlayer
from pysidtracker import SidImage, gridcmp
from unitracker_grammar import document as d
from unitracker_player.player import play

from unitracker.direct import cli
from unitracker.direct.defmon import META, NOTES, TABLES, call, clock_hz, filtered, mods
from unitracker.direct.defmon import note_name, pitch, text, transpose
from unitracker.direct.dmcode import STATE
from unitracker.direct.dmimage import LiftError, locate, row_fields

VOICES = (0x00, 0x31, 0x62)
ARRANGERS, PATTERNS = (0x1B00, 0x1C00, 0x1D00), (0x1A00, 0x1A80)
SIDTAB_LO, SIDTAB_HI, SIDTAB_DL = 0x1800, 0x1900, 0x1E00
ARMED, ARR_Y, COUNTER = 0x10D9, 0x10EB, 0x114A
BASE, NOTE, FREQ, PULSE, SLIDE = (
    0x12ED,
    0x137F,
    (0x102D, 0x102F),
    (0x1023, 0x1025),
    (0x1019, 0x101A),
)
WG, BAND_OP, AD, SR, D417, D418 = (0x103B, 0x103D), 0x103C, 0x1039, 0x1037, 0x10AA, 0x10AF
ALT, GATES, EOR = 0x80, (0x40, 0x20, 0x10), 0x49
LAYERS, RUNS, LEAD = ("a", "b"), ("run", "b.run"), 256
MAX_EVENTS, PROBE, MAX_TICKS = 512, 256, 1 << 18
SLOTS = (
    ("freq", (d.Slot("ni"), d.Slot("n", call("nr"), reset=("note",)), d.Slot("p"))),
    (
        "pw",
        (
            d.Slot("pz", call("pzero"), reset=("note",)),
            d.Slot("pm", reset=("row.freq",)),
            d.Slot("pi"),
            d.Slot("pa"),
            d.Slot("pb"),
            d.Slot("sw", call("pul", g=0, p=0, h=0)),
        ),
    ),
)


class Tune:  # pylint: disable=too-many-public-methods
    """One subtune of a defMON replay as its data: the replay's memory after init, read at the
    places of the ``$1000`` build's addresses (:func:`~unitracker.direct.dmimage.locate`)."""

    def __init__(self, raw: bytes, subtune: int = 0):
        self.raw, self.subtune = raw, subtune
        self.header = SidImage.from_bytes(raw).header
        if self.header.second_sid:
            raise LiftError("a multi-SID tune")
        self.player = DefmonPlayer(raw, subtune=subtune)
        self.mem = self.player._mem  # pylint: disable=protected-access
        self.at, self.variants = locate(self.mem)
        for v in range(3):
            if (self(0x101F, v), self(0x1020, v), self(0x1021, v)) != (v, 1 << v, 0xFF ^ 1 << v):
                raise LiftError(f"voice {v + 1}'s detune and routing are not standard")
        if self(0x10B8) != self(0x10BF) or self(0x10B8) not in (0x69, 0xE9):
            raise LiftError("ACID opcodes are not a matching SBC/ADC pair")
        if self.cutoff not in (0xEA, 0x0A):
            raise LiftError("the cutoff output is neither NOP nor ASL")

    def __call__(self, addr: int, voice: int = 0) -> int:
        return self.mem[self.at[addr] + VOICES[voice]]

    def word(self, pair: tuple, voice: int = 0) -> int:
        """The word of the cells ``pair`` (low, high) of ``voice``."""
        return self(pair[0], voice) | self(pair[1], voice) << 8

    def table(self, base: int, k: int) -> int:
        """Entry ``k`` of the 256-byte table at ``base``."""
        return self.mem[(self.at[base] + k) & 0xFFFF]

    @property
    def cutoff(self) -> int:
        """The cutoff output's opcode ($10D4): NOP where the build has none."""
        return 0xEA if "uncut" in self.variants else self(0x10D4)

    @property
    def floor(self) -> int:
        """The ACID floor: the cell $10CE, or a direct build's two immediates, which must agree."""
        if "direct" not in self.variants:
            return self(0x10CE)
        first, second = self.mem[self.at[0x10B8] + 12], self.mem[self.at[0x10B8] + 21]
        if first != second:
            raise LiftError("the ACID floors differ")
        return first

    # ---- patterns and the arranger ------------------------------------------------------

    def events(self, pattern: int):
        """``(offset, (flag, a, b, n))`` of each event of ``pattern``'s stream to its ALT: the
        offset in pattern steps (an event holds its duration nibble plus 2), a, b and n None
        where the flag's gate is clear ($117F)."""
        ptr, t = self.table(PATTERNS[0], pattern) | self.table(PATTERNS[1], pattern) << 8, 0
        for _ in range(MAX_EVENTS):
            flag, out, k = self.mem[ptr], [], 1
            for gate in GATES:
                out.append(self.mem[(ptr + k) & 0xFFFF] if flag & gate else None)
                k += "fixed" in self.variants or bool(flag & gate)
            yield t, (flag, *out)
            if flag & ALT:
                return
            t, ptr = t + (flag & 0x0F) + 2, (ptr + k) & 0xFFFF

    def step(self, patterns: tuple) -> tuple:
        """``(length, events)``: a song step's length in pattern steps and per voice its
        ``(steps, event)`` pairs. The first ALT any voice plays arms the arranger, which
        restarts every voice ($10D8) after the ALT's duration nibble, voice 3's over voice 1's."""
        streams = [list(self.events(p)) for p in patterns]
        alts = [(s[-1][0], -v) for v, s in enumerate(streams) if s[-1][1][0] & ALT]
        if not alts:
            raise LiftError(f"patterns {patterns} reach no ALT event")
        end, v = min(alts)
        length = end + (streams[-v][-1][1][0] & 0x0F) + 2
        out = []
        for stream in streams:
            played = [(t, ev) for t, ev in stream if t <= end]
            ends = [t for t, _ in played[1:]] + [length]
            out.append(tuple((e - t, ev) for (t, ev), e in zip(played, ends)))
        return length, out

    def arranger(self) -> tuple:
        """``(items, loop)``: the arranger's positions from init's on, a counted jump's body as
        ``(positions, times)``, and the item an unconditional jump continues at ($10EC). A counted
        jump over a counted jump never ends: the inner one takes the one counter ($14EC)."""
        y, items, where = self(ARR_Y), [], {}
        counted = "counted" in self.variants
        for _ in range(4 * 256):
            if self.table(ARRANGERS[0], y) & 0x80:
                target, count = self.table(ARRANGERS[1], y), self.table(ARRANGERS[2], y)
                first = where.get(target)
                if counted and count and first is None and target != (y + 1) & 0xFF:
                    raise LiftError(f"arranger jump at {y}: a counted jump forward to {target}")
                if counted and count and first is not None:
                    if not all(isinstance(i, int) for i in items[first:]):
                        return tuple(items), first
                    body = tuple(items[first:])
                    for pos in body[1:]:
                        where.pop(pos)
                    items[first:] = [(body, count + 1)]
                    y = (y + 1) & 0xFF
                elif target in where:
                    return tuple(items), where[target]
                else:
                    y = target
                if self.table(ARRANGERS[0], y) & 0x80:
                    raise LiftError(f"arranger jump at {y}: a jump onto a jump")
                continue
            if y in where:
                return tuple(items), where[y]
            where[y] = len(items)
            items.append(y)
            y = (y + 1) & 0xFF
        raise LiftError("the arranger does not loop")  # pragma: no cover

    @cached_property
    def song(self) -> tuple:
        """``(items, loop, steps)``: :meth:`arranger`'s items and loop, and each position's
        :meth:`step`."""
        items, loop = self.arranger()
        flat = [y for i in items for y in (i[0] if isinstance(i, tuple) else (i,))]
        steps = {
            y: self.step(tuple(self.table(a, y) for a in ARRANGERS)) for y in dict.fromkeys(flat)
        }
        return items, loop, steps

    def armed(self) -> list:
        """Per layer the sidTAB entries the played pattern events arm, in first-armed order."""
        out = [{}, {}]
        for _, events in self.song[2].values():
            for voice in events:
                for _, ev in voice:
                    for layer in (0, 1):
                        if ev[1 + layer] is not None:
                            out[layer].setdefault(ev[1 + layer])
        return [list(layer) for layer in out]

    def cleared(self) -> list:
        """The notes init leaves a voice at apart from its note base ($153F clears the note)."""
        return sorted({self(NOTE, v) for v in range(3) if self(BASE, v) != self(NOTE, v)})

    # ---- sidTAB ---------------------------------------------------------------------------

    def sidtab(self, column: int, row: int) -> int:
        """sidTAB ``column``'s byte of ``row``."""
        return self.mem[(self.at[column] + row) & 0xFFFF]

    def columns(self, row: int) -> dict:
        """The present columns of sidTAB ``row``'s body ($16B0)."""
        return row_fields(self.mem, self.sidtab(SIDTAB_LO, row) | self.sidtab(SIDTAB_HI, row) << 8)

    def walk(self, entry: int) -> tuple:
        """``(rows, loop)``: the sidTAB rows the cascade fetches from ``entry`` ($12DF), a jump
        row ($1900 zero) reading its target's body, to a STop (loop None) or a row fetched
        before."""
        rows, at, row = [], {}, entry
        while True:
            if not self.sidtab(SIDTAB_HI, row):
                row = self.sidtab(SIDTAB_LO, row)
                if not self.sidtab(SIDTAB_HI, row):
                    raise LiftError(f"sidTAB row {entry}'s walk jumps onto jump row {row}")
            if row in at:
                return tuple(rows), at[row]
            at[row] = len(rows)
            rows.append(row)
            if self.sidtab(SIDTAB_DL, row) & 0x80:
                return tuple(rows), None
            row = (row + 1) & 0xFF

    def controls(self, rows: tuple, loop: int | None) -> list:
        """Per row the set of ``(waveform, control)`` nibble pairs it leaves in the replay's
        shadow ($103B, $103D) on each pass of its program, a nibble no earlier row of the
        program wrote None; empty where the row writes neither."""
        out, seen, wg, k = [set() for _ in rows], set(), (None, None), 0
        while (k, wg) not in seen:
            seen.add((k, wg))
            cols = self.columns(rows[k])
            if "wgh" in cols or "wgl" in cols:
                wg = (cols.get("wgh", wg[0]), cols.get("wgl", wg[1]))
                out[k].add(wg)
            k += 1
            if k == len(rows):
                if loop is None:
                    break
                k = loop
        return out


class Lift:
    """The document of a :class:`Tune`; ``gaps`` the ``(entry, row, nibble pairs)`` whose control
    byte the grammar cannot spell, ``cuts`` the ``(pattern, steps)`` a step cuts to a phrase of
    its own (:meth:`phrases`)."""

    def __init__(self, tune: Tune):
        self.tune, self.gaps, self.cuts = tune, [], []
        self.eor = tune(BAND_OP) == EOR
        self.absolute = "absolute" in tune.variants
        self.period, self.first = cadence(tune)

    def byte(self, wg: tuple) -> int:
        """The control register byte of a nibble pair, as the SID band ($103A) combines them."""
        return wg[0] ^ wg[1] if self.eor else wg[0] | wg[1]

    def program(self, entry: int) -> d.Program:
        """sidTAB ``entry``'s walk as a control program, a row each sidTAB row."""
        rows, loop = self.tune.walk(entry)
        out = []
        for row, wgs in zip(rows, self.tune.controls(rows, loop)):
            ctrl = None
            if len(wgs) == 1 and None not in next(iter(wgs)):
                ctrl = self.byte(next(iter(wgs)))
            elif wgs and (entry, row) not in {g[:2] for g in self.gaps}:
                self.gaps.append((entry, row, tuple(sorted(wgs, key=str))))
            out.append(self.row(row, ctrl))
        return d.Program(out, loop)

    def row(self, row: int, ctrl: int | None) -> d.Row:
        """sidTAB ``row``'s columns as a program row, ``ctrl`` its control byte: each cell a
        column sets is a slot of its own, so the last row to set it wins, as in the replay."""
        cols, dl = self.tune.columns(row), self.tune.sidtab(SIDTAB_DL, row)
        keys = [] if dl & 0x80 or not dl else [("hold", dl + 1)]
        keys += [] if ctrl is None else [("ctrl", ctrl)]
        keys += [transpose(cols["tr"], self.absolute)] if "tr" in cols else []
        keys += [(k, cols[k]) for k in ("ad", "sr") if k in cols]
        if "pw" in cols:
            keys.append(("pa.pw", d.Once(call("pwset", p=cols["pw"]))))
        if "ps" in cols:
            keys.append(("pb.pw", d.Once(call("psset", h=cols["ps"]))))
        if "af" in cols:
            keys += [("p.freq", pitch(cols["af"])), ("pm.pw", call("pmode", m=cols["af"]))]
        if "cp" in cols:
            keys.append(("kc.cut", d.Once(call("cpset", c=cols["cp"]))))
        keys += [(f"{k}.cut", d.Once(c)) for k, c in acid(cols.get("acid"))]
        return d.Row(keys + filtered(cols.get("re"), cols.get("fv")))

    def event(self, steps: int, ev: tuple) -> d.Event:
        """Pattern event ``(flag, a, b, n)`` lasting ``steps``: its note and its layers' runs."""
        runs = [(p, f"c{x}") for p, x in zip(RUNS, ev[1:3]) if x is not None]
        note = ev[3]
        if note is not None and self.absolute and note >= 0x80:
            raise LiftError(f"note ${note:02X} past the note column")
        return d.Event(
            "-" if note is None else note_name(note),
            commands=runs,
            steps=steps if steps > 1 else None,
        )

    def phrases(self) -> tuple:
        """``(phrases, entries)``: each played pattern's phrase by name, and per position the
        voices' phrase names; a pattern a step cuts short at another length is a phrase of its
        own."""
        phrases, names, entries = {}, {}, {}
        for y, (_, events) in self.tune.song[2].items():
            row = []
            for v, voice in enumerate(events):
                body = tuple(self.event(*e) for e in voice)
                pattern = self.tune.table(ARRANGERS[v], y)
                if body not in names:
                    name = f"p{pattern}"
                    if name in phrases:
                        name = f"p{LEAD + 1 + len(phrases)}"
                        self.cuts.append((pattern, sum(e.steps or 1 for e in body)))
                    names[body], phrases[name] = name, body
                row.append(names[body])
            entries[y] = row
        return phrases, entries

    def lead(self) -> tuple:
        """The lead-in phrase: init's tick, the play routine's calls before its first full one,
        which arms the arranger ($10D9), then the steps to the first events; the pitch step is
        bound from the first call on."""
        armed = self.tune(ARMED)
        if not armed & 0x80:
            raise LiftError("init leaves the arranger unarmed")
        binds = (("p.freq", call("pitch")),)
        steps = (armed & 0x0F) + 1 if armed & 0x0F else None
        if not self.first:
            return d.Event("-"), d.Event("-", "i1", binds + (("tempo", self.period),), steps=steps)
        return (
            d.Event("-"),
            d.Event("-", "i1", binds, steps=self.first if self.first > 1 else None),
            d.Event("-", commands=(("tempo", self.period),), steps=steps),
        )

    def orderlists(self, entries: dict) -> tuple:
        """Per voice its orderlist: the lead-in, then each arranger item's phrase."""
        items, loop, _ = self.tune.song
        out = []
        for v in range(3):
            plays = [d.Play(f"p{LEAD}")]
            for item in items:
                if isinstance(item, tuple):
                    plays.append(d.Group(tuple(d.Play(entries[y][v]) for y in item[0]), item[1]))
                else:
                    plays.append(d.Play(entries[item][v]))
            out.append(d.Voice(plays, loop + 1))
        return tuple(out)

    def init(self) -> tuple:
        """Each voice's init event: its note base (the event note TR adds to), its note where
        init leaves it apart (:meth:`Tune.cleared`), the carried frequency, slide accumulator,
        pulse and control cells; voice 1 also $D417 and $D418."""
        t, out = self.tune, []
        for v in range(3):
            commands = [
                ("ni.freq", d.Once(call("ninit", k=v, f=t.word(FREQ, v), u=t.word(SLIDE, v)))),
                ("pi.pw", d.Once(call("pinit", k=v, f=t.word(PULSE, v), u=t.word(SLIDE, v)))),
            ]
            commands += [(k, t(a, v)) for k, a in (("ad", AD), ("sr", SR)) if t(a, v)]
            ctrl = self.byte((t(WG[0], v), t(WG[1], v)))
            commands += [("ctrl", ctrl)] if ctrl else []
            if not v:
                regs = (("res", t(D417) >> 4), ("route", t(D417) & 0x0F), ("mode", t(D418) & 0xF0))
                commands += [("vol", 15)] + [(k, x) for k, x in regs if x]
            ins = f"i{2 + t.cleared().index(t(NOTE, v))}" if t(NOTE, v) != t(BASE, v) else None
            out.append(d.Event(note_name(t(BASE, v)), ins, commands))
        return tuple(out)

    def header(self) -> d.Header:
        """The header: the SID's texts and clock, the slots, the two control layers, state."""
        t = self.tune
        cut = call(
            "cinitd" if "direct" in t.variants else "cinit",
            u=t(0x10B8) == 0xE9,
            f=t.floor,
            k=t.cutoff == 0x0A,
        )
        slots = SLOTS + (
            ("cut", tuple(map(d.Slot, ("kc", "kr", "kz", "ko"))) + (d.Slot("st", cut),)),
        )
        return d.Header(
            meta=tuple((k, text(getattr(t.header, a))) for k, a in META),
            clock=(int(t.player.cycles_per_frame), clock_hz(t.header)),
            tempo=1,
            slots=slots,
            layers=(("ctrl", LAYERS),),
            state=tuple((cell, tuple(names.items())) for cell, names in STATE.items()),
        )

    def tables(self) -> tuple:
        """The replay's portamento table, slide rates and note table, trailing zeros dropped."""
        out = []
        for name, (lo, hi, n) in (TABLES | {"W": (*NOTES, 256)}).items():
            words = [self.tune.table(lo, k) | self.tune.table(hi, k) << 8 for k in range(n)]
            while len(words) > 1 and not words[-1]:
                words.pop()
            out.append((name, tuple(words)))
        return tuple(out)

    def document(self) -> str:
        """The document of the tune's song."""
        phrases, entries = self.phrases()
        armed = self.tune.armed()
        programs = {f"c{e}": self.program(e) for e in dict.fromkeys(armed[0] + armed[1])}
        cleared = self.tune.cleared()
        programs |= {f"c{2 * LEAD + n}": d.Program([{"note": note_name(n)}]) for n in cleared}
        instruments = {"i1": d.Instrument({"p.freq": call("pitch")})}
        for k, n in enumerate(cleared):
            instruments[f"i{2 + k}"] = d.Instrument({"ctrl": f"c{2 * LEAD + n}"})
        defs = [("ins", instruments)]
        defs += [("ctrl", programs)] if programs else []
        defs.append(("phrase", {f"p{LEAD}": self.lead()} | phrases))
        doc = d.Document(
            self.header(), self.tables(), (), defs, self.init(), self.orderlists(entries)
        )
        return d.printed(d.replace(doc, mods=mods(d.printed(doc), self.absolute, own=True)))

    def ticks(self) -> int:
        """The song's ticks from init through its arranger's last item."""
        items, _, steps = self.tune.song
        flat = [y for i in items for y in (i[0] * i[1] if isinstance(i, tuple) else (i,))]
        lead = 1 + self.first + ((self.tune(ARMED) & 0x0F) + 1) * self.period
        return lead + self.period * sum(steps[y][0] for y in flat)


def acid(value: int | None) -> tuple:
    """``(slot, call)`` of each cell an ACID column ``value`` sets ($176D): below $8000 the
    accumulator and a zero step, else the step and its direction (bit 14 subtracts)."""
    if value is None:
        return ()
    if value < 0x8000:
        return ("kr", call("accset", j=value)), ("kz", call("stepset", k=0))
    return ("kz", call("stepset", k=value & 0x3FFF)), ("ko", call("dirset", u=value >> 14 & 1))


def cadence(tune: Tune) -> tuple:
    """``(period, first)``: the play routine's calls per pattern step and its first call that
    runs the pattern step (it steps voice 1's counter or the arranger flag)."""
    ref = DefmonPlayer(tune.raw, subtune=tune.subtune)
    mem = ref._mem  # pylint: disable=protected-access
    marks, full = (tune.at[ARMED], tune.at[COUNTER]), []
    for c in range(PROBE):
        before = [mem[a] for a in marks]
        ref.play_frame()
        if [mem[a] for a in marks] != before:
            full.append(c)
    gaps = set(np.diff(full).tolist())
    if len(gaps) != 1:
        raise LiftError(f"the play routine runs the pattern step at uneven calls {full[:8]}")
    return gaps.pop(), full[0]


def transcribe(raw: bytes, subtune: int = 0) -> str:
    """The document of one subtune of a defMON SID."""
    return Lift(Tune(raw, subtune)).document()


def native_counts(raw: bytes, subtune: int = 0) -> dict:
    """The tune's object counts (:mod:`unitracker.direct.invariants`): per control layer the
    sidTAB entries its played events arm, the patterns played and per voice the arranger's
    positions, with init's arranger arm (the lead-in) and notes (:meth:`Tune.cleared`)."""
    tune = Tune(raw, subtune)
    armed = tune.armed()
    armed[0] += [2 * LEAD + n for n in tune.cleared()]
    items = tune.song[0]
    entries = 1 + sum(len(i[0]) if isinstance(i, tuple) else 1 for i in items)
    patterns = {tune.table(a, y) for y in tune.song[2] for a in ARRANGERS}
    return {
        "ctrl_layers": tuple(map(len, armed)),
        "phrases": 1 + len(patterns),
        "song": (entries,) * 3,
    }


def verify(raw: bytes, subtune: int = 0, frames: int | None = None) -> tuple:
    """``(cells, document)``: the ``(tick, register, ours, theirs)`` cells where the document's
    grid differs from the replay's over ``frames`` ticks, the song's own (to MAX_TICKS) where
    None."""
    lift = Lift(Tune(raw, subtune))
    text_ = lift.document()
    ours = play(text_, ticks=frames or min(lift.ticks(), MAX_TICKS))
    theirs = np.asarray(DefmonPlayer(raw, subtune=subtune).render_grid(len(ours)), dtype=np.uint8)
    return gridcmp.cell_diff(ours, theirs), text_


def main(argv=None) -> int:
    """Transliterate a SID, or verify SIDs against pydefmon's replay."""
    return cli.main(argv, __doc__.split("\n", 1)[0], transcribe, verify)


if __name__ == "__main__":
    sys.exit(main())
