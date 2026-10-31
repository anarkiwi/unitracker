"""Lift GoatTracker 2 tunes and verify each document against the reference player, and the
pysidtracker survey renderer.

A tune is a path, a name under ``--hvsc`` or an HVSC name; a ``.csv`` is a survey ``find``
list or an earlier ``check`` table. Usage: README.md, "Triage".
"""

import argparse
import csv
import os
import re
import sys
import traceback
from collections import Counter
from pathlib import Path

import numpy as np
from pysidtracker.jobs import deadline, imap
from tqdm import tqdm
from unitracker_player.player import Player, load, play

import shared
from unitracker import cli

FIELDS = ("doc", "path", "md5", "player", "verified", "cells", "first", "gap")


def failure(exc: Exception) -> str:
    """An exception's one line: its type and its message's first line."""
    return f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"


def write_failure(path: Path, line: str) -> None:
    """``path``: an exception's ``line``, then the traceback being handled."""
    path.write_text(f"{line}\n{traceback.format_exc()}", encoding="utf-8")


def check_one(row: dict, opts: dict) -> dict | None:
    """One tune's result row: its format, whether its document plays the reference grid over
    ``frames`` ticks, the differing cells and the first, or the exception it raised; None for a
    multi-SID tune unless ``opts["multi"]``."""
    res = dict.fromkeys(FIELDS, "")
    res.update(doc=shared.name(row), path=row["path"], md5=row.get("md5", ""), verified="no")
    out = Path(opts["out"])
    try:
        with deadline(opts["timeout"]):
            raw = shared.tune(row["path"], opts["hvsc"]).read_bytes()
            if not opts.get("multi") and shared.multi_sid(raw):
                return None
            res["player"], module = cli.lifter(raw)
            cells, text = module.verify(raw, opts["subtune"], opts["frames"])
    except Exception as exc:  # pylint: disable=broad-exception-caught
        res["gap"] = failure(exc)
        write_failure(out / f"{res['doc']}.err", res["gap"])
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
    opts.update(subtune=args.subtune, frames=args.frames, multi=args.multi_sid)
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
    print(f"{rows.count(None)} multi-SID tunes excluded")
    rows = sorted(filter(None, rows), key=lambda r: (r["player"], r["doc"]))
    report(rows)
    with (args.out / "results.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return 0


def song_ticks(text: str, limit: int) -> int:
    """The ticks a document plays to its end or until a row starts at or before the row that
    started last, its loop (at most ``limit``)."""
    player, starts = Player(load(text)), []
    seq = player._seq  # pylint: disable=protected-access
    start_row = seq._start_row  # pylint: disable=protected-access

    def counted(t, ending):
        start_row(t, ending)
        starts.append((seq.row, t))

    seq._start_row = counted  # pylint: disable=protected-access
    while player.tick < limit and player.step() is not None:
        if len(starts) > 1 and starts[-1][0] <= starts[-2][0]:
            return starts[-1][1]
    return player.tick


def render(data, song, nframes):
    """The survey renderer: 1-based ``song`` of a ``.sid`` lifted and its document
    played on unitracker-player; a multi-SID tune is refused."""
    if shared.multi_sid(data):
        raise ValueError("a multi-SID tune")
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
    p.add_argument("--multi-sid", action="store_true", help="include multi-SID tunes")
    args = ap.parse_args(argv)
    return check(args)


if __name__ == "__main__":
    sys.exit(main())
