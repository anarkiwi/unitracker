"""Lift tunes of any registered format and verify each document against the spec player: how many
lift exactly, how many are refused (by class) and how many diverge, and the size of each exact
document against the tune's own data.

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
from unitracker.lift.invariants import ModelError

FRAMES = 20000


def _expired(*_):
    raise TimeoutError("over the time limit")


def sizes(report: dict) -> dict:
    """``{objects, song_bytes, fixed_bytes, native_bytes}`` of a lifter's ``report``: each kind's
    ``[lifted, native]`` objects, the document's song and fixed bytes and the tune's data bytes."""
    return {
        "objects": {k: list(v) for k, v in report["counts"].items()},
        "song_bytes": report["song"],
        "fixed_bytes": report["fixed"],
        "native_bytes": report["native_bytes"],
    }


def verdict(lifter, raw: bytes, subtune: int, frames: int) -> tuple:
    """``(verdict, detail)`` of one subtune: ``exact``, ``refused`` (its class in detail),
    ``oversized`` (more objects than the tune has) or ``diverged`` (the cells and the first)."""
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
    """``{name, verdict, detail}`` of one tune: ``exact`` (with its :func:`sizes`), ``refused``
    (its class in detail), ``oversized``, ``diverged`` (the cells and the first), ``other``
    (another format than ``only``), ``missing`` or ``error``. With ``subtunes`` it is the first
    subtune's verdict that is not exact, and its number is in detail after ``@``.
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
        return {"name": name, "verdict": "exact", "detail": ""} | sizes(lifter.report(raw, 0))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {"name": name, "verdict": "error", "detail": f"{type(exc).__name__}: {exc}"[:200]}
    finally:
        signal.alarm(0)


def totals(results: list) -> list:
    """Each exact tune's lifted/native objects per kind and its song and fixed bytes against the
    native data bytes; then the copies (lifted less native objects) per kind over the corpus, the
    song bytes over the native data bytes and the five tunes whose song bytes are the largest
    multiple of it."""
    exact = [r for r in results if r["verdict"] == "exact"]
    out, copies = [], Counter()
    for r in exact:
        kinds = " ".join(f"{k}:{a}/{b}" for k, (a, b) in r["objects"].items())
        out.append(
            f"{r['name']}: {kinds} song {r['song_bytes']} fixed {r['fixed_bytes']} "
            f"native {r['native_bytes']}"
        )
        copies.update({k: a - b for k, (a, b) in r["objects"].items() if k != "song"})
    out.append(f"copies per kind: {dict(copies)}")
    song, native = sum(r["song_bytes"] for r in exact), sum(r["native_bytes"] for r in exact)
    out.append(f"song bytes {song} native data bytes {native}")
    worst = sorted(exact, key=lambda r: -r["song_bytes"] / max(r["native_bytes"], 1))[:5]
    out += [f"worst {r['name']}: {r['song_bytes']}/{r['native_bytes']}" for r in worst]
    return out


def main(argv=None) -> int:
    """Run the corpus and print the verdict counts, refusal classes and the divergent tunes."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("names", type=Path)
    parser.add_argument("--format", help="lift only tunes of this format")
    parser.add_argument("--frames", type=int, default=FRAMES, help="at least this many frames")
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
        if r["verdict"] in ("diverged", "error", "oversized"):
            print(f"{r['verdict']:9} {r['name']}: {r['detail']}")
    print(*totals(results), sep="\n")
    if args.out:
        args.out.write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
