"""Pair the expressions two sets of generic documents write differently and prove each pair equal.

    python tools/expr_diff.py OLD_DIR NEW_DIR [--bits B]

For each ``OLD_DIR/<tune>.yaml`` with a ``NEW_DIR`` namesake, prints every expression (an ``x``
record, or a ``pw``, ``cut`` or ``freq`` field of a defined record) whose text differs, before
and after, with tokens and characters, and whether the two parse to terms equal
for every ``v`` (z3), else for ``0 <= v < 2**B``, else modulo the largest ``2**k`` (``k <= B``) for
that ``v``, as a unit's wrap to ``k`` bits writes them; the number of pairs not in fewer tokens.
"""

import argparse
import re
import sys
from pathlib import Path

import yaml
import z3
from tqdm import tqdm

from unitracker.modfit import grammar as G

FIELDS = {"pw", "cut", "freq"}
NAME = re.compile(r"[a-z]+\d+|none")


def leaves(x, path=()):
    """``(path, value)`` of every scalar of a loaded YAML document."""
    if isinstance(x, dict):
        for k, v in x.items():
            yield from leaves(v, (*path, k))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from leaves(v, (*path, i))
    else:
        yield path, x


def expressions(doc: dict) -> dict:
    """``{path: text}`` of every expression a generic document's records define."""
    out = {}
    for path, value in leaves(doc.get("song") or [], ("song",)):
        if len(path) < 5 or path[2] != "def" or path[3] == "phrase" or not isinstance(value, str):
            continue
        if (path[3] == "x" or path[-1] in FIELDS) and not NAME.fullmatch(value):
            out[path] = value
    return out


def equal(a: str, b: str, bits: int | None, wrap: int | None = None) -> bool:
    """True when grammar texts ``a`` and ``b`` agree for every value (``0 <= v < 2**bits``),
    modulo ``2**wrap``."""
    # pylint: disable=protected-access
    env, facts = {}, []
    za, zb = G._z3_free(G.parse(a), env, facts), G._z3_free(G.parse(b), env, facts)
    if bits is not None and "v" in env:
        facts += [env["v"] >= 0, env["v"] < (1 << bits)]
    solver = z3.Solver()
    differ = za != zb if wrap is None else (za - zb) & ((1 << wrap) - 1) != 0
    solver.add(*facts, differ)
    return solver.check() == z3.unsat


def changed(old: Path, new: Path) -> list:
    """``(tune, path, before, after)`` of every differing expression of the namesake documents."""
    out = []
    for f in sorted(old.glob("*.yaml")):
        if (new / f.name).exists():
            a = expressions(yaml.safe_load(f.read_text(encoding="utf-8")))
            b = expressions(yaml.safe_load((new / f.name).read_text(encoding="utf-8")))
            out += [(f.stem, k, x, b[k]) for k, x in a.items() if b.get(k, x) != x]
    return out


def main(argv=None) -> int:
    """Run the tool."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("old", type=Path)
    ap.add_argument("new", type=Path)
    ap.add_argument("--bits", type=int, default=16)
    args = ap.parse_args(argv)
    longer = 0
    for tune, path, a, b in tqdm(changed(args.old, args.new), desc="pairs", unit="pair"):
        proof = "every v" if equal(a, b, None) else f"v < 2**{args.bits}"
        if proof != "every v" and not equal(a, b, args.bits):
            wraps = [k for k in range(args.bits, 0, -1) if equal(a, b, args.bits, k)]
            proof = f"v < 2**{args.bits} modulo 2**{wraps[0]}" if wraps else "NOT EQUAL"
        sizes = (G.tokens(a), len(a)), (G.tokens(b), len(b))
        longer += sizes[1][0] >= sizes[0][0]
        print(f"{tune} {'/'.join(map(str, path))}: equal for {proof}")
        print(f"  before ({sizes[0][0]} tokens, {sizes[0][1]} chars): {a}")
        print(f"  after  ({sizes[1][0]} tokens, {sizes[1][1]} chars): {b}")
    print(f"{longer} pairs not in fewer tokens")
    return int(longer > 0)


if __name__ == "__main__":
    sys.exit(main())
