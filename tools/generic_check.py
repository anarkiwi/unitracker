"""Verify tunes' lifted generic documents on unitracker-player against their native grids.

``--out`` keeps the documents and results.csv (rows count each tune's differing cells), ``--diff DIR`` each tune's machine-readable diff.

    python tools/generic_check.py [--csv CSV] [--hvsc DIR] [--jobs J] [--out DIR]
        [--diff DIR] [--against RESULTS.csv]
"""

import argparse
import csv
import json
import os
import re
from collections import Counter
from pathlib import Path

import numpy as np

CORPUS = Path("out/sample128")
FIELDS = (
    "doc",
    "player",
    "verified",
    "ticks",
    "onset",
    "onset_reg",
    "motion",
    "whole",
    "whole_reg",
    "cells",
    "gap",
)
GROUPS = ("freq", "freq", "pw", "pw", "ctrl", "ad", "sr")
GLOBALS = ("cut", "cut", "res/route", "mode/vol")


def tunes(csv_path: Path) -> dict:
    """``{document name: HVSC path}`` from a ``sample.csv`` (``path``, ``output``, ``error``: the
    rows that lifted) or a survey ``tunes.csv`` (``path``, ``md5``)."""
    with csv_path.open(newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["path"] and not r.get("error")]
    return {
        r.get("output") or f"{r['md5'][:8]}-{Path(r['path']).stem}.yaml": r["path"] for r in rows
    }


def motion(theirs, ours) -> str:
    """Which grid holds a register across a mismatch's tick, from the two ticks' values:
    ``native holds`` where the native player keeps the value the document changes."""
    if theirs[0] == theirs[1]:
        return "native holds"
    return "document holds" if ours[0] == ours[1] else "both move"


def check(doc: str, sid: str, out: str | None, diff: str | None = None) -> dict:
    """Tune ``sid``'s row, named ``doc``: its player, whether its lift's generic document plays
    the native grid, the first mismatch under the project criterion, which grid holds it, the
    first from tick 0, its differing cell count, or the lifting gap; an exception is its gap."""
    os.environ["UNITRACKER_WORKERS"] = "1"
    from unitracker.lift import backends  # pylint: disable=import-outside-toplevel

    row = dict.fromkeys(FIELDS, "")
    row.update(doc=doc, verified="no")
    try:
        lift = backends.lifter(Path(sid).read_bytes())
        row["player"] = lift.playroutine
        lift.run()
        return _check(lift, row, out, diff)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        row["gap"] = f"{type(exc).__name__}: {exc}"
        return row


def _check(lift, row: dict, out: str | None, diff: str | None) -> dict:
    from unitracker.player import oracle  # pylint: disable=import-outside-toplevel

    verdict, text = lift.verdict, lift.document
    if text is not None and out:
        Path(out, row["doc"]).write_text(text, encoding="utf-8")
    if verdict is None:
        row["gap"] = lift.gap
        return row
    row.update(
        verified="yes" if verdict.ok else "no", ticks=verdict.ticks, cells=len(verdict.cells)
    )
    if diff is not None:
        Path(diff, f"{row['doc']}.diff.json").write_text(
            json.dumps(verdict.summary(), indent=2) + "\n", encoding="utf-8"
        )
    for key, found in (("onset", verdict.onset), ("whole", verdict.whole)):
        if found is not None:
            row[key], row[f"{key}_reg"] = found[0], f"${found[1]:02X}"
    if verdict.onset is not None and verdict.onset[0]:
        tick, reg = verdict.onset
        theirs = np.asarray(lift.native().render_grid(tick + 1))
        ours = oracle.play(text, tick + 1)[0]
        row["motion"] = motion(theirs[tick - 1 : tick + 1, reg], ours[tick - 1 : tick + 1, reg])
    elif verdict.onset is None and not verdict.ok:
        row["gap"] = f"the document ends at tick {verdict.played}"
    return row


def category(row: dict) -> str:
    """A row's category: verified, the register group of its first mismatch and which grid
    holds it, or its gap with its numbers and records elided."""
    if row["verified"] == "yes":
        return "verified"
    if row["gap"]:
        return "gap: " + re.sub(r"\d+", "N", re.sub(r"\{.*?\}|\[.*?\]", "...", row["gap"]))[:80]
    reg = int(row["onset_reg"][1:], 16)
    group = GROUPS[reg % 7] if reg < 21 else GLOBALS[reg - 21]
    return f"mismatch: {group}, {row['motion'] or 'first tick'}"


def report(rows: list) -> None:
    """Print the rows, each player's verified count and the categories."""
    print(f"{'doc':40} {'player':12} {'ok':3} {'onset':>10} {'':14} {'from tick 0':>11}  gap")
    for r in rows:
        onset = f"{r['onset']} {r['onset_reg']}" if r["onset"] != "" else ""
        whole = f"{r['whole']} {r['whole_reg']}" if r["whole"] != "" else ""
        print(
            f"{r['doc'][:40]:40} {r['player']:12} {r['verified']:3} {onset:>10}"
            f" {r['motion']:14} {whole:>11}  {r['gap']}"
        )
    print()
    for player in sorted({r["player"] for r in rows}):
        mine = [r for r in rows if r["player"] == player]
        print(f"{player}: {sum(r['verified'] == 'yes' for r in mine)}/{len(mine)} verified")
    for name, count in Counter(category(r) for r in rows).most_common():
        print(f"  {count:4d}  {name}")


def against(rows: list, path: Path) -> None:
    """Print each row's first mismatch beside the one of the same document in ``path``."""
    with path.open(newline="") as fh:
        before = {r["doc"]: r for r in csv.DictReader(fh)}

    def where(r: dict | None) -> str:
        if r is None:
            return "-"
        if r["verified"] == "yes":
            return "verified"
        return f"{r['onset']} {r['onset_reg']}".strip() or r["gap"][:40]

    print(f"\n{'doc':40} {'before':>12} {'after':>12}")
    for r in rows:
        print(f"{r['doc'][:40]:40} {where(before.get(r['doc'])):>12} {where(r):>12}")


def main(argv=None) -> int:
    """Command-line entry."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--hvsc", type=Path, default=Path(os.environ.get("HVSC", ".")))
    parser.add_argument("--jobs", type=int, default=max(1, len(os.sched_getaffinity(0)) // 4))
    parser.add_argument("--out", type=Path, help="keep the generic documents and results.csv")
    parser.add_argument("--diff", type=Path, help="keep each tune's machine-readable diff")
    parser.add_argument("--against", type=Path, help="an earlier results.csv to compare with")
    args = parser.parse_args(argv)
    sources = (CORPUS / "tunes.csv", CORPUS / "sample.csv")
    csv_path = args.csv or next((p for p in sources if p.is_file()), None)
    if csv_path is None:
        parser.error("no tunes.csv or sample.csv to read the tunes from")
    for path in (args.out, args.diff):
        if path:
            path.mkdir(parents=True, exist_ok=True)
    from unitracker.modfit.exprs import Pool  # pylint: disable=import-outside-toplevel

    out = str(args.out) if args.out else None
    diff = str(args.diff) if args.diff else None
    tasks = [(doc, str(args.hvsc / path), out, diff) for doc, path in tunes(csv_path).items()]
    pool = Pool(args.jobs, len(tasks))
    try:
        rows = sorted(pool.map(check, tasks, "verify"), key=lambda r: (r["player"], r["doc"]))
    finally:
        pool.close()
    report(rows)
    if args.against:
        against(rows, args.against)
    if args.out:
        with (args.out / "results.csv").open("w", newline="") as fh:
            writer = csv.DictWriter(fh, FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
