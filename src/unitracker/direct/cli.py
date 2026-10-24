"""The command line the direct transcriptions share: print one SID's document, or verify SIDs."""

# pylint: disable=duplicate-code

import argparse
import signal
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from pysidtracker import gridcmp
from tqdm import tqdm


def summary(cells, runs: int = 0) -> str:
    """``exact``, or the first differing cell and the count; with ``runs``, that many of each
    register's differing runs."""
    if not cells.size:
        return "exact"
    out = [f"first {tuple(int(c) for c in cells[0][:2])} cells {len(cells)}"]
    for reg in gridcmp.runs(cells) if runs else ():
        spans = ", ".join(f"{r['from']}-{r['to']} {r['values'][0][2:]}" for r in reg["runs"][:runs])
        out.append(f"  {reg['name']} {reg['cells']}: {spans}")
    return "\n".join(out)


def _expired(*_):
    raise TimeoutError("over the time limit")


def _check(job: tuple) -> tuple:
    verify, path, subtune, frames, runs, limit = job
    signal.signal(signal.SIGALRM, _expired)
    signal.alarm(limit)
    try:
        cells = verify(path.read_bytes(), subtune, frames)[0]
        return path, not cells.size, summary(cells, runs)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return path, False, f"{type(exc).__name__}: {exc}"
    finally:
        signal.alarm(0)


def main(argv, description: str, transcribe, verify) -> int:
    """Write ``transcribe(raw, subtune)`` of a SID, or with ``--verify`` each SID's
    ``verify(raw, subtune, frames)`` verdict."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("sids", nargs="+", type=Path)
    parser.add_argument("--subtune", type=int, default=0)
    parser.add_argument("--frames", type=int, default=20000)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--diff", type=int, default=0, help="differing runs per register")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=600, help="seconds per SID")
    args = parser.parse_args(argv)
    if not args.verify:
        sys.stdout.write(transcribe(args.sids[0].read_bytes(), args.subtune))
        return 0
    jobs = [
        (verify, path, args.subtune, args.frames, args.diff, args.timeout) for path in args.sids
    ]
    exact = 0
    with ProcessPoolExecutor(args.jobs) as pool:
        futures = [pool.submit(_check, job) for job in jobs]
        progress = tqdm(as_completed(futures), total=len(jobs), unit="sid")
        for done in progress:
            path, ok, verdict = done.result()
            exact += ok
            print(f"{path}: {verdict}", flush=True)
            progress.set_postfix(exact=exact)
    return 0
