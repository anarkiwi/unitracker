"""Classify why a ``corpus.py check`` table's differing tunes diverge from their references.

Each document's grid is compared with its verify reference and an alternative one (GoatTracker 2,
Music Assembler: the standard note table; defMON: the native one). Usage:
``divergence.py out/check/results.csv`` writes ``classes.csv`` beside it and prints the tally.
"""

import argparse
import csv
import os
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from pysidtracker import gridcmp
from pysidtracker.jobs import deadline, imap
from tqdm import tqdm
from unitracker_grammar.notes import frequency
from unitracker_player.player import play

import shared

NREGS = gridcmp.NREGS
GROUPS = {"freq": "pitch", "pw": "pulse", "ctrl": "ctrl", "ad": "adsr", "sr": "adsr"}
GROUPS |= {"fc": "cutoff", "res": "filter", "vol": "filter"}
FIELDS = (
    "doc",
    "player",
    "class",
    "cells",
    "native_cells",
    "first",
    "groups",
    "lag_cells",
    "init_cells",
    "late_cells",
    "flags",
)


def group(reg: int) -> str:
    """The register group of grid column ``reg`` (several chips' columns repeat per chip)."""
    return GROUPS[gridcmp.ROLES[reg % NREGS]]


def aligned(ours, ref, lag: int):
    """``ours`` and ``ref`` from tick 2 with ``ref`` lagging ``lag`` ticks behind (negative:
    ahead), the pair cut to a common length."""
    n = min(len(ours), len(ref)) - abs(lag)
    return (ours[2:n], ref[2 + lag : n + lag]) if lag >= 0 else (ours[2 - lag : n - lag], ref[2:n])


def _init_run(cells, reg: int) -> bool:
    """Whether register ``reg`` differs in a single run of ticks from tick 0."""
    ticks = cells[cells[:, 1] == reg][:, 0]
    return bool(ticks[0] == 0 and ticks[-1] == len(ticks) - 1)


def classify(ours, std, native) -> dict:
    """The divergence class of grid ``ours`` from the standard-note-table reference ``std``.

    ``pitch-standard``: only ``native`` differs; ``lag±1``: a tick's shift halves the cells;
    ``init``: runs from the start; then the groups differing after tick 1 (``gate``, ``desync``)."""
    n = min(len(ours), len(std), len(native))
    ours, std = np.asarray(ours[:n], np.uint8), np.asarray(std[:n], np.uint8)
    native_cells = gridcmp.cell_diff(ours, np.asarray(native[:n], np.uint8))
    cells = gridcmp.cell_diff(ours, std)
    out = {"cells": len(cells), "native_cells": len(native_cells), "first": "", "groups": ""}
    if cells.size == 0:
        return out | {"class": "pitch-standard" if len(native_cells) else "exact"}
    out["first"] = f"{int(cells[0][0])} ${int(cells[0][1]):02X}"
    out["groups"] = "+".join(sorted({group(int(r)) for r in cells[:, 1]}))
    counts = {k: int(np.not_equal(*aligned(ours, std, k)).sum()) for k in (0, 1, -1)}
    lag = min(counts, key=counts.get)
    lag = lag if counts[lag] * 2 < counts[0] else 0
    out["lag_cells"] = counts[lag]
    diff = gridcmp.cell_diff(*aligned(ours, std, lag))
    start = np.array([_init_run(diff, int(c[1])) for c in diff], bool)
    late = diff[~start] if len(diff) else diff
    out |= {"init_cells": int(start.sum()), "late_cells": len(late)}
    head = [f"lag{lag:+d}"] * bool(lag) + ["init"] * bool(start.any())
    if late.size == 0:
        return out | {"class": "/".join(head or ["ticks0-1"])}
    groups = sorted({group(int(r)) for r in late[:, 1]})
    if groups == ["ctrl"] and not ((late[:, 2] ^ late[:, 3]) & 0xFE).any():
        groups = ["gate"]
    ticks = np.unique(late[:, 0])
    if len(groups) >= 4 and "ctrl" in groups and len(ticks) * 2 > ticks[-1] - ticks[0]:
        groups = ["desync"]
    return out | {"class": "/".join(head + ["+".join(groups)])}


def _filter_voices(song, subtune: int) -> int:
    """GoatTracker 2 channels of ``subtune`` that start a filter program (an instrument's filter
    pointer or command A)."""
    out = 0
    for chan in song.subtunes[subtune].channels:
        rows = [r for e in chan.entries if hasattr(e, "num") for r in song.patterns[e.num].rows]
        filt = any(r.command == 0xA or song.instrument(r.instrument).filter_ptr for r in rows)
        out += filt
    return out


def _gt2(raw: bytes, subtune: int, frames: int):
    # pylint: disable=import-outside-toplevel
    from pygoattracker.player import Player
    from pygoattracker.sid import decompile_sid

    from unitracker.direct import goattracker2 as gt2

    model = decompile_sid(raw, subtune)
    ours = play(gt2.Transcriber(model).document(), ticks=frames)
    ref = model.player().render_grid(len(ours))
    table = [frequency(k, gt2.CLOCK["clock_hz"]) for k in range(128)]
    alt = Player(
        model.typed,
        subtune=model.subtune,
        build=model.info.playback,
        state=model.info.state,
        freq_table=table,
    ).render_grid(len(ours))
    pb, state = model.info.playback, model.info.state
    cmds = Counter(r.command for p in model.song.patterns for r in p.rows if r.command)
    flags = {
        "ghost": bool(state and getattr(state, "ghost", False)),
        "simplepulse": pb.simplepulse,
        "init_regs": len(pb.init_regs),
        "overlay": pb.overlay is not None,
        "voices": pb.voices is not None,
        "calls": pb.calls,
        "wrapper_flush": pb.wrapper_flush,
        "cmd9": cmds[9],
        "cmdA": cmds[0xA],
        "filter_voices": _filter_voices(model.song, model.subtune),
        "wavecmds": "".join(
            sorted({f"{w & 15:X}" for w in model.song.wavetable.left if 0xF0 <= w < 0xFF})
        ),
    }
    return ours, alt, ref, flags


def _ma(raw: bytes, subtune: int, frames: int):
    # pylint: disable=import-outside-toplevel
    from pymusicassembler.player import Player

    from unitracker.direct import musicassembler as ma

    one = ma.transcriber(raw, subtune)
    ours = play(one.document(), ticks=frames)
    ref = Player(one.song, subtune).render_grid(len(ours))
    return ours, ma.standard(one.song, subtune).render_grid(len(ours)), ref, {}


def _dm(raw: bytes, subtune: int, frames: int):
    # pylint: disable=import-outside-toplevel
    from pydefmon import DefmonPlayer

    from unitracker.direct import defmon as dm

    ours = play(dm.transcribe(raw, subtune), ticks=frames)
    ref = dm.standard(raw, subtune).render_grid(len(ours))
    return ours, ref, DefmonPlayer(raw, subtune=subtune).render_grid(len(ours)), {}


GRIDS = {"goattracker2": _gt2, "musicassembler": _ma, "defmon": _dm}


def classify_one(row: dict, opts: dict) -> dict:
    """One differing tune's class row."""
    res = dict.fromkeys(FIELDS, "") | {"doc": row["doc"], "player": row["player"]}
    try:
        with deadline(opts["timeout"]):
            raw = shared.tune(row["path"], opts["hvsc"]).read_bytes()
            ours, std, native, flags = GRIDS[row["player"]](raw, opts["subtune"], opts["frames"])
            res |= classify(np.asarray(ours), np.asarray(std), np.asarray(native))
            res["flags"] = " ".join(f"{k}={v}" for k, v in flags.items())
    except Exception as exc:  # pylint: disable=broad-exception-caught
        res["class"] = f"error: {type(exc).__name__}: {exc}"[:120]
    return res


def main(argv=None) -> int:
    """Classify a check table's differing tunes into ``classes.csv`` and print the tally."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("results", type=Path, help="a corpus.py check results.csv")
    ap.add_argument("--hvsc", type=Path, default=os.environ.get("HVSC"))
    ap.add_argument("--frames", type=int, default=3000)
    ap.add_argument("--subtune", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--jobs", type=int, default=max(1, len(os.sched_getaffinity(0)) // 4))
    args = ap.parse_args(argv)
    with args.results.open(newline="", encoding="utf-8") as fh:
        todo = [r for r in csv.DictReader(fh) if r["verified"] == "no" and not r["gap"]]
    opts = {"hvsc": args.hvsc, "frames": args.frames, "subtune": args.subtune}
    opts["timeout"] = args.timeout
    rows = [None] * len(todo)
    for i, value, error in tqdm(
        imap(classify_one, [(r, opts) for r in todo], args.jobs), total=len(todo)
    ):
        rows[i] = value or dict.fromkeys(FIELDS, "") | {
            "doc": todo[i]["doc"],
            "player": todo[i]["player"],
            "class": f"error: {type(error).__name__}",
        }
    rows.sort(key=lambda r: (r["player"], r["class"], r["doc"]))
    with (args.results.parent / "classes.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    for (player, cls), count in sorted(Counter((r["player"], r["class"]) for r in rows).items()):
        print(f"{player:15} {cls:24} {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
