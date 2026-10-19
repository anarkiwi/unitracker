"""Each expression's native firing schedule over the generic document's modulators.

    python tools/firing_schedule.py DOC.yaml [--cell pw:0] [--ticks A:B]

Prints, per expression a modulator of the cell plays in the document before re-timing (with
``--timed``, after), its modulators' distinct schedules (the ticks the native unit holds, by
``φ``) and whether they agree; with ``--ticks``, each tick's native occurrence and the generic
modulator's ``φ``.
"""

import argparse
import os
from collections import Counter

MAX_TICKS, TICKS = 100_000, 3007


def main(argv=None) -> int:
    """Command-line entry."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("doc")
    parser.add_argument("--cell", help="KEY:VOICE (voice from 0) or cut")
    parser.add_argument("--ticks", help="A:B, the ticks to list")
    parser.add_argument("--shown", type=int, default=6, help="schedules shown per expression")
    parser.add_argument("--timed", action="store_true", help="the re-timed document's modulators")
    args = parser.parse_args(argv)
    os.environ.setdefault("UNITRACKER_WORKERS", "1")
    # pylint: disable=import-outside-toplevel
    from unitracker.format import generic, retime, songio
    from unitracker.player import oracle

    ir = songio.load(args.doc)
    native = generic.native_state(ir, MAX_TICKS, TICKS)
    text = generic.dumps(ir, native) if args.timed else generic.untimed(ir, native)[0]
    want = None
    if args.cell:
        key, _, voice = args.cell.partition(":")
        want = (key, int(voice) if voice else None)
    found: dict = {}
    for cell, ticks, origin in oracle.firings(text, native.ticks, progress=True):
        fired = native.fired.get(cell) or {}
        occ = next((fired[t] for t in ticks if t in fired), None)
        if occ is None or (want and cell != want):
            continue
        skips = tuple(phi for phi, t in enumerate(ticks) if fired.get(t) != occ)
        mods = found.setdefault((cell[0], native.texts[occ]), [])
        mods.append((skips, len(ticks), ticks[0], origin))
    for (key, expr), mods in sorted(found.items()):
        merged = retime.merged(retime.Schedule(frozenset(m[0]), m[1]) for m in mods)
        state = "agree" if merged is not None else "DIFFER"
        print(f"{key} {expr!r}: {len(mods)} modulators, {state}")
        counts = Counter((m[0], m[1], m[3]) for m in mods)
        for (skips, n, origin), count in counts.most_common(args.shown):
            first = next(m[2] for m in mods if (m[0], m[1], m[3]) == (skips, n, origin))
            print(
                f"    {count:5d} x length {n:5d} from tick {first:6d} by {origin}:"
                f" skips {list(skips[:16])}"
            )
    if args.ticks and want:
        lo, hi = (int(x) for x in args.ticks.split(":"))
        fired = native.fired.get(want) or {}
        phis = {}
        for cell, ticks, _ in oracle.firings(text, hi):
            if cell == want:
                phis.update((t, (phi, ticks[0])) for phi, t in enumerate(ticks))
        for t in range(lo, hi):
            occ = fired.get(t)
            print(t, "native", occ, native.texts.get(occ), "generic (φ, start)", phis.get(t))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
