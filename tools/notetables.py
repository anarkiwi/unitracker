"""Fit each tracker's native note table to one generator: a scale and a rounding rule.

Under rounding ``R`` each entry bounds the scale ``A`` of ``R(A * 2**((k - 57) / 12))`` to an
interval; the point most intervals cover is the best ``A`` (exact, or the fewest misses), given
as semitones and cents from A4 = 440 Hz at PAL and as the grammar's ``clock_hz``.
"""

import argparse
import json
import math
from fractions import Fraction
from pathlib import Path

PAL_HZ = 985248
PHASE = 1 << 24
A4_HZ = 440
A4 = 57
WORD = 1 << 16
ROUNDINGS = {
    "round": (Fraction(-1, 2), Fraction(1, 2)),
    "floor": (Fraction(0), Fraction(1)),
    "ceil": (Fraction(-1), Fraction(0)),
}


def ratio(k: int) -> float:
    """``2 ** ((k - A4) / 12)``."""
    return 2.0 ** ((k - A4) / 12)


def best_scale(words: dict, rounding: str) -> tuple:
    """``(hits, lo, hi, misses)``: the most entries one scale reproduces under ``rounding``,
    the scale interval doing so and the notes it misses."""
    below, above = ROUNDINGS[rounding]
    events = []
    for k, w in words.items():
        g = ratio(k)
        events.append((float(w + below) / g, 0, k))
        events.append((float(w + above) / g, 1, k))
    events.sort()
    live, best, chosen = set(), (0, 0.0, 0.0), set()
    for i, (x, kind, k) in enumerate(events):
        if kind:
            live.discard(k)
            continue
        live.add(k)
        nxt = events[i + 1][0] if i + 1 < len(events) else x
        if len(live) > best[0] and nxt > x:
            best, chosen = (len(live), x, nxt), set(live)
    return (*best, sorted(set(words) - chosen))


def describe(scale: float) -> dict:
    """A scale as semitones and cents from A4 = 440 Hz at PAL, and as ``clock_hz``."""
    cents = 1200 * math.log2(scale / (A4_HZ * PHASE / PAL_HZ))
    semis = round(cents / 100)
    return {
        "semitones": semis,
        "cents": round(cents - 100 * semis, 3),
        "clock_hz": round(A4_HZ * PHASE * 2 ** (semis / 12) / scale, 1),
    }


def octave_rule(words: dict) -> dict:
    """``(matched, pairs)`` of each entry against the entry an octave above, halved."""
    pairs = [(words[k + 12], w) for k, w in words.items() if k + 12 in words]
    return {
        "halve_floor": (sum(u >> 1 == w for u, w in pairs), len(pairs)),
        "halve_round": (sum((u + 1) >> 1 == w for u, w in pairs), len(pairs)),
    }


def fit(words: dict) -> dict:
    """Every rounding's best scale, misses and pitch, and the octave rules."""
    out = {"entries": len(words), "first": min(words), "octave": octave_rule(words)}
    for rounding in ROUNDINGS:
        hits, lo, hi, misses = best_scale(words, rounding)
        out[rounding] = {"hits": hits, "misses": misses, **describe((lo + hi) / 2)}
        out[rounding]["clock_hz_range"] = [
            round(A4_HZ * PHASE / hi, 2),
            round(A4_HZ * PHASE / lo, 2),
        ]
    return out


def usable(words: list) -> dict:
    """``{note: word}`` of a table's rising run of real entries (nonzero, below ``$FFFF``)."""
    out, last = {}, 0
    for k, w in enumerate(words):
        if not w or w >= WORD - 1 or w <= last:
            if out:
                break
            continue
        out[k], last = w, w
    return out


def goattracker2(_path=None) -> dict:
    """GoatTracker 2's editor table."""
    from pygoattracker import constants  # pylint: disable=import-outside-toplevel

    return usable(list(constants.FREQ_TABLE))


def musicassembler(path: Path) -> dict:
    """Music Assembler's table as a tune's image holds it."""
    import pymusicassembler as ma  # pylint: disable=import-outside-toplevel

    song = ma.read(str(path))
    return usable([lo | hi << 8 for lo, hi in zip(song.freq_lo, song.freq_hi)])


def defmon(path: Path) -> dict:
    """defMON's note table ``G`` as a tune's replay holds it."""
    from unitracker.direct import defmon as dm  # pylint: disable=import-outside-toplevel

    line = dm.transcribe(path.read_bytes()).split("\n  G: [", 1)[1].split("]", 1)[0]
    return usable([int(x) for x in line.replace("\n", " ").split(",")])


EXTRACT = {
    "goattracker2": goattracker2,
    "musicassembler": musicassembler,
    "defmon": defmon,
}


def main(argv=None) -> int:
    """Fit every tracker's table and print one line per tracker and rounding."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--tune", action="append", default=[], metavar="TRACKER=PATH")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    tunes = dict(t.split("=", 1) for t in args.tune)
    report = {}
    for name, extract in EXTRACT.items():
        if name in ("musicassembler", "defmon") and name not in tunes:
            continue
        r = report[name] = fit(extract(Path(tunes[name]) if name in tunes else None))
        print(f"{name}: {r['entries']} entries from index {r['first']}, octave {r['octave']}")
        for rounding in ROUNDINGS:
            f = r[rounding]
            print(
                f"  {rounding:5} {f['hits']}/{r['entries']}  {f['semitones']:+d} st "
                f"{f['cents']:+.3f} c  clock_hz {f['clock_hz_range']}  misses {f['misses'][:10]}"
            )
    if args.json:
        args.json.write_text(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
