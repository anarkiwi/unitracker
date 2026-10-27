"""A tracer's event stream (:mod:`unitracker.direct.trace`) built into a canonical document.

Programs are walked from their table positions, spelled without rows or keys that change
nothing (an all-empty tail, an empty row, a key re-setting what every execution already has),
shared by content and named in order of first use; docs/direct/builder.md.
"""

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import unitracker_player
from pysidtracker import gridcmp
from pysidtracker.whydiff import explain_divergence
from unitracker_grammar import code
from unitracker_grammar import document as d
from unitracker_player.player import Player, load, play

from unitracker.direct.trace import Effect, Note, Start, Step, program

CELLS = ("freq", "pw", "cut")
SHARED = ("cut",)
PREFIX = {"ctrl": "c", "pw": "pw", "filter": "f", "phrase": "p"}
MAX_WALK = 1 << 20
# builder options from UNITRACKER_BUILDER: "stated", each phrase's first note states its instrument
OPTIONS = frozenset(os.environ.get("UNITRACKER_BUILDER", "").split(","))
COMPILED = {"mask": "ctrl", "route_on": "route", "route_off": "route"}


class BuildError(ValueError):
    """The trace is not one the builder spells."""


@dataclass
class Built:
    """A built document and its text."""

    doc: d.Document
    text: str


def collect(tracer) -> tuple:
    """``(step ticks, loop step, end tick, {(tick, voice): [note, pos, commands]})`` of the
    trace to the first step whose key recurs."""
    steps, seen, cells = [], {}, defaultdict(lambda: [None, None, []])
    for ev in tracer.events():
        if isinstance(ev, Step):
            if ev.key in seen:
                return steps, seen[ev.key], ev.tick, cells
            seen[ev.key] = len(steps)
            steps.append(ev.tick)
        elif isinstance(ev, Note):
            cells[ev.tick, ev.voice][0] = ev
        elif isinstance(ev, Start):
            cells[ev.tick, ev.voice][1] = ev.pos
        else:
            cells[ev.tick, ev.voice][2].append((ev.key, ev.value))
    raise BuildError("the trace ends without a loop")


def walk(tracer, pos, voice: int) -> tuple:
    """``(rows, loop)``: ``[hold, keys, source]`` of each fetching tick from ``pos`` on, to a
    stop or a position fetched before."""
    rows, seen, last = [], {}, 0
    for t in range(MAX_WALK):
        if pos is None:
            return rows, None
        nxt, fetched = tracer.step(pos)
        if fetched:
            if pos in seen:
                rows[-1][0] = t - last
                return rows, seen[pos]
            seen[pos] = len(rows)
            if rows:
                rows[-1][0] = t - last
            rows += [[0, bound(tracer, keys, voice), pos] for keys in fetched]
            rows[-1][0], last = 1, t
        pos = nxt
    raise BuildError("a program walks without end")


def bound(tracer, keys, voice: int) -> tuple:
    """``keys`` with each Effect replaced by the tracer's bindings of it."""
    return tuple(
        kv for k in keys for kv in (tracer.bind(k, voice) if isinstance(k, Effect) else (k,))
    )


def tidy(rows: list, loop: int | None) -> tuple:
    """``(rows, loop)`` without an all-empty looping tail, trailing empty rows or an empty row
    past the first: its ticks the row before's, and an empty loop row's the loop's last row's
    too; a last row's hold is 1."""
    rows = [list(r) for r in rows]
    if loop is not None and not any(k for _, k, _ in rows[loop:]):
        rows, loop = rows[:loop], None
    while loop is None and rows and not rows[-1][1]:
        rows.pop()
    while loop and not rows[loop][1]:
        hold = rows.pop(loop)[0]
        rows[loop - 1][0] += hold
        rows[-1][0] += hold
    out, at = [], None
    for i, (hold, keys, src) in enumerate(rows):
        if out and not keys and i != loop:
            out[-1][0] += hold
            continue
        at = len(out) if i == loop else at
        out.append([hold, keys, src])
    if out and at is None:
        out[-1][0] = 1
    return out, at


def content(rows: list, loop: int | None) -> tuple:
    """A program's identity: its rows' holds and keys, and its loop."""
    return tuple((h, k) for h, k, _ in rows), loop


# ---- the slot state every execution of a program shares ----------------------------------


def facts(key: str, value, slots: dict) -> tuple:
    """``(name, value)`` of what a row key sets that the voice keeps: its control, gate, note,
    envelope and modulator bindings; empty for what the builder does not track."""
    cell = key.rpartition(".")[2]
    if cell in CELLS:
        return ((key if "." in key else f"{slots[cell][0]}.{cell}", (value, False)),)
    if key == "note":
        return (("rel", int(value)), ("abs", None)) if isinstance(value, int) else (("abs", value),)
    if key in ("ctrl", "gate", "ad", "sr"):
        return ((key, value),)
    return (("mask", value & 0xFE),) if key == "mask" else ()


def apply(state: dict, pairs) -> dict:
    """``state`` after setting each ``(name, value)``."""
    return state | {n: frozenset((v,)) for n, v in pairs}


def join(a: dict | None, b: dict) -> dict:
    """What holds in both states; a name either leaves unknown is unknown."""
    return b if a is None else {n: a[n] | b[n] for n in a.keys() & b.keys()}


@dataclass
class Context:
    """What may happen around a program: its executions' joined entry state, the note bindings
    of notes during it, the names others may set during it and its voices."""

    entry: dict | None = None
    notes: set = field(default_factory=set)
    tops: set = field(default_factory=set)
    voices: set = field(default_factory=set)


def noted(state: dict, binds) -> dict:
    """``state`` after a note whose instrument sets ``binds``."""
    return apply(state, (("rel", 0), ("abs", None), ("gate", 1), *binds))


def boundary(state: dict, ctx: Context) -> dict:
    """``state`` joined with what may happen between two rows."""
    out = {n: v for n, v in state.items() if n not in ctx.tops}
    for binds in ctx.notes:
        out = join(out, noted(out, binds))
    return out


def plain(rows: list, loop: int | None, ctx: Context, slots: dict, listen: set) -> list:
    """``rows`` without the keys whose value every execution already holds (by a dataflow
    over the program's rows and loop), but a key a slot's ``row.`` event needs."""
    ins, last = [None] * len(rows), None
    for _ in range(MAX_WALK):
        state, before = ctx.entry, list(ins)
        for i, (_, keys, _) in enumerate(rows):
            if i:
                state = boundary(state, ctx)
            ins[i] = state = join(ins[i] if i == loop else None, state)
            state = apply(state, (f for k, v in keys for f in facts(k, v, slots)))
        last = boundary(state, ctx)
        if loop is not None:
            ins[loop] = join(ins[loop], last)
        if ins == before:
            break
    return [[h, kept(keys, ins[i], slots, listen), s] for i, (h, keys, s) in enumerate(rows)]


def kept(keys: tuple, state: dict, slots: dict, listen: set) -> tuple:
    """The keys of a row that set something ``state`` does not already hold, and of each
    compiled key a slot's ``row.`` event listens for, its first where all would go."""
    same = [
        bool(fs := facts(k, v, slots)) and all(state.get(n) == {x} for n, x in fs) for k, v in keys
    ]
    for name in {compiled(k) for k, _ in keys}:
        mine = [i for i, (k, _) in enumerate(keys) if compiled(k) == name]
        if f"row.{name}" in listen and all(same[i] for i in mine):
            same[mine[0]] = False
    if "row" in listen:
        rowed = [i for i, (k, _) in enumerate(keys) if compiled(k) not in ("ctrl", "gate")]
        if rowed and all(same[i] for i in rowed):
            same[rowed[0]] = False
    return tuple(kv for kv, s in zip(keys, same) if not s)


def compiled(key: str) -> str:
    """The player's key of a row key: its cell, ``ctrl`` for ``mask``, ``route`` for both
    route forms."""
    cell = key.rpartition(".")[2]
    return cell if cell in CELLS else COMPILED.get(key, key)


def interfere(ctx: dict, slots: dict) -> dict:
    """``ctx`` with what a program's own pulse programs set, and its shared cells' slots
    others bind, unknown between its rows."""
    binders = defaultdict(set)
    for form, c in ctx.items():
        for _, keys in form[0]:
            for k, val in keys:
                if k == "run" and isinstance(val, d.Run):
                    rows = val.program.rows
                    c.tops |= {n for r in rows for kv in r.keys for n, _ in facts(*kv, slots)}
                elif k.rpartition(".")[2] in SHARED:
                    binders[k] |= c.voices
    for form, c in ctx.items():
        c.tops |= {k for _, keys in form[0] for k, _ in keys if len(binders.get(k, ())) > 1}
    return ctx


# ---- the document -------------------------------------------------------------------------


class Builder:  # pylint: disable=too-many-instance-attributes
    """One tracer's document."""

    def __init__(self, tracer, options=OPTIONS):
        self.tracer, self.head, self.stated = tracer, tracer.head(), "stated" in options
        self.ins = []
        self.slots = {cell: tuple(s.name for s in slots) for cell, slots in self.head["slots"]}
        self.listen = {
            re.sub(r"[-+]\d+$", "", e)
            for _, slots in self.head["slots"]
            for s in slots
            for e in (s.reset or ()) + (s.hold or ())
        }
        self.binds = {
            name: tuple(f for k, v in ins.fields for f in facts(k, v, self.slots))
            for name, ins in self.head["instruments"].items()
        }
        self.walks, self.forms = {}, {}

    def program(self, pos, voice: int) -> tuple:
        """The tidy program walked from ``pos`` on ``voice``, by content."""
        if (pos, voice) not in self.walks:
            rows, loop = tidy(*walk(self.tracer, pos, voice))
            self.walks[pos, voice] = form = content(rows, loop)
            self.forms.setdefault(form, (rows, loop))
        return self.walks[pos, voice]

    def contexts(self, cells: dict, init: tuple) -> dict:
        """Each program's :class:`Context` over the trace's executions."""
        ctx, ins = defaultdict(Context), [e.ins for e in init]
        current = [None] * len(init)
        for (_, v), (note, pos, cmds) in sorted(cells.items()):
            binds = self.binds.get(ins[v] if note is None or note.ins is None else note.ins, ())
            names = {n for k, val in cmds for n, _ in facts(k, val, self.slots)}
            if pos is not None:
                current[v] = form = self.program(pos, v)
                c = ctx[form]
                c.voices.add(v)
                entry = noted({}, binds) if note is not None and not note.legato else {}
                c.entry = join(c.entry, {n: x for n, x in entry.items() if n not in names})
            elif current[v] is not None:
                c = ctx[current[v]]
                c.tops |= names
                if note is not None:
                    c.notes.add(binds)
            if note is not None and note.ins is not None:
                ins[v] = note.ins
        return interfere(ctx, self.slots)

    def build(self) -> Built:
        """The document of the trace to its loop."""
        init = self.tracer.init()
        steps, loop, end, cells = collect(self.tracer)
        ctx = self.contexts(cells, init)
        final = {
            form: content(
                *tidy(plain(*self.forms[form], c, self.slots, self.listen), self.forms[form][1])
            )
            for form, c in ctx.items()
        }
        bounds = [0, *steps, end]
        self.ins = [e.ins for e in init]
        timeline = [
            tuple(self.phrase(cells, v, a, b, final) for v in range(len(init)))
            for a, b in zip(bounds, bounds[1:])
        ]
        return self.document(tuple(self.event(e) for e in init), timeline, loop + 1)

    @staticmethod
    def event(e: d.Event, form=None) -> d.Event:
        """``e`` with each inline program as its content key, and a start of ``form``."""
        commands = [
            (k, (v.kind, inline(v.program)) if isinstance(v, d.Run) else v) for k, v in e.commands
        ]
        return d.replace(e, commands=tuple(commands) + ((("run", ("ctrl", form)),) if form else ()))

    def phrase(self, cells: dict, v: int, a: int, b: int, final: dict) -> tuple:
        """Voice ``v``'s events of ticks ``a`` to ``b``, one a tick, rests folded; where
        ``stated``, the first note states the voice's instrument."""
        out, stated = [], not self.stated
        for t in range(a, b):
            note, pos, cmds = cells.get((t, v), (None, None, ()))
            if note is None:
                event = d.Event("-", None, tuple(cmds))
            else:
                self.ins[v] = note.ins or self.ins[v]
                event = d.Event(
                    note.note, note.ins or (None if stated else self.ins[v]), tuple(cmds)
                )
                stated = True
            event = self.event(event, None if pos is None else final[self.walks[pos, v]])
            if out and event == d.Event("-") and out[-1].note == "-" and not out[-1].commands:
                out[-1] = d.replace(out[-1], steps=(out[-1].steps or 1) + 1)
            else:
                out.append(event)
        return tuple(out)

    def document(self, init: tuple, timeline: list, loop: int) -> Built:
        """Name every program and phrase in order of first use and print the document."""
        names, records = {}, defaultdict(dict)

        def name(kind: str, key, body) -> str:
            if (kind, key) not in names:
                names[kind, key] = f"{PREFIX[kind]}{sum(k == kind for k, _ in names) + 1}"
                records[kind][names[kind, key]] = body()
            return names[kind, key]

        def ref(value):
            if isinstance(value, d.Run):
                value = value.kind, inline(value.program)
            if not isinstance(value, tuple):
                return value
            kind, (rows, loop) = value
            return name(
                kind,
                value[1],
                lambda: program(
                    [(h, tuple((k, ref(v) if k == "run" else v) for k, v in ks)) for h, ks in rows],
                    loop,
                ),
            )

        def events(evs) -> tuple:
            return tuple(
                d.replace(
                    e, commands=tuple((k, ref(v) if k == "run" else v) for k, v in e.commands)
                )
                for e in evs
            )

        init = events(init)
        rows = [
            d.SongRow(tuple(d.Play(name("phrase", evs, lambda e=evs: events(e))) for evs in row))
            for row in timeline
        ]
        head = self.head
        defs = [("ins", head["instruments"])]
        defs += [(k, records[k]) for k in ("ctrl", "pw", "filter", "phrase") if records[k]]
        doc = d.Document(
            d.Header(
                meta=head["meta"],
                clock=head["clock"],
                voices=None if len(init) == 3 else len(init),
                tempo=head.get("tempo", 1),
                slots=head["slots"],
                state=head["state"],
            ),
            head["tables"],
            (),
            defs,
            init,
            rows,
            loop,
        )
        doc = d.replace(doc, mods=mods(d.printed(doc), head["code"]))
        return Built(doc, d.printed(doc))


def inline(prog: d.Program) -> tuple:
    """An inline program's content key."""
    return (
        tuple((r.get("hold", 1), tuple(kv for kv in r.keys if kv[0] != "hold")) for r in prog.rows),
        prog.loop,
    )


def mods(body: str, templates: dict) -> tuple:
    """Each code template ``body`` calls, by its signature, in first-call order."""
    first = {}
    for name in templates:
        found = re.search(rf"(?<![a-z0-9_.]){name}\(", body)
        if found:
            first[name] = found.start()
    return tuple(
        (d.signature(name, code.params(code.read(templates[name]))), d.Code.of(templates[name]))
        for name in sorted(first, key=first.get)
    )


def build(tracer, options=OPTIONS) -> Built:
    """The document of ``tracer``'s song, with builder ``options``."""
    return Builder(tracer, options).build()


def verify(
    built: Built, reference, raw: bytes, subtune: int, frames: int
) -> tuple:  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """``(cells, text, traps)``: ``built``'s grid against ``reference(raw, subtune, ticks)``'s."""
    ours = play(built.text, ticks=frames)
    theirs, found = reference(raw, subtune, len(ours))
    return gridcmp.cell_diff(ours, np.asarray(theirs, dtype=np.uint8)), built.text, found


class DocumentPlayer:
    """A document played by unitracker-player as :mod:`pysidtracker.whydiff` drives a player:
    ``play_frame`` steps a tick, ``regs`` holds the registers it wrote."""

    def __init__(self, text: str):
        self.text, self.player = text, Player(load(text))

    def play_frame(self) -> None:
        """Play one tick."""
        self.player.step()

    def render_grid(self, frames: int) -> np.ndarray:
        """The document's registers over ``frames`` ticks."""
        return play(self.text, ticks=frames)


def explain(text: str, raw: bytes, reference, subtune: int = 0, frames: int = 3000):
    """:func:`pysidtracker.whydiff.explain_divergence` of ``text``'s first cell differing from
    ``reference`` (a grid), traced through unitracker-player; None where none differs."""
    return explain_divergence(
        raw,
        lambda _: DocumentPlayer(text),
        reference,
        song=subtune + 1,
        frames=frames,
        label="document",
        cell=lambda p, register: (p.player.regs, register),
        roots=[os.path.dirname(unitracker_player.__file__)],
        state=True,
    )
