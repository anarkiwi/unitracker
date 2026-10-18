"""Record every display form a song's expression conversion chooses, and compare them later.

    python tools/display_corpus.py record OUT.pkl SONG.sid|HVSC/PATH.sid ... [--subtune N]
    python tools/display_corpus.py compare OUT.pkl ...

``record`` converts each tune in process without caches and keeps each ``grammar.polar`` input,
domain and written form; ``compare`` shows every recorded input with the current code and
prints each form that differs, before and after, with whether the two terms are equal over
their domain, then the ``display`` and ``polar`` calls per input.
"""

import argparse
import functools
import pickle
import sys
from pathlib import Path

from tqdm import tqdm

from unitracker.modfit import convert as C
from unitracker.modfit import grammar as G

from profile_convert import lifted  # pylint: disable=wrong-import-order


class Calls:
    """Counts of ``display`` and ``polar`` calls while installed, ``display`` uncached."""

    def __init__(self):
        self.display = self.polar = 0
        self.saved = G.display, G.polar

    def __enter__(self):
        inner = G.display.__wrapped__

        @functools.lru_cache(maxsize=None)
        def display(*args):
            self.display += 1
            return inner(*args)

        def polar(*args):
            self.polar += 1
            return self.saved[1](*args)

        G.display, G.polar = display, polar
        return self

    def __exit__(self, *exc):
        G.display, G.polar = self.saved


def record(out: Path, tunes: list, subtune) -> None:
    """Convert each tune and keep ``(tune, e, domain, text)`` per ``polar`` call; the text, as an
    unpickled term is rebuilt evaluated."""
    rows, polar = [], G.polar

    def kept(e, domain=()):
        shown = polar(e, domain)
        rows.append((tune, e, tuple(domain), G.text(shown)))
        return shown

    G.polar = kept
    try:
        for tune in tqdm(tunes, desc="tunes", unit="tune"):
            C.analyse(lifted(tune, subtune), cache=None, workers=1)
    finally:
        G.polar = polar
    out.write_bytes(pickle.dumps(rows))
    print(f"{len(rows)} display forms of {len(tunes)} tunes")


def equal(a, b, domain: tuple) -> bool:
    """True when ``a`` and ``b`` agree for every value of their domain's symbols (z3)."""
    # pylint: disable=import-outside-toplevel,protected-access
    import z3

    env, facts = {}, []
    za, zb = G._z3_free(a, env, facts), G._z3_free(b, env, facts)
    facts += [z3.And(env[n] >= lo, env[n] <= hi) for n, lo, hi in domain if n in env]
    solver = z3.Solver()
    solver.add(*facts, za != zb)
    return solver.check() == z3.unsat


def compare(paths: list) -> int:
    """Print the recorded forms the current code writes differently; the number of them."""
    rows = [r for p in paths for r in pickle.loads(Path(p).read_bytes())]
    unique = list(dict.fromkeys((e, d) for _, e, d, _ in rows))
    before = {(e, d): s for _, e, d, s in rows}
    changed, most = 0, (0, 0)
    for e, d in tqdm(unique, desc="forms", unit="form"):
        with Calls() as calls:
            shown = G.polar(e, d)
        most = max(most, (calls.display, calls.polar))
        if G.text(shown) != before[e, d]:
            changed += 1
            old = G.parse(before[e, d], e.free_symbols)
            print(f"before: {before[e, d]}\nafter:  {G.text(shown)}")
            print(f"equal over {d}: {equal(old, shown, d)}")
    print(f"{changed} of {len(unique)} forms differ; most display calls {most[0]}")
    return changed


def main(argv=None) -> int:
    """Run the tool."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("mode", choices=("record", "compare"))
    ap.add_argument("out", type=Path)
    ap.add_argument("tunes", nargs="*")
    ap.add_argument("--subtune", type=int)
    args = ap.parse_args(argv)
    if args.mode == "record":
        record(args.out, args.tunes, args.subtune)
        return 0
    return int(compare([args.out, *args.tunes]) > 0)


if __name__ == "__main__":
    sys.exit(main())
