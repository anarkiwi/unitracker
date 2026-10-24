"""``python -m unitracker.optimiser.code6502 MODULE [...]``: optimise each code template of a
module (``CODE`` and ``STATE`` dicts, as unitracker.direct's ``*code`` modules define), prove
each, group equivalent ones and print a markdown table."""

import argparse
import importlib
import os
import runpy
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import z3
from tqdm import tqdm
from unitracker_grammar.code import read, symbols

from unitracker.optimiser.code6502.groups import groups
from unitracker.optimiser.code6502.pipeline import optimise
from unitracker.optimiser.code6502.semantics import semantics


def load(spec: str) -> dict:
    """The globals of a module given as a dotted name or a file path."""
    if spec.endswith(".py") or "/" in spec:
        return runpy.run_path(spec)
    return vars(importlib.import_module(spec))


def cell_state(text: str, cells: dict) -> dict:
    """``{name: size}`` of the state variables ``text`` names, all of one cell."""
    named = set(symbols(read(text)))
    owners = [c for c, variables in cells.items() if named & set(variables)]
    if len(owners) > 1:
        raise ValueError(f"names state of cells {owners}")
    variables = cells[owners[0]] if owners else {}
    return {n: s for n, s in variables.items() if n in named}


def table(results, found) -> str:
    """The markdown results table and the equivalence groups."""
    rows = [
        "| routine | instructions | bytes | worst cycles | terms lifted / z3 / egglog "
        "| leaves superoptimised | proved | differential |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        old, new = r.measures
        rows.append(
            f"| {r.name} | {old.instructions} → {new.instructions} | {old.size} → {new.size} "
            f"| {old.cycles} → {new.cycles} "
            f"| {r.sizes['lifted']} / {r.sizes['z3']} / {r.sizes['egglog']} "
            f"| {sum(f.instructions is not None for _, _, f in r.searches)}/{len(r.searches)} "
            f"| {'yes' if r.proved else 'no'} | {r.differential[0]}/{r.differential[1]} |"
        )
    searches = [f for r in results for _, _, f in r.searches]
    if searches:
        tried, seconds = sum(f.candidates for f in searches), sum(f.seconds for f in searches)
        shorter = sum(f.instructions is not None for f in searches)
        rows.append("")
        rows.append(
            f"superopt: {len(searches)} leaves searched, {shorter} shorter, {tried:.3g} "
            f"candidates in {seconds:.0f} s ({tried / max(seconds, 1e-9):.3g}/s)"
        )
    rows.append("")
    rows.append("| equivalence group | identical optimised text |")
    rows.append("|---|---|")
    for members, same in found:
        names = ", ".join(name for name, _ in members)
        rows.append(f"| {names} | {'yes' if same else 'no'} |")
    if not found:
        rows.append("| (none) | |")
    return "\n".join(rows) + "\n"


def _one(job, cases: int, superopt):
    """A job's :class:`~.pipeline.Result` without its z3 terms, or why it failed."""
    name, text, state = job
    try:
        result = optimise(name, text, state, cases, superopt)
    except (ValueError, AssertionError, z3.Z3Exception) as exc:
        return f"{type(exc).__name__}: {exc}"
    result.semantics = None
    return result


def main(argv=None) -> int:  # pylint: disable=too-many-locals
    """Optimises, proves and tabulates each template of the modules named."""
    parser = argparse.ArgumentParser(prog="python -m unitracker.optimiser.code6502")
    parser.add_argument("modules", nargs="+", help="dotted module names or .py paths")
    parser.add_argument("--only", help="comma-separated template names")
    parser.add_argument("--cases", type=int, default=2000, help="differential cases each")
    parser.add_argument("--show", action="store_true", help="print each optimised template")
    parser.add_argument(
        "--superopt", type=int, default=0, help="superoptimise final leaves up to this length"
    )
    parser.add_argument("--backend", default="auto", choices=("auto", "numpy", "cuda"))
    parser.add_argument("-o", "--out", type=Path, help="write the table here")
    parser.add_argument("-j", "--jobs", type=int, default=os.cpu_count(), help="processes")
    args = parser.parse_args(argv)
    only = set(args.only.split(",")) if args.only else None
    jobs = []
    for spec in args.modules:
        module = load(spec)
        prefix = Path(spec).stem if spec.endswith(".py") else spec.rsplit(".", 1)[-1]
        for name, text in module["CODE"].items():
            if only is None or name in only:
                jobs.append((f"{prefix}.{name}", text, cell_state(text, module["STATE"])))
    superopt = {"max_length": args.superopt, "backend": args.backend} if args.superopt else None
    results, failed = [], []
    with ProcessPoolExecutor(args.jobs) as pool:
        futures = [pool.submit(_one, job, args.cases, superopt) for job in jobs]
        for job, future in tqdm(zip(jobs, futures), total=len(jobs), desc="routines"):
            result = future.result()
            if isinstance(result, str):
                failed.append((job[0], result))
            else:
                result.semantics = semantics(read(result.before), job[2])
                results.append(result)
    found = groups(results)
    text = table(results, found)
    if failed:
        text += "\n| failed | why |\n|---|---|\n"
        text += "".join(f"| {n} | {why} |\n" for n, why in failed)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    if args.show:
        for r in results:
            sys.stdout.write(f"\n{r.name}:\n{r.after}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
