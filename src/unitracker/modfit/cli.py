"""``unitracker-modfit``: fit, arithmetise and orbit-test playroutine modulations."""

import argparse
import contextlib
import sys

import z3
from tqdm import tqdm

from unitracker.modfit import playroutine, resolve, specs
from unitracker.modfit.arith import Arith
from unitracker.modfit.fit import Fitter, model_text, src
from unitracker.modfit.orbit import report


def fit_lines(name: str, path: str, cell: str) -> list:
    """Each dispatch guard of a modulation with its proved accumulator or a witness."""
    pr = playroutine(name)
    actions, fitter = resolve(pr, path), Fitter(pr)
    lines = [f"== {name}:{path} -> {cell}"]
    for guard, found in fitter.fit(actions, cell):
        lines.append("   when " + (" and ".join(src(g) for g in guard) or "always"))
        if found:
            lines.append(f"      {found}")
            continue
        cand, model = fitter.rejection(actions, guard, cell)
        lines.append("      no single accumulator")
        if cand:
            lines.append(f"      against {cand}: {model_text(model) if model else 'undecided'}")
    return lines


def check_lines(name: str, path: str, cell: str, candidate: dict) -> list:
    """A given candidate for a modulation, proved or refuted with a witness."""
    pr = playroutine(name)
    actions, fitter = resolve(pr, path), Fitter(pr)
    result, model = fitter.check(fitter.form.paths(actions), (), cell, candidate)
    verdict = "proved" if result == z3.unsat else "refuted"
    lines = [f"== {name}:{path} -> {cell}", f"   check {candidate}: {verdict}"]
    return lines + ([f"      witness {model_text(model)}"] if model else [])


def poly_lines(name: str, path: str) -> list:
    """The arithmetic form of an operator, one line per bracket monomial."""
    arith = Arith(playroutine(name))
    lines = [f"== {name}:{path}"]
    for cell, expr in arith.operator(resolve(arith.form.pr, path)).items():
        lines.append(f"  {cell}' = {arith.pretty(expr)}")
    return lines


def run(args) -> list:
    """Report lines of the chosen subcommand."""
    if args.command == "fit":
        if args.units:
            units = [u.split(":") for u in args.units]
            return [x for u in tqdm(units, desc="fit", unit="cell") for x in fit_lines(*u)]
        table = specs()
        return [
            x for f in tqdm(table["fits"], desc="fit", unit="cell") for x in fit_lines(*f.values())
        ] + [
            x
            for c in tqdm(table["checks"], desc="check", unit="cell")
            for x in check_lines(*c.values())
        ]
    if args.command == "poly":
        units = [u.split(":") for u in args.units]
        return [x for u in tqdm(units, desc="poly", unit="op") for x in poly_lines(*u)]
    table = specs()["orbits"]
    return [x for name in args.units or table for x in report(name, table[name], args.ticks)]


def main(argv=None) -> None:
    """Parse arguments and print or write the report."""
    ap = argparse.ArgumentParser(
        prog="unitracker-modfit", description=__doc__.split("\n", maxsplit=1)[0]
    )
    ap.add_argument("--out", help="write the report to this file")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("fit", help="prove accumulator rewrites").add_argument(
        "units", nargs="*", metavar="PLAYROUTINE:PATH:CELL"
    )
    sub.add_parser("poly", help="print the arithmetic form").add_argument(
        "units", nargs="+", metavar="PLAYROUTINE:PATH"
    )
    orbit = sub.add_parser("orbit", help="test vibrato orbits as reflect accumulators")
    orbit.add_argument("units", nargs="*", metavar="PLAYROUTINE")
    orbit.add_argument("--ticks", type=int, default=600)
    args = ap.parse_args(argv)
    text = "\n".join(run(args)) + "\n"
    with (
        open(args.out, "w", encoding="utf-8") if args.out else contextlib.nullcontext(sys.stdout)
    ) as fh:
        fh.write(text)
