"""Summarise a ``lift_corpus.py --out`` file: verdicts, refusals, failures and the size totals."""

import collections
import json
import sys
from pathlib import Path

from lift_corpus import totals


def main(argv=None) -> int:
    """Print the verdict counts, every tune that is not exact and the corpus totals."""
    results = json.loads(Path((argv or sys.argv[1:])[0]).read_text(encoding="utf-8"))
    print(dict(collections.Counter(r["verdict"] for r in results)))
    for r in results:
        if r["verdict"] != "exact":
            print(f"{r['verdict']:9} {r['name']}: {r['detail']}")
    print(*totals(results), sep="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
