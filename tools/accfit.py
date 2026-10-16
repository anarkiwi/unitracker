"""Rewrite playroutine modulations as compact bounded accumulators (prototype).

One-tick: candidate ``accumulate`` actions from each cell's step, thresholds and reloads,
kept when they agree on sampled states. Multi-tick: a phase-gated step becomes a ping-pong.
"""

import argparse
import random
import sys
from pathlib import Path

import yaml
import z3
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))
from abstract import ROOT, resolve  # noqa: E402  pylint: disable=wrong-import-position
from opform import Form, atoms, lin  # noqa: E402  pylint: disable=wrong-import-position

SAMPLES = 400


def src(t) -> str:
    """Python expression source of an opform term."""
    # pylint: disable=too-many-return-statements
    if isinstance(t, bool):
        return "1" if t else "0"
    if isinstance(t, int):
        return str(t)
    if isinstance(t, str):
        return f"(({t.split(':')[0]} << 8) | {t.split(':')[1]})" if ":" in t else t
    head = t[0]
    if head == "lin":
        parts = [str(t[1])] if t[1] else []
        parts += [src(a) if k == 1 else f"{k} * {src(a)}" for a, k in t[2]]
        return "(" + " + ".join(parts) + ")"
    if head == "iv":
        return f"(1 if {src(t[1])} else 0)"
    if head == "ite":
        return f"({src(t[2])} if {src(t[1])} else {src(t[3])})"
    binary = {"and": "&", "or": "|", "xor": "^", "shr": ">>", "mul": "*"}
    if head in binary:
        return f"({src(t[1])} {binary[head]} {src(t[2])})"
    if head in ("idx", "rec"):
        return f"{t[1]}[{src(t[2])}]"
    if head == "fld":
        return f"{src(t[2])}.{t[1]}"
    if head == "lt":
        return f"({src(t[1])} < 0)"
    if head == "eq":
        return f"({src(t[1])} == 0)"
    if head == "not":
        return f"(not {src(t[1])})"
    if head in ("land", "lor"):
        return "(" + (" and " if head == "land" else " or ").join(src(x) for x in t[1:]) + ")"
    raise ValueError(f"no source for {t!r}")


class Fitter:
    """Accumulator fits for one playroutine."""

    def __init__(self, pr: dict, seed: int = 1):
        self.pr = pr
        self.rand = random.Random(seed)
        self.sym = Form(pr)

    def records(self) -> dict:
        """Random byte rows for every record type, indices 0..15."""
        out = {}
        for name, rt in (self.pr.get("record_types") or {}).items():
            fields = rt.get("fields") or []
            out[name] = {i: {f: self.rand.randrange(256) for f in fields} for i in range(16)}
        return out

    def state(self, cells) -> dict:
        """Random values for ``cells`` within their widths."""
        out = {}
        for c in cells:
            w = self.sym.width.get(c)
            out[c] = self.rand.randrange(1 << w) if w else self.rand.randrange(-128, 128)
        return out

    def run(self, actions, state: dict, data: dict) -> dict:
        """The concrete state after ``actions``."""
        paths = Form(self.pr, data).paths(actions, (), state)
        if len(paths) != 1:
            raise ValueError(f"{len(paths)} paths in a concrete run")
        return {**state, **paths[0][1]}

    def holds(self, conds, state: dict, data: dict) -> bool:
        """True when every condition is true in a concrete state."""
        form = Form(self.pr, data)
        return all(form.truth(form.expr(src(c), state)) is True for c in conds)

    def cases(self, actions, cell: str) -> list:
        """``[(dispatch guard, dynamic guard, value)]`` of ``cell`` over the symbolic paths."""
        out = []
        for conds, final in self.sym.paths(actions):
            if cell not in final or final[cell] == cell:
                continue
            dyn = tuple(c for c in conds if cell in atoms(c))
            out.append((tuple(c for c in conds if c not in dyn), dyn, final[cell]))
        return out

    def candidates(self, cell: str, cases: list) -> list:
        """``accumulate`` actions from the step, thresholds and reload values the cases expose."""
        mask = (1 << self.sym.width[cell]) - 1 if self.sym.width.get(cell) else None
        steps, bounds, values = set(), set(), set()
        for _, dyn, value in cases:
            inner = (
                value[1]
                if isinstance(value, tuple) and value[0] == "and" and value[2] == mask
                else value
            )
            if lin(inner)[1].get(cell) == 1:
                steps.add(self.sym.add(inner, self.sym.neg(cell)))
            elif cell not in atoms(value):
                values.add(value)
            bounds.update(values)
            for cond in dyn:
                if cond[0] in ("lt", "eq"):
                    c, coeffs = lin(cond[1])
                    k = coeffs.pop(cell, 0)
                    if k in (1, -1):
                        rest = self.sym.add(c, *(self.sym.scale(v, a) for a, v in coeffs.items()))
                        bounds.add(self.sym.scale(-k, rest))
        out = [{"set": {cell: src(v)}} for v in values]
        out += [{"accumulate": cell, "step": src(s)} for s in steps]
        for s in steps:
            for b in bounds:
                for shift in (0, 1, -1):
                    edge = src(self.sym.add(b, shift))
                    out.append({"accumulate": cell, "step": src(s), "toward": edge})
                    for key in ("max", "min"):
                        out.append({"accumulate": cell, "step": src(s), key: edge})
                        out += [
                            {
                                "accumulate": cell,
                                "step": src(s),
                                key: edge,
                                "clamp": False,
                                "on_bound": [{"set": {cell: src(v)}}],
                            }
                            for v in values
                        ]
        return sorted(out, key=lambda a: len(str(a)))

    def witness(self, actions, guard, cell: str, candidate) -> str | None:
        """A model where ``candidate`` and the original disagree on ``cell``, as text."""
        mine = self.sym.paths([candidate])
        for conds, final in self.sym.paths(actions):
            for cconds, cfinal in mine:
                solver, term, cond = self.sym.solver()
                solver.add(*(cond(c) for c in (*guard, *conds, *cconds)))
                solver.add(term(final.get(cell, cell)) != term(cfinal.get(cell, cell)))
                if solver.check() == z3.sat:
                    m = solver.model()
                    return ", ".join(
                        f"{d}={m[d].as_signed_long()}" for d in m.decls() if d.arity() == 0
                    )
        return None

    def agrees(self, actions, guard, cell: str, candidate) -> bool:
        """True when, under ``guard``, every original path and candidate path give ``cell`` equal values."""
        mine = self.sym.paths([candidate])
        for conds, final in self.sym.paths(actions):
            for cconds, cfinal in mine:
                solver, term, cond = self.sym.solver()
                solver.add(*(cond(c) for c in (*guard, *conds, *cconds)))
                solver.add(term(final.get(cell, cell)) != term(cfinal.get(cell, cell)))
                if solver.check() != z3.unsat:
                    return False
        return True

    def fit(self, actions, cell: str) -> list:
        """``[(dispatch guard, compact accumulator or None)]`` per dispatch group of ``cell``."""
        groups = {}
        for guard, dyn, value in self.cases(actions, cell):
            groups.setdefault(guard, []).append((guard, dyn, value))
        out = []
        for guard, cases in groups.items():
            found = None
            for cand in self.candidates(cell, cases):
                try:
                    if self.agrees(actions, guard, cell, cand):
                        found = cand
                        break
                except (ValueError, KeyError, IndexError, ZeroDivisionError):
                    continue
            out.append((guard, found))
        return out


def deltas(fitter, actions, state, data, cell, n) -> list:
    """Per-tick change of ``cell`` over ``n`` ticks of ``actions``."""
    seq = []
    for _ in range(n):
        nxt = fitter.run(actions, state, data)
        w = fitter.sym.width.get(cell)
        d = nxt[cell] - state[cell]
        seq.append(((d + (1 << (w - 1))) % (1 << w)) - (1 << (w - 1)) if w else d)
        state = nxt
    return seq


def pingpong(steps) -> tuple | None:
    """``(lo, hi, first step)`` when the cumulative offsets are one ping-pong between ``lo`` and ``hi``."""
    pos, offsets = 0, [0]
    for d in steps:
        pos += d
        offsets.append(pos)
    lo, hi, size = min(offsets), max(offsets), abs(steps[0]) if steps else 0
    if size == 0 or any(abs(d) != size for d in steps):
        return None
    pos, sign = 0, (1 if steps[0] > 0 else -1)
    for want in offsets[1:]:
        if not lo <= pos + sign * size <= hi:
            sign = -sign
        pos += sign * size
        if pos != want:
            return None
    return lo, hi, steps[0]


VIBRATOS = {
    "goattracker2": {
        "path": "procs.vibrato",
        "cell": "freq",
        "periods": range(0, 128),
        "state": lambda p: {"vibtime": 0, "freq": 20000, "lastnote": 40, "arg": 1},
        "data": lambda p: {"speedtab": {1: {"l": p, "r": 1}}},
    },
    "sidwizard": {
        "path": "procs.tick_vibrato",
        "cell": "voff",
        "periods": range(1, 128),
        "state": lambda p: {
            "ci": 1,
            "svib": 0x10,
            "vdc": -1,
            "vfc": 0,
            "vper": p,
            "fml": 1,
            "fmh": 0,
            "voff": 0,
        },
        "data": lambda p: {},
    },
}


def vibrato_report(key: str, ticks: int) -> None:
    """Ping-pong accumulator bounds of a vibrato, per period value, from the exact orbit."""
    spec = VIBRATOS[key]
    with open(ROOT / f"{key}.yaml", encoding="utf-8") as fh:
        pr = yaml.safe_load(fh)
    fitter, actions = Fitter(pr), resolve(pr, spec["path"])
    rows, fails = [], []
    for p in tqdm(spec["periods"], desc=f"{key} vibrato orbits", unit="period"):
        pp = pingpong(
            deltas(fitter, actions, spec["state"](p), spec["data"](p), spec["cell"], ticks)
        )
        (rows if pp else fails).append((p, pp))
    print(
        f"== {key} vibrato as one ping-pong accumulator on {spec['cell']} (unit depth, from the note pitch)"
    )
    print(
        f"   matches for {len(rows)} of {len(rows) + len(fails)} period values over {ticks} ticks"
    )
    for p, (lo, hi, first) in rows[:8] + rows[-2:]:
        print(f"   period {p}: offset bounds [{lo}, {hi}], first step {first:+d}")
    if fails:
        print(f"   no single ping-pong for periods {[p for p, _ in fails][:20]}")


def main():
    """Fit the named modulations and report the vibrato rewrites."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "units",
        nargs="*",
        default=[
            "goattracker2:record_types.pulsetab.each:pulse",
            "goattracker2:records.tickn.1.do:freq",
            "goattracker2:procs.toneporta:freq",
            "defmon:procs.pulse:pw",
            "defmon:procs.pitch:sacc",
            "sidwizard:procs.tick_porta:voff",
            "sidwizard:procs.tick_vibrato:vfc",
        ],
    )
    ap.add_argument("--ticks", type=int, default=600)
    ap.add_argument("--no-vibrato", action="store_true")
    args = ap.parse_args()
    for unit in args.units:
        key, path, cell = unit.split(":")
        with open(ROOT / f"{key}.yaml", encoding="utf-8") as fh:
            pr = yaml.safe_load(fh)
        print(f"== {key}:{path} -> {cell}")
        for guard, found in Fitter(pr).fit(resolve(pr, path), cell):
            when = " and ".join(src(g) for g in guard) or "always"
            print(f"   when {when[:150]}")
            print(f"      {found if found else 'no single accumulator'}")
    if not args.no_vibrato:
        for key in VIBRATOS:
            vibrato_report(key, args.ticks)


if __name__ == "__main__":
    main()
