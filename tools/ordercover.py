"""The fewest per-voice write orders that between them play every frame exactly as its own player
does, and which note-ons had a settled hard restart (gate off, release 0, long enough).

``python tools/ordercover.py TUNES.csv|TUNE.sid ... [--horizon N] [--out F.json]``; see
docs/writeorder.md.
"""

import sys
from collections import Counter, defaultdict
from itertools import combinations

import numpy as np
from pysidtracker.jobs import deadline
from vagrant_moses import memmap as mm

import orderaudio as A
import shared
import writeorder as W


def same(a, b) -> bool:
    """Whether two envelope traces are identical."""
    return len(a) == len(b) and all(
        np.array_equal(a[k], b[k]) for k in ("cycle", "envelope", "state")
    )


def settle_cycles(clock: float) -> int:
    """The most cycles a hard restart (gate off, release rate 0) takes to leave a voice in the
    same state whatever came before: a full wrap of the rate counter, during which the envelope
    does not step, then the release from the highest level to 0 at rate 0."""
    sid = A.chips(clock, 8000.0, "decimate")[0]
    sid.run([(1, 5, 0x00), (2, 6, 0xF0), (3, 4, A.SAW | W.GATE)], 2 * 19656)
    start = sid.cycle
    trace = sid.envelope_trace(0, [(start, 6, 0x00), (start, 4, A.SAW)], start + 19656)
    silent = trace.cycle[trace.envelope == 0]
    return 0x7FFF + int(silent[0] - start)


def hard_restarts(row: dict, hvsc, args) -> dict:  # pylint: disable=too-many-locals
    """Each note-on (a gate-on from gate off): how many cycles before its frame's first write the
    gate had been off with SR's release nibble 0, counted by whole frames, and whether that is
    at least :func:`settle_cycles`."""
    init, frames = W.capture(row, hvsc, args)
    settle = args.settle
    since, regs = [None] * 3, np.zeros(W.REGS, np.int64)
    regs[init[:, 1]] = init[:, 2]
    found = Counter()
    for _, w in frames:
        for v in range(3):
            ctrl, sr = int(regs[7 * v + 4]), int(regs[7 * v + 6])
            mine = w[(w[:, 1] >= 7 * v + 4) & (w[:, 1] <= 7 * v + 6)]
            ons = mine[(mine[:, 1] == 7 * v + 4) & (mine[:, 2] & W.GATE != 0)]
            if len(ons) and not ctrl & W.GATE:
                first = int(mine[0, 0])
                held = first - since[v] if since[v] is not None else 0
                found[(held >= settle, held // 19656)] += 1
            for cycle, reg, value in mine.tolist():
                ctrl, sr = (
                    (value, sr) if reg == 7 * v + 4 else (ctrl, value if reg == 7 * v + 6 else sr)
                )
                quiet = not ctrl & W.GATE and not sr & 0x0F
                since[v] = (since[v] if since[v] is not None else cycle) if quiet else None
        regs[w[:, 1]] = w[:, 2]
    return {"notes": [[int(s), int(k), n] for (s, k), n in found.items()]}


def measure(row: dict, hvsc, args) -> dict:  # pylint: disable=too-many-locals
    """Each distinct set of orders that keeps a (frame, voice) envelope identical over
    ``args.horizon`` frames, as a mask over :data:`writeorder.ORDERS`, with its count and an
    example native pattern."""
    init, frames = W.capture(row, hvsc, args)
    sids, regs, found = A.chips(mm.PAL.clock_hz), np.zeros(W.REGS, np.int64), Counter()
    examples = {}
    regs[init[:, 1]] = init[:, 2]
    for v, sid in enumerate(sids):
        sid.run(A.voice_writes(init[init[:, 0] >= sid.cycle], v), frames[0][0])
    for f, (_, w) in enumerate(frames[: -args.horizon]):
        for v, sid in enumerate(sids):
            mine = A.voice_writes(w, v)
            start = tuple(int(x) for x in regs[7 * v + 4 : 7 * v + 7])
            native = W.events(start, [(r, x) for _, r, x in mine.tolist()])
            if native:
                tail = [A.voice_writes(frames[g][1], v) for g in range(f + 1, f + args.horizon)]
                until = frames[f + args.horizon][0]
                ref = sid.envelope_trace(0, np.concatenate([mine, *tail]), until)
                ok, seen = [], {}
                for order in W.ORDERS:
                    new = A.ordered(mine, order)
                    key = new.tobytes()
                    if key not in seen:
                        seen[key] = np.array_equal(new, mine) or same(
                            sid.envelope_trace(0, np.concatenate([new, *tail]), until), ref
                        )
                    ok.append(seen[key])
                mask = int("".join("1" if x else "0" for x in ok), 2)
                found[mask] += 1
                examples.setdefault(mask, W.text(native))
            sid.run(mine, frames[f + 1][0])
        regs[w[:, 1]] = w[:, 2]
    return {"masks": [[hex(m), n, examples[m]] for m, n in found.items()]}


def timed(row: dict, hvsc, args) -> dict:
    """:func:`measure` of a tune row within ``args.timeout`` seconds."""
    with deadline(args.timeout):
        return measure(row, hvsc, args) | {"hr": hard_restarts(row, hvsc, args)}


def bits(mask: int) -> np.ndarray:
    """``mask`` as a boolean array over :data:`writeorder.ORDERS`."""
    return np.array([c == "1" for c in format(mask, f"0{len(W.ORDERS)}b")])


def covers(masks: dict, most: int) -> tuple:
    """``(size, [cover])``: every smallest set of orders (at most ``most``) such that each mask
    with any order in it has one of them."""
    needed = np.array([bits(m) for m in masks if m])
    for size in range(1, most + 1):
        found = [
            c for c in combinations(range(len(W.ORDERS)), size) if needed[:, c].any(axis=1).all()
        ]
        if found:
            return size, found
    return None, []


def hard_restart_lines(recs: list, settle: int) -> list:
    """Per player: the note-ons, how many had a settled hard restart, and the frames of hard
    restart (gate off, release 0) the unsettled ones had."""
    lines = [
        f"  hard restart template: gate off and SR release 0 from at least {settle} cycles "
        f"({-(-settle // 19656)} PAL frames) before the note's frame"
    ]
    by = defaultdict(Counter)
    for r in recs:
        for settled, frames, n in r.get("hr", {}).get("notes", []):
            by[r.get("player", "")][(settled, frames)] += n
    for player, notes in sorted(by.items()):
        total = sum(notes.values())
        good = sum(n for (s, _), n in notes.items() if s)
        short = Counter()
        for (s, k), n in notes.items():
            if not s:
                short[k] += n
        lines.append(
            f"    {player}: {total} note-ons, {good} settled; unsettled by frames of hard restart: "
            + " ".join(f"{k}:{n}" for k, n in sorted(short.items()))
        )
    return lines


def summary(recs: list, most: int, settle: int = 0) -> str:  # pylint: disable=too-many-locals
    """The cases, those no order keeps (with their commonest patterns), and every smallest cover
    with how many cases each of its orders keeps."""
    masks, players, example = Counter(), defaultdict(Counter), {}
    for r in recs:
        for m, n, text in r.get("masks", []):
            masks[int(m, 16)] += n
            players[r.get("player", "")][int(m, 16)] += n
            if not int(m, 16):
                example[text] = example.get(text, 0) + n
    total = sum(masks.values())
    lines = [f"{total} (frame, voice) cases with envelope events, {len(masks)} distinct order sets"]
    lines.append(f"  kept by no order: {masks[0]}")
    lines += [f"    {p}: {ms[0]} of {sum(ms.values())}" for p, ms in sorted(players.items())]
    lines += [f"    {n:6}  {t}" for t, n in Counter(example).most_common(8)]
    lines += hard_restart_lines(recs, settle)
    size, found = covers(masks, most)
    if size is None:
        lines.append(f"  no cover of {most} orders or fewer")
        return "\n".join(lines)
    lines.append(f"  smallest covers: {len(found)} of {size} orders")
    for cover in found[:10]:
        lines.append("    " + " | ".join(",".join(W.ORDERS[i]) for i in cover))
        for i in cover:
            alone = sum(n for m, n in masks.items() if m and bits(m)[i])
            lines.append(f"      {alone:7} cases kept by {','.join(W.ORDERS[i])}")
    return "\n".join(lines)


def main(argv=None) -> int:
    """Parse the command line, measure the tunes in parallel and print the covers."""
    ap = W.capture_arguments(shared.corpus_parser(__doc__))
    ap.add_argument("--horizon", type=int, default=10, help="frames compared from each case")
    ap.add_argument("--most", type=int, default=4, help="largest cover searched")
    args = ap.parse_args(argv)
    args.settle = settle_cycles(mm.PAL.clock_hz)
    rows = shared.rows(args.sources, args.player)
    tasks = [(r, args.hvsc, args) for r in rows]
    recs = shared.run_corpus(timed, rows, tasks, args.jobs, args.out)
    print(summary(recs, args.most, args.settle))
    return 0


if __name__ == "__main__":
    sys.exit(main())
