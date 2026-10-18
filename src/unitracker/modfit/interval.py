"""One-dimensional intervals: the range of a symbol or remainder that bracket facts bound, and
the value a remainder, quotient, ``abs`` or bracket of a linear function takes over one."""

import sympy


def bounds(context: tuple, domain: tuple) -> dict:
    """``{x: (lo, hi)}`` of each symbol or remainder by a constant ``x`` that facts ``[x + c >= 0]``
    or ``[x + c == 0]`` of ``context`` bound, within its own range; ``u % m == w`` bounds
    ``(u - w) % m`` to 0."""
    out = {}
    for b, value in context:
        rel = b.args[0]
        line = atom(rel.lhs, domain)
        if line is None and isinstance(rel, sympy.Eq) and value:
            line = pinned(rel.lhs, domain)
        if line is not None:
            x, c = line[0], -line[1]
            lo, hi = out.get(x, extent(x, domain))
            if isinstance(rel, sympy.Ge):
                out[x] = (max(lo, c), hi) if value else (lo, min(hi, c - 1))
            elif value:
                out[x] = max(lo, c), min(hi, c)
            else:
                out[x] = lo + int(lo == c), hi - int(hi == c)
    return out


def atom(e, domain: tuple):
    """``(x, c)`` when ``e`` is ``x + c``, ``x`` a symbol or a remainder by a constant; a
    remainder of a symbol plus a constant that stays in one period over the symbol's range is
    read as that symbol."""
    const, x = e.as_coeff_Add()
    if not (const.is_Integer and (x.is_Symbol or remainder(x))):
        return None
    if remainder(x):
        d, sym = x.args[0].as_coeff_Add()
        lo, hi = extent(sym, domain) if sym.is_Symbol else (0, sympy.oo)
        q = sympy.floor((lo + d) / x.args[1])
        if q == sympy.floor((hi + d) / x.args[1]):
            return sym, const + d - q * x.args[1]
    return x, const


def pinned(e, domain: tuple):
    """:func:`atom` of ``(u - w) % m`` when ``e`` is ``±(u % m - w)``, alike modulo ``m`` where
    ``e == 0``."""
    rems = [t for t in sympy.Add.make_args(e) if remainder(t) or remainder(-t)]
    if len(rems) != 1:
        return None
    sign = 1 if remainder(rems[0]) else -1
    u, m = (sign * rems[0]).args
    rest = sympy.Mod(sympy.expand(u + sign * (e - rems[0])), m)
    return atom(rest, domain) if remainder(rest) else None


def remainder(x) -> bool:
    """True when ``x`` is a remainder by a positive constant."""
    return isinstance(x, sympy.Mod) and x.args[1].is_Integer and x.args[1] > 0


def extent(x, domain: tuple) -> tuple:
    """``(lo, hi)`` of a symbol over ``domain`` or of a remainder ``% m``, ``±oo`` unbounded."""
    if remainder(x):
        return sympy.Integer(0), x.args[1] - 1
    given = [(sympy.Integer(lo), sympy.Integer(hi)) for n, lo, hi in domain if n == x.name]
    return given[0] if given else (sympy.Integer(0) if x.is_nonnegative else -sympy.oo, sympy.oo)


def settled(n, y, low, high):
    """``n``, a ``//``, ``abs`` or bracket of ``y``, as what it is wherever ``low <= y <= high``,
    else ``n``."""
    if isinstance(n, sympy.floor):
        return sympy.floor(low) if sympy.floor(low) == sympy.floor(high) else n
    if isinstance(n, sympy.Abs):
        return y if low >= 0 else -y if high <= 0 else n
    if isinstance(n.args[0], sympy.Eq):
        return sympy.Integer(low == 0) if low == high or low > 0 or high < 0 else n
    return sympy.Integer(bool(low >= 0)) if low >= 0 or high < 0 else n
