"""Lift tunes of any registered format and verify each document against the spec player: how many
lift exactly, how many are refused (by class) and how many diverge.

``python tools/lift_corpus.py NAMES.txt [--format NAME] [--frames N] [--jobs J] [--subtunes]
[--out F.json]`` where NAMES lists one HVSC path per line (quotes allowed), fetched with
``pysidtracker.testing``. With ``--subtunes`` every subtune of a tune is checked and the first
that is not exact is its verdict.
"""

import argparse
import json
import signal
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from importlib import import_module
from pathlib import Path

from pysidtracker import SidImage, find_format
from pysidtracker.testing import resolve_tune
from tqdm import tqdm
from unitracker.lift.invariants import KINDS, ModelError


def _expired(*_):
    raise TimeoutError("over the time limit")


def verdict(lifter, raw: bytes, subtune: int, frames: int) -> tuple:
    """``(verdict, detail)`` of one subtune: ``exact``, ``refused`` (its class in detail) or
    ``diverged`` (the cells and the first)."""
    try:
        cells, _ = lifter.verify(raw, subtune, frames)
    except lifter.Refusal as exc:
        return "refused", exc.kind
    except ModelError as exc:
        return "oversized", str(exc)
    if not cells.size:
        return "exact", ""
    return "diverged", f"{len(cells)} cells, first {tuple(int(c) for c in cells[0][:2])}"


def classify(
    name: str, frames: int, limit: int, only: str | None = None, subtunes: bool = False
) -> dict:
    """``{name, verdict, detail}`` of one tune: ``exact``, ``refused`` (its class in detail),
    ``diverged`` (the cells and the first), ``other`` (another format than ``only``) or ``error``.
    With ``subtunes`` it is the first subtune's verdict that is not exact, and its number is in
    detail after ``@``.
    """
    signal.signal(signal.SIGALRM, _expired)
    signal.alarm(limit)
    try:
        path = resolve_tune(name)
        if path is None:
            return {"name": name, "verdict": "missing", "detail": ""}
        raw = Path(path).read_bytes()
        fmt = find_format(raw).name
        if only and fmt != only:
            return {"name": name, "verdict": "other", "detail": fmt}
        lifter = import_module(f"unitracker.lift.{fmt}")
        count = SidImage.from_bytes(raw).header.songs if subtunes else 1
        for subtune in range(count):
            kind, detail = verdict(lifter, raw, subtune, frames)
            if kind != "exact":
                return {"name": name, "verdict": kind, "detail": detail + subtunes * f" @{subtune}"}
        sizes = lifter.report(raw, 0) if hasattr(lifter, "report") else None
        return {"name": name, "verdict": "exact", "detail": "", "sizes": sizes}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {"name": name, "verdict": "error", "detail": f"{type(exc).__name__}: {exc}"[:200]}
    finally:
        signal.alarm(0)


def size_line(name: str, sizes: dict) -> str:
    """One tune's lifted/native count of each kind (``song`` its largest voice) and its document
    bytes against its native song data bytes."""
    counts = []
    for kind in KINDS:
        have, bound = sizes["lifted"][kind], sizes["native"][kind]
        if kind == "song":
            have, bound = max(have), max(bound)
        counts.append(f"{kind} {have}/{bound}")
    return f"{name}: {' '.join(counts)} bytes {sizes['bytes']}/{sizes['native_bytes']}"


def main(argv=None) -> int:
    """Run the corpus and print the verdict counts, refusal classes and the divergent tunes."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("names", type=Path)
    parser.add_argument("--format", help="lift only tunes of this format")
    parser.add_argument("--frames", type=int, default=6000)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--subtunes", action="store_true", help="check every subtune")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    names = [n.strip().strip('"') for n in args.names.read_text().splitlines() if n.strip()]
    results = []
    with ProcessPoolExecutor(args.jobs) as pool:
        futures = [
            pool.submit(classify, n, args.frames, args.timeout, args.format, args.subtunes)
            for n in names
        ]
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
            print(size_line(r["name"], r["sizes"]))
    if args.out:
        args.out.write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
