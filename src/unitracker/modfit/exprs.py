"""The modulators of a song as phase and value expressions: per-start derivations and proofs.

Each unit's start control state is derived once (:mod:`unitracker.modfit.phase`); songs are
lifted with their native modulations, which :mod:`unitracker.modfit.convert` rewrites.
"""

import dataclasses

from unitracker.modfit import grammar as G
from unitracker.modfit.form import atoms
from unitracker.modfit.phase import Derived, Lift, Machine, derive, dispatch
from unitracker.modfit.replay import Stepper
from unitracker.player.modulation import INIT, declared
from unitracker.playroutine.schema import run_of

# ---- derivation --------------------------------------------------------------------------

MISSING = ("<missing>",)


def refusal(exc: BaseException) -> str:
    """The text of a failure to derive a start: the message, named when it is not a ValueError."""
    return str(exc) if isinstance(exc, ValueError) else f"{type(exc).__name__}: {exc}"


@dataclasses.dataclass
class Result:
    """One start's derived expressions with what it was derived from."""

    derived: object
    start: tuple
    v0: object
    stepper: Stepper | None = None
    start_value: object = None

    def step(self, v: int, phi: int, n: int) -> tuple:
        """``(v', φ')`` of one firing."""
        self.stepper.note = n
        return self.stepper.step(v, phi)


class Analysis:  # pylint: disable=too-many-instance-attributes
    """The units of one song: machines and derivations cached by start."""

    def __init__(self, ir: dict):
        self.ir = ir
        self.spec = declared(ir["playroutine"])
        self.units = self.spec["units"]
        self.freq = self.spec["freq"]
        self.machines = {u["name"]: Machine(ir, u, self.spec) for u in self.units}
        self.offset = int(ir["playroutine"].get("note_offset", 0))
        self.tables = next(iter(self.machines.values())).form.pr["tables"]
        self.cache = {}
        self.laid, self.values, self.keys = {}, {}, {}

    def unit(self, name: str) -> dict:
        """The unit declaration named ``name``."""
        return next(u for u in self.units if u["name"] == name)

    def shift(self, cells: dict) -> int:
        """The internal note less ``N``: the note offset less the voice's transpose."""
        return self.offset - int(cells.get(self.spec.get("transpose"), 0) or 0)

    def note_term(self, cells: dict):
        """The internal note cell of sounding format note ``N``."""
        k = self.shift(cells)
        return ("lin", k, (("N", 1),)) if k else "N"

    def overlay(self, machine: Machine, hook: tuple, start: dict) -> dict:
        """Cells the start hook writes from the note, as terms of ``N`` at the start's transpose.

        Its symbolic paths run once per hook, note term and value of each cell they read.
        """
        at, row, pre = hook
        term = self.note_term(start)
        state = {k: v for k, v in pre.items() if isinstance(v, int) and k not in machine.parts}
        state.update(dict.fromkeys(self.spec["notes"], term))
        known = self.laid.setdefault((machine.unit["name"], repr(at), row, term), {})
        for cells, outs in known.items():
            got = outs.get(tuple(state.get(c, MISSING) for c in cells))
            if got is not None:
                return got
        with machine.form.tracking() as reads:
            out = self._overlay(machine, at, row, state)
        cells = tuple(sorted(reads))
        known.setdefault(cells, {})[tuple(state.get(c, MISSING) for c in cells)] = out
        return out

    def _overlay(self, machine: Machine, at: dict, row, state: dict) -> dict:
        """Uncached ``overlay`` from the hook's state."""
        if "proc" in at:
            actions = [{"call": at["proc"]}]
        else:
            index = row if "type" in at else at.get("index")
            if not isinstance(index, int):
                return {}
            actions = [{"run": {**run_of(at), "index": int(index)}}]
        try:
            paths = machine.form.paths(dispatch(machine.form.pr, actions), (), state)
        except (ValueError, KeyError):
            return {}
        if len(paths) != 1:
            return {}
        final = paths[0][1]
        out = {c: t for c, t in final.items() if not isinstance(t, int) and "N" in atoms(t)}
        if machine.chip:
            stored = [c for c in machine.parts - {machine.cell} if final.get(c, c) != c]
            written = machine.form.wrap(
                machine.cell, machine.form.expr(machine.chip["value"], final)
            )
            if stored and not atoms(written) & machine.parts:
                out[machine.cell] = written
            return out
        written = final.get(machine.cell, machine.cell)
        if machine.cell not in machine.form.pair and written != machine.cell:
            out[machine.cell] = written
        return out

    def key(self, unit: dict, occ) -> tuple | None:
        """``(unit, start control, start value term)`` of an occurrence, or None (``occ.error``).

        The start hook's cells written from the note become terms of ``N`` where they still
        hold their values at the first firing. Computed once per hook overlay, start cells it
        reads and first value.
        """
        try:
            machine = self.machines[unit["name"]]
            lay = (
                self.overlay(machine, occ.hook, occ.start) if occ.hook and occ.hook != INIT else {}
            )
        except (ValueError, TypeError, RecursionError) as exc:
            occ.error = refusal(exc)
            return None
        cells = self.start_cells(machine, unit)
        sig = (
            unit["name"],
            frozenset(lay.items()),
            tuple(occ.start.get(c, MISSING) for c in cells),
        )
        sig += (occ.v,)
        if sig not in self.keys:
            self.keys[sig] = self._key(unit, machine, lay, occ)
        key, error = self.keys[sig]
        if error:
            occ.error = error
        return key

    def start_cells(self, machine: Machine, unit: dict) -> list:
        """The start cells a key reads besides its hook's: those the unit's list reads or keeps,
        the notes, the transpose and the relative cells."""
        return sorted(
            machine.reads
            | set(machine.control)
            | set(self.spec["notes"])
            | set(unit.get("relative") or [])
            | {self.spec.get("transpose")} - {None}
        )

    def _key(self, unit: dict, machine: Machine, lay: dict, occ) -> tuple:
        """``(key, error)`` of an occurrence with its hook's overlay ``lay``."""
        try:
            values = dict(occ.start)
            for c in self.spec["notes"]:
                values[c] = self.note_term(occ.start)
            n = occ.start.get(self.spec["notes"][0], 0) - self.shift(occ.start)
            for c, t in lay.items():
                if c in machine.reads or c in machine.control:
                    if self.evaluate(machine, t, n) == occ.start.get(c):
                        values[c] = t
            for c in unit.get("relative") or []:
                if c not in lay and (c in machine.reads or c in machine.control):
                    k = int(occ.start[c]) - n
                    values[c] = ("lin", k, (("N", 1),)) if k else "N"
            v0 = lay.get(machine.cell)
            if v0 is not None and self.evaluate(machine, v0, n) != occ.v:
                v0 = None
            return (unit["name"], machine.start(values), v0), ""
        except (ValueError, TypeError, RecursionError) as exc:
            return None, refusal(exc)

    def derived(self, key: tuple):
        """The proved expressions of a start key."""
        name, start, v0 = key
        machine = self.machines[name]
        lift = Lift(machine.form, machine.cell, self.freq)
        try:
            v0e = None if v0 is None else lift.fold(lift.term(v0))
            return derive(machine, start, v0e, self.freq), v0e
        except (ValueError, TypeError, RecursionError) as exc:
            return Derived(error=refusal(exc)), None

    def result(self, key: tuple, found: tuple) -> Result:
        """A start's result with a stepper over this song's tables."""
        d, v0e = found
        res = Result(d, key[1], key[2], start_value=v0e)
        if d.value is not None:
            res.stepper = Stepper(*d.update, self.tables, self.freq, self.offset)
        return res

    def derive(self, unit: dict, occ):
        """The derivation of an occurrence's start, cached by key (for in-process replays)."""
        key = self.key(unit, occ)
        occ.key = key
        if key is None:
            return None
        if key not in self.cache:
            self.cache[key] = self.result(key, self.derived(key))
        res = self.cache[key]
        return res if res.stepper is not None else None

    def evaluate(self, machine: Machine, term, n: int):
        """A term of ``N`` at note ``n``, once per term and note."""
        key = (machine.unit["name"], term, n)
        if key not in self.values:
            self.values[key] = self._evaluate(machine, term, n)
        return self.values[key]

    def _evaluate(self, machine: Machine, term, n: int):
        """Uncached ``evaluate``."""
        lift = Lift(machine.form, machine.cell, self.freq)
        e = lift.fold(lift.term(term))
        if e.free_symbols - {G.N}:
            return None
        st = Stepper(0, e, self.tables, self.freq, self.offset)
        st.note = n
        return st.step(0, 0)[0]
