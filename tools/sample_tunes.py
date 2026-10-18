"""Lift a random sample of HVSC tunes, balanced across the playroutines unitracker lifts.

    python -m pysidtracker.survey find $HVSC --out tunes.csv --player 'goattracker|wizard|defmon'
    python tools/sample_tunes.py $HVSC tunes.csv OUTDIR [--count N] [--seed S] [--jobs J] \
        [--subtunes 1]

The sample is drawn from the survey's ``tunes.csv``: ``--subtunes`` keeps tunes with at most that
many subtunes (default single-subtune tunes), then the count of *exported* songs is split evenly
over the lifted players. Each player's shuffled pool is drawn in rounds until its quota has
lifted; a pool that runs out gives its remaining quota to the others. Each tune's start subtune is
lifted in its own process to ``OUTDIR/<md5 prefix>-<name>.yaml``; ``OUTDIR/sample.csv`` lists every
attempt with its player, output path and error, and ``OUTDIR/failures.csv`` only the lift errors.
"""

import argparse
import csv
import os
import random
import re
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

PLAYERS = {
    "goattracker2": re.compile(r"^GoatTracker_V2\.x$"),
    "sidwizard": re.compile(r"SidWizard", re.I),
    "defmon": re.compile(r"^DefMon$"),
}
FIELDS = ["path", "md5", "player", "backend", "songs", "output", "error"]


def pool_by_player(tunes_csv: Path, max_songs: int) -> dict:
    """``{backend: [tune rows]}`` of the survey's tunes with at most ``max_songs`` subtunes."""
    pools = {name: [] for name in PLAYERS}
    with tunes_csv.open(newline="") as fh:
        for row in csv.DictReader(fh):
            if int(row["songs"]) > max_songs:
                continue
            for name, pattern in PLAYERS.items():
                if pattern.search(row["player"]):
                    pools[name].append(row)
    return pools


def quotas(count: int, open_pools: list, done: dict) -> dict:
    """Each open pool's next draw: an even share of what the closed pools left, less its lifts."""
    wanted = count - sum(n for name, n in done.items() if name not in open_pools)
    share, extra = divmod(max(wanted, 0), max(len(open_pools), 1))
    return {name: max(share + (i < extra) - done[name], 0) for i, name in enumerate(open_pools)}


def output_name(outdir: Path, row: dict) -> Path:
    """``OUTDIR/<md5 prefix>-<file stem>.yaml``, the prefix keeping same-named tunes apart."""
    return outdir / f"{row['md5'][:8]}-{Path(row['path']).stem}.yaml"


def lift_one(hvsc: str, row: dict, output: str) -> str:
    """Lift ``row``'s start subtune to ``output``; return its error text or ``''``."""
    os.environ["UNITRACKER_WORKERS"] = "1"
    from unitracker.format import songio  # pylint: disable=import-outside-toplevel
    from unitracker.lift import backends  # pylint: disable=import-outside-toplevel

    try:
        song = backends.lift((Path(hvsc) / row["path"]).read_bytes())
        songio.dump(song, Path(output))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}".strip()
    return ""


def run(hvsc: Path, pools: dict, count: int, outdir: Path, jobs: int, rng) -> list:
    """Lift from each pool until ``count`` songs are written; return the rows of ``sample.csv``."""
    # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    outdir.mkdir(parents=True, exist_ok=True)
    pending = {name: list(rows) for name, rows in pools.items()}
    for rows in pending.values():
        rng.shuffle(rows)
    done = dict.fromkeys(pools, 0)
    rows = []
    progress = tqdm(total=count, desc="exported", unit="tune")
    with ProcessPoolExecutor(jobs) as ex:
        while sum(done.values()) < count and any(pending.values()):
            open_pools = [name for name, queue in pending.items() if queue]
            futures = {}
            draws = quotas(count, open_pools, done)
            if not any(draws.values()):
                break
            for name, quota in draws.items():
                for row in pending[name][:quota]:
                    output = output_name(outdir, row)
                    futures[ex.submit(lift_one, str(hvsc), row, str(output))] = (name, row, output)
                del pending[name][:quota]
            for fut in as_completed(futures):
                backend, row, output = futures[fut]
                try:
                    error = fut.result()
                except Exception:  # pylint: disable=broad-exception-caught
                    error = traceback.format_exc().splitlines()[-1]
                done[backend] += not error
                progress.update(not error)
                rows.append(
                    {
                        "path": row["path"],
                        "md5": row["md5"],
                        "player": row["player"],
                        "backend": backend,
                        "songs": row["songs"],
                        "output": str(output.relative_to(outdir)) if not error else "",
                        "error": error,
                    }
                )
    progress.close()
    return sorted(rows, key=lambda r: (r["backend"], r["path"]))


def write_csv(path: Path, rows: list):
    """Write ``rows`` with :data:`FIELDS`."""
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None) -> int:
    """Sample and lift; print the per-backend tallies."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("hvsc", type=Path)
    parser.add_argument("tunes_csv", type=Path)
    parser.add_argument("outdir", type=Path)
    parser.add_argument("--count", type=int, default=128)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--jobs", type=int, default=len(os.sched_getaffinity(0)) // 2)
    parser.add_argument("--subtunes", type=int, default=1, help="keep tunes with at most this many")
    args = parser.parse_args(argv)
    pools = pool_by_player(args.tunes_csv, args.subtunes)
    rows = run(args.hvsc, pools, args.count, args.outdir, args.jobs, random.Random(args.seed))
    write_csv(args.outdir / "sample.csv", rows)
    write_csv(args.outdir / "failures.csv", [r for r in rows if r["error"]])
    for backend in PLAYERS:
        mine = [r for r in rows if r["backend"] == backend]
        failed = sum(1 for r in mine if r["error"])
        print(f"{backend}: {len(mine) - failed}/{len(mine)} lifted (pool {len(pools[backend])})")
    return 0 if sum(1 for r in rows if not r["error"]) >= args.count else 1


if __name__ == "__main__":
    sys.exit(main())
