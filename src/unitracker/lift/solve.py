"""One note's frequency words, factored into the player's shapes over a parameter bag.

Each shape is derived from the note and then proved with the generic player's own function
(the one the document plays), so a fit is the native word itself, tick for tick.
"""

from unitracker_player.modulation import SHAPES  # the generic player's canonical shapes

F16 = 0xFFFF


def _signed(x: int) -> int:
    return x - 0x10000 if x >= 0x8000 else x


def _fit(name: str, bag: dict, words: list, base: int) -> bool:
    """Whether the player's function ``name`` over ``bag`` is ``words`` at every tick."""
    f = SHAPES[name]
    return all(f(bag, 0, t, base) & F16 == w for t, w in enumerate(words))


def set_(delta: list) -> dict | None:
    """A constant offset: every word the same."""
    if len(set(delta)) == 1:
        return {"offset": delta[0]}
    return None


def _first(delta: list) -> int | None:
    return next((t for t in range(len(delta)) if delta[t] != 0), None)


def _walk(delta: list) -> tuple[int, int] | None:
    """A walk from zero's first step and the delay before it."""
    p = _first(delta)
    if p is None or p == 0:
        return None
    return p - 1, delta[p]


def ramp(delta: list) -> dict | None:
    """A walk from zero: linear to the note's end, held where it clamps to a max, or back at
    zero once its ticks have run."""
    found = _walk(delta)
    if found is None:
        return None
    delay, step = found
    n = len(delta)

    def at(t: int) -> int:
        return 0 if t <= delay else (t - delay) * step

    if delta == [at(t) for t in range(n)]:
        return {"step": step, "delay": delay}
    hi = max(delta)
    if step > 0 and delta == [0 if t <= delay else min((t - delay) * step, hi) for t in range(n)]:
        return {"step": step, "delay": delay, "max": hi}
    r = next((t for t in range(delay + 1, n) if delta[t] == 0), None)
    if r is not None and delta[:r] == [at(t) for t in range(r)] and all(t == 0 for t in delta[r:]):
        return {"step": step, "delay": delay, "ticks": r - delay}
    return None


def slew_to_target(delta: list) -> dict | None:
    """A walk from zero that lands on its target and holds it."""
    found = _walk(delta)
    if found is None:
        return None
    delay, step = found
    target = delta[-1]
    if target * step <= 0:
        return None

    def want(t: int) -> int:
        if t <= delay:
            return 0
        return target if (t - delay) * step >= abs(target) else (t - delay) * step

    if delta == [want(t) for t in range(len(delta))]:
        return {"target": target, "step": step, "delay": delay}
    return None


def slew_to_zero(delta: list) -> dict | None:
    """A start the note holds on its first tick, falling to zero at ``amp`` plus one per tick."""
    s0 = delta[0]
    if not delta or s0 == 0:
        return None
    if len(delta) == 1 or delta[1] == 0:
        return None
    step = abs(s0) - abs(delta[1])
    if step <= 1:
        return None
    return {"start": s0, "amp": step - 1}


def triangle(delta: list) -> dict | None:
    """The native vibrato: equal-amplitude steps on a 128-step period, its second half within
    the extent negative, the first step on the tick its delay elapses."""
    n = len(delta)
    amp = None
    for t in range(1, n):
        d = abs(delta[t] - delta[t - 1])
        if d:
            amp = d if amp is None else amp
            if amp != d:
                return None
    if amp is None:
        return None
    d0 = _first(delta) or 0
    first = delta[d0] - (delta[d0 - 1] if d0 else 0)
    sign = 1 if first > 0 else -1
    i1 = next((t for t in range(1, min(n, d0 + 256)) if (delta[t] - delta[t - 1]) * sign < 0), None)
    extent = 128 if i1 is None else 2 * (i1 - d0 - 1)
    return {"step": amp, "extent": extent, "delay": d0}


def solve(words: list, base: int) -> tuple[str, dict] | None:
    """The shape and bag whose function over ``base`` is ``words`` at every tick, or None: a
    note with no offset (every word the base) factors into no shape at all."""
    delta = [_signed(w - base) for w in words]
    if not any(delta):
        return None
    for name, derive in (
        ("set", set_),
        ("ramp", ramp),
        ("slew_to_target", slew_to_target),
        ("slew_to_zero", slew_to_zero),
        ("triangle", triangle),
    ):
        bag = derive(delta)
        if bag is None:
            continue
        bags = [bag]
        if name == "triangle" and bag["extent"] and bag["extent"] < 126:
            # a triangle and its extent plus one share the ticks before the first negative
            bags.append({**bag, "extent": bag["extent"] + 1})
        for candidate in bags:
            if _fit(name, candidate, words, base):
                return name, candidate
    return None


def emit(bags: list[dict]) -> tuple[dict, list[dict]]:
    """The instrument's first note's bag and each later note's ``!set`` difference, empty for
    a bag equal to the first's."""
    first = bags[0]
    out = []
    for bag in bags[1:]:
        diff = {k: v for k, v in first.items() if bag.get(k) != v}
        for k, v in bag.items():
            if k not in first and v is not None:
                diff[k] = v
        out.append(diff)
    return first, out
