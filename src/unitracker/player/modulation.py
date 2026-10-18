"""A song's modulator units: what starts each one, its firings, and the expression driving it.

A start opens an occurrence at the unit's next firing (its first to last ``fire`` entry). While an
expression is active each firing sets ``v' = f(v, φ, N)`` from the cell's value before the
firing, then ``φ += 1``; otherwise the playroutine's own firing stands. A unit with a ``chip``
map drives the chip register value its cells give (``value``); an expression's result is stored
through ``store`` when the cells do not already give it.
"""

import dataclasses

from unitracker.format.codec import command_code, evaluate
from unitracker.format.records import EXPR, record_types

VALUE = "value"


@dataclasses.dataclass
class Occurrence:  # pylint: disable=too-many-instance-attributes
    """One run of a unit on a voice, from the firing after a start to the next start."""

    unit: str
    voice: int | None
    tick: int
    label: tuple
    start: dict
    hook: tuple
    derived: object = None
    v: int = 0
    phi: int = 0
    firings: int = 0
    mismatch: tuple | None = None
    crossed: tuple | None = None
    error: str = ""
    key: object = None
    v_in: int = 0
    first: int | None = None


def pair_parts(cell: str) -> tuple:
    """``(hi, lo, k)`` of a pair name ``hi:lo[:k]`` (k 8 when omitted), or ``(cell, None, 0)``."""
    parts = cell.split(":")
    if len(parts) == 1:
        return cell, None, 0
    return parts[0], parts[1], int(parts[2]) if len(parts) > 2 else 8


def joined(snap: dict, cell: str) -> int:
    """A cell's value in a snapshot of cells by name, a pair joined."""
    hi, lo, k = pair_parts(cell)
    return snap[hi] if lo is None else (snap[hi] << k) | (snap[lo] & ((1 << k) - 1))


class Cells:
    """Reads and writes of a running player's cells by name, a pair as one value."""

    def __init__(self, player):
        self.player = player
        self.gindex, self.vindex = player._index  # pylint: disable=protected-access
        self.masks = player._masks  # pylint: disable=protected-access

    def slot(self, name: str, voice):
        """``(list, index, mask)`` of a cell in a voice's or the global list."""
        if voice is not None and name in self.vindex:
            return self.player.voice_cells[voice], self.vindex[name], self.masks[1].get(name)
        return self.player.globals, self.gindex[name], self.masks[0].get(name)

    def get(self, name: str, voice) -> int:
        """A cell's value; a pair's ``hi << k | lo & (2**k - 1)``."""
        hi, lo, k = pair_parts(name)
        h = self._get(hi, voice)
        return h if lo is None else (h << k) | (self._get(lo, voice) & ((1 << k) - 1))

    def _get(self, name, voice):
        cells, i, _ = self.slot(name, voice)
        return cells[i]

    def set(self, name: str, voice, value: int) -> None:
        """Store a value, a pair split into its parts, each wrapped to its width."""
        hi, lo, k = pair_parts(name)
        if lo is None:
            self._set(hi, voice, value)
            return
        self._set(hi, voice, value >> k)
        self._set(lo, voice, value & ((1 << k) - 1))

    def _set(self, name, voice, value):
        cells, i, mask = self.slot(name, voice)
        cells[i] = value if mask is None else value & mask

    def snapshot(self, voice) -> dict:
        """Every global cell and the voice's cells by name."""
        out = {n: self.player.globals[i] for n, i in self.gindex.items()}
        if voice is not None:
            cells = self.player.voice_cells[voice]
            out.update({n: cells[i] for n, i in self.vindex.items()})
        return out


def declared(playroutine: dict) -> dict:
    """A playroutine's ``modulators`` declaration."""
    return playroutine.get("modulators") or {}


def units(playroutine: dict) -> list:
    """A playroutine's declared modulator units."""
    return list(declared(playroutine).get("units") or [])


def function(item) -> dict:
    """The function a ``fire`` entry names: ``{proc}``, ``{type, template}`` or ``{record, field}``
    (see the schema's modulator declarations); an entry that is no proc name or ``run`` is one."""
    if isinstance(item, str):
        return {"proc": item}
    if "run" not in item:
        return item
    run = item["run"]
    if "template" in run:
        return {"type": run["record"], "template": run["template"]}
    return {"record": run["record"], "field": run["field"]}


def run_of(ref: dict) -> dict:
    """The ``run`` spec, less its index, of a template or record function."""
    if "type" in ref:
        return {"record": ref["type"], "template": ref["template"]}
    return {"record": ref["record"], "field": ref["field"]}


def unit_command(playroutine: dict, unit: dict) -> int | None:
    """The code of the command naming a unit's expression (``!freq``): for a unit that replaces
    native ``commands``, the playroutine's command named its key that references ``x``."""
    if not unit.get("commands"):
        return None
    return command_code(playroutine.get("commands"), unit["key"], EXPR)


def watched(playroutine: dict) -> set:
    """The cells whose writes a unit's start hooks detect."""
    out = set()
    for unit in units(playroutine):
        for hook in unit.get("hooks") or []:
            out |= set(hook.get("written") or []) | set(hook.get("pointer") or [])
    return out


class Modulation:  # pylint: disable=too-many-instance-attributes
    """The units of a running player: starts, firings and the expressions driving their cells.

    ``select(unit, occurrence)`` gives an opened occurrence a stepper whose ``step(v, φ, N)``
    returns ``(v', φ')``, or None to leave the playroutine's own firing in place.
    """

    def __init__(self, player, select, override=lambda occ: True, keep=True):
        self.player = player
        pr = player.song["playroutine"]
        self.units = units(pr)
        self.rtypes = record_types(pr)
        self.cells = Cells(player)
        self.select, self.override = select, override
        self.ns = player.namespace
        self.writes = self.ns["_W"]
        self.note = self.ns.get("_n")
        self.voice_of = {id(v): i for i, v in enumerate(player.voice_cells)}
        self.active, self.pending, self.pre = {}, {}, {}
        self.occurrences, self.keep = [], keep
        self.chips = {}
        for i, unit in enumerate(self.units):
            if unit.get("chip"):
                cells = list(unit["chip"]["store"])
                stores = [(c, self.ns[f"_cs_{i}_{k}"]) for k, c in enumerate(cells)]
                self.chips[unit["name"]] = (self.ns[f"_cv_{i}"], stores)
        for unit in self.units:
            fire = unit["fire"]
            self.wrap(function(fire[0]), lambda args, unit=unit: self.on_pre(unit, args), None)
            self.wrap(function(fire[-1]), None, lambda args, unit=unit: self.on_post(unit, args))
            for hook in unit.get("hooks") or []:
                self.wrap_hook(unit, hook)

    @property
    def tick(self) -> int:
        """The tick being played."""
        return self.player.ticks

    # wrapping ----------------------------------------------------------------------
    def functions(self, ref: dict) -> list:
        """``(container, key, function, row)`` of every generated function named by ``ref``."""
        if "proc" in ref:
            return [
                (self.ns, n, self.ns[n], None)
                for n in (f"f_v_proc_{ref['proc']}", f"f_g_proc_{ref['proc']}")
                if n in self.ns
            ]
        record, name = run_of(ref).values()
        out = []
        for scope in "vg":
            table = self.ns.get(f"D_{scope}_{record}_{name}")
            for k, (fn, row) in (table or {}).items():
                index = ref.get("index")
                if index is None or k in (index if isinstance(index, list) else [index]):
                    out.append((table, k, fn, row))
        return out

    def wrap(self, ref: dict, before, after) -> None:
        """Call ``before(args)`` and ``after(args)`` around every function of ``ref``."""
        for container, key, fn, row in self.functions(ref):

            def wrapped(*args, fn=fn):
                if before:
                    before(args)
                out = fn(*args)
                if after:
                    after(args)
                return out

            container[key] = wrapped if container is self.ns else (wrapped, row)

    def voice(self, args) -> int | None:
        """The voice of a call, or None for a global call."""
        v = args[0] if args and isinstance(args[0], list) else None
        return None if v is None else self.voice_of[id(v)]

    def owner(self, unit: dict, args) -> tuple | None:
        """``(state key, voice)`` of a call, or None for a call of a global unit's other voices.

        A global unit's occurrence belongs to the voice whose hook started it.
        """
        voice = self.voice(args)
        if unit.get("scope") != "global":
            return voice, voice
        name = (unit["name"], None)
        if name in self.pending:
            mine = self.pending[name][0]
        else:
            mine = self.active[name].voice if name in self.active else None
        if voice is not None and mine is not None and voice != mine:
            return None
        return None, voice if voice is not None else mine

    def token(self, voice, cell) -> tuple:
        """The key of a cell's write count."""
        if voice is not None and cell in self.cells.vindex:
            return (id(self.player.voice_cells[voice]), cell)
        return (0, cell)

    def wrap_hook(self, unit: dict, hook: dict) -> None:
        """A start hook: a restart when the hook writes ``written`` or changes ``changed``.

        A written ``pointer`` restarts only when the program it selects sets the unit's fields; a
        hook with none of the three restarts on every call, before it runs.
        """
        state = {}
        watch = list(hook.get("written") or []) + list(hook.get("pointer") or [])
        always = not (watch or hook.get("changed"))

        def before(args):
            voice = self.voice(args)
            state["pre"] = self.cells.snapshot(voice)
            if always:
                self.restart(unit, voice, (hook["at"], None, state["pre"]))
            state["key"] = tuple(self.cells.get(c, voice) for c in hook.get("changed") or [])
            state["counts"] = [self.writes.get(self.token(voice, c), 0) for c in watch]

        def after(args):
            voice = self.voice(args)
            written = {
                c
                for c, n in zip(watch, state["counts"])
                if self.writes.get(self.token(voice, c), 0) != n
            }
            key = tuple(self.cells.get(c, voice) for c in hook.get("changed") or [])
            moved = written & set(hook.get("pointer") or [])
            self.applied(unit, voice)
            if always:
                return
            if written - moved or key != state["key"] or (moved and self.relevant(unit, voice)):
                row = args[1] if len(args) > 1 else None
                self.restart(unit, voice, (hook["at"], row, state["pre"]))

        self.wrap(hook["at"], before, after)

    # events -------------------------------------------------------------------------
    def applied(self, unit: dict, voice) -> None:
        """A start hook of ``unit`` ran on ``voice``, restarting it or not."""

    def fired(self, unit: dict, occ: Occurrence) -> None:
        """``occ`` fired."""

    def restart(self, unit: dict, voice, hook: tuple) -> None:
        """Mark ``voice``'s occurrence of ``unit`` to start again at its next firing."""
        self.pending[(unit["name"], None if unit.get("scope") == "global" else voice)] = (
            voice,
            hook,
        )

    def relevant(self, unit: dict, voice) -> bool:
        """True when the unit's program from its pointer sets one of its program fields."""
        prog = unit.get("program")
        if not prog:
            return True
        rtype = self.rtypes[prog["record"]]
        rows = (self.player.song.get("records") or {}).get(prog["record"]) or {}
        row, seen = self.cells.get(prog["pointer"], voice), set()
        while row in rows and row not in seen:
            seen.add(row)
            full = {**rtype.fields, **rows[row]}
            if any(full[f] != rtype.default(f) for f in prog["fields"]):
                return True
            if rtype.stop is not None and evaluate(rtype.stop, full):
                break
            row = full.get(rtype.chain, 0)
        return False

    def read(self, unit: dict, voice) -> int:
        """The unit's value: its chip map's register value, else its cell's."""
        chip = self.chips.get(unit["name"])
        if chip is None:
            return self.cells.get(unit["cell"], voice)
        cells = self.player.voice_cells[voice] if voice is not None else None
        return chip[0](cells) & ((1 << int(unit["chip"]["width"])) - 1)

    def write(self, unit: dict, voice, value: int) -> None:
        """Store a value in the unit's cells; a chip map's store runs only when they differ."""
        chip = self.chips.get(unit["name"])
        if chip is None:
            self.cells.set(unit["cell"], voice, value)
            return
        if self.read(unit, voice) == value:
            return
        cells = self.player.voice_cells[voice] if voice is not None else None
        for cell, fn in chip[1]:
            self.cells.set(cell, voice, fn(cells, value))

    def note_of(self, voice) -> int:
        """The sounding format note ``N`` of a voice."""
        if voice is None or self.note is None:
            return 0
        return self.note(self.player.voice_cells[voice])

    def on_pre(self, unit: dict, args) -> None:
        """Before a firing: the cell's value, a start's snapshot, writes between firings."""
        owner = self.owner(unit, args)
        if owner is None:
            return
        key, voice = owner
        name = (unit["name"], key)
        snap = self.capture(unit, voice) if name in self.pending else None
        occ = self.active.get(name)
        driven = snap.get("x") if snap is not None else occ is not None and occ.derived
        now = self.read(unit, voice) if self.keep or driven else None
        self.pre[name] = (voice, now, snap)
        if now is not None and occ is not None and snap is None and occ.crossed is None:
            if now != occ.v and occ.firings:
                occ.crossed = (self.tick, occ.phi, occ.v, now)

    def on_post(self, unit: dict, args) -> None:
        """After a firing: open a started occurrence, then apply its expression."""
        owner = self.owner(unit, args)
        if owner is None:
            return
        key, voice = owner
        name = (unit["name"], key)
        if name not in self.pre:
            return
        pre_voice, before, snap = self.pre.pop(name)
        if voice is None:
            voice = pre_voice
        if name in self.pending and snap is not None:
            self.open(unit, name, voice, snap, before)
        occ = self.active.get(name)
        if occ is None:
            return
        self.fired(unit, occ)
        if occ.derived is None:
            if self.keep:
                occ.v = self.read(unit, voice)
                occ.first = occ.v if occ.firings == 0 else occ.first
            occ.firings += 1
            return
        native = self.read(unit, voice)
        phi = occ.phi
        occ.v, occ.phi = occ.derived.step(before, phi, self.note_of(voice))
        occ.firings += 1
        if occ.v != native and occ.mismatch is None:
            occ.mismatch = (self.tick, phi, before, native, occ.v)
        if self.override(occ):
            cell = self.cells.get(unit["cell"], voice)
            self.write(unit, voice, occ.v)
            occ.v = self.read(unit, voice)
            mirror = unit.get("mirror")
            if mirror and self.cells.get(mirror, voice) == cell:
                self.cells.set(mirror, voice, self.cells.get(unit["cell"], voice))

    def capture(self, unit: dict, voice) -> dict:  # pylint: disable=unused-argument
        """What a start keeps of the cells before its first firing: every cell by name."""
        return self.cells.snapshot(voice)

    def open(self, unit: dict, name: tuple, voice, snap: dict, before: int) -> None:
        """Start a pending occurrence from its pre-firing snapshot and cell value."""
        hvoice, hook = self.pending.pop(name)
        occ = Occurrence(
            unit["name"],
            hvoice if hvoice is not None else voice,
            self.tick,
            tuple(snap.get(c) for c in unit.get("label") or []),
            snap,
            hook,
        )
        occ.v = occ.v_in = before
        occ.derived = self.select(unit, occ)
        self.active[name] = occ
        if self.keep:
            self.occurrences.append(occ)

    def state(self) -> tuple:
        """Each active occurrence's unit, voice, expression and phase."""
        return tuple(
            (name[0], occ.voice, repr(occ.key), occ.phi)
            for name, occ in sorted(self.active.items(), key=lambda kv: repr(kv[0]))
        )
