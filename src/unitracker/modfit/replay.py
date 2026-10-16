"""A song played with its modulated cells driven by their phase and value expressions.

Each unit's generated firing, start hook and pre/post functions are wrapped in the compiled
player. A start opens an occurrence at the next firing from the cell's incoming value and phase
0; each firing applies ``v' = f(v, φ)``, ``φ' = φ + a`` and, when overriding, writes ``v'``.
"""

import dataclasses

from tqdm import tqdm

from unitracker.modfit import grammar as G
from unitracker.vm import SongPlayer

SENTINEL = -(1 << 40)


@dataclasses.dataclass
class Occurrence:  # pylint: disable=too-many-instance-attributes
    """One run of a unit's expressions on a voice."""

    unit: str
    voice: int
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
    key: tuple | None = None


def pair_parts(cell: str) -> tuple:
    """``(hi, lo, k)`` of a pair name, or ``(cell, None, 0)``."""
    parts = cell.split(":")
    if len(parts) == 1:
        return cell, None, 0
    return parts[0], parts[1], int(parts[2]) if len(parts) > 2 else 8


class Cells:
    """Reads and writes of a compiled player's cells, pairs as one value."""

    def __init__(self, player: SongPlayer):
        self.player = player
        self.gindex, self.vindex = player._index  # pylint: disable=protected-access

    def slot(self, name: str, voice):
        """``(list, index)`` of a cell in a voice's or the global list."""
        if voice is not None and name in self.vindex:
            return self.player.voice_cells[voice], self.vindex[name]
        return self.player.globals, self.gindex[name]

    def get(self, name: str, voice):
        """A cell's value; a pair's ``hi << k | lo & (2**k - 1)``."""
        hi, lo, k = pair_parts(name)
        h = self._get(hi, voice)
        return h if lo is None else (h << k) | (self._get(lo, voice) & ((1 << k) - 1))

    def _get(self, name, voice):
        cells, i = self.slot(name, voice)
        return cells[i]

    def set(self, name: str, voice, value: int) -> None:
        """Store a value, a pair split into its parts."""
        hi, lo, k = pair_parts(name)
        if lo is None:
            self._set(hi, voice, value)
            return
        self._set(hi, voice, value >> k)
        self._set(lo, voice, value & ((1 << k) - 1))

    def _set(self, name, voice, value):
        cells, i = self.slot(name, voice)
        cells[i] = value

    def snapshot(self, voice) -> dict:
        """Every global cell and the voice's cells by name."""
        out = {n: self.player.globals[i] for n, i in self.gindex.items()}
        if voice is not None:
            cells = self.player.voice_cells[voice]
            out.update({n: cells[i] for n, i in self.vindex.items()})
        return out


class Replay:
    """One song's player with every unit's occurrences tracked and optionally overridden.

    ``derive(unit, occurrence)`` returns the occurrence's derived expressions (or None);
    ``override(occurrence)`` says whether its values are written into the player.
    """

    # pylint: disable=too-many-instance-attributes

    def __init__(self, song: dict, units: list, derive, override=lambda occ: False, note=None):
        self.player = SongPlayer(song)
        self.note = note or (lambda cells, voice: 0)
        self.cells = Cells(self.player)
        self.units, self.derive, self.override = units, derive, override
        self.ns = self.player._entry[1].__globals__  # pylint: disable=protected-access
        self.voice_of = {id(v): i for i, v in enumerate(self.player.voice_cells)}
        self.active, self.pending, self.pre = {}, {}, {}
        self.occurrences = []
        self.tick = 0
        for unit in units:
            self.wrap(unit["pre"], lambda args, unit=unit: self.on_pre(unit, args), None)
            self.wrap(
                unit.get("post", unit["pre"]),
                None,
                lambda args, unit=unit: self.on_post(unit, args),
            )
            for hook in unit.get("hooks") or []:
                self.wrap_hook(unit, hook)

    # wrapping ----------------------------------------------------------------------
    def functions(self, ref: str) -> list:
        """``(container, key, function, row)`` of every generated function named by ``ref``."""
        parts = ref.split(".")
        if parts[0] == "procs":
            return [
                (self.ns, n, self.ns[n], None)
                for n in (f"f_v_proc_{parts[1]}", f"f_g_proc_{parts[1]}")
                if n in self.ns
            ]
        out = []
        for scope in "vg":
            table = self.ns.get(f"D_{scope}_{parts[1]}_{parts[2]}")
            for k, (fn, row) in (table or {}).items():
                if len(parts) < 4 or int(parts[3]) == k:
                    out.append((table, k, fn, row))
        return out

    def wrap(self, ref: str, before, after) -> None:
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
        if v is not None:
            return self.voice_of[id(v)]
        return None

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

    def wrap_hook(self, unit: dict, hook: dict) -> None:
        """A start hook: mark a restart when the hook writes ``written`` or changes ``key``."""
        state = {}

        def before(args):
            voice = self.voice(args)
            state["pre"] = self.cells.snapshot(voice)
            state["key"] = tuple(self.cells.get(c, voice) for c in hook.get("key") or [])
            state["saved"] = {}
            for c in (hook.get("written") or []) + (hook.get("pointer") or []):
                state["saved"][c] = self.cells.get(c, voice)
                self.cells.set(c, voice, SENTINEL)

        def after(args):
            voice = self.voice(args)
            written = set()
            for c, old in state["saved"].items():
                if self.cells.get(c, voice) == SENTINEL:
                    self.cells.set(c, voice, old)
                else:
                    written.add(c)
            key = tuple(self.cells.get(c, voice) for c in hook.get("key") or [])
            moved = written & set(hook.get("pointer") or [])
            if written - moved or key != state["key"] or (moved and self.relevant(unit, voice)):
                row = args[1] if len(args) > 1 else None
                self.restart(unit, voice, (hook["at"], row, state["pre"]))

        self.wrap(hook["at"], before, after)

    # events -------------------------------------------------------------------------
    def restart(self, unit: dict, voice, hook: tuple) -> None:
        """Mark ``voice``'s occurrence of ``unit`` to start again at its next firing."""
        self.pending[(unit["name"], None if unit.get("scope") == "global" else voice)] = (
            voice,
            hook,
        )

    def relevant(self, unit: dict, voice) -> bool:
        """True when the unit's program from its pointer sets one of its fields."""
        prog = unit.get("program")
        if not prog:
            return True
        song = self.player.song
        rows = (song.get("records") or {}).get(prog["record"]) or {}
        rtype = song["playroutine"]["record_types"][prog["record"]]
        fields = rtype.get("fields") or {}
        row, seen = self.cells.get(prog["pointer"], voice), set()
        while row in rows and row not in seen:
            seen.add(row)
            full = {**(fields if isinstance(fields, dict) else {}), **rows[row]}
            if any(full.get(f, -1) >= 0 for f in prog["fields"]):
                return True
            if full.get("dl", 0) >= 0x80:
                break
            row = full.get(rtype.get("chain", "next"), 0)
        return False

    def on_pre(self, unit: dict, args) -> None:
        """Before a firing: the start snapshot and the check for writes between firings."""
        owner = self.owner(unit, args)
        if owner is None:
            return
        key, voice = owner
        name = (unit["name"], key)
        self.pre[name] = (voice, self.cells.snapshot(voice))
        occ = self.active.get(name)
        if occ is not None and name not in self.pending and occ.crossed is None:
            now = self.cells.get(unit["cell"], voice)
            if now != occ.v and occ.firings:
                occ.crossed = (self.tick, occ.phi, occ.v, now)

    def on_post(self, unit: dict, args) -> None:
        """After a firing: start or continue the occurrence and apply its expressions."""
        owner = self.owner(unit, args)
        if owner is None:
            return
        key, voice = owner
        name = (unit["name"], key)
        if name not in self.pre:
            return
        pre_voice, snap = self.pre.pop(name)
        if voice is None:
            voice = pre_voice
        if name in self.pending:
            self.open(unit, name, voice, snap)
        occ = self.active.get(name)
        if occ is None or occ.derived is None:
            return
        native = self.cells.get(unit["cell"], voice)
        v, phi = occ.v, occ.phi
        occ.v, occ.phi = occ.derived.step(v, phi, self.note(self.cells, voice))
        occ.firings += 1
        if occ.v != native and occ.mismatch is None:
            occ.mismatch = (self.tick, phi, v, native, occ.v)
        if self.override(occ):
            self.cells.set(unit["cell"], voice, occ.v)
            mirror = unit.get("mirror")
            if mirror and self.cells.get(mirror, voice) == native:
                self.cells.set(mirror, voice, occ.v)

    def open(self, unit: dict, name: tuple, voice, snap: dict) -> None:
        """Start a pending occurrence from its pre-firing snapshot."""
        hvoice, hook = self.pending.pop(name)
        occ = Occurrence(
            unit["name"],
            hvoice if hvoice is not None else voice,
            self.tick,
            tuple(snap.get(c) for c in unit.get("label") or []),
            snap,
            hook,
        )
        occ.v = G_value(snap, unit["cell"])
        occ.derived = self.derive(unit, occ)
        self.active[name] = occ
        self.occurrences.append(occ)

    def run(self, ticks: int, desc: str = "replay", position: int = 0):
        """The register grid of ``ticks`` ticks."""
        grid = []
        for self.tick in tqdm(range(ticks), desc=desc, unit="tick", leave=False, position=position):
            self.player.play_frame()
            grid.append(list(self.player.regs))
        return grid


def G_value(snap: dict, cell: str) -> int:  # pylint: disable=invalid-name
    """A cell's value in a snapshot, a pair joined."""
    hi, lo, k = pair_parts(cell)
    return snap[hi] if lo is None else (snap[hi] << k) | (snap[lo] & ((1 << k) - 1))


class Stepper:
    """Compiled ``(v, φ) -> (v', φ')`` of derived expressions over a player's tables."""

    def __init__(self, phase, value, tables: dict, freq: dict, offset: int):
        self.funcs = {}
        if "word" in freq:
            word = tables[freq["word"]]
            self.funcs["F"] = lambda k: _at(word, k + offset)
        else:
            hi, lo = tables[freq["hi"]], tables[freq["lo"]]
            self.funcs["F"] = lambda k: (_at(hi, k + offset) << 8) | _at(lo, k + offset)
        for name, data in tables.items():
            self.funcs.setdefault(name, lambda k, data=data: _at(data, k))
        self.f = G.compiled(value)
        self.a = G.compiled(phase)
        self.note = 0

    def step(self, v: int, phi: int) -> tuple:
        """``(f(v, φ), φ + a(v, φ))``."""
        return (
            self.f(v, phi, self.note, **self.kw(self.f)),
            phi + self.a(v, phi, self.note, **self.kw(self.a)),
        )

    def kw(self, fn) -> dict:
        """The table callables ``fn`` takes."""
        names = fn.__code__.co_varnames[3 : fn.__code__.co_argcount]
        return {n: self.funcs[n] for n in names}


def _at(table, i):
    return table[i] if 0 <= i < len(table) else 0
