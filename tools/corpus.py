"""Lift tunes by their direct lifters and verify each document against its native player, and
the pysidtracker survey renderer (``--render corpus:render``).

A tune is a path, a name under ``--hvsc`` or an HVSC name; a ``.csv`` is a survey ``find``
list or an earlier ``check`` table. Usage: README.md, "Triage".
"""

import argparse
import csv
import os
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from pysidtracker.jobs import deadline, imap
from tqdm import tqdm
from unitracker_player.player import play

import shared
from unitracker import cli

FIELDS = ("doc", "path", "md5", "player", "verified", "cells", "first", "gap")


def check_one(row: dict, opts: dict) -> dict:
    """One tune's result row: its format, whether its document plays the native grid over
    ``frames`` ticks, the differing cells and the first, or the exception it raised."""
    res = dict.fromkeys(FIELDS, "")
    res.update(doc=shared.name(row), path=row["path"], md5=row.get("md5", ""), verified="no")
    out = Path(opts["out"])
    try:
        with deadline(opts["timeout"]):
            raw = shared.tune(row["path"], opts["hvsc"]).read_bytes()
            res["player"], _, verify = cli.lifter(raw)
            cells, text = verify(raw, opts["subtune"], opts["frames"])
    except Exception as exc:  # pylint: disable=broad-exception-caught
        res["gap"] = f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
        (out / f"{res['doc']}.err").write_text(res["gap"] + "\n", encoding="utf-8")
        return res
    (out / f"{res['doc']}.yaml").write_text(text, encoding="utf-8")
    res.update(verified="no" if len(cells) else "yes", cells=len(cells))
    if len(cells):
        res["first"] = f"{int(cells[0][0])} ${int(cells[0][1]):02X}"
    return res


def category(row: dict) -> str:
    """A row's category: verified, mismatching, or its gap with numbers and records elided."""
    if row.get("verified") == "yes":
        return "verified"
    if row.get("gap"):
        return "gap: " + re.sub(r"\d+", "N", re.sub(r"\{.*?\}|\[.*?\]", "...", row["gap"]))[:80]
    return "mismatch"


def report(rows: list) -> None:
    """Print the rows, each format's verified count and the categories."""
    for r in rows:
        detail = r["gap"] or (f"{r['cells']} cells from {r['first']}" if r["first"] else "")
        print(f"{r['doc'][:40]:40} {r['player']:12} {r['verified']:3}  {detail}")
    print()
    for player in sorted({r["player"] for r in rows}):
        mine = [r for r in rows if r["player"] == player]
        print(f"{player or '?'}: {sum(r['verified'] == 'yes' for r in mine)}/{len(mine)} verified")
    for cause, count in Counter(category(r) for r in rows).most_common():
        print(f"  {count:4d}  {cause}")


def check(args) -> int:
    """The ``check`` command: write each tune's document or error and ``results.csv``."""
    args.out.mkdir(parents=True, exist_ok=True)
    opts = {"out": str(args.out), "hvsc": args.hvsc, "timeout": args.timeout}
    opts.update(subtune=args.subtune, frames=args.frames)
    tunes = shared.rows(args.tunes or shared.REFERENCES, args.player, args.subtunes)
    rows = [None] * len(tunes)
    tasks = [(row, opts) for row in tunes]
    for i, value, error in tqdm(imap(check_one, tasks, args.jobs), total=len(tasks), unit="sid"):
        rows[i] = value
        if error is not None:
            rows[i] = dict.fromkeys(FIELDS, "") | {
                "doc": shared.name(tunes[i]),
                "path": tunes[i]["path"],
                "verified": "no",
                "gap": f"{type(error).__name__}: {error}",
            }
    rows.sort(key=lambda r: (r["player"], r["doc"]))
    report(rows)
    with (args.out / "results.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return 0


def render(data, song, nframes):
    """The survey renderer: 1-based ``song`` of a ``.sid`` lifted by its direct lifter and its
    document played on unitracker-player."""
    text = cli.lift(data, song - 1)
    played = play(text, ticks=nframes)
    return np.asarray(played, np.int16), {"backend": cli.lifter(data)[0], "chars": len(text)}


def main(argv=None) -> int:
    """Parse the command line and run the command."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0], fromfile_prefix_chars="@")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check", help="lift and verify tunes in parallel; verdicts and causes")
    p.add_argument("tunes", nargs="*", metavar="TUNE.sid|LIST.csv")
    p.add_argument("--hvsc", type=Path, default=os.environ.get("HVSC"))
    p.add_argument("--out", type=Path, default=Path("out/check"))
    p.add_argument("--subtune", type=int, default=0, help="0-based")
    p.add_argument("--frames", type=int, default=3000)
    p.add_argument("--player", default="", help="a list's SIDId players (regex)")
    p.add_argument("--subtunes", type=int, help="a list's tunes with at most this many")
    p.add_argument("--timeout", type=float, default=0, help="seconds per tune (0: none)")
    p.add_argument("--jobs", type=int, default=max(1, len(os.sched_getaffinity(0)) // 4))
    args = ap.parse_args(argv)
    return check(args)


if __name__ == "__main__":
    sys.exit(main())
