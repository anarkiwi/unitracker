"""Print a defMON tune's lifted objects by native object: ``lift_origins.py TUNE [SUBTUNE]`` lists
each native object with more than one lifted copy, and the contexts that tell the copies apart."""

import collections
import sys

from pysidtracker.testing import resolve_tune
from unitracker.lift import defmon


def copies(raw: bytes, subtune: int = 0) -> dict:
    """``{kind: {native: [context, ...]}}`` of the natives that more than one object stands for."""
    _, _, lift = defmon.lifted(raw, subtune)
    out = {}
    for kind, origins in lift.origins.items():
        found = collections.defaultdict(list)
        for origin in origins.values():
            found[origin.native].append(origin.context)
        out[kind] = {n: c for n, c in found.items() if len(c) > 1}
    return out


def main(argv=None) -> int:
    """Print the copies."""
    argv = sys.argv[1:] if argv is None else argv
    raw = resolve_tune(argv[0]).read_bytes()
    for kind, found in copies(raw, int(argv[1]) if len(argv) > 1 else 0).items():
        for native, contexts in found.items():
            print(kind, native, *contexts, sep="\n  ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
