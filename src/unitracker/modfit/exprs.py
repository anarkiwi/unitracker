"""The modulators of a song as phase and value expressions: per-start derivations and proofs.

Each unit's start control state is derived once (:mod:`unitracker.modfit.phase`); songs are
lifted with their native modulations, which :mod:`unitracker.modfit.convert` rewrites.
"""

import concurrent.futures
import dataclasses
import fcntl
import hashlib
import multiprocessing
import os
from pathlib import Path

from tqdm import tqdm

from unitracker.modfit import grammar as G
from unitracker.modfit.form import atoms
from unitracker.modfit.phase import Lift, Machine, derive, dispatch
from unitracker.modfit.replay import Stepper
from unitracker.player.modulation import declared

# ---- derivation --------------------------------------------------------------------------

CHUNK = 256


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
        """Cells the start hook writes from the note, as terms of ``N`` at the start's transpose."""
        # pylint: disable=too-many-locals
        at, row, pre = hook
        parts = at.split(".")
        if parts[0] == "procs":
            actions = [{"call": parts[1]}]
        else:
            index = (
                row if parts[0] == "record_types" else (int(parts[3]) if len(parts) > 3 else None)
            )
            if index is None:
                return {}
            actions = [{"run": {"record": parts[1], "index": int(index), "field": parts[2]}}]
        state = {k: v for k, v in pre.items() if isinstance(v, int) and k not in machine.parts}
        for c in self.spec["notes"]:
            state[c] = self.note_term(start)
        try:
            paths = machine.form.paths(_dispatched(machine, actions), (), state)
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
        hold their values at the first firing.
        """
        machine = self.machines[unit["name"]]
        values = dict(occ.start)
        lay = self.overlay(machine, occ.hook, occ.start) if occ.hook else {}
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
        try:
            return unit["name"], machine.start(values), v0
        except ValueError as exc:
            occ.error = str(exc)
            return None

    def derived(self, key: tuple):
        """The proved expressions of a start key."""
        name, start, v0 = key
        machine = self.machines[name]
        lift = Lift(machine.form, machine.cell, self.freq)
        v0e = None if v0 is None else lift.fold(lift.term(v0))
        return derive(machine, start, v0e, self.freq), v0e

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
        """A term of ``N`` at note ``n``."""
        lift = Lift(machine.form, machine.cell, self.freq)
        e = lift.fold(lift.term(term))
        if e.free_symbols - {G.N}:
            return None
        return _eval(e, n, self.tables, self.freq, self.offset)


def _eval(e, n: int, tables: dict, freq: dict, offset: int):
    """``e`` at ``N = n``."""
    st = Stepper(0, e, tables, freq, offset)
    st.note = n
    return st.step(0, 0)[0]


def _dispatched(machine: Machine, actions: list) -> list:
    """``actions`` with playroutine record runs as ``if`` chains."""
    return dispatch(machine.form.pr, actions)


# ---- caching and parallel work -----------------------------------------------------


def content_key(*parts) -> str:
    """The sha256 of ``parts`` (bytes, text or anything with a stable ``repr``)."""
    h = hashlib.sha256()
    for part in parts:
        h.update(part if isinstance(part, bytes) else str(part).encode())
        h.update(b"\0")
    return h.hexdigest()


def _cached(path: Path | None, compute, load_fn, save_fn):
    """``compute()``, read from and written to ``path`` when caching.

    One process computes an entry while others wait on its lock; the entry appears whole.
    """
    if path is None:
        return compute()
    if path.exists():
        return load_fn(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            return load_fn(path)
        out = compute()
        tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp{path.suffix}")
        save_fn(tmp, out)
        tmp.replace(path)
    return out


class Pool:
    """A process pool over the host's cores with two BLAS threads per worker, or in-process."""

    def __init__(self, workers: int, items: int):
        self.workers = max(1, min(workers or len(os.sched_getaffinity(0)), items))
        self.pool = None
        if self.workers > 1:
            for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
                os.environ[var] = "2"
            ctx = multiprocessing.get_context("spawn")
            self.pool = concurrent.futures.ProcessPoolExecutor(self.workers, mp_context=ctx)

    def map(self, fn, tasks: list, desc: str) -> list:
        """``[fn(*task)]`` in order, with a progress bar over completed tasks."""
        progress = tqdm(total=len(tasks), desc=desc, unit="task", leave=False)
        if self.pool is None:
            out = []
            for t in tasks:
                out.append(fn(*t))
                progress.update()
            progress.close()
            return out
        futures = {self.pool.submit(fn, *t): i for i, t in enumerate(tasks)}
        out = [None] * len(tasks)
        for f in concurrent.futures.as_completed(futures):
            out[futures[f]] = f.result()
            progress.update()
        progress.close()
        return out

    def close(self) -> None:
        """Shut the pool down."""
        if self.pool is not None:
            self.pool.shutdown()
