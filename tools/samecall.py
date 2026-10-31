"""Control, AD and SR registers written more than once within one play call of a tune's own player:
per player, the calls and tunes where a voice's register takes two values in one call, and the
commonest value sequences (a gate or test bit toggled within the call is invisible on a frame grid).

``python tools/samecall.py TUNES.csv|TUNE.sid ... [--player RE] [--frames N] [--out F.json]``
"""

import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

from pysidtracker import SidImage
from pysidtracker.emu import call_play, observe_data_access
from pysidtracker.jobs import deadline, imap
from pysidtracker.oracle import boot_flat
from tqdm import tqdm

import shared

SID, VOICE = 0xD400, 7
NAMES = {4: "ctrl", 5: "ad", 6: "sr"}
GATE, TEST = 0x01, 0x08


def kind(reg: int, values: list) -> str:
    """How a register's values written in one call differ: ``same`` (rewritten with one value),
    ``gate``/``test`` (a control write toggling that bit within the call), else ``value``."""
    if len(set(values)) == 1:
        return "same"
    if reg == 4:
        for bit, name in ((GATE, "gate"), (TEST, "test")):
            if len({v & bit for v in values}) > 1:
                return name
    return "value"


def measure(row: dict, hvsc, frames: int) -> dict:  # pylint: disable=too-many-locals
    """One tune's counts: per register name and kind, the calls; and its value sequences."""
    image = SidImage.from_bytes(shared.tune(row["path"], hvsc).read_bytes())
    if not image.header.play_address:
        return {"skipped": "no play address"}
    log = []

    def setup(mpu, _):
        def write(addr, value):
            off = addr - SID
            if 0 <= off < 3 * VOICE and off % VOICE in NAMES:
                log.append((off, value))

        observe_data_access(mpu, on_write=write)

    _, mpu, mem, play = boot_flat(image, int(row.get("subtune", 0)), setup=setup)
    calls, seqs = Counter(), Counter()
    for _ in range(frames):
        log.clear()
        call_play(mpu, mem, play)
        by = defaultdict(list)
        for off, value in log:
            by[off].append(value)
        for off, values in by.items():
            if len(values) > 1:
                reg = off % VOICE
                k = kind(reg, values)
                calls[f"{NAMES[reg]}:{k}"] += 1
                if k != "same":
                    seqs[f"{NAMES[reg]} " + ">".join(f"{v:02x}" for v in values)] += 1
    return {"calls": dict(calls), "seqs": dict(seqs.most_common(8)), "frames": frames}


def timed(row: dict, hvsc, frames: int, timeout: float) -> dict:
    """:func:`measure` of a tune row within ``timeout`` seconds."""
    with deadline(timeout):
        return measure(row, hvsc, frames)


def summary(recs: list) -> str:
    """Per player: tunes measured, and per register and kind the tunes and calls, then the
    commonest value sequences over the tunes."""
    lines, by = [], defaultdict(list)
    for r in recs:
        by[r.get("player", "")].append(r)
    for player, group in sorted(by.items()):
        ok = [r for r in group if "calls" in r]
        lines.append(f"{player}: {len(ok)} tunes measured, {len(group) - len(ok)} failed/skipped")
        tunes, calls, seqs = Counter(), Counter(), Counter()
        for r in ok:
            tunes.update(r["calls"].keys())
            calls.update(r["calls"])
            seqs.update(r["seqs"].keys())
        total = sum(r["frames"] for r in ok)
        for key in sorted(tunes):
            lines.append(
                f"  {key:12} {tunes[key]:5} tunes {calls[key]:9} calls"
                f" ({calls[key] / max(1, total):.2%} of calls)"
            )
        lines += [f"  seq {s} in {n} tunes" for s, n in seqs.most_common(10)]
    return "\n".join(lines)


def main(argv=None) -> int:
    """Parse the command line, measure the tunes in parallel and print the summary."""
    ap = shared.corpus_parser(__doc__)
    ap.add_argument("--frames", type=int, default=3000)
    ap.add_argument("--timeout", type=float, default=300, help="seconds per tune")
    ap.add_argument("--jobs", type=int, default=max(1, len(os.sched_getaffinity(0)) - 2))
    ap.add_argument("--out", type=Path, help="write each tune's record as JSON")
    args = ap.parse_args(argv)
    rows = shared.rows(args.sources, args.player)
    tasks = [(r, args.hvsc, args.frames, args.timeout) for r in rows]
    recs = []
    for i, rec, err in tqdm(imap(timed, tasks, args.jobs), total=len(tasks), unit="tune"):
        head = {"path": rows[i]["path"], "player": rows[i].get("player", "")}
        recs.append(head | (rec if rec is not None else {"error": repr(err)}))
    print(summary(recs))
    if args.out:
        args.out.write_text(json.dumps(recs, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
