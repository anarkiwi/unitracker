"""Multi-tick analysis: a modulation's exact orbit tested against one reflect accumulator."""

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


def reflector(pr: dict, cell: str, origin: int, pp: tuple) -> tuple:
    """``(playroutine, accumulate action, state)`` of the reflect accumulator of a ping-pong.

    A fresh 1-bit cell holds the direction; the cell is signed when its bounds need it.
    """
    lo, hi, first = pp
    section = "voice" if cell in (pr.get("voice") or {}) else "global"
    cells = {**(pr.get("global") or {}), **(pr.get("voice") or {})}
    vd = "vd"
    while vd in cells:
        vd += "_"
    d, w = abs(first), (cells[cell] or {}).get("width")
    acc = {
        "accumulate": cell,
        "step": f"{d} if {vd} == 0 else 0 - {d}",
        "min": origin + lo,
        "max": origin + hi,
        "clamp": "reflect",
        "on_bound": [{"set": {vd: f"{vd} ^ 1"}}],
    }
    if w and not (0 <= acc["min"] and acc["max"] < 1 << w):
        acc["signed"] = True
    pr = {**pr, section: {**(pr.get(section) or {}), vd: {"width": 1}}}
    return pr, acc, {cell: origin, vd: int(first < 0)}


def orbits(name: str, spec: dict, ticks: int) -> dict:
    """``{period: None or (lo, hi, first step, matching accumulate action or None)}``.

    The ping-pong of each period's orbit gives the bounds of a reflect accumulator, which is
    accepted only when its per-tick deltas equal the original's over the whole run.
    """
    pr = playroutine(name)
    actions, cell = resolve(pr, spec["path"]), spec["cell"]
    first, last = spec["periods"]
    out = {}
    for p in tqdm(range(first, last + 1), desc=f"{name} orbits", unit="period"):
        state, data = start(spec, p)
        out[p] = reflect_match(pr, deltas(Form(pr, data), actions, state, cell, ticks), cell, state)
    return out


def reflect_match(pr: dict, steps, cell: str, state: dict) -> tuple | None:
    """``(lo, hi, first step, accumulate action or None)`` of an orbit's ping-pong, or None."""
    pp = pingpong(steps)
    if pp is None:
        return None
    rpr, acc, rstate = reflector(pr, cell, state[cell], pp)
    same = np.array_equal(deltas(Form(rpr), [acc], rstate, cell, len(steps)), steps)
    return (*pp, acc if same else None)


def report(name: str, spec: dict, ticks: int) -> list:
    """Report lines of ``orbits``: one per period, then the periods with no reflect accumulator."""
    found = orbits(name, spec, ticks)
    lines = [f"== {name}:{spec['path']} {spec['cell']} over {ticks} ticks, by {spec['period']}"]
    for p, r in found.items():
        if r is None:
            lines.append(f"   {p}: no ping-pong")
            continue
        head = f"   {p}: ping-pong [{r[0]}, {r[1]}], first step {r[2]:+d}"
        lines.append(f"{head}, reflect {r[3]}" if r[3] else f"{head}, reflect differs")
    fails = [p for p, r in found.items() if r is None or r[3] is None]
    lines.append(f"   reflect for {len(found) - len(fails)} of {len(found)}; fails: {fails}")
    return lines
