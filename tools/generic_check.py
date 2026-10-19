"""Verify lifted documents on unitracker-player against their tunes' native register grids.

    python tools/generic_check.py [DOC.yaml ...] [--csv CSV] [--hvsc DIR] [--jobs J] [--out DIR]

Default: ``out/sample128/*.yaml``, tunes by ``sample.csv`` or the survey ``tunes.csv``'s md5.
"""

import argparse
import csv
import os
import re
from collections import Counter
from pathlib import Path

import numpy as np

CORPUS = Path("out/sample128")
MAX_TICKS, TICKS = 100_000, 3007  # a song whose state does not recur by MAX_TICKS plays TICKS
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
    "gap",
)
GROUPS = ("freq", "freq", "pw", "pw", "ctrl", "ad", "sr")
GLOBALS = ("cut", "cut", "res/route", "mode/vol")


def tune_paths(docs: list[Path], csv_path: Path) -> dict:
    """``{doc: HVSC path}`` from a ``sample.csv`` (``output``, ``path``) or a survey
    ``tunes.csv`` (``md5``, ``path``) whose md5 prefix starts the document's name."""
    with csv_path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    by_output = {r["output"]: r["path"] for r in rows if r.get("output")}
    by_md5 = {r["md5"][:8]: r["path"] for r in rows if r.get("md5")}
    found = ((d, by_output.get(d.name) or by_md5.get(d.name.split("-", 1)[0])) for d in docs)
    return {doc: path for doc, path in found if path}


def motion(theirs, ours) -> str:
    """Which grid holds a register across a mismatch's tick, from the two ticks' values:
    ``native holds`` where the native player keeps the value the document changes."""
    if theirs[0] == theirs[1]:
        return "native holds"
    return "document holds" if ours[0] == ours[1] else "both move"


def check(doc: str, sid: str, out: str | None) -> dict:
    """One document's row (player, verified, the first mismatch under the project criterion,
    which grid holds it, the first from tick 0, or the lifting gap); an exception is its gap."""
    os.environ["UNITRACKER_WORKERS"] = "1"
    from unitracker.format import songio  # pylint: disable=import-outside-toplevel

    ir = songio.load(doc)
    row = dict.fromkeys(FIELDS, "")
    row.update(doc=Path(doc).name, player=ir["playroutine"]["name"], verified="no")
    try:
        return _check(ir, row, sid, out)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        row["gap"] = f"{type(exc).__name__}: {exc}"
        return row


def _check(ir: dict, row: dict, sid: str, out: str | None) -> dict:
    # pylint: disable=import-outside-toplevel,too-many-locals
    from unitracker.format import generic
    from unitracker.lift import backends, verify
    from unitracker.player import oracle

    try:
        native = generic.native_state(ir, MAX_TICKS, TICKS)
        text = generic.dumps(ir, native)
    except generic.GenericError as exc:
        row["gap"] = str(exc)
        return row
    if out:
        Path(out, row["doc"]).write_text(text, encoding="utf-8")
    grid = np.asarray(backends.native_player(Path(sid).read_bytes()).render_grid(native.ticks))
    verdict = verify.compare(text, grid)
    row.update(verified="yes" if verdict.ok else "no", ticks=verdict.ticks)
    for key, found in (("onset", verdict.onset), ("whole", verdict.whole)):
        if found is not None:
            row[key], row[f"{key}_reg"] = found[0], f"${found[1]:02X}"
    if verdict.onset is not None and verdict.onset[0]:
        ours = oracle.play(text, verdict.onset[0] + 1)[0]
        row["motion"] = motion(*(g[-2:, verdict.onset[1]] for g in (grid[: len(ours)], ours)))
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


def main(argv=None) -> int:
    """Command-line entry."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("docs", nargs="*", type=Path)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--hvsc", type=Path, default=Path(os.environ.get("HVSC", ".")))
    parser.add_argument("--jobs", type=int, default=max(1, len(os.sched_getaffinity(0)) // 4))
    parser.add_argument("--out", type=Path, help="keep the generic documents and results.csv")
    args = parser.parse_args(argv)
    docs = args.docs or sorted(CORPUS.glob("*.yaml"))
    sources = (CORPUS / "sample.csv", CORPUS / "tunes.csv")
    csv_path = args.csv or next((p for p in sources if p.is_file()), None)
    if csv_path is None:
        parser.error("no sample.csv or tunes.csv to map the documents to their tunes")
    tunes = tune_paths(docs, csv_path)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
    from unitracker.modfit.exprs import Pool  # pylint: disable=import-outside-toplevel

    out = str(args.out) if args.out else None
    tasks = [(str(doc), str(args.hvsc / path), out) for doc, path in tunes.items()]
    pool = Pool(args.jobs, len(tasks))
    try:
        rows = sorted(pool.map(check, tasks, "verify"), key=lambda r: (r["player"], r["doc"]))
    finally:
        pool.close()
    report(rows)
    if len(docs) > len(tunes):
        print(f"{len(docs) - len(tunes)} documents have no tune in {csv_path}")
    if args.out:
        with (args.out / "results.csv").open("w", newline="") as fh:
            writer = csv.DictWriter(fh, FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
