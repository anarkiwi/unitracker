"""Bounded accumulator specs: the one form every player effect takes, and the engine running it.

Specs hold shared-unit numbers (int or Fraction), expression strings and at-bound option
maps. :func:`parse` validates a document's spec, :func:`render` writes one back,
:class:`Compiled` binds one to a lane, and :class:`Lane` runs it (see docs/format.md).
"""

import json
import re
from fractions import Fraction

PARAMS = (
    "from",
    "step",
    "up",
    "down",
    "carry_from",
    "dir",
    "flip",
    "toward",
    "every",
    "for",
    "lo",
    "at_lo",
    "hi",
    "at_hi",
    "width",
    "reset",
)
AT_OPTIONS = ("clamp", "reverse", "wrap", "stop")
RESETS = ("row", "command")
DIRS = ("up", "down")
_FRACTION = re.compile(r"^-?\d+/\d+$")


class SpecError(ValueError):
    """A spec breaks the accumulator grammar."""


def number(value, what: str):
    """``value`` as an int or Fraction, from an int, a Fraction or an ``"a/b"`` string."""
    if isinstance(value, bool):
        raise SpecError(f"{what}: {value!r} is not a number")
    if isinstance(value, int):
        return value
    if isinstance(value, Fraction):
        return value.numerator if value.denominator == 1 else value
    if isinstance(value, str) and _FRACTION.match(value.strip()):
        return number(Fraction(value.strip()), what)
    raise SpecError(f"{what}: {value!r} is not a number")


def value_of(value, what: str):
    """A number, or an expression string."""
    try:
        return number(value, what)
    except SpecError:
        if isinstance(value, str) and value.strip():
            return value.strip()
        raise


def _flag(value, what: str) -> int:
    if value in (True, 1, "on"):
        return 1
    raise SpecError(f"{what}: {value!r} must be on")


def _count(value, what: str, lowest: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < lowest:
        raise SpecError(f"{what}: {value!r} must be an integer >= {lowest}")
    return value


def _phase_step(value, what: str):
    if isinstance(value, dict):
        if set(value) - {"step", "carry_from"} or "step" not in value:
            raise SpecError(f"{what}: a phase step is a value or {{step, carry_from}}")
        out = {"step": value_of(value["step"], what)}
        if "carry_from" in value:
            out["carry_from"] = str(value["carry_from"])
        return out
    return value_of(value, what)


def _at(value, what: str) -> dict:
    if not isinstance(value, dict) or not value or set(value) - set(AT_OPTIONS):
        raise SpecError(f"{what}: options are {', '.join(AT_OPTIONS)}")
    return {
        key: value_of(value[key], what) if key == "clamp" else _flag(value[key], what)
        for key in AT_OPTIONS
        if key in value
    }


def _param(key, raw, label):
    """One parameter's canonical value."""
    # pylint: disable=too-many-return-statements
    if key in ("step", "toward"):
        return value_of(raw, label)
    if key in ("up", "down"):
        return _phase_step(raw, label)
    if key in ("from", "lo", "hi"):
        return number(raw, label)
    if key in ("at_lo", "at_hi"):
        return _at(raw, label)
    if key in ("every", "width"):
        return _count(raw, label, 1)
    if key == "for":
        return _count(raw, label)
    if key == "flip":
        if not isinstance(raw, list) or not raw:
            raise SpecError(f"{label}: a list of periods")
        return [_count(p, label, 1) for p in raw]
    if key == "reset":
        items = raw if isinstance(raw, list) else [raw]
        if not items or any(r not in RESETS for r in items):
            raise SpecError(f"{label}: triggers are {', '.join(RESETS)}")
        return sorted(set(items), key=RESETS.index)
    if key == "dir":
        if raw not in DIRS:
            raise SpecError(f"{label}: up or down")
        return raw
    return str(raw)


def parse(value, what: str = "spec") -> dict:
    """The canonical spec for a mapping read from a document; raise SpecError if malformed."""
    if not isinstance(value, dict):
        raise SpecError(f"{what}: a spec is a mapping")
    unknown = set(value) - set(PARAMS)
    if unknown:
        raise SpecError(f"{what}: unknown parameters {sorted(unknown)}")
    if "step" in value and ("up" in value or "down" in value):
        raise SpecError(f"{what}: use step, or up and down")
    if ("up" in value) != ("down" in value):
        raise SpecError(f"{what}: up and down go together")
    if "flip" in value and "up" in value:
        raise SpecError(f"{what}: flip reverses step; it does not take up and down")
    out = {k: _param(k, value[k], f"{what}.{k}") for k in PARAMS if k in value}
    for bound in ("lo", "hi"):
        if f"at_{bound}" in out and bound not in out:
            raise SpecError(f"{what}: at_{bound} needs {bound}")
    return out


def moves(spec: dict) -> bool:
    """Whether the spec accumulates at all (a spec of only ``from`` just sets)."""
    return any(k in spec for k in ("step", "up", "toward"))


def spec_key(spec: dict) -> str:
    """A canonical text identifying the spec."""
    return json.dumps(render(spec), sort_keys=True)


def render_value(value):
    """A number or nested value as document values: fractions become ``"a/b"``."""
    if isinstance(value, Fraction):
        if value.denominator == 1:
            return value.numerator
        return f"{value.numerator}/{value.denominator}"
    if isinstance(value, dict):
        return {k: render_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [render_value(v) for v in value]
    return value


def render(spec: dict) -> dict:
    """The spec as document values: fractions as ``"a/b"``, flags as ``True``."""
    out = {}
    for name in PARAMS:
        if name not in spec:
            continue
        value = spec[name]
        if name in ("at_lo", "at_hi"):
            value = {k: (True if k != "clamp" else v) for k, v in value.items()}
        elif name == "reset":
            value = value[0] if len(value) == 1 else list(value)
        out[name] = render_value(value)
    return out


def scale_number(value, scale: int, what: str) -> int:
    """A shared-unit number in a lane's internal units."""
    scaled = Fraction(value) * scale
    if scaled.denominator != 1:
        raise SpecError(f"{what}: {value} is not a multiple of 1/{scale}")
    return int(scaled)


class Compiled:
    """A spec bound to a lane: callables of ``(cells, value)`` and internal-unit numbers."""

    # pylint: disable=too-many-instance-attributes,too-few-public-methods

    def __init__(self, spec, scale, compile_expr, carry_cell):
        """``compile_expr(text, what)`` gives a shared-unit callable of ``(cells, value)``;
        ``carry_cell(lane)`` gives a callable of ``cells`` reading that lane's carry."""

        def fn(value, what):
            if isinstance(value, str):
                shared = compile_expr(value, what)
                if scale == 1:
                    return shared
                return lambda c, x: scale_number(shared(c, Fraction(x, scale)), scale, what)
            const = scale_number(value, scale, what)
            return lambda c, x: const

        def phase(value, what):
            if isinstance(value, dict):
                carry = value.get("carry_from")
                return fn(value["step"], what), (carry_cell(carry) if carry else None)
            return fn(value, what), None

        self.start = scale_number(spec["from"], scale, "from") if "from" in spec else None
        self.moves = moves(spec)
        self.step = fn(spec["step"], "step") if "step" in spec else None
        step = spec.get("step")
        self.const_step = step if isinstance(step, (int, Fraction)) else None
        self.up = self.down = self.up_carry = self.down_carry = None
        if "up" in spec:
            self.up, self.up_carry = phase(spec["up"], "up")
            self.down, self.down_carry = phase(spec["down"], "down")
        self.carry = carry_cell(spec["carry_from"]) if "carry_from" in spec else None
        self.dir = None if "dir" not in spec else (1 if spec["dir"] == "up" else -1)
        self.flip = spec.get("flip")
        self.toward = fn(spec["toward"], "toward") if "toward" in spec else None
        self.every = spec.get("every")
        self.for_n = spec.get("for")
        bits = spec.get("width")
        self.mask = None if bits is None else (1 << (bits + scale.bit_length() - 1)) - 1
        self.lo = scale_number(spec["lo"], scale, "lo") if "lo" in spec else None
        self.hi = scale_number(spec["hi"], scale, "hi") if "hi" in spec else None
        self.at_lo = self._at(spec.get("at_lo"), fn)
        self.at_hi = self._at(spec.get("at_hi"), fn)
        self.reset = set(spec.get("reset") or ())

    @staticmethod
    def _at(at, fn):
        if at is None:
            return None
        clamp = fn(at["clamp"], "clamp") if "clamp" in at else None
        return clamp, bool(at.get("reverse")), bool(at.get("wrap")), bool(at.get("stop"))


class Lane:
    """One target's accumulator state: its driver, phase, duration and last hit."""

    # pylint: disable=too-many-instance-attributes

    OFF, LIVE, ACC = 0, 1, 2

    def __init__(self, mask):
        self.mask = mask
        self.driver = Lane.OFF
        self.spec = None
        self.live = None
        self.stored = 0
        self.pos = self.idx = 0
        self.dir = 1
        self.start_dir = 1
        self.left = None
        self.ecount = 0
        self.hit = 0
        self.carry = 0
        self.stopping = False

    def state(self) -> tuple:
        """The lane's complete state (its spec and live expression by identity)."""
        return (
            self.driver,
            id(self.spec),
            id(self.live),
            self.stored,
            self.pos,
            self.idx,
            self.dir,
            self.start_dir,
            self.left,
            self.ecount,
            self.hit,
            self.carry,
        )

    def _wrap(self, value):
        return value if self.mask is None else value & self.mask

    def set(self, value):
        """Assign the lane's value; a running accumulator continues from it."""
        self.stored = self._wrap(value)
        return self.stored

    def stop(self):
        """Stop the lane's accumulator or live value; the value stays."""
        self.driver, self.spec, self.live, self.hit = Lane.OFF, None, None, 0

    def restart(self):
        """Zero the phase: the flip schedule starts over, going up."""
        self.pos = self.idx = 0
        self.dir = 1

    def start(self, spec: Compiled, by: str):
        """Start ``spec``; the lane's new value, or None when unchanged."""
        out = None if spec.start is None else self.set(spec.start)
        self.hit = 0
        if not spec.moves:
            self.stop()
            return out
        self.driver, self.spec, self.live = Lane.ACC, spec, None
        self.left, self.ecount, self.stopping = spec.for_n, 0, False
        if by in spec.reset:
            self.restart()
        if spec.dir is not None:
            self.dir = spec.dir
        elif spec.const_step is not None and spec.flip is None:
            self.dir = -1 if spec.const_step < 0 else 1
        self.start_dir = 1 if spec.flip is not None else self.dir
        return out

    def set_live(self, fn):
        """Drive the lane by a live expression, evaluated on each advance."""
        self.driver, self.spec, self.live, self.hit = Lane.LIVE, None, fn, 0

    def advance(self, cells):
        """One tick of the lane's driver; the new value, or None when unchanged."""
        self.hit = 0
        if self.driver == Lane.LIVE:
            return self._wrap(self.live(cells, self.stored))
        if self.driver != Lane.ACC:
            return None
        spec = self.spec
        if spec.for_n == 0:
            return None
        out = None
        due = True
        if spec.every:
            self.ecount += 1
            due = self.ecount >= spec.every
            if due:
                self.ecount = 0
        if due:
            out = self.set(self.update(spec, cells))
        if self.left is not None:
            self.left -= 1
            if self.left <= 0:
                self.stop()
        if self.stopping:
            self.stopping = False
            self.stop()
        return out

    def once(self, spec: Compiled, cells):
        """One update by ``spec`` now, leaving the running driver alone; the new value."""
        self.hit = 0
        if spec.start is not None:
            self.set(spec.start)
        if not spec.moves:
            return None if spec.start is None else self.stored
        value = self.set(self.update(spec, cells))
        self.stopping = False
        return value

    def update(self, spec: Compiled, cells) -> int:
        """The accumulator rule: one update of the stored value by ``spec``."""
        old = self.stored
        if spec.toward is not None:
            return self._toward(spec, cells, old)
        if spec.up is not None:
            up = self.dir > 0
            step = (spec.up if up else spec.down)(cells, old)
            carry = spec.up_carry if up else spec.down_carry
        else:
            step = spec.step(cells, old)
            if spec.flip is not None:
                step *= self.dir
            elif spec is self.spec:
                step *= self.dir * self.start_dir
            carry = None
        carry = carry or spec.carry
        if carry is not None:
            step += carry(cells)
        new = old + step
        if spec.mask is not None:
            self.carry = int((old & spec.mask) + (step & spec.mask) > spec.mask)
            new &= spec.mask
        if spec.hi is not None and old <= spec.hi < new:
            new = self._bound(spec.at_hi, spec.hi, new, spec, cells)
        elif spec.lo is not None and old >= spec.lo > new:
            new = self._bound(spec.at_lo, spec.lo, new, spec, cells)
        if spec.flip is not None:
            self.pos += 1
            if self.pos >= spec.flip[min(self.idx, len(spec.flip) - 1)]:
                self.pos = 0
                self.idx = min(self.idx + 1, len(spec.flip))
                self.dir = -self.dir
        return new

    def _toward(self, spec, cells, old):
        target = spec.toward(cells, old)
        if spec.step is None:
            self.hit = 1
            return target
        if old == target:
            return old
        step = spec.step(cells, old)
        new = old + step if old < target else old - step
        if spec.mask is not None:
            new &= spec.mask
        if old < target < new or old > target > new:
            self.hit = 1
            return target
        return new

    def _bound(self, at, bound, new, spec, cells):
        """The value after crossing ``bound``, applying its at-bound options."""
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        self.hit = 1
        if at is None:
            return bound
        clamp, reverse, wrap, stop = at
        if wrap and spec.lo is not None and spec.hi is not None:
            span = spec.hi - spec.lo + 1
            new = new - span if bound == spec.hi else new + span
        if clamp is not None:
            new = clamp(cells, new)
        if reverse:
            self.dir = -self.dir
        if stop:
            self.stopping = True
        return new
