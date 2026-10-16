"""Multi-tick analysis: a modulation's exact orbit tested as one ping-pong accumulator."""

import copy

import numpy as np
from tqdm import tqdm

from unitracker.modfit import playroutine, resolve
from unitracker.modfit.form import Form


def deltas(form: Form, actions, state: dict, cell: str, n: int) -> np.ndarray:
    """Per-tick change of ``cell`` over ``n`` concrete ticks, wrapped signed to its width."""
    values = [state[cell]]
    for _ in range(n):
        paths = form.paths(actions, (), state)
        if len(paths) != 1:
            raise ValueError(f"{len(paths)} paths in a concrete run")
        state = paths[0][1]
        values.append(state[cell])
    d = np.diff(np.asarray(values, dtype=np.int64))
    w = form.width.get(cell)
    return (d + (1 << (w - 1))) % (1 << w) - (1 << (w - 1)) if w else d


def pingpong(steps) -> tuple | None:
    """``(lo, hi, first step)`` when the offsets ping-pong between ``lo`` and ``hi``.

    Steps share one size and reverse only at a bound; each bound is the start or a reversal,
    so a slide that never turns at its far end is not a ping-pong.
    """
    steps = np.asarray(steps, dtype=np.int64)
    if not steps.size or steps[0] == 0 or np.any(np.abs(steps) != abs(steps[0])):
        return None
    offsets = np.cumsum(steps)
    lo, hi = min(int(offsets.min()), 0), max(int(offsets.max()), 0)
    turns = set(offsets[:-1][steps[1:] != steps[:-1]].tolist())
    if not turns <= {lo, hi} or not {lo, hi} <= turns | {0}:
        return None
    return lo, hi, int(steps[0])


def start(spec: dict, period: int) -> tuple:
    """``(state, data)`` of an orbit spec with its period set."""
    state, data = dict(spec["state"]), copy.deepcopy(spec.get("data") or {})
    where = spec["period"].split(".")
    if len(where) == 1:
        state[where[0]] = period
    else:
        record, index, field = where
        data.setdefault(record, {}).setdefault(int(index), {})[field] = period
    return state, data


def orbits(name: str, spec: dict, ticks: int) -> dict:
    """``{period: (lo, hi, first step) or None}`` over the spec's inclusive period range."""
    pr = playroutine(name)
    actions = resolve(pr, spec["path"])
    first, last = spec["periods"]
    out = {}
    for p in tqdm(range(first, last + 1), desc=f"{name} orbits", unit="period"):
        state, data = start(spec, p)
        out[p] = pingpong(deltas(Form(pr, data), actions, state, spec["cell"], ticks))
    return out


def report(name: str, spec: dict, ticks: int) -> list:
    """Report lines of ``orbits``: one per period, then the periods with no ping-pong."""
    found = orbits(name, spec, ticks)
    lines = [f"== {name}:{spec['path']} {spec['cell']} over {ticks} ticks, by {spec['period']}"]
    for p, pp in found.items():
        lines.append(
            f"   {p}: ping-pong [{pp[0]}, {pp[1]}], first step {pp[2]:+d}"
            if pp
            else f"   {p}: no ping-pong"
        )
    fails = [p for p, pp in found.items() if pp is None]
    lines.append(f"   ping-pong for {len(found) - len(fails)} of {len(found)}; fails: {fails}")
    return lines
