"""Lift GoatTracker 2 tunes and verify each document against the spec player: how many lift
exactly, how many are refused (by class) and how many diverge.

``python tools/lift_corpus.py NAMES.txt [--frames N] [--jobs J] [--out F.json]`` where NAMES
lists one HVSC path per line (quotes allowed), fetched with ``pysidtracker.testing``.
"""

import argparse
import json
import signal
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from pysidtracker import SidImage, find_format
from pysidtracker.testing import resolve_tune
from tqdm import tqdm

from unitracker.lift import goattracker2, invariants


def _expired(*_):
    raise TimeoutError("over the time limit")


def sizes(raw: bytes, text: str, objects: dict) -> dict:
    """``{objects, song_bytes, fixed_bytes, native_bytes}`` of a lifted document: each kind's
    ``[lifted, native]`` objects, its song and fixed bytes (:func:`invariants.sizes`) and the
    tune's data bytes."""
    split = invariants.sizes(text)
    return {
        "objects": {k: list(v) for k, v in objects.items()},
        "song_bytes": split["song"],
        "fixed_bytes": split["fixed"],
        "native_bytes": len(SidImage.from_sid(raw).image),
    }


def classify(name: str, frames: int, limit: int) -> dict:
    # pylint: disable=too-many-return-statements
    """``{name, verdict, detail}`` of one tune: ``exact``, ``refused`` (its class in detail),
    ``diverged`` (the cells and the first) or ``error``."""
    signal.signal(signal.SIGALRM, _expired)
    signal.alarm(limit)
    try:
        path = resolve_tune(name)
        if path is None:
            return {"name": name, "verdict": "missing", "detail": ""}
        raw = Path(path).read_bytes()
        if find_format(raw).name != "goattracker2":
            return {"name": name, "verdict": "other", "detail": find_format(raw).name}
        cells, text = goattracker2.verify(raw, 0, frames)
        if not cells.size:
            objects = invariants.provenance(
                text, goattracker2.lifted(goattracker2.core_of_sid(raw))[1]
            )
            return {"name": name, "verdict": "exact", "detail": ""} | sizes(raw, text, objects)
        first = tuple(int(c) for c in cells[0][:2])
        return {"name": name, "verdict": "diverged", "detail": f"{len(cells)} cells, first {first}"}
    except invariants.ModelError as exc:
        return {"name": name, "verdict": "exceeded", "detail": str(exc)}
    except goattracker2.Refusal as exc:
        return {"name": name, "verdict": "refused", "detail": exc.kind}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {"name": name, "verdict": "error", "detail": f"{type(exc).__name__}: {exc}"[:200]}
    finally:
        signal.alarm(0)


def print_sizes(results: list) -> None:
    """Each exact tune's lifted/native objects per kind, its song and fixed bytes against the
    native data bytes, and the copies (lifted less native objects) per kind over the corpus."""
    copies = Counter()
    for r in results:
        if r["verdict"] != "exact":
            continue
        kinds = " ".join(f"{k}:{a}/{b}" for k, (a, b) in r["objects"].items())
        print(
            f"{r['name']}: {kinds} song {r['song_bytes']} fixed {r['fixed_bytes']} "
            f"native {r['native_bytes']}"
        )
        copies.update({k: a - b for k, (a, b) in r["objects"].items()})
    print("copies per kind:", dict(copies))


def main(argv=None) -> int:
    """Run the corpus and print the verdict counts, refusal classes and the divergent tunes."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("names", type=Path)
    parser.add_argument("--frames", type=int, default=6000)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    names = [n.strip().strip('"') for n in args.names.read_text().splitlines() if n.strip()]
    results = []
    with ProcessPoolExecutor(args.jobs) as pool:
        futures = [pool.submit(classify, n, args.frames, args.timeout) for n in names]
        for done in tqdm(as_completed(futures), total=len(futures), unit="sid"):
            results.append(done.result())
    results.sort(key=lambda r: r["name"])
    counts = Counter(r["verdict"] for r in results)
    print(dict(counts))
    refused = Counter(r["detail"] for r in results if r["verdict"] == "refused")
    print("refusal classes:", dict(refused.most_common()))
    for r in results:
        if r["verdict"] in ("diverged", "error", "exceeded"):
            print(f"{r['verdict']:9} {r['name']}: {r['detail']}")
    print_sizes(results)
    if args.out:
        args.out.write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
