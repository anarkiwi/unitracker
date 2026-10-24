"""Lift tunes by their direct lifters and verify each document against its native player, write
each tune's document forms (lifted and optimised), and the pysidtracker survey renderer.

A tune is a path, a name under ``--hvsc`` or an HVSC name; a ``.csv`` is a survey ``find``
list or an earlier ``check`` table. Usage: README.md, "Triage".
"""

import argparse
import csv
import inspect
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from pysidtracker.jobs import deadline, imap
from tqdm import tqdm
from unitracker_grammar.document import printed, read
from unitracker_player.player import Player, load, play

import shared
from unitracker import cli
from unitracker.optimiser import Guarantee, optimise
from unitracker.optimiser.inline import MODES, inline

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


def forms() -> dict:
    """``{form: optimise keywords}`` of the forms this optimiser writes, in dependency order:
    the lifted document (None), the named form, each other inline mode, and lossy when the
    optimiser has it."""
    out = {"lifted": None, "named": {}} | {m: {"inline": m} for m in MODES if m != "named"}
    if "lossy" in inspect.signature(optimise).parameters:
        out["lossy"] = {"lossy": True}
    return out


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


def _form(spec, text: dict, opts: dict) -> str:
    """A form's text: an inline mode of the named form, else the lifted text optimised."""
    if set(spec) == {"inline"} and "named" in text:
        named = text["named"]
        holds = Guarantee(named, opts["ticks"]).holds
        return printed(inline(read(named), spec["inline"], holds)[0])
    return optimise(text["lifted"], opts["ticks"], **spec).text


def forms_one(row: dict, opts: dict) -> dict:
    """One tune's forms under ``out/<form>/<doc>.yaml``, each kept from an earlier run, a failed
    one's exception (a timeout's from the start) in ``<doc>.err``, and ``out/meta/<doc>.json``
    with its format and song length in ticks."""
    out, doc = Path(opts["out"]), shared.name(row)
    meta, text = out / "meta" / f"{doc}.json", {}
    res = {"doc": doc} | dict.fromkeys(opts["forms"], "")
    for name in opts["forms"]:
        path = out / name / f"{doc}.yaml"
        err = path.with_suffix(".err")
        if path.exists():
            text[name], res[name] = path.read_text(encoding="utf-8"), "ok"
            continue
        if err.exists() and not opts["retry"]:
            res[name] = err.read_text(encoding="utf-8").strip()
            continue
        if "lifted" not in text and name != "lifted":
            res[name] = "no lifted document"
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        err.write_text(f"TimeoutError: exceeded {opts['timeout']}s\n", encoding="utf-8")
        try:
            with deadline(opts["timeout"]):
                if name == "lifted":
                    raw = shared.tune(row["path"], opts["hvsc"]).read_bytes()
                    meta.parent.mkdir(parents=True, exist_ok=True)
                    meta.write_text(json.dumps({"player": cli.lifter(raw)[0]}), "utf-8")
                    text[name] = cli.lift(raw, opts["subtune"])
                    ticks = song_ticks(text[name], opts["limit"])
                    meta.write_text(json.dumps(json.loads(meta.read_text()) | {"ticks": ticks}))
                else:
                    text[name] = _form(forms()[name], text, opts)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            res[name] = f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
            err.write_text(res[name] + "\n", encoding="utf-8")
            continue
        path.write_text(text[name], encoding="utf-8")
        err.unlink()
        res[name] = "ok"
    return res | (json.loads(meta.read_text()) if meta.exists() else {})


def write_forms(args) -> int:
    """The ``forms`` command: each tune's forms and ``forms.csv``, its forms' status."""
    opts = {"out": str(args.out), "hvsc": args.hvsc, "timeout": args.timeout}
    opts.update(subtune=args.subtune, ticks=args.ticks, limit=args.limit, retry=args.retry)
    opts["forms"] = [f for f in forms() if f == "lifted" or not args.forms or f in args.forms]
    tunes = shared.rows(args.tunes, args.player, args.subtunes)
    rows = []
    for i, value, error in tqdm(
        imap(forms_one, [(r, opts) for r in tunes], args.jobs), total=len(tunes), unit="sid"
    ):
        rows.append(value or {"doc": shared.name(tunes[i]), "lifted": repr(error)})
    rows.sort(key=lambda r: r["doc"])
    fields = ["doc", "player", "ticks", *opts["forms"]]
    with (args.out / "forms.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    for name in opts["forms"]:
        done = Counter(r.get("player", "") for r in rows if r.get(name) == "ok")
        print(f"{name}: {sum(done.values())}/{len(rows)} {dict(sorted(done.items()))}")
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
    p = sub.add_parser("forms", help="write each tune's lifted and optimised forms, cached")
    p.add_argument("tunes", nargs="+", metavar="TUNE.sid|LIST.csv|DIR")
    p.add_argument("--hvsc", type=Path, default=os.environ.get("HVSC"))
    p.add_argument("--out", type=Path, default=Path("out/tokenize"))
    p.add_argument("--subtune", type=int, default=0, help="0-based")
    p.add_argument("--forms", nargs="*", help=f"of {', '.join(forms())} (default all)")
    p.add_argument("--ticks", type=int, default=3000, help="the optimiser's guarantee horizon")
    p.add_argument("--limit", type=int, default=100000, help="most ticks of a song's length")
    p.add_argument("--player", default="", help="a list's SIDId players (regex)")
    p.add_argument("--subtunes", type=int, help="a list's tunes with at most this many")
    p.add_argument("--timeout", type=float, default=600, help="seconds per form (0: none)")
    p.add_argument("--retry", action="store_true", help="retry forms that failed before")
    p.add_argument("--jobs", type=int, default=max(1, len(os.sched_getaffinity(0)) - 2))
    args = ap.parse_args(argv)
    return write_forms(args) if args.command == "forms" else check(args)


if __name__ == "__main__":
    sys.exit(main())
