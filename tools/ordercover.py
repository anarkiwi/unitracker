"""The fewest per-voice write orders that between them play every frame exactly as its own player
does, and which note-ons had a settled hard restart (gate off, release 0, long enough).

``python tools/ordercover.py TUNES.csv|TUNE.sid ... [--horizon N] [--out F.json]``; see
docs/writeorder.md.
"""

import sys
from collections import Counter, defaultdict, deque
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


def neutral(rows: np.ndarray, since: int) -> np.ndarray:
    """Voice-1 ``rows`` with every AD write from cycle ``since`` on set to 0 and every SR write
    from then on to its release nibble alone."""
    out = rows.copy()
    late = out[:, 0] >= since
    out[late & (out[:, 1] == 5), 2] = 0
    sr = late & (out[:, 1] == 6)
    out[sr, 2] &= 0x0F
    return out


def hard_restarts(row: dict, hvsc, args) -> dict:  # pylint: disable=too-many-locals
    """Each note-on (a gate-on from gate off): the whole frames before its frame's first write
    that the gate had been off with SR's release nibble 0, whether that is at least
    :func:`settle_cycles`, and whether the AD and SR values written meanwhile matter."""
    init, frames = W.capture(row, hvsc, args)
    starts = [s for s, _ in frames]
    sids = A.chips(mm.PAL.clock_hz, 8000.0, "decimate")
    rows = [[A.voice_writes(w, v) for v in range(3)] for _, w in frames]
    for v, sid in enumerate(sids):
        sid.run(A.voice_writes(init[init[:, 0] >= sid.cycle], v), starts[0])
    regs = np.zeros(W.REGS, np.int64)
    regs[init[:, 1]] = init[:, 2]
    since, past, found = [None] * 3, [deque(maxlen=5) for _ in range(3)], Counter()
    for f in range(len(frames) - args.horizon):
        for v, sid in enumerate(sids):
            past[v].append((f, sid.copy()))
            mine, ctrl, sr = rows[f][v], int(regs[7 * v + 4]), int(regs[7 * v + 6])
            ons = mine[(mine[:, 1] == 4) & (mine[:, 2] & W.GATE != 0)]
            if len(ons) and not ctrl & W.GATE:
                held = int(mine[0, 0]) - since[v] if since[v] is not None else 0
                kept = None
                begun = int(np.searchsorted(starts, since[v], side="right")) - 1 if held else f
                base = next((c for g, c in past[v] if g == begun), None)
                if base is not None:
                    writes = np.concatenate([rows[g][v] for g in range(begun, f + args.horizon)])
                    until = starts[f + args.horizon]
                    ref = base.envelope_trace(0, writes, until)
                    trial = neutral(writes, since[v] if held else int(mine[0, 0]))
                    trial[trial[:, 0] >= int(mine[0, 0])] = writes[writes[:, 0] >= int(mine[0, 0])]
                    kept = same(base.envelope_trace(0, trial, until), ref)
                found[(held >= args.settle, held // 19656, kept)] += 1
            for cycle, reg, value in mine.tolist():
                ctrl, sr = (value, sr) if reg == 4 else (ctrl, value if reg == 6 else sr)
                quiet = not ctrl & W.GATE and not sr & 0x0F
                since[v] = (since[v] if since[v] is not None else cycle) if quiet else None
            sid.run(mine, starts[f + 1])
        regs[frames[f][1][:, 1]] = frames[f][1][:, 2]
    return {"notes": [[int(s), int(k), i, n] for (s, k, i), n in found.items()]}


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
    """Per player: the note-ons by whole frames of hard restart (gate off, release 0) before
    their frame, settled or not, and how many of each the AD and SR values written meanwhile
    leave identical (checked where the hard restart began within the last four frames)."""
    lines = [
        f"  hard restart: gate off and SR release 0; settled from {settle} cycles "
        f"({-(-settle // 19656)} PAL frames) before the note's frame whatever came before"
    ]
    by, top = defaultdict(Counter), -(-settle // 19656)
    for r in recs:
        for settled, frames, kept, n in r.get("hr", {}).get("notes", []):
            by[r.get("player", "")][(settled, min(frames, top), kept)] += n
    for player, notes in sorted(by.items()):
        lines.append(f"    {player}: {sum(notes.values())} note-ons")
        for frames in sorted({k for _, k, _ in notes}):
            row = {key: n for key, n in notes.items() if key[1] == frames}
            total = sum(row.values())
            same_values = sum(n for (_, _, i), n in row.items() if i)
            checked = sum(n for (_, _, i), n in row.items() if i is not None)
            settled = sum(n for (s, _, _), n in row.items() if s)
            label = f"{frames}+" if frames == top else str(frames)
            lines.append(
                f"      {label:>2} frames: {total:6} notes, {settled:6} settled, "
                f"values irrelevant in {same_values} of {checked} checked"
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
