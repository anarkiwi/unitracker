"""A defMON tune as a format 2.5 document, from pydefmon's song model and the build facts of its
bound playspec; ``verify`` plays the document against the spec player with the standard note
table (docs/lift/defmon.md)."""

import re
import sys
from collections import Counter
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np
from pydefmon import DefmonSong
from pydefmon.spec import build_facts, player_bases, spec
from pysidtracker import NTSC_CLOCK_HZ, PAL_CLOCK_HZ, SidImage, find_format, gridcmp
from pysidtracker.cadence import cadence_info
from pysidtracker.errors import SidParseError
from pysidtracker.formats import SidFormatError
from unitracker_grammar import code as asm
from unitracker_grammar.document import (
    Call,
    Code,
    Document,
    Event,
    Expr,
    Group,
    Header,
    Instrument,
    Once,
    Play,
    Program,
    Row,
    Section,
    Signed,
    Slot,
    Voice,
    printed,
    signature,
)
from unitracker_grammar.expr import canonical
from unitracker_player.player import play

from unitracker.lift import cli, invariants
from unitracker.lift.dmcode import ROUTINES, STATE
from unitracker.lift.dmmem import LiftError, Refusal, Replay
from unitracker.lift.shared import meta_text, note_name, rests

CYCLES = {PAL_CLOCK_HZ: 19656, NTSC_CLOCK_HZ: 17095}
VOICE = 0x31
CHECK = 4000
WINDOW = 64
TRIGGERS = ("pal_video", "ntsc_video", "cia_timer")
LIMIT = 1 << 15
LOW = -24
ORDER = tuple("events read pulse ctrl filter freq pw cut ctl ad sr res route mode vol".split())
summary = cli.summary
CELL_OF = {
    "freq": "up dn up7 dn7 clr seed",
    "pw": "cup cdn cup7 cdn7 pclr pseed psw pwinit pwset psset pwps",
}
CELL_OF = {t: c for c, names in CELL_OF.items() for t in names.split()}
CELL_OF |= {t: "cut" for t in ("cp", "acc", "add", "sub", "step", "stepx")}


class Divergence(LiftError):
    """The document's grid differs from the reference player's."""


@dataclass(frozen=True)
class Core:  # pylint: disable=too-many-instance-attributes
    """What a lift needs of a tune: the replay's song tables, the build flags, the state init
    leaves, the slide rate table, the clock and the title lines."""

    replay: Replay
    flags: dict
    init: dict
    rates: tuple
    clock: int
    head: tuple
    cp_extra: bool = True
    calls: tuple = (1,)


def _state(mem, binding) -> dict:
    """The values the replay's init leaves in the cells the first play call continues from."""
    voices = (
        "pw_lo pw_hi freq_lo freq_hi sr ad wgh wgl slide_lo slide_hi mode depth detune vbit vmask"
        " note note_base"
    ).split()
    out = {n: tuple(mem[binding[n] + v * VOICE] for v in range(3)) for n in voices}
    single = "res fv cut_lo cut_hi cut_step_lo cut_step_hi op_lo op_hi extra floor shift".split()
    single += "trigger cursor owner count".split()
    return out | {n: mem[binding[n]] for n in single}


def core_of_sid(raw: bytes, subtune: int = 0) -> Core:
    """The core of a SID's subtune; a Refusal where the tune is not a single-SID defMON song."""
    try:
        fmt = find_format(raw)
    except SidParseError as exc:
        raise Refusal("format", str(exc)) from exc
    if fmt.name != "defmon":
        raise Refusal("format", fmt.name)
    song = DefmonSong.from_sid_bytes(raw)
    song.subtune = subtune
    try:
        fmt.spec(song)
    except SidFormatError as exc:
        raise Refusal("multi-SID", str(exc)) from exc
    if (trigger := cadence_info(raw, subtune + 1)["trigger"]) not in TRIGGERS:
        raise Refusal("digi", f"the play source is {trigger}")
    player = fmt.spec_player(song, notes=True)
    mem = player.memory
    base = player_bases(mem)[0]
    facts = build_facts(mem, base)
    replay = Replay(mem, facts.binding, facts.flags["fixed"], not facts.flags["uncounted"])
    calls = _schedule(_entries_entered(player, WINDOW), mem, base)
    head = SidImage.from_bytes(raw).header
    rates = tuple(
        mem[facts.binding["rate_lo"] + r] | mem[facts.binding["rate_hi"] + r] << 8
        for r in range(128)
    )
    return Core(
        replay,
        dict(facts.flags),
        _state(mem, facts.binding),
        rates,
        NTSC_CLOCK_HZ if head.is_ntsc else PAL_CLOCK_HZ,
        (head.name, head.author, head.released),
        facts.binding["cp"] == facts.binding["extra"],
        calls,
    )


def _entries_entered(player, frames: int) -> list:
    """The entry each of the player's first ``frames`` calls enters.

    pysidtracker's ``SpecPlayer`` has no public hook for this: it runs a call through its private
    ``_run(entry)`` and keeps no record of the entries, so ``_run`` is wrapped here. A public
    per-call entry observer on ``SpecPlayer`` would replace this function.
    """
    entered = []
    run = player._run  # pylint: disable=protected-access

    def trace(entry):
        entered.append(entry)
        return run(entry)

    player._run = trace  # pylint: disable=protected-access
    for _ in range(frames):
        player.play_frame()
    return entered


def _schedule(entered: list, mem, base: int) -> tuple:
    """The repeating pattern of full (1) and sub-tick (0) calls the tune's interrupt makes, from
    the entry each of its first calls enters."""
    full = {e: int(not build_facts(mem, base, e).flags["reduced"]) for e in set(entered)}
    calls = [full[e] for e in entered]
    period = next((p for p in range(1, WINDOW // 4 + 1) if calls == calls[:p] * (WINDOW // p)), 0)
    if len(calls) != WINDOW or not period:
        raise Refusal("multispeed", "the calls are not periodic")
    return tuple(calls[:period])


def _refuse(core: Core) -> None:
    st = core.init
    if re.search(r"\bnative\b", spec().source or ""):
        raise Refusal("native", "the spec has native blocks")
    if not st["trigger"] & 0x80:
        raise Refusal("init", "the arranger is not armed")
    if st["vbit"] != (1, 2, 4) or st["vmask"] != (254, 253, 251):
        raise Refusal("init", "voice routing bits")
    zero = "mode depth wgh wgl ad sr".split()
    if any(any(st[n]) for n in zero) or any(
        st[n] for n in ("cut_lo", "cut_hi", "cut_step_lo", "cut_step_hi")
    ):
        raise Refusal("init", "state init does not clear")
    if st["op_lo"] != st["op_hi"] or st["op_lo"] not in (0x69, 0xE9) or st["extra"]:
        raise Refusal("init", "cutoff operation or extra")


def chain(row, y: int) -> tuple:
    """``(rows reached from y, loop index)``: follow ``row(y).next`` until a stop or a revisit."""
    order, at = [], {}
    while y is not None and y not in at:
        at[y] = len(order)
        order.append(y)
        y = row(y).next
    return order, at.get(y)


class Repeat(NamedTuple):
    """A block of steps played ``times`` in a row."""

    body: tuple
    times: int


def fold(keys: list) -> list:
    """``keys`` with each block repeated in a row as a ``Repeat``, the most it saves first, and
    the blocks folded the same way."""
    out, i = [], 0
    while i < len(keys):
        best, size = 1, 1
        for width in range(1, (len(keys) - i) // 2 + 1):
            times = 1
            while keys[i + times * width : i + (times + 1) * width] == keys[i : i + width]:
                times += 1
            if width * (times - 1) > size * (best - 1):
                best, size = times, width
        if best > 1:
            out.append(Repeat(tuple(fold(keys[i : i + size])), best))
        else:
            out.append(keys[i])
        i += size * best
    return out


def emit(items: list, voice: int) -> tuple:
    """The orderlist entries of ``voice`` for folded ``items``."""
    return tuple(
        Group(emit(item.body, voice), item.times) if isinstance(item, Repeat) else Play(item[voice])
        for item in items
    )


def first_cutoff(sub: bool, bottom: int, shift: bool) -> tuple:
    """``(accumulator low, high, output)`` after the first cutoff step from the cleared state: the
    accumulator stays, or a subtraction borrows through it and its high byte falls to the floor."""
    lo, hi = (255, bottom) if sub else (0, 0)
    return lo, hi, bottom << 1 & 255 if shift else bottom


class Step(NamedTuple):
    """An arranger step the walker starts: the patterns of its voices, the delay before its
    events, the full call it starts at, the full calls to its ALT step and the delay its ALT step
    sets for the next step."""

    patterns: tuple
    delay: int
    now: int
    last: int
    after: int


class Names:
    """Definitions by kind, numbered in first-use order."""

    def __init__(self):
        self.kinds = {}

    def name(self, kind: str, prefix: str, value) -> str:
        """The name of ``value`` among the ``kind`` records, adding it."""
        store = self.kinds.setdefault(kind, {})
        for name, held in store.items():
            if held == value:
                return name
        store[name := f"{prefix}{len(store) + 1}"] = value
        return name

    def section(self, kind: str) -> list:
        """The records of ``kind``."""
        return list(self.kinds.get(kind, {}).items())


def _order(slot: str) -> tuple:
    """The order the replay visits a voice-and-layer slot ``kNl`` in: layer, voice, kind."""
    return slot[2], slot[1], slot[0]


def _const(value: int) -> Expr:
    return Expr(canonical(f"(0 & v) + {value}"))


class Lift:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """One core's document."""

    def __init__(self, core: Core):
        _refuse(core)
        self.core, self.replay, self.st, self.flags = core, core.replay, core.init, core.flags
        self.names, self.calls = Names(), []
        self.extra, self.fulls = int(not core.calls[0]), sum(core.calls)
        self.steps, self.loop = self.walk()
        events = self.applied()
        cols = [(n, v) for row in self.reached(events) for n, v in row.columns]
        self.used = {n for n, _ in cols}
        self.plain = bool(self.flags["plain_tr"])
        traces = [v for n, v in cols if n == "TR"]
        self.rel = any(self.plain or not v & 0x80 for v in traces)
        self.slides = any(n == "AF" and v & 0x80 for n, v in cols)
        self.ps = "PS" in self.used
        self.acid = "ACID" in self.used or ("CP" in self.used and core.cp_extra)
        self.two = {"WGh", "WGl"} <= self.used
        self.mask7 = self.rel and not self.plain
        notes = [e.note for e in events if e.note is not None]
        if self.mask7 and max(notes + list(self.st["note_base"]), default=0) >= 128:
            raise Refusal("note", "a note past the 7-bit wrap in the absolute build")

    # ---- expressions ------------------------------------------------------------------------

    @property
    def key(self) -> str:
        """The note register's index from the sounding note."""
        return f"(N + 24 & {127 if self.mask7 else 255})" if self.rel else "(N + 24)"

    def word(self) -> str:
        """The standard word of the sounding note."""
        return f"F({self.key} - 24)"

    def step(self, mode: int) -> str:
        """The portamento's step at note index + ``mode``."""
        y = f"({self.key} + {mode} & 255)"
        return f"F({y} - 48) - F({y} - 49)"

    def pitch(self, voice: int, mode: int):
        """The pitch binding of ``mode`` for ``voice``."""
        d = self.st["detune"][voice]
        if mode == 0:
            w = self.word()
            return Expr(canonical(f"({w} & 65280) | ({w} + {d} & 255)" if d else w))
        if mode < 128:
            return Expr(canonical(f"u16({self.word()} + {self.step(mode)})"))
        return self.slide(mode, "")

    def carry(self, voice: int, mode: int):
        """The binding of the pulse sweep's carry from the pitch step of ``mode``."""
        w = self.word()
        if mode == 0:
            return Expr(canonical(f"(({w} & 255) + {self.st['detune'][voice]}) >> 8"))
        if mode < 128:
            return Expr(canonical(f"({w} + (({self.step(mode)}) & 65535)) >> 16"))
        return self.slide(mode, "c")

    def slide(self, mode: int, prefix: str) -> Call:
        """The slide template of ``mode``: ``prefix`` selects the pitch or the mirror."""
        name = prefix + ("dn" if mode & 0x40 else "up") + ("7" if self.mask7 else "")
        return self.call(name, r=self.core.rates[mode << 1 & 0x7F])

    def call(self, name: str, **params) -> Call:
        """The call of template ``name`` with ``params`` in its signature's order."""
        if name not in self.calls:
            self.calls.append(name)
        return Call(name, tuple(params[k] for k in asm.params(ROUTINES[name])))

    # ---- sidTAB rows ------------------------------------------------------------------------

    def keys(self, row, layer: str, voice: int) -> dict:
        """The program row keys of a sidTAB row of ``voice`` run in ``layer``."""
        col = dict(row.columns)
        out = {}
        if row.hold not in (None, 1):
            out["hold"] = row.hold
        if "AD" in col:
            out["ad"] = col["AD"]
        if "SR" in col:
            out["sr"] = col["SR"]
        self.wave(col, out)
        if "TR" in col:
            t = col["TR"]
            out["note"] = note_name((t & 127) + LOW) if not self.plain and t & 0x80 else Signed(t)
        if "AF" in col:
            out["p.freq"] = self.pitch(voice, col["AF"])
            if self.ps:
                out["m.pw"] = self.carry(voice, col["AF"])
        self.pulse(col, out, layer)
        if "RE" in col:
            self.filt(col["RE"], out, layer, voice)
        if "FV" in col:
            out["mode"] = col["FV"] & 0xF0
        if "CP" in col and self.core.cp_extra:
            out[f"e{voice + 1}{layer}.cut"] = Once(self.call("cp", p=col["CP"]))
        if "ACID" in col:
            self.acid_keys(col["ACID"], out, layer, voice)
        return out

    def wave(self, col: dict, out: dict) -> None:
        """The control byte keys of the WGh and WGl columns."""
        if not self.two:
            if "WGh" in col:
                out["ctrl"] = col["WGh"]
            if "WGl" in col:
                out["ctrl"] = col["WGl"]
            return
        op = "|" if self.flags["or_ctrl"] else "^"
        if "WGh" in col:
            out["h.ctrl"] = _const(col["WGh"])
        if "WGl" in col:
            out["l.ctrl"] = Expr(canonical(f"v {op} {col['WGl']}"))

    def pulse(self, col: dict, out: dict, layer: str) -> None:
        """The pulse width keys of the PW and PS columns."""
        if not self.ps:
            if "PW" in col:
                out["pw"] = (col["PW"] & 15) << 8 | col["PW"] & 0xF0
            return
        w, s = col.get("PW"), col.get("PS")
        if w is not None and s is not None:
            out[f"w{layer}.pw"] = Once(self.call("pwps", p=w, q=s))
        elif w is not None:
            out[f"w{layer}.pw"] = Once(self.call("pwset", p=w))
        elif s is not None:
            out[f"w{layer}.pw"] = Once(self.call("psset", q=s))

    @staticmethod
    def filt(b: int, out: dict, layer: str, voice: int) -> None:
        """The resonance and routing keys of the RE column: the routing is a once binding of the
        voice's slot, so rows of one tick apply in the order the replay applies them."""
        slot, bit = f"r{voice + 1}{layer}.route", 1 << voice
        if b == 0:
            out[slot] = Once(Expr(canonical(f"v & {~bit & 15}")))
            return
        out["res"] = b >> 4
        out[slot] = Once(Expr(canonical(f"v | {b & 15 | bit}")) if b & 8 else _const(b & 15))

    def acid_keys(self, value: int, out: dict, layer: str, voice: int) -> None:
        """The cutoff slide key of the ACID column."""
        lo, hi = value & 255, value >> 8
        if not hi & 0x80:
            call = self.call("acc", p=lo, q=hi)
        else:
            call = self.call(
                "sub" if hi & 0x40 else "add", p=lo, q=hi & (0x3F if hi & 0x40 else 0x7F)
            )
        out[f"k{voice + 1}{layer}.cut"] = Once(call)

    def program(self, y: int, layer: str, voice: int) -> str:
        """The ``ctrl`` program name of the cascade armed at row ``y``."""
        order, loop = chain(self.replay.row, y)
        rows = [Row(self.keys(self.replay.row(r), layer, voice)) for r in order]
        return self.names.name("ctrl", "c", Program(tuple(rows), loop))

    # ---- patterns and the arranger ----------------------------------------------------------

    def arranger(self, y: int) -> tuple:
        """The arranger row ``y``: ``(V1, V2, V3)``."""
        try:
            return self.replay.arranger(y)
        except LookupError as exc:
            raise Refusal("arranger", f"row ${y:02x} is not in the song") from exc

    def tick(self, t: int) -> int:
        """The document tick of the walker's full call ``t``: with a schedule that starts on a
        sub-tick call the document has a full call 0 of its own, at which its init events apply
        and no step does."""
        return t + self.extra * (t // self.fulls + 1)

    def walk(self) -> tuple:
        """``(steps, loop)``: each arranger step the walker starts, in order, as ``Step``, and the
        index the walk returns to. A step is the same once the row, owner, count, delay and (with
        an extra full call) the place in the schedule recur; the first step is its own where
        calls precede its events."""
        st = self.st
        y, owner, count, t = st["cursor"], st["owner"], st["count"], st["trigger"] & 15
        seen, steps, now = {}, [], 0
        while len(steps) < LIMIT:
            phase = now % self.fulls if self.extra else 0
            at = (y, owner, count, t, phase, bool(self.tick(t) and not steps))
            if at in seen:
                return steps, seen[at]
            seen[at] = len(steps)
            row = self.arranger(y)
            if row[0] & 0x80:
                y, owner, count = self.jump(row, y, owner, count)
                row = self.arranger(y)
            last, delay = self.length(row)
            steps.append(Step(row, t, now, last, delay))
            now += t + last + 2
            t = delay
            y = (y + 1) % 256
        raise Refusal("arranger", "no loop within the limit")

    def jump(self, row: tuple, y: int, owner: int, count: int) -> tuple:
        """``(row played, owner, count)`` after the jump row ``y``."""
        target, n = row[1], row[2]
        if not self.replay.counted or not n:
            return target, owner, count
        if owner != y:
            return target, y, n
        count = (count - 1) & 255
        return (target, owner, count) if count else (y + 1, 0, count)

    def length(self, voices: tuple) -> tuple:
        """``(full calls to the ALT step, its delay)`` of the patterns played together: the first
        ALT step, the last voice's delay where several end together."""
        ends = [self.replay.end(p) for p in voices]
        first = min(at for at, _ in ends)
        return first, [d for at, d in ends if at == first][-1]

    def applied(self) -> list:
        """The pattern events the walk applies: those at or before each step's ALT step."""
        done = {(p, s.last) for s in self.steps for p in s.patterns}
        return [ev for p, last in sorted(done) for ev in self.replay.events(p, last)]

    def reached(self, events: list) -> list:
        """The sidTAB rows the events arm and the rows after them."""
        rows = {r for ev in events for y in (ev.a, ev.b) if y is not None for r in self.chain(y)}
        return [self.replay.row(r) for r in sorted(rows)]

    def chain(self, y: int) -> list:
        """The rows run from row ``y``."""
        return chain(self.replay.row, y)[0]

    def event(self, ev, voice: int) -> Event:
        """The document event of a pattern step."""
        cmds = {}
        if ev.a is not None:
            cmds["run"] = self.program(ev.a, "a", voice)
        if ev.b is not None:
            cmds["b.run"] = self.program(ev.b, "b", voice)
        atom = "-" if ev.note is None else note_name(ev.note + LOW)
        return Event(atom, None, cmds)

    def phrase(self, p: int, voice: int, step: Step, first: bool = False) -> str:
        """The name of the phrase of pattern ``p`` in ``step``: from the call its events follow
        the delay on, to the call the next step's does; from call 0 where it is the ``first``
        step."""
        base = step.now + step.delay
        start = 0 if first else self.tick(base)
        ticks = [Event("-")] * (self.tick(base + step.last + 2 + step.after) - start)
        for ev in self.replay.events(p, step.last):
            ticks[self.tick(base + 1 + ev.at) - start] = self.event(ev, voice)
        return self.names.name("phrase", "p", tuple(rests(ticks)))

    def voices(self) -> tuple:
        """The orderlist of each voice: the steps with each repeated block a group, and the
        loop at the top-level entry the walk returns to."""
        keys = [
            tuple(self.phrase(p, v, step, not i) for v, p in enumerate(step.patterns))
            for i, step in enumerate(self.steps)
        ]
        head = fold(keys[: self.loop])
        items = head + fold(keys[self.loop :])
        return tuple(Voice(emit(items, v), len(head)) for v in range(3))

    # ---- the document -----------------------------------------------------------------------

    def instrument(self, voice: int) -> Instrument:
        """The instrument of ``voice``: its notes restore the pitch mode and clear the slide."""
        fields = {"p.freq": self.pitch(voice, 0)}
        if self.ps and "AF" in self.used:
            fields["m.pw"] = self.carry(voice, 0)
        if self.slides:
            fields["n.freq"] = Once(self.call("clr"))
            if self.ps:
                fields["n.pw"] = Once(self.call("pclr"))
        return Instrument(fields)

    def start(self, voice: int) -> Event:
        """The init event of ``voice``: the note base, the seeds, then the first tick's program."""
        st = self.st
        row = {"note": note_name(LOW), "gate": 1, "p.freq": self.pitch(voice, 0)}
        if self.ps:
            row["m.pw"] = self.carry(voice, 0)
        prog = self.names.name("ctrl", "c", Program((Row(row),), None))
        cmds = {"legato": True, "run": prog}
        acc = st["slide_lo"][voice] | st["slide_hi"][voice] << 8
        if self.slides and acc:
            cmds["n.freq"] = Once(self.call("seed", p=acc & 255, q=acc >> 8))
            if self.ps:
                cmds["n.pw"] = Once(self.call("pseed", p=acc & 255, q=acc >> 8))
        if self.ps:
            cmds["i.pw"] = Once(self.call("pwinit", p=st["pw_lo"][voice], q=st["pw_hi"][voice]))
        if voice == 0:
            cmds["tempo"] = 1
            if self.seeded:
                cmds["i.cut"] = Once(self.call("acc", p=self.cut0[0], q=self.cut0[1]))
        return Event(note_name(st["note_base"][voice] + LOW), f"i{voice + 1}", cmds)

    @property
    def bottom(self) -> int:
        """The cutoff's lowest output."""
        return self.st["floor"] if self.flags["floored"] else 0

    @property
    def shift(self) -> bool:
        """Whether the cutoff output doubles."""
        return bool(self.flags["shifts"]) and self.st["shift"] == 0x0A

    @property
    def cut0(self) -> tuple:
        """``(accumulator low, high, output)`` after the first call's cutoff step."""
        return first_cutoff(self.st["op_lo"] == 0xE9, self.bottom, self.shift)

    @property
    def seeded(self) -> bool:
        """Whether the accumulator after the first step is not zero."""
        return self.stepping and self.cut0[:2] != (0, 0)

    @property
    def stepping(self) -> bool:
        """Whether the cutoff moves: a row sets it or the build subtracts from the start."""
        return self.acid or self.st["op_lo"] == 0xE9

    def step_call(self) -> Call | None:
        """The cutoff step's call, None where the output stays at the first step's."""
        if not self.stepping:
            return None
        sub = self.st["op_lo"] == 0xE9
        return self.call("stepx" if self.shift else "step", i=2 if sub else 1, b=self.bottom)

    def regs(self) -> list:
        """The registers before tick 0."""
        st, regs = self.st, [0] * 25
        for v in range(3):
            regs[7 * v : 7 * v + 4] = [
                st["freq_lo"][v],
                st["freq_hi"][v],
                st["pw_lo"][v],
                st["pw_hi"][v] & 15,
            ]
        regs[22] = self.cut0[2]
        regs[23] = st["res"]
        regs[24] = st["fv"] | 15
        return regs

    def slots(self) -> dict:
        """The cells' slots in firing order: those the programs bind, then the defaults."""
        bound = Counter()
        for _, prog in self.names.section("ctrl"):
            for row in prog.rows:
                bound.update(k.rpartition(".")[::2] for k, _ in row.keys if "." in k)
        slots = {"freq": [Slot("n")] * self.slides + [Slot("p")]}
        if self.two:
            slots["ctrl"] = [Slot("h", _const(0)), Slot("l")]
        if self.ps:
            setters = [Slot(f"w{l}") for l in "ab" if (f"w{l}", "pw") in bound]
            seed = [Slot("n")] * self.slides
            slots["pw"] = [Slot("i")] + setters + seed + [Slot("m"), Slot("s", self.call("psw"))]
        if keys := sorted((k for k, cell in bound if cell == "route"), key=_order):
            slots["route"] = [Slot(k) for k in keys]
        if step := self.step_call():
            keys = sorted((k for k, cell in bound if cell == "cut"), key=_order)
            slots["cut"] = [Slot("i")] * self.seeded + [Slot(k) for k in keys]
            slots["cut"] += [Slot("step", step)]
        return slots

    def header(self) -> Header:
        """The header lines."""
        core = self.core
        used = {CELL_OF[n] for n in self.calls}
        state = tuple((c, STATE[c]) for c in ("freq", "pw", "cut") if c in used)
        regs = self.regs()
        calls = (1,) + core.calls[1:]
        name, author, released = (meta_text(s) for s in core.head)
        return Header(
            (("title", name), ("author", author), ("released", released)),
            clock=(CYCLES[core.clock], core.clock),
            slots=self.slots(),
            layers={"ctrl": ("a", "b")},
            order=ORDER if self.ps and "AF" in self.used else None,
            state=state or None,
            regs=tuple(regs) if any(regs) else None,
            lag=1,
            calls=(1, calls) if 0 in calls else None,
        )

    def document(self) -> Document:
        """The document of the subtune."""
        voices = self.voices()
        init = tuple(self.start(v) for v in range(3))
        ins = tuple((f"i{v + 1}", self.instrument(v)) for v in range(3))
        defs = [Section("ins", ins), Section("ctrl", tuple(self.names.section("ctrl")))]
        defs += [Section("phrase", tuple(self.names.section("phrase")))]
        head = self.header()
        mods = tuple((signature(n, asm.params(ROUTINES[n])), Code(ROUTINES[n])) for n in self.calls)
        return Document(head, (), mods, defs, init, voices, None)


def document_text(core: Core) -> str:
    """The document of ``core``, printed."""
    return printed(Lift(core).document())


def _contexts(core: Core) -> dict:
    """The song data the replay can reach from its init state: ``plays`` and ``jumps``, the
    arranger rows; ``ends``, for each played row the full calls to its ALT step and the delay it
    sets; ``armed``, the ``(voice, layer, row)`` the played events arm; ``patterns``."""
    replay = core.replay
    plays, jumps = replay.reach(core.init["cursor"])
    ends, armed, patterns = {}, set(), set()
    for y in plays:
        row = replay.arranger(y)
        found = [replay.end(p) for p in row]
        first = min(at for at, _ in found)
        ends[y] = first, [d for at, d in found if at == first][-1]
        patterns.update(row)
        for v, p in enumerate(row):
            for ev in replay.events(p, first):
                armed.update((v, layer, r) for layer, r in zip("ab", (ev.a, ev.b)) if r is not None)
    return {"plays": plays, "jumps": jumps, "ends": ends, "armed": armed, "patterns": patterns}


def native_counts(core: Core) -> dict:
    """The objects of each kind the replay's own song data has, the most a lift may hold
    (:mod:`unitracker.lift.invariants`), read from the arranger rows, pattern streams and sidTAB
    rows the replay can reach from its init state:

    - ``instruments``: 3, one a voice, which holds the voice's detune;
    - ``ctrl``: a program a ``(voice, layer, row)`` the played events arm, as a program's keys
      name its voice and layer, and one a voice for the init event's first-tick program, which
      binds the voice's detuned pitch;
    - ``restart``, ``pw``, ``filter``: 0, the lift binds the pulse and the cutoff by modulators;
    - ``phrases``: a phrase is a pattern on a voice (the programs its events run name the
      voice's slots) cut at its step's ALT step and padded to the next step's, so its distinct
      values are the ``(voice, pattern, ALT step, delay after)`` of each played
      row, times the place in the schedule where a full call of the document's own is added
      (the schedule's full calls), plus one a voice for the first step's calls before its events;
    - ``song``: an entry a reached arranger row, a jump row once (a counted jump is a group and
      the walk's return a ``loop``).
    """
    found = _contexts(core)
    row = core.replay.arranger
    phrases = {(v, p, *found["ends"][y]) for y in found["plays"] for v, p in enumerate(row(y))}
    phases = sum(core.calls) if not core.calls[0] else 1
    return {
        "instruments": 3,
        "restart": 0,
        "ctrl": len(found["armed"]) + 3,
        "pw": 0,
        "filter": 0,
        "phrases": len(phrases) * phases + 3,
        "song": (len(found["plays"]) + len(found["jumps"]),) * 3,
    }


def native_bytes(core: Core) -> int:
    """The bytes of the song data the replay can reach: three a arranger row, the pattern
    streams to their ALT steps and the sidTAB rows the played events arm with the rows after
    them, a row's bitmaps, columns and DL."""
    found, replay = _contexts(core), core.replay
    rows = {r for _, _, y in found["armed"] for r in chain(replay.row, y)[0]}
    size = 3 * (len(found["plays"]) + len(found["jumps"]))
    return size + sum(replay.size(p) for p in found["patterns"]) + sum(map(replay.row_size, rows))


# ---- the reference --------------------------------------------------------------------------


def reference(raw: bytes, subtune: int = 0, frames: int = 20000, notes: bool = True):
    """The register grid of the spec player over ``frames`` frames."""
    fmt = find_format(raw)
    song = DefmonSong.from_sid_bytes(raw)
    song.subtune = subtune
    return np.asarray(fmt.spec_player(song, notes=notes).render_grid(frames), dtype=np.uint8)


def verify(raw: bytes, subtune: int = 0, frames: int = 20000) -> tuple:
    """``(cells, text)`` of a SID: the cells where its document differs from the reference; a
    ModelError where the document holds more of a kind than the replay's song data."""
    core = core_of_sid(raw, subtune)
    text = document_text(core)
    invariants.check(text, native_counts(core))
    ours = np.asarray(play(text, frames), dtype=np.uint8)
    theirs = reference(raw, subtune, len(ours))
    return gridcmp.cell_diff(ours, theirs), text


def report(raw: bytes, subtune: int = 0) -> dict:
    """The size of a SID's document: ``lifted`` and ``native`` counts of each kind, and the
    document's ``bytes`` against the ``native_bytes`` of the song data it lifts."""
    core = core_of_sid(raw, subtune)
    text = document_text(core)
    return {
        "lifted": invariants.lifted(text),
        "native": native_counts(core),
        "bytes": len(text.encode()),
        "native_bytes": native_bytes(core),
    }


def checked(text_cells: tuple, frames: int) -> str:
    """The text of ``(cells, text)``, or a Divergence naming the first differing cell."""
    cells, text = text_cells
    if cells.size:
        frame, reg = (int(c) for c in cells[0][:2])
        raise Divergence(f"frame {frame} register {reg}: {len(cells)} cells over {frames} frames")
    return text


def lift(raw: bytes, subtune: int = 0, frames: int | None = None) -> str:
    """The document of a SID's subtune, checked against the reference over ``frames`` frames
    (default :data:`CHECK`); a LiftError otherwise."""
    frames = frames or CHECK
    return checked(verify(raw, subtune, frames), frames)


def main(argv=None) -> int:
    """Lift a SID, or verify SIDs against the spec player."""
    return cli.main(argv, __doc__.split("\n", 1)[0], lift, verify)


if __name__ == "__main__":
    sys.exit(main())
