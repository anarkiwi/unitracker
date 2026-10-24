"""Expression forms fitted to a code modulator call's firings: each form a template text over
``v``, φ, ``N``, ``n`` and integer parameters, a fit of its parameters from the firings and its
value on them (numpy), for the lossy mode (docs/optimiser.md)."""

from dataclasses import dataclass, field

import numpy as np
from unitracker_grammar import frequency

PHI = "phi"

BITS = {"freq": 16, "pw": 12, "cut": 11}
# the base a form's shape adds to: the slot's input, nothing, or the sounding or event note
BASES = {"v": "v", "0": "0", "F(N)": "N", "F(n)": "n"}
_PERIOD = 64


@dataclass(frozen=True)
class Tolerance:
    """A cell's error bound: its 95th percentile and maximum, in cents for the frequency, in
    register units for the pulse width and cutoff, or there in percent of the reference value
    where ``relative``."""

    p95: float
    max: float
    relative: bool = False

    @classmethod
    def parse(cls, text: str) -> "Tolerance":
        """``P95,MAX``, each optionally ending ``%`` (relative)."""
        p95, top = text.split(",")
        relative = p95.endswith("%") or top.endswith("%")
        return cls(float(p95.rstrip("%")), float(top.rstrip("%")), relative)

    def __str__(self):
        unit = "%" if self.relative else ""
        return f"{self.p95:g}{unit},{self.max:g}{unit}"


TOLERANCES = {"freq": Tolerance(10, 25), "pw": Tolerance(32, 128), "cut": Tolerance(16, 64)}


def error(cell: str, tol: Tolerance, pred: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """The error of each predicted value of ``cell`` against its reference: cents for the
    frequency (infinite where exactly one is 0), else the register distance, or its percent of
    the reference where ``tol`` is relative."""
    pred, ref = pred.astype(np.float64), ref.astype(np.float64)
    if cell == "freq":
        with np.errstate(divide="ignore", invalid="ignore"):
            cents = np.abs(1200 * np.log2(pred / ref))
        return np.where(pred == ref, 0.0, np.where((pred > 0) & (ref > 0), cents, np.inf))
    out = np.abs(pred - ref)
    return out * 100 / np.maximum(ref, 1) if tol.relative else out


def stats(err: np.ndarray) -> tuple:
    """``(p50, p95, max)`` of errors, zeros where there are none."""
    if err.size == 0:
        return 0.0, 0.0, 0.0
    return tuple(float(x) for x in np.percentile(err, [50, 95, 100], method="higher"))


def within(err: np.ndarray, tol: Tolerance) -> bool:
    """Whether errors keep to ``tol``."""
    _, p95, top = stats(err)
    return p95 <= tol.p95 and top <= tol.max


def signed(x: np.ndarray, bits: int) -> np.ndarray:
    """``x`` wrapped to ``bits`` as a signed difference."""
    half = 1 << (bits - 1)
    return (x + half) % (1 << bits) - half


def _wmedian(x: np.ndarray, w: np.ndarray) -> int:
    order = np.argsort(x, kind="stable")
    cum = np.cumsum(w[order])
    return int(round(x[order][np.searchsorted(cum, cum[-1] / 2)]))


def summary(phi: np.ndarray, r: np.ndarray) -> tuple:
    """``(u, m, w)``: each φ fired at, the median residual there and its firing count."""
    order = np.lexsort((r, phi))
    phi, r = phi[order], r[order]
    u, start, w = np.unique(phi, return_index=True, return_counts=True)
    return u, r[start + (w - 1) // 2], w


# ---- shapes: g(φ) added to a base, each (text, fit, value) ------------------------------------


def _fit_off(_u, m, w):
    return {"q": _wmedian(m, w)}


def _fit_lin(u, m, w):
    if len(u) < 2:
        return None
    s = int(round(np.polyfit(u, m, 1, w=np.sqrt(w))[0]))
    return {"q": _wmedian(m - s * u, w), "s": s} if s else None


def _fit_ramp(sign):
    """A line on the φ short of the extreme (the maximum for ``sign`` 1), held there."""

    def fit(u, m, w):
        cap = int(m.max() if sign > 0 else m.min())
        free = m != cap
        lin = _fit_lin(u[free], m[free], w[free])
        if lin is None or np.sign(lin["s"]) != sign:
            return None
        return lin | {"m": cap}

    return fit


def _square(u, h, o):
    return (u + o) // h % 2


def _fit_sq(u, m, w):
    """Two levels alternating every ``h`` φ from offset ``o``: the ``(h, o)`` of least weighted
    absolute error over every half period to :data:`_PERIOD` and every offset."""
    best = (np.inf, 0, 0)
    for h in range(1, min(_PERIOD, int(u.max()) + 1) + 1):
        o = np.arange(2 * h)[:, None]
        hi = (u[None, :] + o) // h % 2 == 1
        lo_w, hi_w = (w * ~hi).sum(1), (w * hi).sum(1)
        lo_v = np.round((w * m * ~hi).sum(1) / np.maximum(lo_w, 1))
        hi_v = np.round((w * m * hi).sum(1) / np.maximum(hi_w, 1))
        cost = (w * np.abs(m - np.where(hi, hi_v[:, None], lo_v[:, None]))).sum(1)
        k = int(np.argmin(cost))
        if cost[k] < best[0]:
            best = (cost[k], h, k)
    _, h, o = best
    hi = _square(u, h, o) == 1
    if hi.all() or not hi.any():
        return None
    q = _wmedian(m[~hi], w[~hi])
    swing = _wmedian(m[hi], w[hi]) - q
    return {"q": q, "w": swing, "o": o, "h": h} if swing else None


def _triangle(u, h, o):
    return np.abs((u + o) % (2 * h) - h)


def _fit_tri(u, m, w):
    """``q + s*|(φ + o) % 2h - h|``: per half period ``h`` to :data:`_PERIOD` and offset ``o``,
    ``q`` and ``s`` by weighted least squares, the ``(h, o)`` of least weighted absolute
    error."""
    best = (np.inf, 0, 0, 0, 0)
    for h in range(1, min(_PERIOD, int(u.max()) + 1) + 1):
        tri = _triangle(u[None, :], h, np.arange(2 * h)[:, None]).astype(np.float64)
        mt, mm = (w * tri).sum(1) / w.sum(), (w * m).sum() / w.sum()
        var = (w * (tri - mt[:, None]) ** 2).sum(1)
        slope = np.round((w * (tri - mt[:, None]) * (m - mm)).sum(1) / np.maximum(var, 1e-9))
        level = np.round(mm - slope * mt)
        cost = (w * np.abs(m - level[:, None] - slope[:, None] * tri)).sum(1)
        cost[slope == 0] = np.inf
        k = int(np.argmin(cost))
        if cost[k] < best[0]:
            best = (cost[k], h, k, int(level[k]), int(slope[k]))
    cost, h, o, q, slope = best
    if not np.isfinite(cost):
        return None
    return {"q": q, "s": slope, "o": o, "h": h}


def _filled(u, m) -> np.ndarray:
    """The median residual at every φ to the last fired at, one not fired at taking the one
    before."""
    values = np.zeros(int(u.max()) + 1, dtype=np.int64)
    values[u] = m
    seen = np.zeros(len(values), dtype=bool)
    seen[u] = True
    return values[np.maximum.accumulate(np.where(seen, np.arange(len(values)), 0))]


def _fit_tab(u, m, w):
    """A table of the residual at each φ to its last change, held after."""
    del w
    values = _filled(u, m)
    change = np.flatnonzero(np.diff(values))
    last = int(change[-1]) + 1 if len(change) else 0
    return {"table": tuple(int(x) for x in values[: last + 1]), "l": last}


def _fit_loop(u, m, w):
    """A table of the residual at each φ to ``l + p``, from ``l`` on repeating every ``p``: the
    shortest such table over two periods or more of the firings."""
    del w
    values = _filled(u, m)
    best = None
    for p in range(1, len(values) // 2 + 1):
        differ = np.flatnonzero(values[p:] != values[:-p])
        l = int(differ[-1]) + 1 if len(differ) else 0
        if len(values) - l >= 2 * p and (best is None or l + p < sum(best)):
            best = (l, p)
    if best is None or best[1] == 1:
        return None
    l, p = best
    return {"table": tuple(int(x) for x in values[: l + p]), "l": l, "p": p}


def _loop_index(phi, p):
    return np.minimum(phi, p["l"]) + np.maximum(phi - p["l"], 0) % p["p"]


SHAPES = {
    "keep": ("0", lambda u, m, w: {}, lambda phi, p: np.zeros_like(phi)),
    "off": ("q", _fit_off, lambda phi, p: np.full_like(phi, p["q"])),
    "lin": (f"q + s*{PHI}", _fit_lin, lambda phi, p: p["q"] + p["s"] * phi),
    "up": (
        f"min(q + s*{PHI}, m)",
        _fit_ramp(1),
        lambda phi, p: np.minimum(p["q"] + p["s"] * phi, p["m"]),
    ),
    "down": (
        f"max(q + s*{PHI}, m)",
        _fit_ramp(-1),
        lambda phi, p: np.maximum(p["q"] + p["s"] * phi, p["m"]),
    ),
    "sq": (
        f"q + w*(({PHI} + o) // h % 2)",
        _fit_sq,
        lambda phi, p: p["q"] + p["w"] * _square(phi, p["h"], p["o"]),
    ),
    "tri": (
        f"q + s*abs(({PHI} + o) % (2*h) - h)",
        _fit_tri,
        lambda phi, p: p["q"] + p["s"] * _triangle(phi, p["h"], p["o"]),
    ),
    "tab": (
        f"TABLE[k + min({PHI}, l)] - z",
        _fit_tab,
        lambda phi, p: np.asarray(p["table"], dtype=np.int64)[np.minimum(phi, p["l"])],
    ),
    "loop": (
        f"TABLE[k + min({PHI}, l) + max({PHI} - l, 0) % p] - z",
        _fit_loop,
        lambda phi, p: np.asarray(p["table"], dtype=np.int64)[_loop_index(phi, p)],
    ),
}
TABLES = ("tab", "loop")
LATE, HELD = 1, 2


@dataclass
class Form:
    """A fitted form: its shape, base, how it waits ``d`` φ before acting (``delay``: 0 not,
    :data:`LATE` adding nothing to its base, :data:`HELD` passing its input), its parameters
    and its errors over the firings it was fitted to."""

    shape: str
    base: str
    delay: int
    params: dict
    err: np.ndarray = field(repr=False)

    @property
    def key(self) -> tuple:
        """What forms of one template share: their shape, base and delay."""
        return self.shape, self.base, self.delay

    def text(self) -> str:
        """The form's template text, every parameter by its name."""
        if self.shape == "first":
            return f"v + ({PHI} < l)*({self.base} + q - v)"
        if self.shape == "toward":
            return f"v + max(-s, min(s, {self.base} - v))"
        g = SHAPES[self.shape][0]
        if self.delay & LATE:
            g = f"({PHI} >= d)*({g})"
        if self.delay & HELD:
            return f"v + ({PHI} >= {'e' if self.delay & LATE else 'd'})*({self.base} + {g} - v)"
        return g if self.base == "0" else f"{self.base} + {g}"


class Firings:  # pylint: disable=too-few-public-methods
    """A call's firings on one cell, with each base's value at each, and where each takes
    the value its voice's firing before left (a chain, along which errors carry)."""

    def __init__(self, rec: np.ndarray, cell: str, clock_hz: int):
        self.cell, self.bits = cell, BITS[cell]
        self.mask = (1 << self.bits) - 1
        order = np.lexsort((rec["t"], rec["voice"]))
        rec = rec[order]
        notes = np.array([frequency(k, clock_hz) for k in range(256)] + [0], dtype=np.int64)
        self.phi, self.out = rec["phi"].astype(np.int64), rec["out"].astype(np.int64)
        self.base = {"v": rec["v"].astype(np.int64), "0": np.zeros_like(self.phi)}
        if cell == "freq":
            for base, k in BASES.items():
                if k in "Nn":
                    x = rec[k].astype(np.int64)
                    self.base[base] = notes[np.where((x >= 0) & (x < 256), x, 256)]
        chained = np.zeros(len(rec), dtype=bool)
        chained[1:] = (rec["voice"][1:] == rec["voice"][:-1]) & (rec["v"][1:] == rec["out"][:-1])
        self.start = np.maximum.accumulate(np.where(chained, 0, np.arange(len(rec))))

    def predict(self, form: Form) -> np.ndarray:
        """The cell value ``form`` gives at each firing, a chain's input the value its form
        gave the firing before."""
        phi, p, v = self.phi, form.params, self.base["v"]
        if form.shape == "first":
            anchor = (phi < p["l"]) | (self.start == np.arange(len(phi)))
            at = np.maximum.accumulate(np.where(anchor, np.arange(len(phi)), 0))
            first = (self.base[form.base] + p["q"]) & self.mask
            return np.where(phi < p["l"], first, v)[at]
        if form.shape == "toward":
            start = self.start == np.arange(len(phi))
            return approach(start, v, self.base[form.base], p["s"]) & self.mask
        g = SHAPES[form.shape][2](phi, p)
        if form.delay & LATE:
            g = np.where(phi >= p["d"], g, 0)
        if form.delay & HELD:
            held = p["e"] if form.delay & LATE else p["d"]
            return np.where(phi >= held, (self.base[form.base] + g) & self.mask, v)
        if form.base != "v":
            return (self.base[form.base] + g) & self.mask
        total = np.cumsum(g)
        return (v[self.start] + total - total[self.start] + g[self.start]) & self.mask


def _first(fir: Firings) -> list:
    """The ``first`` forms: the input kept from φ ``l`` on, a base plus ``a`` before."""
    moved = fir.out != fir.base["v"]
    if not moved.any():
        return []
    l = int(fir.phi[moved].max()) + 1
    if l > 4:
        return []
    early = fir.phi < l
    out = []
    for base in fir.base:
        if base == "v" or base == "0" and fir.cell == "freq":
            continue
        r = signed(fir.out[early] - fir.base[base][early], fir.bits)
        out.append(("first", base, False, {"q": _wmedian(r, np.ones_like(r)), "l": l}))
    return out


def _toward(fir: Firings) -> list:
    """The ``toward`` forms: the input moved toward a note by at most ``s`` per firing, ``s``
    the median move."""
    moves = np.abs(signed(fir.out - fir.base["v"], fir.bits))
    if not moves.any() or fir.cell != "freq":
        return []
    s = int(np.median(moves[moves > 0]))
    return [("toward", base, 0, {"s": s}) for base in ("F(N)", "F(n)")]


def approach(start: np.ndarray, v: np.ndarray, target: np.ndarray, s: int) -> np.ndarray:
    """Each firing's input moved toward ``target`` by at most ``s``, a firing not ``start``ing
    a chain taking the one before's output as its input."""
    out = np.empty_like(v)
    prev = 0
    for i, given in enumerate(v):
        x = given if start[i] else prev
        prev = x + min(s, max(-s, target[i] - x))
        out[i] = prev
    return out


def _delays(shape: str, base: str, late: int, held: int, after: int) -> dict:
    """``{delay: its parameters}`` a ``shape`` on ``base`` is fitted with: none, adding nothing
    until φ ``late``, passing the input until φ ``held``, and that then nothing until
    ``after``."""
    out = {0: {}}
    shaped = shape not in ("keep", *TABLES)
    if late and shaped:
        out[LATE] = {"d": late}
    if held and base != "v":
        out[HELD] = {"d": held}
        if after > held and shaped:
            out[HELD | LATE] = {"e": held, "d": after}
    return out


# pylint: disable-next=too-many-locals
def candidates(fir: Firings) -> list:
    """``(shape, base, delay, params)`` of every form fitted to ``fir``."""
    out = _first(fir) + _toward(fir)
    moves = fir.out != fir.base["v"]
    held = int(fir.phi[moves].min()) if moves.any() else 0
    for base, value in fir.base.items():
        r = fir.out if base == "0" else signed(fir.out - value, fir.bits)
        u, m, w = summary(fir.phi, r)
        moved = u[m != 0]
        late = int(moved.min()) if base != "0" and len(moved) else 0
        later = moved[moved >= held] if held and base not in ("0", "v") else ()
        after = int(later.min()) if len(later) else 0
        for shape, (_, fit, _) in SHAPES.items():
            if (shape, base) == ("keep", "0") or (shape, base, fir.cell) == ("off", "0", "freq"):
                continue
            for delay, wait in _delays(shape, base, late, held, after).items():
                keep = u >= wait.get("d", 0)
                params = fit(u[keep], m[keep], w[keep])
                if params is not None:
                    out.append((shape, base, delay, params | wait))
    return out


def forms(fir: Firings, tol: Tolerance) -> list:
    """Every form fitted to ``fir``, with its errors under ``tol``'s measure."""
    out = []
    for shape, base, delay, params in candidates(fir):
        form = Form(shape, base, delay, params, np.empty(0))
        form.err = error(fir.cell, tol, fir.predict(form), fir.out)
        out.append(form)
    return out


def fits(fir: Firings, tol: Tolerance) -> list:
    """The forms fitted to ``fir`` whose errors keep to ``tol``."""
    return [f for f in forms(fir, tol) if within(f.err, tol)]


def nearest(fir: Firings, tol: Tolerance) -> str:
    """The form fitted to ``fir`` of least maximum error, and its 95th percentile and maximum
    error, as text."""
    best = min(forms(fir, tol), key=lambda f: (stats(f.err)[2], stats(f.err)[1]))
    _, p95, top = stats(best.err)
    return f"{stem(*best.key)} p95 {p95:.1f} max {top:.1f}"


# ---- names: a template's name by its shape and base, its parameters' by their role ------------

_KIND = {"v": "v", "0": "0", "F(N)": "N", "F(n)": "n"}
_STEMS = {
    "keep": {"v": "thru", "N": "pitch"},
    "off": {"v": "ramp", "0": "level", "N": "detune"},
    "lin": {"v": "accel", "0": "line", "N": "glide"},
    "up": {"v": "accelup", "0": "rise", "N": "glideup"},
    "down": {"v": "acceldown", "0": "fall", "N": "glidedown"},
    "sq": {"v": "stepsq", "0": "square", "N": "vibsq"},
    "tri": {"v": "steptri", "0": "tri", "N": "vib"},
    "tab": {"v": "steps", "0": "seq", "N": "pitchseq"},
    "loop": {"v": "stepcycle", "0": "cycle", "N": "pitchcycle"},
    "first": {"0": "start", "N": "note"},
    "toward": {"N": "glideto"},
}
_ROLES = {
    "q": {"v": "step", "0": "lo", "N": "offset"},
    "s": {"v": "accel", "0": "step", "N": "step"},
    "m": "limit",
    "w": "depth",
    "o": "phase",
    "h": "half",
    "k": "at",
    "l": "last",
    "p": "period",
    "z": "bias",
    "d": "after",
    "e": "held",
}
_ALTERNATES = {
    "step": ("inc", "delta"),
    "lo": ("low", "floor"),
    "offset": ("shift", "detune"),
    "accel": ("rate", "curve"),
    "limit": ("cap", "bound"),
    "depth": ("amp", "swing"),
    "phase": ("skew", "lag"),
    "half": ("width", "span"),
    "at": ("pos", "origin"),
    "last": ("tail", "stop"),
    "period": ("cycle", "repeat"),
    "bias": ("lift", "raise"),
    "after": ("delay", "onset"),
    "held": ("hold2", "keepin"),
    "value": ("val", "amount"),
    "len": ("count", "ticks"),
}
_NEUTRAL = {"q": 0, "o": 0, "k": 0}


def natural(shape: str, param: str, values) -> bool:
    """Whether ``param`` of a ``shape`` form stays a parameter of its template with
    ``values`` in its calls: any but a table's ``z`` and one at its neutral value in all."""
    neutral = 1 if (shape, param) == ("first", "l") else _NEUTRAL.get(param)
    return param != "z" and any(x != neutral for x in values)


def stem(shape: str, base: str, delay: int) -> str:
    """A form's template name by what it does: ``ramp`` a constant step on the input,
    ``level`` a constant, ``vib`` a triangle about the note; ``ev`` before one about the event
    note, ``late`` before one adding nothing, ``held`` one passing its input, until φ ``d``."""
    kind = _KIND[base]
    name = _STEMS[shape].get("N" if kind == "n" else kind, shape)
    return ("", "late", "held", "heldlate")[delay] + ("ev" if kind == "n" else "") + name


def role(shape: str, base: str, param: str) -> str:
    """Parameter ``param``'s name in a form of ``shape`` on ``base``."""
    if shape == "first":
        return {"q": "value" if base == "0" else "offset", "l": "len"}[param]
    if shape == "off" and base == "0":
        return "value"
    named = _ROLES[param]
    return named if isinstance(named, str) else named[_KIND[base] if base != "F(n)" else "N"]


def vacant(name: str, taken) -> str:
    """``name``, else its first alternate, else ``name`` and the least count, not in
    ``taken``."""
    for option in (name, *_ALTERNATES.get(name, ())):
        if option not in taken:
            return option
    k = 2
    while f"{name}{k}" in taken:
        k += 1
    return f"{name}{k}"
