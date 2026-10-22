"""Expressions re-timed from a native unit's firings to the generic player's ticks: over a
modulator's ticks the native unit holds on ``skips`` and its firing at tick ``φ`` is its
``k(φ) = φ - Σ (φ >= s)``-th, so a closed form ``g`` plays ``g(k(φ))`` and a recurrence ``f``
plays ``v + h(φ)·(f(v, k(φ)) - v)``, the hold factor ``h`` 0 on a skipped tick.
"""

import ast
import functools
import re
from collections import Counter
from dataclasses import dataclass

from unitracker.playroutine import xexpr

PHI = xexpr.PHI
RUN, STEPS = 3, 3  # the fewest skips a run part and a periodic part spell
_HEAD, _STRIDES = 3, 4  # periodic parts start at one of the first skips, period to the next
_TABLE = re.compile(r"\b([A-Z][A-Za-z]*)\[")


@dataclass(frozen=True)
class Schedule:
    """The ticks ``0 <= φ < length`` of a modulator on which its native unit does not fire."""

    skips: frozenset
    length: int

    def within(self, length: int) -> frozenset:
        """The skips before tick ``length``."""
        return frozenset(s for s in self.skips if s < length)

    def covers(self, other: "Schedule") -> bool:
        """Whether ``other`` is no longer and its skips are this schedule's before its end."""
        return other.length <= self.length and other.skips == self.within(other.length)

    @property
    def lead(self) -> int:
        """The ticks the native unit holds from the start, before its first firing."""
        m = 0
        while m in self.skips:
            m += 1
        return m


def merged(schedules) -> Schedule | None:
    """The one schedule of modulators sharing an expression: the longest, when every other is
    its prefix; None when two differ on a tick both cover, or there are none."""
    schedules = list(schedules)
    if not schedules:
        return None
    longest = max(schedules, key=lambda s: s.length)
    return longest if all(longest.covers(s) for s in schedules) else None


def consensus(schedules) -> Schedule | None:
    """The schedule that covers the most of ``schedules`` (the longer of two that cover as
    many), or None for none."""
    counts = Counter(schedules)
    return max(
        counts,
        key=lambda c: (sum(n for s, n in counts.items() if c.covers(s)), c.length),
        default=None,
    )


@dataclass(frozen=True)
class Part:
    """Skips ``start, start + step, ...`` below ``end`` (None: every one), ``step`` 1 a run;
    ``count`` is ``Σ (φ >= s)`` over them and ``skipped`` 1 on one of them, else 0."""

    start: int
    step: int
    end: int | None

    def _cap(self, text: str, n: int) -> str:
        return text if self.end is None else f"min({text}, {n})"

    def count(self) -> str:
        """``Σ (φ >= s)`` over the part's skips."""
        a, t = self.start, self.step
        n = None if self.end is None else len(range(a, self.end, t))
        if t == 1:
            if n == 1:
                return f"({PHI} >= {a})" if a else "1"
            inner = f"{PHI} + 1" if a == 0 else f"max(0, {PHI} - {a - 1})"
            return self._cap(inner, n)
        if a < t:
            return self._cap(f"({PHI} + {t - a}) // {t}", n)
        return self._cap(f"max(0, ({PHI} - {a - t}) // {t})", n)

    def skipped(self) -> str:
        """1 on one of the part's skips, else 0."""
        a, t = self.start, self.step
        if t == 1:
            if self.end == a + 1:
                return f"({PHI} == {a})"
            low = f"({PHI} >= {a})" if a else "1"
            return low if self.end is None else f"{low} - ({PHI} >= {self.end})"
        ticks = f"({PHI} % {t} == {a % t})"
        low = f"*({PHI} >= {a})" if a >= t else ""
        high = f"*({PHI} < {self.end})" if self.end is not None else ""
        return ticks + low + high


def parts(schedule: Schedule, periodic: bool = True) -> list[Part]:
    """A schedule's skips as parts: runs of at least ``RUN`` ticks, then (with ``periodic``)
    the longest progressions of at least ``STEPS`` of the rest in turn, then single ticks; a
    part reaching the end of the schedule is left unbounded."""
    skips = sorted(schedule.skips)
    out, rest = [], []
    k = 0
    while k < len(skips):
        j = k
        while j + 1 < len(skips) and skips[j + 1] == skips[j] + 1:
            j += 1
        if j - k + 1 >= RUN:
            out.append(Part(skips[k], 1, _end(skips[j] + 1, 1, schedule.length)))
        else:
            rest += skips[k : j + 1]
        k = j + 1
    while periodic and len(rest) >= STEPS:
        best = _progression(rest)
        if len(best) < STEPS:
            break
        step = best[1] - best[0]
        out.append(Part(best[0], step, _end(best[-1] + 1, step, schedule.length)))
        rest = [s for s in rest if s not in set(best)]
    return out + [Part(s, 1, s + 1) for s in rest]


def _end(stop: int, step: int, length: int) -> int | None:
    """None where the progression's next tick is past the schedule, else ``stop``."""
    return None if stop - 1 + step >= length else stop


def _progression(rest: list) -> list:
    """The longest progression of ``rest`` from one of its first members."""
    members, best = set(rest), []
    for i, a in enumerate(rest[:_HEAD]):
        for b in rest[i + 1 : i + 1 + _STRIDES]:
            run = list(range(a, rest[-1] + 1, b - a))
            n = next((k for k, s in enumerate(run) if s not in members), len(run))
            if n > len(best):
                best = run[:n]
    return best


class _Phi(ast.NodeTransformer):
    """``φ`` replaced by an expression."""

    def __init__(self, text: str):
        self.text = text

    def visit_Name(self, node):  # pylint: disable=invalid-name
        """``φ`` as the replacement."""
        return _parse(self.text) if node.id == PHI else node


def _parse(text: str):
    return ast.parse(text.strip(), mode="eval").body


def _spelled(text: str, found: list, hold: str | None, zero: bool) -> str:
    """``text`` over ``k(φ)``; ``hold`` the factor of its update, None for none, which with
    ``zero`` multiplies the value instead."""
    count = " + ".join(p.count() for p in found)
    body = ast.unparse(_Phi(f"{PHI} - ({count})").visit(_parse(text)))
    if hold is None:
        return xexpr.canonical(body)
    if zero:
        return xexpr.canonical(f"({hold}) * ({body})")
    return xexpr.canonical(f"v + ({hold}) * ({body} - v)")


def _compiles(text: str) -> bool:
    """Whether a player that nests each operation in parentheses compiles ``text``."""
    try:
        compile(xexpr.source(text, _TABLE.findall(text)), "<expression>", "eval")
    except (SyntaxError, RecursionError, MemoryError):
        return False
    return True


@functools.lru_cache(maxsize=None)
def retimed(text: str, schedule: Schedule, zero: bool = False) -> str | None:
    """Expression ``text``, whose ``φ`` counts its native unit's firings, with ``φ`` counting
    the ticks of ``schedule``: the shorter of its spellings with and without periodic parts
    that compiles, or None where neither does. A closed form holds ``v`` before its first
    firing, or with ``zero`` is 0 there."""
    if not schedule.skips:
        return text
    closed = not any(isinstance(n, ast.Name) and n.id == "v" for n in ast.walk(_parse(text)))
    out = []
    for found in {tuple(parts(schedule, p)) for p in (True, False)}:
        if not closed:
            hold = "1 - (" + " + ".join(p.skipped() for p in found) + ")"
        else:
            hold = f"{PHI} >= {schedule.lead}" if schedule.lead else None
        try:
            spelled = _spelled(text, list(found), hold, zero and closed)
        except RecursionError:
            continue
        if _compiles(spelled):
            out.append(spelled)
    return min(out, key=lambda t: (len(t), t), default=None)
