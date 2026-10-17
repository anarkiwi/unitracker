"""Table programs as generic accumulators, each stepped exactly through its playroutine's list.

A tick runs the list on the concrete control state (pointer, row timer) with the swept cell
symbolic, so it is a proved ``set c`` or ``add d``. The control state recurs; the moving ticks
of the steady run are matched to one accumulator, run with ``vm._acc`` and compared tick by tick.
"""

import dataclasses
from pathlib import Path

from tqdm import tqdm

from unitracker.format import songio
from unitracker.lift import backends
from unitracker.modfit import resolve, specs
from unitracker.modfit.bounce import bounce_lines
from unitracker.modfit.fit import src
from unitracker.modfit.flow import Writes
from unitracker.modfit.form import Form, Programs, defaults, lin, record_data
from unitracker.modfit.invariant import Slicer
from unitracker.modfit.keywords import role_text, signer
from unitracker.modfit.orbit import pingpong
from unitracker.modfit.timer import runs
from unitracker.player.vm import _acc


def load(path: Path) -> dict:
    """The internal form of a song YAML, or of a tune lifted with its native modulations."""
    if path.suffix in (".yaml", ".yml"):
        return songio.loads(path.read_text(encoding="utf-8"))
    return backends.lift(path.read_bytes(), None, expressions=False)


def entries(song: dict, record: str) -> dict:
    """``{start row: [(referrer, index)]}`` of the programs of ``record``.

    A start is a reference field of any record (``when`` evaluated on the row), or a command
    argument with the referrer ``"command"`` and the command code.
    """
    pr = song["playroutine"]
    none = pr["record_types"][record].get("none", 0)
    out = {}
    for start, who in (*ref_starts(song, record), *command_starts(song, record)):
        if start != none:
            out.setdefault(start, set()).add(who)
    return {k: sorted(v, key=str) for k, v in sorted(out.items())}


def ref_starts(song: dict, record: str):
    """``(start, (record type, index))`` of each reference field naming a ``record`` row."""
    pr = song["playroutine"]
    form = Form(pr)
    for name, rows in (song.get("records") or {}).items():
        rtype = pr["record_types"].get(name) or {}
        for field, refs in (rtype.get("refs") or {}).items():
            for ref in refs if isinstance(refs, list) else [{"type": refs}]:
                for i, row in rows.items() if ref["type"] == record else ():
                    full = {**defaults(rtype), **row}
                    if form.truth(form.expr(ref.get("when", 1), {}, full)) is True:
                        yield int(full.get(field, 0)), (name, i)


def command_starts(song: dict, record: str):
    """``(start, ("command", code))`` of each command argument naming a ``record`` row."""
    commands = song["playroutine"].get("commands") or {}
    for phrase in (song.get("phrases") or {}).values():
        for event in phrase:
            for code, arg in event.get("commands") or [event.get("command") or (0, 0)]:
                cmd = commands.get(code)
                if isinstance(cmd, dict) and cmd.get("ref") == record:
                    yield int(arg), ("command", code)


@dataclasses.dataclass
class Run:
    """A program's ticks: each op (``("set", c)`` or ``("add", d)``) and the cycle's start."""

    ops: list
    loop: int
    error: str = ""


class Stepper:
    """Exact per-tick ops of one sweep spec's action list over a song's records."""

    def __init__(self, song: dict, spec: dict):
        pr = dict(song["playroutine"])
        pr["tables"] = {**(pr.get("tables") or {}), **(song.get("tables") or {})}
        self.form = Programs(pr, record_data(song, pr.get("record_types") or {}))
        self.spec, self.cell = spec, spec["cell"]
        self.parts = (
            set(self.form.pair[self.cell][:2]) if self.cell in self.form.pair else {self.cell}
        )
        self.top = (1 << self.form.width[self.cell]) - 1
        f = self.form
        f.procs = dict(f.procs)
        keep = self.parts | {spec["pointer"], *spec.get("state", {})}
        self.actions = Slicer(pr, Writes(pr), f.procs).seq(resolve(pr, spec["path"]), keep, keep)[0]
        self.memo = {}

    def tick(self, state: dict) -> tuple:
        """``(op, next control state)`` of one tick, or ``(None, reason)``."""
        key = tuple(sorted(state.items()))
        if key not in self.memo:
            self.memo[key] = self.evaluate(state)
        return self.memo[key]

    def evaluate(self, state: dict) -> tuple:
        """Uncached ``tick``: every path must give one op and one concrete control state."""
        f, found = self.form, set()
        for conds, final in f.paths(self.actions, (), state):
            fused = f.fuse(conds, final) if self.cell in f.pair else final
            op = self.op(fused.get(self.cell, self.cell))
            if op is None:
                return None, f"value {src(fused[self.cell])}"
            nxt = {
                c: v
                for c, v in final.items()
                if c not in self.parts and c not in f.scratch and c != "row"
            }
            bad = sorted(c for c, v in nxt.items() if not isinstance(v, int))
            if bad:
                return None, f"control {', '.join(bad)} not constant"
            found.add((op, tuple(sorted({**state, **nxt}.items()))))
        if len(found) != 1:
            return None, f"{len(found)} distinct ticks by value"
        op, nxt = found.pop()
        return op, dict(nxt)

    def op(self, value) -> tuple | None:
        """``("set", c)``, ``("add", d)`` with ``d`` signed at the width, or None."""
        if isinstance(value, int):
            return "set", value
        inner = value[1] if isinstance(value, tuple) and value[0] == "and" else value
        if value is not inner and value[2] != self.top:
            return None
        c, coeffs = lin(inner)
        if coeffs != {self.cell: 1}:
            return None
        d = c % (self.top + 1)
        return "add", d - (self.top + 1) if d > self.top // 2 else d

    def run(self, start: int, context: dict) -> Run:
        """The ops from the program at row ``start`` until its control state recurs."""
        state = {self.spec["pointer"]: start, **self.spec.get("state", {}), **context}
        seen, ops = {}, []
        while (key := tuple(sorted(state.items()))) not in seen:
            seen[key] = len(ops)
            op, state = self.tick(state)
            if op is None:
                return Run(ops, -1, state)
            ops.append(op)
        return Run(ops, seen[key])


def simulate(acc: dict, v: int, n: int, mask: int) -> list:
    """The ``(value, direction)`` states of ``n`` moving ticks through ``vm._acc``, from ``v``."""
    vd = acc.get("vd", 0)
    out = [(v, vd)]
    signed = not 0 <= acc.get("min", 0) <= acc.get("max", mask) <= mask
    clamp = {True: 1, False: 0, "reflect": 2}[acc.get("clamp", True)]
    for _ in range(n):
        step = acc["step"] * (-1 if vd else 1)
        v, hit = _acc(v, step, mask, acc.get("min"), acc.get("max"), None, clamp, signed)
        if hit and "reload" in acc:
            v = acc["reload"]
        if hit and clamp == 2:
            vd ^= 1
        out.append((v, vd))
    return out


def text(acc: dict) -> str:
    """Source of a candidate accumulator."""
    kind = {True: "clamp", False: "wrap", "reflect": "reflect"}[acc.get("clamp", True)]
    if "reload" in acc:
        kind = f"reload {acc['reload']}"
    bounds = (
        f" [{acc.get('min', '-')}, {acc.get('max', '-')}]" if acc.keys() & {"min", "max"} else ""
    )
    first = f", first {'-' if acc.get('vd') else '+'}{acc['step']}" if kind == "reflect" else ""
    return f"accumulate step {acc['step']}{bounds} {kind}{first}"


def steady(run: Run) -> tuple:
    """``(base, ops after it, reload)``: from the cycle's set row, else the prefix's last one.

    ``base`` is None when no set row fixes the value; a cycle's set row is its reload, closing it.
    """
    prefix, cycle = run.ops[: run.loop], run.ops[run.loop :]
    sets = [i for i, (k, _) in enumerate(cycle) if k == "set"]
    if sets:
        rot = cycle[sets[0] :] + cycle[: sets[0]]
        return rot[0][1], rot[1:] + rot[:1], rot[0][1]
    last = max((i for i, (k, _) in enumerate(prefix) if k == "set"), default=None)
    if last is None:
        return None, prefix + cycle + cycle, None
    return prefix[last][1], prefix[last + 1 :] + cycle + cycle, None


def candidate(trace: list, reload, endless: bool, sets: int) -> tuple:
    """``(accumulator, reason)`` of the moving ticks' values ``trace`` (from the base value).

    A reload closes a sawtooth; otherwise one step size is a ping-pong, an endless sweep (wrap)
    or a sweep that stops (clamp at its last value).
    """
    # pylint: disable=too-many-return-statements
    deltas = [b - a for a, b in zip(trace, trace[1:])]
    if not any(deltas):
        return {}, ""
    if reload is not None:
        if sets > 1:
            return None, f"{sets} set rows in the cycle"
        steps = set(deltas[:-1])
        if len(steps) != 1:
            return None, f"steps {', '.join(f'{d:+d}' for d in sorted(steps))} between reloads"
        (s,) = steps
        return {"step": s, ("max" if s > 0 else "min"): trace[-2], "clamp": False,
                "reload": reload}, ""  # fmt: skip
    sizes = sorted({abs(d) for d in deltas})
    if len(sizes) > 1:
        return None, "step sizes " + ", ".join(f"{d:+d} x{n}" for d, n in runs(deltas))
    pp = pingpong(deltas)
    if pp is not None:
        lo, hi, first = pp
        return {"step": sizes[0], "min": trace[0] + lo, "max": trace[0] + hi,
                "clamp": "reflect", "vd": int(first < 0)}, ""  # fmt: skip
    if len(set(deltas)) == 1:
        if endless:
            return {"step": deltas[0], "clamp": False}, ""
        return {"step": deltas[0], ("max" if deltas[0] > 0 else "min"): trace[-1]}, ""
    turns = [trace[i + 1] for i in range(len(deltas) - 1) if deltas[i] != deltas[i + 1]]
    return None, f"step size {sizes[0]} turning at {', '.join(map(str, turns))}, not two bounds"


def verdict(run: Run, top: int) -> str:
    """One accumulator over the program's steady run, verified, or exactly why there is none."""
    if run.loop < 0:
        return f"not stepped: {run.error}"
    cycle = run.ops[run.loop :]
    timing = timing_text(run.ops[: run.loop], cycle)
    base, ops, reload = steady(run)
    if base is None:
        return f"starts from the incoming value (no set row); {timing}"
    trace, v = [base], base
    for kind, x in ops:
        v = x if kind == "set" else v + x
        if kind == "set" or x:
            trace.append(v)
    moving = sum(1 for k, x in cycle if k == "set" or x)
    acc, reason = candidate(trace, reload, moving > 0, sum(k == "set" for k, _ in cycle))
    if acc is None:
        return f"set {base}; not one accumulator: {reason}; {timing}"
    if not acc:
        return f"set {base}, then holds; {timing}"
    return f"set {base}{matches(acc, trace, reload is not None, moving, top)}; {timing}"


def matches(acc: dict, trace: list, closed: bool, moving: int, top: int) -> str:
    """``, then <accumulator>`` when it gives every moving tick of ``trace``, else why not.

    A bounded accumulator's state must also recur with the cycle: after the reload when
    ``closed``, else over the last ``moving`` ticks.
    """
    want = [x & top for x in trace[1:]]
    states = simulate(acc, trace[0], len(want), top)
    got = [v for v, _ in states[1:]]
    if got != want:
        bad = next(i for i, (a, b) in enumerate(zip(got, want)) if a != b)
        return f"; {text(acc)} differs at moving tick {bad + 1}"
    if (
        moving
        and acc.keys() & {"min", "max"}
        and states[-1] != states[0 if closed else -1 - moving]
    ):
        return f"; {text(acc)} does not recur with the cycle"
    return f", then {text(acc)}, equal on every moving tick"


def timing_text(prefix: list, cycle: list) -> str:
    """The moving and holding runs of the prefix and the cycle, in ticks."""

    def pattern(ops) -> str:
        return ", ".join(
            f"{'moves' if m else 'holds'} {n}"
            for m, n in runs(k == "set" or x != 0 for k, x in ops)
        )

    head = f"ticks {pattern(prefix)}; " if prefix else ""
    return f"{head}cycle of {len(cycle)} ticks: {pattern(cycle)}"


def song_lines(path: Path) -> list:
    """Each sweep program of a song with its verdict."""
    song = load(path)
    name = song["playroutine"]["name"]
    lines = [f"== {path.name} ({name})"]
    for spec in specs()["sweeps"].get(name, []):
        lines += spec_lines(song, spec, path.stem)
    return lines


def spec_lines(song: dict, spec: dict, label: str) -> list:
    """The programs of one sweep spec in a song, each started from its first referrer."""
    pr = song["playroutine"]
    ins_type = (pr.get("instrument") or {}).get("type")
    stepper = Stepper(song, spec)
    role = role_text(signer(pr["name"]).cell_role(spec["cell"]))
    lines = [f"   {spec['record']} -> {spec['cell']} ({role}), list {spec['path']}"]
    found = entries(song, spec["record"])
    for start, refs in tqdm(found.items(), desc=f"{label} {spec['record']}", leave=False):
        ins = [i for t, i in refs if t == ins_type]
        context = {spec["instrument"]: ins[0]} if ins and spec.get("instrument") else {}
        who = ", ".join(f"{t} {i}" for t, i in refs)
        lines.append(
            f"      row {start} ({who}): {verdict(stepper.run(start, context), stepper.top)}"
        )
    return lines


def report(songs, fused=()) -> list:
    """The sweeps report: bounce orbits, the given fused pair fit lines, each song's programs."""
    lines = ["# Bounce orbits", ""] + bounce_lines(tuple(u.values()) for u in specs()["bounces"])
    lines += ["", "# Fused pair fits", ""] + list(fused) + ["", "# Table programs", ""]
    for path in songs:
        lines += song_lines(Path(path))
    return lines
