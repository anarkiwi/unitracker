"""The fewest per-voice write orders that between them play every frame exactly as its own player
does, and which note-ons had a settled hard restart (gate off, release 0, long enough).

``python tools/ordercover.py TUNES.csv|TUNE.sid ... [--horizon N] [--out F.json]``; see
docs/writeorder.md.
"""

import sys
from collections import Counter, defaultdict

from pysidtracker.jobs import deadline
from vagrant_moses import memmap as mm
from vagrant_moses import sidorder as S

import shared
import writeorder as W


def hard_restarts(tl, args) -> dict:
    """Each note-on (a gate-on from gate off): the whole frames before its frame's first write
    that the gate had been off with SR's release nibble 0, whether that is at least
    ``args.settle`` cycles, and whether the AD and SR values written meanwhile matter."""
    found = S.hard_restarts(tl, args.settle, args.horizon)
    return {"notes": [[int(s), int(k), i, n] for (s, k, i), n in found.items()]}


def measure(tl, args) -> dict:
    """Each distinct set of orders that keeps a (frame, voice) envelope identical over
    ``args.horizon`` frames, as a mask over :data:`writeorder.ORDERS`, with its count and an
    example native pattern."""
    return {"masks": [[hex(m), n, ex] for m, (n, ex) in S.order_masks(tl, args.horizon).items()]}


def timed(row: dict, hvsc, args) -> dict:
    """One tune's cases and hard restarts within ``args.timeout`` seconds."""
    with deadline(args.timeout):
        tl = S.Timeline(*W.capture(row, hvsc, args))
        return measure(tl, args) | {"hr": hard_restarts(tl, args)}


bits, covers = S.bits, S.covers


def hard_restart_lines(recs: list, settle: int) -> list:  # pylint: disable=too-many-locals
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
    args.settle = S.settle_cycles(mm.PAL.clock_hz)
    rows = shared.rows(args.sources, args.player)
    tasks = [(r, args.hvsc, args) for r in rows]
    recs = shared.run_corpus(timed, rows, tasks, args.jobs, args.out)
    print(summary(recs, args.most, args.settle))
    return 0


if __name__ == "__main__":
    sys.exit(main())
