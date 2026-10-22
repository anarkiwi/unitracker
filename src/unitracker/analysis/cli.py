"""``unitracker-modfit``: fit, arithmetise and orbit-test playroutine modulations."""

import argparse
import contextlib
import sys
from pathlib import Path

import z3
from tqdm import tqdm

from unitracker.analysis import exprs
from unitracker.modfit import playroutine, resolve, specs
from unitracker.modfit.arith import Arith
from unitracker.modfit.compose import command_lines
from unitracker.modfit.generic import PLAYERS, report as generic_report
from unitracker.modfit.keywords import table as keyword_table
from unitracker.modfit.fit import Fitter, model_text, src
from unitracker.modfit.invariant import fit_under, invariants
from unitracker.modfit.orbit import report
from unitracker.modfit.sweeps import report as sweeps_report


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
        if model:
            lines += reachable_lines(fitter, (name, path, cell), actions, guard)
    return lines


def reachable_lines(fitter: Fitter, unit: tuple, actions, guard) -> list:
    """The fit of a refuted dispatch group re-proved under the cell's inductive invariants."""
    invs = invariants(*unit)
    if not invs:
        return ["      no inductive invariant"]
    inv, found = fit_under(fitter, actions, unit[2], guard, invs)
    if found:
        return [f"      proved under invariant {inv}: {found}"]
    cands = fitter.candidates(unit[2], fitter.cases(fitter.paths(actions, unit[2]), unit[2]))
    f = fitter.form
    model = fitter.check(
        fitter.paths(actions, unit[2]), (*guard, f.truth(f.expr(inv, {}))), unit[2], cands[0]
    )[1]
    return [f"      refuted under invariant {inv}: against {cands[0]}: {model_text(model)}"]


def check_lines(name: str, path: str, cell: str, candidate: dict, guard: str = "") -> list:
    """A given candidate for a modulation, under an optional guard, proved or refuted."""
    pr = playroutine(name)
    actions, fitter = resolve(pr, path), Fitter(pr)
    f = fitter.form
    conds = (f.truth(f.expr(guard, {})),) if guard else ()
    result, model = fitter.check(fitter.paths(actions, cell), conds, cell, candidate)
    verdict = "proved" if result == z3.unsat else "refuted"
    when = f" when {guard}" if guard else ""
    lines = [f"== {name}:{path} -> {cell}", f"   check {candidate}{when}: {verdict}"]
    return lines + ([f"      witness {model_text(model)}"] if model else [])


def poly_lines(name: str, path: str) -> list:
    """The arithmetic form of an operator, one line per bracket monomial."""
    arith = Arith(playroutine(name))
    lines = [f"== {name}:{path}"]
    for cell, expr in arith.operator(resolve(arith.form.pr, path)).items():
        lines.append(f"  {cell}' = {arith.pretty(expr)}")
    return lines


SONGS = [
    f".tunecache/MUSICIANS/{p}.sid"
    for p in (
        "C/Cadaver/Metal_Warrior_Title_Remix",
        "I/Ilkke/Crosseyed",
        "H/Hermit/Csillag_szazketto_kettoskereszt",
    )
]


def run(args) -> list:
    """Report lines of the chosen subcommand."""
    # pylint: disable=too-many-return-statements
    if args.command == "fit":
        if args.units:
            units = [u.split(":", 2) for u in args.units]
            return [x for u in tqdm(units, desc="fit", unit="cell") for x in fit_lines(*u)]
        table = specs()
        return [
            x for f in tqdm(table["fits"], desc="fit", unit="cell") for x in fit_lines(*f.values())
        ] + [
            x
            for c in tqdm(table["checks"], desc="check", unit="cell")
            for x in check_lines(*c.values())
        ]
    if args.command == "command":
        units = [(u.split(":") + [None])[:2] for u in args.units or specs()["commands"]]
        return [x for name, kw in units for x in command_lines(name, kw)]
    if args.command == "keywords":
        return keyword_table(tuple(args.units) or ("goattracker2", "defmon", "sidwizard"))
    if args.command == "generic":
        return generic_report(tuple(args.units) or PLAYERS, args.ticks)
    if args.command == "sweeps":
        table = specs()
        fused = [x for u in table["fits"] if ":" in u["cell"] for x in fit_lines(*u.values())]
        fused += [x for c in table["checks"] if ":" in c["cell"] for x in check_lines(*c.values())]
        return sweeps_report(((Path(p), exprs.load(p).ir) for p in args.units), fused)
    if args.command == "exprs":
        return exprs.report(args.units or SONGS, args.workers)
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
    sub.add_parser("command", help="compose keywords into setup and per-tick effects").add_argument(
        "units", nargs="*", metavar="PLAYROUTINE[:NAME]"
    )
    sub.add_parser("keywords", help="match keyword and field signatures").add_argument(
        "units", nargs="*", metavar="PLAYROUTINE"
    )
    generic = sub.add_parser("generic", help="factorise every effect as a generic accumulator")
    generic.add_argument("units", nargs="*", metavar="PLAYROUTINE")
    generic.add_argument("--ticks", type=int, default=600)
    sub.add_parser(
        "sweeps", help="bounce orbits, fused pair fits and songs' table programs"
    ).add_argument("units", nargs="*", metavar="SONG")
    report_cmd = sub.add_parser(
        "exprs", help="every song modulator as proved phase and value expressions"
    )
    report_cmd.add_argument("units", nargs="*", metavar="SONG")
    report_cmd.add_argument("--workers", type=int, help="processes (default: the host's cores)")
    orbit = sub.add_parser("orbit", help="test vibrato orbits as reflect accumulators")
    orbit.add_argument("units", nargs="*", metavar="PLAYROUTINE")
    orbit.add_argument("--ticks", type=int, default=600)
    args = ap.parse_args(argv)
    if args.command == "exprs" and not args.out:
        args.out = "out/modfit/exprs.txt"
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(run(args)) + "\n"
    with (
        open(args.out, "w", encoding="utf-8") if args.out else contextlib.nullcontext(sys.stdout)
    ) as fh:
        fh.write(text)
