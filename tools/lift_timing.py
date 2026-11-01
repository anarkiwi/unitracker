"""Time the lift (which includes its verification against the reference) of every tune in a
players table whose player has a lifter, on the whole song.

``python tools/lift_timing.py [PLAYERS.csv] [--root HVSC] [--jobs J] [--limit S] [--slow S]
[--only TEXT] [--max-tunes N] [--out DIR]`` where PLAYERS.csv has ``path`` and ``player`` columns.
Each lift runs in a worker process under a ``--limit`` second alarm; one over ``--slow`` seconds
is lifted again under cProfile for its top functions. Writes ``DIR/lift_times.csv`` and
``DIR/profiles.txt`` (default ``out/timing``), and prints the maximum, median and the slow tunes.
"""

import argparse
import cProfile
import csv
import io
import pstats
import signal
import statistics
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm
from unitracker.cli import lift
from unitracker.lift.shared import LiftError

PLAYERS = {"GoatTracker_V2.x", "Music_Assembler", "DefMon", "Hermit/SidWizard_V1.x"}
ROOT = Path("/scratch/datasets/hvsc/C64Music")
TOP = 5


class Expired(Exception):
    """A lift over the time limit."""


def _expired(*_):
    raise Expired


def verdict(raw: bytes, limit: int) -> tuple:
    """``(seconds, verdict)`` of lifting ``raw``: ``ok``, the LiftError's class, ``timeout`` or
    ``error``."""
    signal.signal(signal.SIGALRM, _expired)
    signal.alarm(limit)
    start = time.perf_counter()
    try:
        lift(raw)
        what = "ok"
    except Expired:
        what = "timeout"
    except LiftError as exc:
        what = type(exc).__name__ + ": " + str(exc)[:80]
    except Exception as exc:  # pylint: disable=broad-exception-caught
        what = "error: " + type(exc).__name__ + ": " + str(exc)[:80]
    finally:
        signal.alarm(0)
    return time.perf_counter() - start, what


def profile(raw: bytes, limit: int) -> str:
    """The top :data:`TOP` functions by cumulative time of lifting ``raw``, within ``limit``
    seconds."""
    prof = cProfile.Profile()
    signal.signal(signal.SIGALRM, _expired)
    signal.alarm(limit)
    try:
        prof.runcall(lift, raw)
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    finally:
        signal.alarm(0)
    out = io.StringIO()
    stats = pstats.Stats(prof, stream=out).sort_stats("cumulative")
    stats.print_stats(TOP + 12)
    rows = [r for r in out.getvalue().splitlines() if "{built-in method builtins.exec}" not in r]
    return "\n".join(r[:160] for r in rows if r.strip())


def timed(path: str, player: str, root: Path, limit: int, slow: float) -> dict:
    """``{path, player, seconds, verdict, profile}`` of one tune."""
    raw = (root / path).read_bytes()
    seconds, what = verdict(raw, limit)
    text = profile(raw, limit) if seconds > slow and what != "timeout" else ""
    return {"path": path, "player": player, "seconds": seconds, "verdict": what, "profile": text}


def tunes(table: Path, root: Path, only: str, count: int | None) -> list:
    """The ``(path relative to root, player)`` rows of ``table`` of a lifted player."""
    with open(table, newline="", encoding="utf-8") as handle:
        rows = [(r["path"], r["player"]) for r in csv.DictReader(handle) if r["player"] in PLAYERS]
    rows = [(str(Path(p).relative_to(root)) if p.startswith("/") else p, k) for p, k in rows]
    return [r for r in rows if only in r[0]][:count]


def report(rows: list, slow: float) -> list:
    """The lines summarising ``rows``: the maximum, median and each tune over ``slow`` seconds."""
    seconds = [r["seconds"] for r in rows]
    lines = [
        f"tunes {len(rows)} max {max(seconds):.2f} s median {statistics.median(seconds):.2f} s"
    ]
    for r in sorted(rows, key=lambda r: -r["seconds"]):
        if r["seconds"] > slow:
            lines.append(f"{r['seconds']:7.2f} s {r['verdict'][:30]:30} {r['player']} {r['path']}")
    return lines


def main(argv=None) -> int:
    """Time the lifts and write the table and the slow tunes' profiles."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("table", nargs="?", type=Path, default=Path("out/writeorder/players.csv"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--slow", type=float, default=10.0)
    parser.add_argument("--only", default="")
    parser.add_argument("--max-tunes", type=int)
    parser.add_argument("--out", type=Path, default=Path("out/timing"))
    args = parser.parse_args(argv)
    todo = tunes(args.table, args.root, args.only, args.max_tunes)
    rows = []
    with ProcessPoolExecutor(args.jobs) as pool:
        futures = [pool.submit(timed, p, k, args.root, args.limit, args.slow) for p, k in todo]
        for future in tqdm(as_completed(futures), total=len(futures), unit="tune"):
            rows.append(future.result())
    rows.sort(key=lambda r: r["path"])
    args.out.mkdir(parents=True, exist_ok=True)
    with open(args.out / "lift_times.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["path", "player", "seconds", "verdict"])
        writer.writerows(
            [r["path"], r["player"], f"{r['seconds']:.3f}", r["verdict"]] for r in rows
        )
    with open(args.out / "profiles.txt", "w", encoding="utf-8") as handle:
        for r in rows:
            if r["profile"]:
                handle.write(f"== {r['path']} ({r['seconds']:.1f} s)\n{r['profile']}\n\n")
    print("\n".join(report(rows, args.slow)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
