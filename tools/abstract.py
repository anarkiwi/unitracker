"""Learn operator abstractions from the arithmetic (Iverson-bracket polynomial) form.

Each written cell of each operator is ``cell' = P(...)``, a canonical polynomial whose own
cell is renamed ``self``; Stitch learns the lambda abstractions that best compress the
corpus, and every rewritten update is expanded back and checked against its input.
"""

import argparse
import re
from pathlib import Path

import sympy
import yaml
from stitch_core import compress
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1] / "src" / "unitracker" / "playroutines"
UNITS = [
    "goattracker2:records.tickn.1.do",
    "goattracker2:records.tickn.2.do",
    "goattracker2:procs.toneporta",
    "goattracker2:procs.vibrato",
    "goattracker2:record_types.pulsetab.each",
    "goattracker2:record_types.filttab.each",
    "defmon:procs.pitch",
    "defmon:procs.pulse",
    "sidwizard:procs.tick_porta",
    "sidwizard:procs.tick_vibrato",
    "sidwizard:procs.tick_pw",
    "sidwizard:procs.tick_filt",
]


def resolve(doc, path: str):
    """The node at dotted ``path``; integer-looking keys also match int keys."""
    for part in path.split("."):
        doc = doc[int(part)] if part.isdigit() and int(part) in doc else doc[part]
    return doc


def name(text: str) -> str:
    """A Stitch-safe symbol."""
    return re.sub(r"[^A-Za-z0-9_]", "_", text)


def rename(t, cell: str):
    """``t`` with the updated cell as ``self`` and symbols made Stitch-safe."""
    if isinstance(t, tuple):
        return tuple(rename(x, cell) for x in t)
    if isinstance(t, str):
        return "self" if t == cell else name(t)
    return t


def nest(head, args):
    """A variadic sum or product as right-nested binary applications."""
    out = args[-1]
    for a in reversed(args[:-1]):
        out = (head, a, out)
    return out


def to_sexpr(e, cell: str, conds: dict):
    """S-expression tuple of a sympy polynomial, the updated cell as ``self``."""
    # pylint: disable=import-outside-toplevel
    from opform import sexpr

    if e.is_Integer:
        return int(e)
    if e.is_Symbol:
        if e.name in conds:
            return ("B", rename(sexpr(conds[e.name]), cell))
        return "self" if e.name == cell else name(e.name)
    args = [to_sexpr(a, cell, conds) for a in e.args]
    if e.func in (sympy.Add, sympy.Mul):
        return nest("+" if e.func is sympy.Add else "*", sorted(args, key=show))
    return ("pow" if e.func is sympy.Pow else name(str(e.func)), *args)


def show(t) -> str:
    """S-expression text of a tuple tree."""
    return "(" + " ".join(show(x) for x in t) + ")" if isinstance(t, tuple) else str(t)


def parse(text: str):
    """Tuple tree of an s-expression."""
    stack = [[]]
    for tok in text.replace("(", " ( ").replace(")", " ) ").split():
        if tok == "(":
            stack.append([])
        elif tok == ")":
            done = tuple(stack.pop())
            stack[-1].append(done)
        else:
            stack[-1].append(int(tok) if tok.lstrip("-").isdigit() else tok)
    return stack[0][0]


def substitute(body, args):
    """``body`` with each hole ``#i`` replaced by ``args[i]``."""
    if isinstance(body, tuple):
        return tuple(substitute(x, args) for x in body)
    if isinstance(body, str) and body.startswith("#"):
        return args[int(body[1:])]
    return body


def expand(t, abstractions: dict):
    """Beta-reduce every saturated abstraction application; application is curried."""
    if not isinstance(t, tuple):
        return expand((t,), abstractions) if abstractions.get(t, (1,))[0] == 0 else t
    head = expand(t[0], abstractions) if isinstance(t[0], tuple) else t[0]
    t = (head, *(expand(x, abstractions) for x in t[1:]))
    while isinstance(t[0], tuple):
        t = (*t[0], *t[1:])
    arity, body = abstractions.get(t[0], (None, None))
    if arity is None or len(t) <= arity:
        return t[0] if len(t) == 1 else t
    head, rest = substitute(body, t[1 : 1 + arity]), t[1 + arity :]
    if rest:
        head = (*head, *rest) if isinstance(head, tuple) else (head, *rest)
    return expand(head, abstractions)


def tokens(text: str) -> set:
    """The symbols of an s-expression text."""
    return set(text.replace("(", " ").replace(")", " ").split())


def polynomials(units, cache: Path) -> list:
    """``[(unit, cell, poly, conds)]``, cached by unit in ``cache``."""
    # pylint: disable=import-outside-toplevel
    import pickle

    from arith import Arith

    store = pickle.loads(cache.read_bytes()) if cache.exists() else {}
    arith = {}
    for unit in tqdm(units, desc="arithmetize", unit="op"):
        if unit in store:
            continue
        key, path = unit.split(":")
        if key not in arith:
            with open(ROOT / f"{key}.yaml", encoding="utf-8") as fh:
                arith[key] = Arith(yaml.safe_load(fh))
        a = arith[key]
        store[unit] = [
            (cell, poly, dict(a.conds))
            for cell, poly in a.operator(resolve(a.form.pr, path)).items()
        ]
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(pickle.dumps(store))
    return [(u, cell, poly, conds) for u in units for cell, poly, conds in store[u]]


def reads(cond, cell: str) -> bool:
    """True when a condition term mentions ``cell``."""
    if isinstance(cond, tuple):
        return any(reads(x, cell) for x in cond)
    return cond == cell


def dynamics(cell: str, poly, conds: dict) -> list:
    """``[(dispatch monomial, delta)]``: ``cell' - cell`` grouped by brackets that do not read it."""
    x = sympy.Symbol(cell)
    groups = {}
    for mono in sympy.Add.make_args(sympy.expand(poly - x)):
        fs = sympy.Mul.make_args(mono)
        key = tuple(
            sorted(
                (
                    f
                    for f in fs
                    if f.is_Symbol and f.name in conds and not reads(conds[f.name], cell)
                ),
                key=str,
            )
        )
        groups[key] = groups.get(key, 0) + sympy.Mul(*(f for f in fs if f not in key))
    return [(k, sympy.expand(v)) for k, v in groups.items() if v != 0]


def params(e, cell: str, conds: dict, found: list):
    """``e`` with each maximal subterm not reading ``cell`` replaced by ``P``; numbers stay."""
    if e.is_Number:
        return e
    if cell not in {s.name for s in e.free_symbols} and not any(
        s.name in conds and reads(conds[s.name], cell) for s in e.free_symbols
    ):
        found.append(e)
        return sympy.Symbol("P")
    if e.is_Symbol:
        return e
    return e.func(*(params(a, cell, conds, found) for a in e.args))


def corpus(units) -> tuple[list, list]:
    """``(labels, programs)``: one ``(upd P)`` program per written cell per operator."""
    # pylint: disable=import-outside-toplevel
    from arith import Arith

    arith, labels, programs = {}, [], []
    for unit in tqdm(units, desc="arithmetize", unit="op"):
        key, path = unit.split(":")
        if key not in arith:
            with open(ROOT / f"{key}.yaml", encoding="utf-8") as fh:
                arith[key] = Arith(yaml.safe_load(fh))
        a = arith[key]
        for cell, poly in a.operator(resolve(a.form.pr, path)).items():
            labels.append(f"{unit}:{cell}")
            programs.append(("upd", to_sexpr(sympy.expand(poly), cell, a.conds)))
    return labels, programs


def main():
    """Arithmetize the units, learn abstractions, print them with the rewritten updates."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("units", nargs="*", default=UNITS)
    ap.add_argument("--iterations", type=int, default=12)
    ap.add_argument("--arity", type=int, default=3)
    ap.add_argument(
        "--dynamics", action="store_true", help="learn over per-dispatch deltas with parameters"
    )
    ap.add_argument("--cache", type=Path, default=Path("out/abstract_polys.pkl"))
    args = ap.parse_args()
    if args.dynamics:
        return report_dynamics(args)
    labels, programs = corpus(args.units)
    result = compress([show(p) for p in programs], iterations=args.iterations, max_arity=args.arity)
    abstractions = {a.name: (a.arity, parse(a.body)) for a in result.abstractions}
    print("== abstractions")
    for a in result.abstractions:
        users = [u for u, r in zip(labels, result.rewritten) if a.name in tokens(r)]
        players = sorted({u.split(":")[0] for u in users})
        print(f"{a.name}/{a.arity} {'+'.join(players)} x{len(users)}: {a.body}")
        for u in users:
            print(f"    {u}")
    print("== updates")
    ok = 0
    for label, src, rewritten in zip(labels, programs, result.rewritten):
        same = expand(parse(rewritten), abstractions) == src
        ok += same
        print(
            f"{label}  [{len(show(src))} -> {len(rewritten)}, round-trip {'ok' if same else 'FAIL'}]"
        )
        print(f"   {rewritten}")
    print(f"== round-trip {ok}/{len(programs)}")


def report_dynamics(args):
    """Group per-dispatch deltas by shape, then learn abstractions over the shapes."""
    units, shapes = [], {}
    for unit, cell, poly, conds in polynomials(args.units, args.cache):
        for key, delta in dynamics(cell, poly, conds):
            found = []
            shape = sympy.expand(params(delta, cell, conds, found))
            text = show(("op", to_sexpr(shape, cell, conds)))
            shapes.setdefault(text, []).append((unit, cell, key, found))
            units.append(text)
    print("== delta shapes (self' - self), parameters as P, most shared first")
    order = sorted(
        shapes.items(),
        key=lambda kv: (-len({u.split(":")[0] for u, *_ in kv[1]}), -len(kv[1]), len(kv[0])),
    )
    for text, uses in order:
        players = sorted({u.split(":")[0] for u, *_ in uses})
        print(f"{'+'.join(players)} x{len(uses)}: {text}")
        for unit, cell, key, found in uses:
            guard = "".join(str(k) for k in key) or "always"
            print(f"    {unit}:{cell}  when {guard[:160]}  P = {[str(f)[:80] for f in found]}")
    texts = sorted(shapes)
    result = compress(texts, iterations=args.iterations, max_arity=args.arity)
    print("== abstractions over shapes")
    for a in result.abstractions:
        users = [t for t, r in zip(texts, result.rewritten) if a.name in tokens(r)]
        players = sorted({u.split(":")[0] for t in users for u, *_ in shapes[t]})
        print(f"{a.name}/{a.arity} {'+'.join(players)} x{len(users)}: {a.body}")


if __name__ == "__main__":
    main()
