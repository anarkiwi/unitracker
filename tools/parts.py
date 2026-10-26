"""Musical parts of documents: each voice's sounds, percussive or tonal by how they are played,
and how much each part repeats where a voice interleaves them.

``python tools/parts.py DOC.yaml ... [--ticks N] [--jobs J] [--out PARTS.json]``
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from pysidtracker.jobs import deadline, imap
from tqdm import tqdm
from unitracker_player.player import load
from unitracker_player.sequencer import Sequencer

FOCUS = 0.9
MIN_NOTES = 8


def sound(ins) -> str:
    """An instrument's sound: its control program (waveform and modulation rows), so that a
    template's instances with other envelopes or arguments are one sound."""
    return repr(ins.ctrl) if ins is not None else "none"


def segments(text: str, ticks: int) -> dict:
    """Per voice, its phrases as played over ``ticks``: each a list of ``(tick in phrase, note,
    sound)`` for its notes, the voice's instrument carried from note to note."""
    seq = Sequencer(load(text))
    out = defaultdict(list)
    current, start, ins = {}, {}, {}
    for tick, voice, event, transpose, last in seq.advance(ticks):
        if voice not in current:
            current[voice], start[voice] = [], tick
        if event is not None:
            if event.ins is not None:
                ins[voice] = sound(event.ins)
            if isinstance(event.note, int):
                note = event.note + transpose
                current[voice].append((tick - start[voice], note, ins.get(voice, "none")))
        if last:
            out[voice].append(current.pop(voice))
    return out


def percussive(segs: dict) -> set:
    """The sounds played at least ``MIN_NOTES`` times with ``FOCUS`` of their notes on at most
    two pitches."""
    notes = defaultdict(Counter)
    for phrases in segs.values():
        for phrase in phrases:
            for _, note, snd in phrase:
                notes[snd][note] += 1
    return {
        s
        for s, c in notes.items()
        if c.total() >= MIN_NOTES and sum(n for _, n in c.most_common(2)) >= FOCUS * c.total()
    }


def parts(path: Path, ticks: int) -> dict:
    """A document's sounds and, per voice that interleaves percussive and tonal sounds, its
    distinct phrases whole and as each part alone."""
    meta = path.parent.parent / "meta" / f"{path.stem}.json"
    limit = min(ticks, json.loads(meta.read_text()).get("ticks", ticks)) if meta.exists() else ticks
    segs = segments(path.read_text(encoding="utf-8"), limit)
    drums = percussive(segs)
    sounds = {s for ph in segs.values() for p in ph for _, _, s in p}
    rec = {"doc": path.stem, "sounds": len(sounds), "percussive": len(drums), "voices": []}
    for voice, phrases in sorted(segs.items()):
        kinds = {s in drums for p in phrases for _, _, s in p}
        whole = {tuple(p) for p in phrases if p}
        drum = {tuple(e for e in p if e[2] in drums) for p in phrases if p}
        tone = {tuple(e for e in p if e[2] not in drums) for p in phrases if p}
        rec["voices"].append(
            {
                "voice": voice,
                "phrases": sum(1 for p in phrases if p),
                "mixed": kinds == {True, False},
                "distinct": len(whole),
                "distinct_percussive": len(drum - {()}),
                "distinct_tonal": len(tone - {()}),
            }
        )
    return rec


def timed(path: Path, ticks: int, timeout: float) -> dict:
    """:func:`parts` within ``timeout`` seconds."""
    with deadline(timeout):
        return parts(path, ticks)


def run(args) -> list:
    """Each document's :func:`parts`, in parallel, a failure's error in its record."""
    tasks = [(path, args.ticks, args.timeout) for path in args.documents]
    out = []
    for i, rec, err in tqdm(imap(timed, tasks, args.jobs), total=len(tasks), unit="doc"):
        out.append(rec or {"doc": args.documents[i].stem, "error": repr(err)})
    return out


def summary(recs: list) -> str:
    """The share of voices interleaving percussive and tonal sounds, and on those voices the
    distinct phrases whole against each part's."""
    ok = [r for r in recs if "voices" in r]
    voices = [v for r in ok for v in r["voices"] if v["phrases"]]
    mixed = [v for v in voices if v["mixed"]]
    total = {k: sum(v[k] for v in mixed) for k in ("phrases", "distinct")}
    total |= {k: sum(v[k] for v in mixed) for k in ("distinct_percussive", "distinct_tonal")}
    with_drums = sum(1 for r in ok if r["percussive"])
    lines = [
        f"{len(ok)} documents ({len(recs) - len(ok)} failed), {with_drums} with percussive sounds",
        f"voices: {len(voices)}, interleaving percussive and tonal: {len(mixed)} "
        f"({len(mixed) / max(1, len(voices)):.0%})",
        f"on those voices: {total['phrases']} phrases played, {total['distinct']} distinct whole, "
        f"{total['distinct_percussive']} distinct percussive parts, "
        f"{total['distinct_tonal']} distinct tonal parts",
        f"sounds per document: median {np.median([r['sounds'] for r in ok]):.0f}, percussive "
        f"median {np.median([r['percussive'] for r in ok]):.0f}",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    """Parse the command line, measure the documents and print the summary."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("documents", nargs="+", type=Path)
    ap.add_argument("--ticks", type=int, default=30000, help="most ticks played per document")
    ap.add_argument("--timeout", type=float, default=120, help="seconds per document")
    ap.add_argument("--jobs", type=int, default=max(1, len(os.sched_getaffinity(0)) - 2))
    ap.add_argument("--out", type=Path, help="write each document's record as JSON")
    args = ap.parse_args(argv)
    recs = run(args)
    if args.out:
        args.out.write_text(json.dumps(recs, indent=1), encoding="utf-8")
    print(summary(recs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
