"""Lift tunes of any registered format and verify each document against the spec player: how many
lift exactly, how many are refused (by class) and how many diverge.

``python tools/lift_corpus.py NAMES.txt [--format F] [--frames N] [--jobs J] [--out F.json]``
where NAMES lists one HVSC path per line (quotes allowed), fetched with ``pysidtracker.testing``.
"""

import argparse
import importlib
import json
import signal
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from pysidtracker import find_format
from pysidtracker.testing import resolve_tune
from tqdm import tqdm


def _expired(*_):
    raise TimeoutError("over the time limit")


def _lifter(fmt: str):
    """The lift module of format ``fmt`` (``unitracker.lift.<format>``), None without one."""
    try:
        return importlib.import_module(f"unitracker.lift.{fmt}")
    except ModuleNotFoundError:
        return None


def classify(name: str, frames: int, limit: int, only: str | None = None) -> dict:
    """``{name, verdict, detail}`` of one tune: ``exact``, ``refused`` (its class in detail),
    ``diverged`` (the cells and the first) or ``error``; ``other`` where its format is not
    ``only`` or has no lifter."""
    signal.signal(signal.SIGALRM, _expired)
    signal.alarm(limit)
    try:
        path = resolve_tune(name)
        if path is None:
            return {"name": name, "verdict": "missing", "detail": ""}
        raw = Path(path).read_bytes()
        fmt = find_format(raw).name
        lifter = _lifter(fmt)
        if lifter is None or only not in (None, fmt):
            return {"name": name, "verdict": "other", "detail": fmt}
        cells, text = lifter.verify(raw, 0, frames)
        sizes = lifter.measure(raw, 0, text) if hasattr(lifter, "measure") else {}
        if not cells.size:
            return {"name": name, "verdict": "exact", "detail": "", "sizes": sizes}
        first = tuple(int(c) for c in cells[0][:2])
        return {"name": name, "verdict": "diverged", "detail": f"{len(cells)} cells, first {first}"}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        kind = getattr(exc, "kind", None)
        if kind is not None:
            return {"name": name, "verdict": "refused", "detail": kind}
        return {"name": name, "verdict": "error", "detail": f"{type(exc).__name__}: {exc}"[:200]}
    finally:
        signal.alarm(0)


def _sizes_line(name: str, sizes: dict) -> str:
    """One tune's lifted/native object counts per kind and document bytes over native bytes."""
    have, native = sizes["lifted"], sizes["native"]
    kinds = " ".join(f"{k}={have[k]}/{native[k]}" for k in native if k != "song")
    song = "/".join(f"{h}:{n}" for h, n in zip(have["song"], native["song"]))
    return f"{name}: {kinds} song={song} bytes={sizes['bytes']}/{sizes['native_bytes']}"


def main(argv=None) -> int:
    """Run the corpus and print the verdict counts, refusal classes and the divergent tunes."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("names", type=Path)
    parser.add_argument("--format", help="lift only tunes of this format")
    parser.add_argument("--frames", type=int, default=6000)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    lines = args.names.read_text().splitlines()
    names = [n.strip().strip('"') for n in lines if n.strip() and not n.startswith("#")]
    results = []
    with ProcessPoolExecutor(args.jobs) as pool:
        futures = [pool.submit(classify, n, args.frames, args.timeout, args.format) for n in names]
        for done in tqdm(as_completed(futures), total=len(futures), unit="sid"):
            results.append(done.result())
    results.sort(key=lambda r: r["name"])
    print(dict(Counter(r["verdict"] for r in results)))
    refused = Counter(r["detail"] for r in results if r["verdict"] == "refused")
    print("refusal classes:", dict(refused.most_common()))
    for r in results:
        if r["verdict"] in ("diverged", "error"):
            print(f"{r['verdict']:9} {r['name']}: {r['detail']}")
    for r in results:
        if r.get("sizes"):
            print(_sizes_line(r["name"], r["sizes"]))
    if args.out:
        args.out.write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
