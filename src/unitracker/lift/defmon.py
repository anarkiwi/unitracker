"""A defMON tune as a format 2.5 document, from pydefmon's song model and the build facts of its
bound playspec; ``verify`` plays the document against the spec player with the standard note
table (docs/lift/defmon.md)."""

import re
import sys
from collections import Counter
from dataclasses import dataclass

import numpy as np
from pydefmon import DefmonSong, Tune
from pydefmon.defmon import DefmonError
from pydefmon.spec import build_facts, player_bases, spec
from pydefmon.tune import Jump
from pysidtracker import NTSC_CLOCK_HZ, PAL_CLOCK_HZ, SidImage, find_format, gridcmp
from pysidtracker.errors import SidParseError
from pysidtracker.formats import SidFormatError
from unitracker_grammar import code as asm
from unitracker_grammar.document import (
    Call,
    Code,
    Document,
    Event,
    Expr,
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

from unitracker.lift import cli
from unitracker.lift.dmcode import ROUTINES, STATE
from unitracker.lift.shared import meta_text, note_name, rests

CYCLES = {PAL_CLOCK_HZ: 19656, NTSC_CLOCK_HZ: 17095}
VOICE = 0x31
CHECK = 4000
LIMIT = 1 << 15
LOW = -24
summary = cli.summary
CELL_OF = {
    "freq": "up dn up7 dn7 clr seed",
    "pw": "cup cdn cup7 cdn7 pclr pseed psw pwset psset pwps",
}
CELL_OF = {t: c for c, names in CELL_OF.items() for t in names.split()}
CELL_OF |= {t: "cut" for t in ("cp", "acc", "add", "sub", "step", "stepx")}
CAUSES = (
    (r"JP onto JP", "jp onto jp"),
    (r"past its 32 steps", "pattern past 32 steps"),
    (r"plays \(", "pattern past $7f"),
    (r"no pattern that ends", "no ending pattern"),
)


class LiftError(ValueError):
    """A tune the lift cannot state exactly, or whose document differs from the reference."""


class Refusal(LiftError):
    """A construct of a tune the document cannot state; ``kind`` names its class."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


class Divergence(LiftError):
    """The document's grid differs from the reference player's."""


@dataclass(frozen=True)
class Core:  # pylint: disable=too-many-instance-attributes
    """What a lift needs of a tune: the song model, the build flags, the state init leaves, the
    slide rate table, the clock and the title lines."""

    tune: Tune
    flags: dict
    init: dict
    rates: tuple
    clock: int
    head: tuple
    cp_extra: bool = True


def _cause(exc: Exception) -> Refusal:
    for pattern, kind in CAUSES:
        if re.search(pattern, str(exc)):
            return Refusal(kind, str(exc))
    return Refusal("song", str(exc))


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
        tune = Tune.from_song(song)
    except DefmonError as exc:
        raise _cause(exc) from exc
    if len(tune.chips) > 1:
        raise Refusal("multi-SID", "stereo")
    try:
        player = fmt.spec_player(song, notes=True)
    except SidFormatError as exc:
        raise Refusal("multi-SID", str(exc)) from exc
    mem = player.memory
    facts = build_facts(mem, player_bases(mem)[0], player.entry)
    head = SidImage.from_bytes(raw).header
    rates = tuple(
        mem[facts.binding["rate_lo"] + r] | mem[facts.binding["rate_hi"] + r] << 8
        for r in range(128)
    )
    return Core(
        tune,
        dict(facts.flags),
        _state(mem, facts.binding),
        rates,
        NTSC_CLOCK_HZ if head.is_ntsc else PAL_CLOCK_HZ,
        (head.name, head.author, head.released),
        facts.binding["cp"] == facts.binding["extra"],
    )


def _refuse(core: Core) -> None:
    st, flags = core.init, core.flags
    if re.search(r"\bnative\b", spec().source or ""):
        raise Refusal("native", "the spec has native blocks")
    if flags["reduced"]:
        raise Refusal("multispeed", "the first call is the sub-tick entry")
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


def _chain(rows: dict, y: int) -> tuple:
    """``(rows reached from y, loop index)``: follow ``next`` until a stop or a revisit."""
    order, at = [], {}
    while y is not None and y not in at:
        at[y] = len(order)
        order.append(y)
        y = rows[y].next
    return order, at.get(y)


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


def _const(value: int) -> Expr:
    return Expr(canonical(f"(0 & v) + {value}"))


class Lift:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """One core's document."""

    def __init__(self, core: Core):
        _refuse(core)
        self.core, self.tune, self.st, self.flags = core, core.tune, core.init, core.flags
        self.names, self.calls = Names(), []
        rows = self.tune.rows.values()
        cols = [(n, v) for row in rows for n, v in row.columns]
        self.used = {n for n, _ in cols}
        self.plain = bool(self.flags["plain_tr"])
        traces = [v for n, v in cols if n == "TR"]
        self.rel = any(self.plain or not v & 0x80 for v in traces)
        self.slides = any(n == "AF" and v & 0x80 for n, v in cols)
        self.ps = "PS" in self.used
        self.acid = "ACID" in self.used or ("CP" in self.used and core.cp_extra)
        self.two = {"WGh", "WGl"} <= self.used
        self.layers = {
            l
            for evs in self.tune.patterns.values()
            for e in evs
            for l in "ab"
            if getattr(e, l) is not None
        }
        self.mask7 = self.rel and not self.plain
        notes = [e.note for evs in self.tune.patterns.values() for e in evs if e.note is not None]
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
            self.filt(col["RE"], out)
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
    def filt(b: int, out: dict) -> None:
        """The resonance and routing keys of the RE column."""
        if b == 0:
            out["route_off"] = 0
        elif b & 8:
            out["res"] = b >> 4
            out["route_on"] = b & 15
        else:
            out["res"] = b >> 4
            out["route"] = b & 15

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
        order, loop = _chain(self.tune.rows, y)
        rows = [Row(self.keys(self.tune.rows[r], layer, voice)) for r in order]
        return self.names.name("ctrl", "c", Program(tuple(rows), loop))

    # ---- patterns and the arranger ----------------------------------------------------------

    def walk(self) -> tuple:
        """``(steps, loop)``: the ``(patterns, delay)`` each arranger step starts, in order, and
        the index the walk returns to."""
        chip, st = self.tune.chips[0], self.st
        y, owner, count, t = st["cursor"], st["owner"], st["count"], st["trigger"] & 15
        seen, steps = {}, []
        while len(steps) < LIMIT:
            at = (y, owner, count, t)
            if at in seen:
                return steps, seen[at]
            seen[at] = len(steps)
            if y not in chip:
                raise Refusal("arranger", f"row ${y:02x} is not in the song")
            node = chip[y]
            if isinstance(node, Jump):
                y, owner, count = self.jump(node, y, owner, count)
                node = chip[y]
            if isinstance(node, Jump):
                raise Refusal("arranger", f"row ${y:02x} lands on a jump")
            steps.append((node.voices, t))
            t = self.length(node.voices)[1]
            y = (y + 1) % 256
        raise Refusal("arranger", "no loop within the limit")

    def jump(self, node: Jump, y: int, owner: int, count: int) -> tuple:
        """``(row played, owner, count)`` after the jump row ``y``."""
        if not self.tune.counted or not node.count:
            return node.target, owner, count
        if owner != y:
            return node.target, y, node.count
        count = (count - 1) & 255
        return (node.target, owner, count) if count else (y + 1, 0, count)

    def length(self, voices: tuple) -> tuple:
        """``(ticks to the ALT step, its delay)`` of the patterns played together: the first
        ALT step, the last voice's delay where several end together."""
        ends = [
            (ev.at, ev.end) for p in voices for ev in self.tune.patterns[p] if ev.end is not None
        ]
        first = min(at for at, _ in ends)
        return first, [d for at, d in ends if at == first][-1]

    def event(self, ev, voice: int) -> Event:
        """The document event of a pattern step."""
        cmds = {}
        if ev.a is not None:
            cmds["run"] = self.program(ev.a, "a", voice)
        if ev.b is not None:
            cmds["b.run"] = self.program(ev.b, "b", voice)
        atom = "-" if ev.note is None else note_name(ev.note + LOW)
        return Event(atom, None, cmds)

    def phrase(self, p: int, voice: int, delay: int, last: int) -> str:
        """The name of the phrase of pattern ``p`` after ``delay`` ticks, ``last`` the ticks to
        the step's ALT step."""
        ticks = [Event("-")] * (delay + last + 2)
        for ev in self.tune.patterns[p]:
            if ev.at <= last:
                ticks[delay + 1 + ev.at] = self.event(ev, voice)
        return self.names.name("phrase", "p", tuple(rests(ticks)))

    def voices(self) -> tuple:
        """The orderlist of each voice."""
        steps, loop = self.walk()
        items = [[Play("p0")] for _ in range(3)]
        for patterns, delay in steps:
            last = self.length(patterns)[0]
            for v, p in enumerate(patterns):
                items[v].append(Play(self.phrase(p, v, delay, last)))
        return tuple(Voice(tuple(i), loop + 1) for i in items)

    # ---- the document -----------------------------------------------------------------------

    def instrument(self, voice: int) -> Instrument | None:
        """The instrument of ``voice``: its notes restore the pitch mode and clear the slide."""
        if "AF" not in self.used:
            return None
        fields = {"p.freq": self.pitch(voice, 0)}
        if self.ps:
            fields["m.pw"] = self.carry(voice, 0)
        if self.slides:
            fields["n.freq"] = Once(self.call("clr"))
            if self.ps:
                fields["n.pw"] = Once(self.call("pclr"))
        return Instrument(fields)

    def start(self, voice: int) -> Event:
        """The init event of ``voice``: the note base, the seeds, then the first tick's program."""
        st = self.st
        row = {"wait": 1, "note": note_name(LOW), "gate": 1, "p.freq": self.pitch(voice, 0)}
        if self.ps:
            row["m.pw"] = self.carry(voice, 0)
        prog = self.names.name("ctrl", "c", Program((Row(row),), None))
        cmds = {"legato": True, "p.freq": Expr("v"), "run": prog}
        acc = st["slide_lo"][voice] | st["slide_hi"][voice] << 8
        if self.slides and acc:
            cmds["n.freq"] = Once(self.call("seed", p=acc & 255, q=acc >> 8))
            if self.ps:
                cmds["n.pw"] = Once(self.call("pseed", p=acc & 255, q=acc >> 8))
        if voice == 0:
            cmds["tempo"] = 1
        name = f"i{voice + 1}" if self.instrument(voice) else None
        return Event(note_name(st["note_base"][voice] + LOW), name, cmds)

    def cutoff(self) -> tuple:
        """``(initial output, cutoff step call or None)``: the step runs where a row sets the
        slide or the build subtracts from the start."""
        st, flags = self.st, self.flags
        bottom = st["floor"] if flags["floored"] else 0
        shift = flags["shifts"] and st["shift"] == 0x0A
        sub = st["op_lo"] == 0xE9
        if not (self.acid or sub):
            out = bottom
            return (out << 1 & 255 if shift else out), None
        step = self.call("stepx" if shift else "step", i=2 if sub else 1, b=bottom)
        return 0, step

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
        regs[22] = self.cutoff()[0]
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
            slots["pw"] = setters + seed + [Slot("m"), Slot("s", self.call("psw"))]
        step = self.cutoff()[1]
        if step:
            keys = sorted(
                (k for k, cell in bound if cell == "cut"), key=lambda k: (k[2], k[1], k[0])
            )
            slots["cut"] = [Slot(k) for k in keys] + [Slot("step", step)]
        return slots

    def header(self) -> Header:
        """The header lines."""
        core = self.core
        used = {CELL_OF[n] for n in self.calls}
        state = tuple((c, STATE[c]) for c in ("freq", "pw", "cut") if c in used)
        regs = self.regs()
        name, author, released = (meta_text(s) for s in core.head)
        return Header(
            (("title", name), ("author", author), ("released", released)),
            clock=(CYCLES[core.clock], core.clock),
            slots=self.slots(),
            layers={"ctrl": ("a", "b")},
            state=state or None,
            regs=tuple(regs) if any(regs) else None,
        )

    def document(self) -> Document:
        """The document of the subtune."""
        voices = self.voices()
        init = tuple(self.start(v) for v in range(3))
        ins = tuple((f"i{v + 1}", self.instrument(v)) for v in range(3) if self.instrument(v))
        defs = [Section("ins", ins)] if ins else []
        defs += [Section("ctrl", tuple(self.names.section("ctrl")))]
        defs += [Section("phrase", (("p0", (Event("-"),)), *self.names.section("phrase")))]
        head = self.header()
        mods = tuple((signature(n, asm.params(ROUTINES[n])), Code(ROUTINES[n])) for n in self.calls)
        return Document(head, (), mods, defs, init, voices, None)


def document_text(core: Core) -> str:
    """The document of ``core``, printed."""
    return printed(Lift(core).document())


# ---- the reference --------------------------------------------------------------------------


def reference(raw: bytes, subtune: int = 0, frames: int = 20000, notes: bool = True):
    """The register grid of the spec player over ``frames`` frames."""
    fmt = find_format(raw)
    song = DefmonSong.from_sid_bytes(raw)
    song.subtune = subtune
    return np.asarray(fmt.spec_player(song, notes=notes).render_grid(frames), dtype=np.uint8)


def verify(raw: bytes, subtune: int = 0, frames: int = 20000) -> tuple:
    """``(cells, text)`` of a SID: the cells where its document differs from the reference."""
    text = document_text(core_of_sid(raw, subtune))
    ours = np.asarray(play(text, frames), dtype=np.uint8)
    theirs = reference(raw, subtune, len(ours))
    return gridcmp.cell_diff(ours, theirs), text


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
