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

from pysidtracker import find_format, parse_sid_header
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


def classify(name: str, frames: int, limit: int, only: str | None = None, subtune: int = 0) -> dict:
    """``{name, verdict, detail}`` of one tune's 0-based ``subtune``: ``exact``, ``refused`` (its
    class in detail), ``diverged`` (the cells and the first) or ``error``; ``other`` where its
    format is not ``only`` or has no lifter."""
    signal.signal(signal.SIGALRM, _expired)
    signal.alarm(limit)
    tag = f"{name}#{subtune}" if subtune else name
    try:
        path = resolve_tune(name)
        if path is None:
            return {"name": tag, "verdict": "missing", "detail": ""}
        raw = Path(path).read_bytes()
        fmt = find_format(raw).name
        lifter = _lifter(fmt)
        if lifter is None or only not in (None, fmt):
            return {"name": tag, "verdict": "other", "detail": fmt}
        cells, text = lifter.verify(raw, subtune, frames)
        sizes = lifter.measure(raw, subtune, text) if hasattr(lifter, "measure") else {}
        if not cells.size:
            return {"name": tag, "verdict": "exact", "detail": "", "sizes": sizes}
        first = tuple(int(c) for c in cells[0][:2])
        return {"name": tag, "verdict": "diverged", "detail": f"{len(cells)} cells, first {first}"}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        kind = getattr(exc, "kind", None)
        if kind is not None:
            return {"name": tag, "verdict": "refused", "detail": kind}
        return {"name": tag, "verdict": "error", "detail": f"{type(exc).__name__}: {exc}"[:200]}
    finally:
        signal.alarm(0)


def _sizes_line(name: str, sizes: dict) -> str:
    """One tune's objects over native objects per kind, song entries a voice over the
    orderlist's bound, the document's fixed and song bytes and the song data's bytes."""
    kinds = " ".join(f"{k}={o}/{n}" for k, (o, n) in sizes["kinds"].items() if o or n)
    song = "/".join(f"{h}:{n}" for h, n in zip(*sizes["song"]))
    fixed, bytes_ = sizes["bytes"]["fixed"], sizes["bytes"]["song"]
    return (
        f"{name}: {kinds} entries={song} fixed={fixed} song={bytes_} native={sizes['native_bytes']}"
    )


def _tasks(names: list, subtune: int, every: bool) -> list:
    """``(name, subtune)`` for each tune: the one ``subtune``, or with ``every`` all the subtunes
    its header counts."""
    if not every:
        return [(n, subtune) for n in names]
    out = []
    for name in names:
        path = resolve_tune(name)
        songs = parse_sid_header(Path(path).read_bytes()).songs if path else 1
        out += [(name, k) for k in range(max(songs, 1))]
    return out


def main(argv=None) -> int:
    """Run the corpus and print the verdict counts, refusal classes and the divergent tunes."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("names", type=Path)
    parser.add_argument("--format", help="lift only tunes of this format")
    parser.add_argument("--subtune", type=int, default=0, help="0-based subtune of every tune")
    parser.add_argument("--all-subtunes", action="store_true", help="every subtune of every tune")
    parser.add_argument("--sizes", type=Path, help="write each exact tune's song bytes here")
    parser.add_argument("--frames", type=int, default=6000)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    lines = args.names.read_text().splitlines()
    names = [n.strip().strip('"') for n in lines if n.strip() and not n.startswith("#")]
    results = []
    with ProcessPoolExecutor(args.jobs) as pool:
        futures = [
            pool.submit(classify, n, args.frames, args.timeout, args.format, k)
            for n, k in _tasks(names, args.subtune, args.all_subtunes)
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
            print(_sizes_line(r["name"], r["sizes"]))
    if args.sizes:
        song = {r["name"]: r["sizes"]["bytes"]["song"] for r in results if r.get("sizes")}
        args.sizes.write_text(json.dumps(song, indent=1, sort_keys=True) + "\n")
    if args.out:
        args.out.write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
