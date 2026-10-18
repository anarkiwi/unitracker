"""The canonical passes: modulation written shattered, found on the internal song.

A ``!freq`` run's groups (one per occurrence) play as one expression ``E`` where
``E(φ + S_k) = e_k(φ)`` over each group's firings, ``S_k`` the firings before group ``k``
(:func:`agrees`); docs/optimise.md, Canonical passes.
"""

import functools
import itertools

import sympy
import z3
from sympy.core.function import AppliedUndef

from unitracker.format.codec import EXPR, NONE, CodecError, command_code
from unitracker.format.events.lifetime import starts
from unitracker.format.refs import entries, slots, view
from unitracker.format.xexpr import ExpressionError, canonical
from unitracker.modfit import convert
from unitracker.modfit import grammar as G
from unitracker.modfit.phase import Order, _clipped, _ramp, by_phase, shortest_lasso
from unitracker.modfit.phase import Trace as Segments
from unitracker.optimise.model import ERRORS, currents, incoming, play_order
from unitracker.player import reach
from unitracker.player.modulation import unit_command, units

FREQ = "freq"
VALUES = ("cut", "ad", "sr", "vol")
_PROBES = ((0, 40), (12345, 61))
_SAMPLE = 64


def freq_codes(ir: dict) -> tuple | None:
    """``(code, none)``: the ``!freq`` command's code and ``!freq:none``'s argument (None where
    the player has none); None for a player without ``!freq``."""
    code = command_code(ir["playroutine"].get("commands"), FREQ, EXPR)
    if code is None:
        return None
    try:
        return code, view(ir).commands.decode(FREQ, NONE)[1]
    except CodecError:
        return code, None


def freq_arg(event: dict, code: int):
    """The expression index of an event's ``!freq``, or None."""
    return next((a for c, a in slots(event) if c == code and a), None)


def value_of(ir: dict, event: dict, key: str):
    """The value an event's command for shared key ``key`` sets, or None."""
    for code, arg in slots(event):
        try:
            name, value = view(ir).commands.encode(code, arg) if code else (None, None)
        except CodecError:
            continue
        if name == key and isinstance(value, int):
            return value
    return None


def played(ir: dict) -> list:
    """The phrases the tracks play, in the order the voices first play them."""
    return sorted({p["phrase"] for t in ir["tracks"] for p in t["plays"]}, key=play_order(ir))


def runs(events: list, carries) -> list:
    """``(start, end)`` of each maximal run of events ``carries`` accepts, no note after the
    first."""
    out, k = [], 0
    while k < len(events):
        if not carries(events[k]):
            k += 1
            continue
        j = k + 1
        while j < len(events) and "note" not in events[j] and carries(events[j]):
            j += 1
        out.append((k, j))
        k = j
    return out


_FIRINGS = {}


def firings(ir: dict) -> dict:
    """``{(phrase, event): firings}`` of each event that opens one occurrence of the unit that
    plays ``!freq``, alike wherever the song plays it; None for an event that opens differing
    ones or one inside it. ``{}`` for a song whose state does not recur."""
    key = convert.ir_key(ir)
    if key not in _FIRINGS:
        _FIRINGS.clear()
        _FIRINGS[key] = _observed(ir)
    return _FIRINGS[key]


def _freq_units(ir: dict) -> set:
    """The names of the units the ``!freq`` command drives."""
    pr, code = ir["playroutine"], (freq_codes(ir) or (None,))[0]
    return {u["name"] for u in units(pr) if code is not None and unit_command(pr, u) == code}


def _observed(ir: dict) -> dict:
    names = _freq_units(ir)
    song = convert.expand_repeats(ir)
    try:
        ticks = convert.window(song) if names else 0
    except (reach.ReachabilityError, *ERRORS):
        return {}
    obs = convert.Observer(song)
    obs.run(ticks, desc="firings")
    per = {}
    for occ in (o for o in obs.occurrences if o.unit in names and o.instance is not None):
        per.setdefault(occ.instance, []).append(occ.firings)
    places = {name: _places(events) for name, events in song["phrases"].items()}
    seen = {}
    for (voice, count, step), fired in per.items():
        name = song["tracks"][voice]["plays"][obs.plays[voice][count]]["phrase"]
        for place in places[name][step : step + 1]:
            seen.setdefault((name, place[0]), set()).add(tuple(fired) if place[1] else ())
    return {at: _single(f) for at, f in seen.items()}


def _places(events: list) -> list:
    """``(event, whether it is the event's first)`` of each step of a phrase."""
    edges = itertools.accumulate((int(e.get("steps", 1)) for e in events), initial=0)
    return [(k, t == a) for k, (a, b) in enumerate(itertools.pairwise(edges)) for t in range(a, b)]


def _single(fired: set):
    one = next(iter(fired))
    return one[0] if len(fired) == 1 and len(one) == 1 else None


def chains(ir: dict) -> list:
    """``(phrase, groups)`` of each maximal sequence of a ``!freq`` run's groups whose firings
    :func:`firings` gives; a group is ``(argument, start, end, firings)``."""
    fc = freq_codes(ir)
    if fc is None:
        return []
    carries = functools.partial(lambda e, c: freq_arg(e, c) is not None, c=fc[0])
    found = [(n, *r) for n in played(ir) for r in runs(ir["phrases"][n], carries)]
    fired = firings(ir) if found else {}
    out = []
    for name, k, j in found:
        args, chain = [freq_arg(e, fc[0]) for e in ir["phrases"][name][k:j]], []
        for arg, group in itertools.groupby(range(k, j), key=lambda i, a=args, k=k: a[i - k]):
            group = list(group)
            n = fired.get((name, group[0]))
            if isinstance(n, int) and n > 0 and all((name, i) not in fired for i in group[1:]):
                chain.append((arg, group[0], group[-1] + 1, n))
            else:
                out.append((name, chain))
                chain = []
        out.append((name, chain))
    return [(name, chain) for name, chain in out if chain]


@functools.lru_cache(maxsize=None)
def term(text: str):
    """The grammar term of an expression text."""
    return G.parse(canonical(str(text)))


def _pseudo(k):
    return (int(k) * 40503 + 1) & 0xFFFF


def _numeric(e):
    fn = G.compiled(e)
    kw = {n: _pseudo for n in fn.__code__.co_varnames[3 : fn.__code__.co_argcount]}
    return lambda v, phi, n: fn(v, phi, n, **kw)


@functools.lru_cache(maxsize=None)
def agrees(a: str, b: str, shift: int, count: int | None) -> bool:
    """Whether expression ``a`` at ``φ + shift`` is ``b`` at ``φ`` for every ``v``, ``N``,
    frequency word and table, over ``0 <= φ < count`` (every ``φ`` for None): refuted at
    sample points, else decided by z3."""
    try:
        ea, eb = term(a), term(b)
        fa, fb = _numeric(ea), _numeric(eb)
        probe = range(_SAMPLE if count is None else count)
        if any(fa(v, p + shift, n) != fb(v, p, n) for v, n in _PROBES for p in probe):
            return False
        return _proved(ea.subs(G.PHI, G.PHI + shift), eb, count)
    except (ExpressionError, ZeroDivisionError, *ERRORS):
        return False


def _proved(ea, eb, count: int | None) -> bool:
    """Whether z3 proves ``ea == eb`` for every ``v``, ``N``, function and ``0 <= φ < count``."""
    phi, facts = z3.BitVec("φ", G.BITS), []
    env = {
        "v": z3.BitVec("v", G.BITS),
        "N": z3.BitVec("N", G.BITS),
        "φ": G.Phase(lambda *cs: facts.extend(cs), phi),
    }
    for f in ea.atoms(AppliedUndef) | eb.atoms(AppliedUndef):
        sort = [z3.BitVecSort(G.BITS)] * (len(f.args) + 1)
        env.setdefault(type(f).__name__, z3.Function(f"u_{type(f).__name__}", *sort))
    za, zb = G.to_z3(ea, env), G.to_z3(eb, env)
    solver = z3.Solver()
    solver.add(*facts, phi >= 0, env["N"] >= 0, za != zb)
    if count is not None:
        solver.add(phi < count)
    return solver.check() == z3.unsat


def fit(items: list, sizes: list):
    """The shortest term in ``φ`` that is ``items[k]`` over the ``sizes[k]`` positions of each
    in turn: the grammar's step and ramp forms of the sequence, or None."""
    want = [c for c, n in zip(items, sizes) for _ in range(n)]
    tokens = [str(c) for c in items]
    forms = [_ramp(Segments([(sympy.Integer(c), 0, n) for c, n in zip(items, sizes)]), G.PHI)]
    for loop in range(len(items)):
        prefix, period = shortest_lasso(tokens, sizes, loop)
        path = _clipped([(k, n, 1) for k, n in enumerate(sizes)], prefix + period)
        o = Order("time", [(k, n) for k, n, _ in path], prefix, period, [1] * len(path), {})
        forms += by_phase([sympy.Integer(items[k]) for k, _ in o.runs], o)
    exact = [f for f in forms if [_numeric(f)(0, p, 0) for p in range(len(want))] == want]
    return G.shortest(exact) if exact else None


def _stitch(name: str, chain: list, a: int, b: int, text: str) -> tuple:
    return ("stitch", (name, chain[a][1], chain[b - 1][2], canonical(text)))


def _offsets(chain: list) -> list:
    return list(itertools.accumulate((g[3] for g in chain), initial=0))


def staircases(ir: dict) -> list:
    """A run of one value command whose values are a function of the step: one expression
    command at its first step."""
    out = []
    for name in played(ir):
        events = ir["phrases"][name]
        for key in VALUES:
            for k, j in runs(events, lambda e, key=key: value_of(ir, e, key) is not None):
                values = [value_of(ir, e, key) for e in events[k:j]]
                found = fit(values, [int(e.get("steps", 1)) for e in events[k:j]])
                if len(set(values)) > 1 and found is not None:
                    out.append(("staircase", (name, k, j, key, G.text(found))))
    return out


def slides(ir: dict) -> list:
    """Groups of constant offsets ``v + c``: one expression in ``φ``."""
    exprs, out = ir.get("exprs") or {}, []
    for name, chain in chains(ir):
        offsets = [sympy.expand(term(exprs[g[0]]) - G.V) for g in chain]
        constant = [c.is_Integer for c in offsets]
        for steady, group in itertools.groupby(range(len(chain)), key=constant.__getitem__):
            group = list(group)
            if steady and len(group) > 1:
                c = fit([int(offsets[i]) for i in group], [chain[i][3] for i in group])
                out += (
                    [_stitch(name, chain, group[0], group[-1] + 1, G.text(G.V + c))]
                    if c is not None
                    else []
                )
    return out


def restitch(ir: dict) -> list:
    """Groups that continue the first's expression: that expression from the first."""
    exprs, out = ir.get("exprs") or {}, []
    for name, chain in chains(ir):
        at, j = _offsets(chain), 0
        while j < len(chain):
            first, k = str(exprs[chain[j][0]]), j + 1
            while k < len(chain) and agrees(
                first, str(exprs[chain[k][0]]), at[k] - at[j], chain[k][3]
            ):
                k += 1
            out += [_stitch(name, chain, j, k, first)] if k - j > 1 else []
            j = k
    return out


def anchors(ir: dict) -> list:
    """A group whose expression is another of the song's ``a`` translated, ``a(φ + d)``, with
    the groups ``d`` firings before it playing ``a``'s first values: ``a`` from those groups."""
    exprs, out = ir.get("exprs") or {}, []
    targets = [str(exprs[x]) for x in entries(ir).get(EXPR, [])]
    for name, chain in chains(ir):
        at, end = _offsets(chain), 0
        for k, group in enumerate(chain):
            mine = str(exprs[group[0]])
            spans = [
                (j, a)
                for a in targets
                for j in range(end, k)
                if a not in {str(exprs[g[0]]) for g in chain[j : k + 1]}
                and agrees(a, mine, at[k] - at[j], None)
                and all(
                    agrees(a, str(exprs[g[0]]), at[i] - at[j], g[3])
                    for i, g in enumerate(chain[j:k], j)
                )
            ]
            if spans:
                j, a = min(spans, key=lambda s: s[0])
                out.append(_stitch(name, chain, j, k + 1, a))
                end = k + 1
    return out


def holds(ir: dict) -> list:
    """A ``!freq`` of ``0``, or of ``v`` on an instrument without ``freq``: ``!freq:none``."""
    fc, exprs = freq_codes(ir), ir.get("exprs") or {}
    if fc is None or fc[1] is None:
        return []
    legato = command_code(ir["playroutine"].get("commands"), "legato")
    out = []
    for name, enter in incoming(ir).items():
        events = ir["phrases"][name]
        ins = currents(events, next(iter(enter)) if len(enter) == 1 else None)
        for k, (event, current) in enumerate(zip(events, ins)):
            arg = freq_arg(event, fc[0])
            if arg is not None and not starts(event, legato):
                text = str(exprs[arg])
                held = _bare(ir, current) and agrees(text, "v", 0, None)
                out += [("hold", (name, k))] if held or agrees(text, "0", 0, None) else []
    return out


def _bare(ir: dict, ins) -> bool:
    """Whether instrument ``ins`` is known and has no ``freq``."""
    v = view(ir)
    row = (ir.get("records") or {}).get(v.ins, {}).get(ins - v.base) if ins is not None else None
    return row is not None and not int(row.get(FREQ, 0))
