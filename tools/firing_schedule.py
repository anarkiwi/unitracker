"""Each expression's native firing schedule over the generic document's modulators.

    python tools/firing_schedule.py TUNE.sid [--subtune N] [--cell pw:0] [--ticks A:B]

Prints, per expression a modulator of the cell plays in the lifted tune's generic document
before re-timing (with ``--timed``, after), its modulators' distinct schedules (the ticks the
native unit holds, by ``φ``) and whether they agree; with ``--ticks``, each tick's native
occurrence and the generic modulator's ``φ``.
"""

import argparse
import os
from collections import Counter
from pathlib import Path

MAX_TICKS, TICKS = 100_000, 3007


def schedules(native, text: str, want) -> dict:
    """``{(cell key, expression): [(skips, length, first tick, origin)]}`` of the modulators of
    cell ``want`` (every cell for None) in document ``text``."""
    from unitracker.player import oracle  # pylint: disable=import-outside-toplevel

    found: dict = {}
    for cell, ticks, origin in oracle.firings(text, native.ticks, progress=True):
        fired = native.fired.get(cell) or {}
        occ = next((fired[t] for t in ticks if t in fired), None)
        if occ is not None and (want is None or cell == want):
            skips = tuple(phi for phi, t in enumerate(ticks) if fired.get(t) != occ)
            mods = found.setdefault((cell[0], native.texts[occ]), [])
            mods.append((skips, len(ticks), ticks[0], origin))
    return found


def report(found: dict, shown: int) -> None:
    """Print each expression's distinct schedules, the most frequent ``shown`` of them."""
    from unitracker.document import retime  # pylint: disable=import-outside-toplevel

    for (key, expr), mods in sorted(found.items()):
        merged = retime.merged(retime.Schedule(frozenset(m[0]), m[1]) for m in mods)
        state = "agree" if merged is not None else "DIFFER"
        print(f"{key} {expr!r}: {len(mods)} modulators, {state}")
        counts = Counter((m[0], m[1], m[3]) for m in mods)
        for (skips, n, origin), count in counts.most_common(shown):
            first = next(m[2] for m in mods if (m[0], m[1], m[3]) == (skips, n, origin))
            print(
                f"    {count:5d} x length {n:5d} from tick {first:6d} by {origin}:"
                f" skips {list(skips[:16])}"
            )


def trace(native, text: str, want, lo: int, hi: int) -> None:
    """Print each tick of ``[lo, hi)``: cell ``want``'s native occurrence and generic ``φ``."""
    from unitracker.player import oracle  # pylint: disable=import-outside-toplevel

    fired, phis = native.fired.get(want) or {}, {}
    for cell, ticks, _ in oracle.firings(text, hi):
        if cell == want:
            phis.update((t, (phi, ticks[0])) for phi, t in enumerate(ticks))
    for t in range(lo, hi):
        occ = fired.get(t)
        print(t, "native", occ, native.texts.get(occ), "generic (φ, start)", phis.get(t))


def run(ir: dict, cell=None, ticks=None, timed: bool = False, shown: int = 6) -> None:
    """Print the report of song ``ir``: ``cell`` a ``(key, voice)`` (None: every cell),
    ``ticks`` an ``(A, B)`` to trace cell's ticks over, ``timed`` the re-timed document's
    modulators, ``shown`` the schedules shown per expression."""
    from unitracker import document  # pylint: disable=import-outside-toplevel
    from unitracker.document import writer  # pylint: disable=import-outside-toplevel

    native = document.native_state(ir, MAX_TICKS, TICKS)
    text = document.write(ir, native) if timed else writer.untimed(ir, native)[0]
    report(schedules(native, text, cell), shown)
    if ticks and cell:
        trace(native, text, cell, *ticks)


def main(argv=None) -> int:
    """Command-line entry."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sid", type=Path)
    parser.add_argument("--subtune", type=int, help="the subtune from 1 (default: the header's)")
    parser.add_argument("--cell", help="KEY:VOICE (voice from 0) or cut")
    parser.add_argument("--ticks", help="A:B, the ticks to list")
    parser.add_argument("--shown", type=int, default=6, help="schedules shown per expression")
    parser.add_argument("--timed", action="store_true", help="the re-timed document's modulators")
    args = parser.parse_args(argv)
    os.environ.setdefault("UNITRACKER_WORKERS", "1")
    from unitracker.lift import backends  # pylint: disable=import-outside-toplevel

    subtune = None if args.subtune is None else args.subtune - 1
    ir = backends.lifter(args.sid.read_bytes(), subtune).run()
    cell = None
    if args.cell:
        key, _, voice = args.cell.partition(":")
        cell = (key, int(voice) if voice else None)
    ticks = tuple(int(x) for x in args.ticks.split(":")) if args.ticks else None
    run(ir, cell, ticks, args.timed, args.shown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
