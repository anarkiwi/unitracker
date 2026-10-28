"""Hard restarts on tunes' native register grids: per voice, each note onset's lead frames (gate,
test bit, AD and SR against the onset's) classified, and per format the classes, leads and values.

``python tools/hardrestart.py TUNES.csv|TUNE.sid ... [--player RE] [--sample N] [--out F.json]``
"""

import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from pysidtracker.jobs import deadline, imap
from pysidtracker.oracle import register_grid
from tqdm import tqdm

import shared

VOICE, CTRL, AD, SR = 7, 4, 5, 6
GATE, TEST = 0x01, 0x08
WRAP = 1 << 15
CYCLES = {"PAL": 63 * 312, "NTSC": 65 * 263}
CLASSES = ("classic", "ad-only", "sr-only", "test-bit", "gate-off", "none")
RESTARTS = CLASSES[:4]
FEATURES = ("gate_off", "test", "ad_lowered", "sr_lowered", "ad_zero", "sr_zero", "rate_safe")


def voices(grid) -> tuple:
    """A register grid's ``(ctrl, ad, sr)``, each ``(frames, 3)``."""
    regs = np.asarray(grid, np.int16)[:, : 3 * VOICE].reshape(len(grid), 3, VOICE)
    return regs[..., CTRL], regs[..., AD], regs[..., SR]


def onsets(ctrl: np.ndarray) -> tuple:
    """The ``(frames, voices)`` of the note onsets: the gate set, or the test bit cleared where
    the gate was held from before the test bit was set."""
    gate, test = (ctrl & GATE) > 0, (ctrl & TEST) > 0
    frame = np.arange(len(ctrl))[:, None]
    began = np.maximum.accumulate(np.where(test[1:] & ~test[:-1], frame[1:], 0), axis=0)
    off = np.cumsum(~gate, axis=0)
    cleared = gate[1:] & gate[:-1] & test[:-1] & ~test[1:]
    held = np.take_along_axis(off, began - 1, 0) == off[1:]
    held &= np.take_along_axis(gate, began - 1, 0) & (began > 0)
    t, v = np.nonzero((gate[1:] & ~gate[:-1]) | (cleared & held))
    return t + 1, v


def lead(regs: tuple, t: np.ndarray, v: np.ndarray, k: int) -> dict:
    """Per onset, each :data:`FEATURES` over its ``k`` lead frames (column j the (j+1)th before
    it, false before the grid): AD and SR lowered from the onset's or zero, and whether the
    envelope rate in effect (release, or the slower of attack and decay under a gate) is no
    slower than the onset's attack."""
    back = t[:, None] - np.arange(1, k + 1)
    ok = back >= 0
    c, a, s = (r[np.maximum(back, 0), v[:, None]] for r in regs)
    a0, s0 = regs[1][t, v][:, None], regs[2][t, v][:, None]
    rate = np.where(c & GATE, np.maximum(a >> 4, a & 15), s & 15)
    return {
        "gate_off": ok & ((c & GATE) == 0),
        "test": ok & ((c & TEST) > 0),
        "ad_lowered": ok & (a < a0),
        "sr_lowered": ok & (s < s0),
        "ad_zero": ok & (a == 0),
        "sr_zero": ok & (s == 0),
        "rate_safe": ok & (rate <= a0 >> 4),
        "values": (a.astype(np.int32) << 8) | s,
    }


def run(mask: np.ndarray) -> np.ndarray:
    """Per row, how many of its leading columns are true."""
    return np.cumprod(mask, axis=1).sum(axis=1)


def classify(feat: dict) -> tuple:
    """Per onset, its class (an index into :data:`CLASSES`), its lead and its restart frames: the
    leading lead frames of lowered AD or SR or a set test bit, else of the gate off; a run filling
    the window is a state held from before it, not a restart (a held gate off is ``none``)."""
    k = feat["gate_off"].shape[1]
    frames = np.cumprod(feat["ad_lowered"] | feat["sr_lowered"] | feat["test"], axis=1) > 0
    frames &= ~frames[:, -1:]
    ad, sr, test, off = (
        (feat[f] & frames).any(axis=1) for f in ("ad_lowered", "sr_lowered", "test", "gate_off")
    )
    gated = run(feat["gate_off"])
    cls = np.select(
        [ad & sr & off, ad & ~sr, sr & ~ad, test, (gated > 0) & (gated < k)],
        np.arange(5),
        5,
    )
    return cls, np.where(cls < 4, frames.sum(axis=1), gated), frames


def wrap_frames(clock: str | None) -> int:
    """The frames the envelope's 15-bit rate prescaler takes to wrap at ``clock``'s frame rate."""
    return math.ceil(WRAP / CYCLES.get((clock or "PAL").upper(), CYCLES["PAL"]))


def histogram(values: np.ndarray, k: int) -> list:
    """The counts of ``values`` 0..``k``."""
    return np.bincount(values, minlength=k + 1)[: k + 1].tolist()


def analyse(grid, k: int = 4, clock: str | None = None) -> dict:
    """A grid's onsets, their classes, per class the lead histogram (0..``k``), per lead frame
    each feature's count, the AD/SR values in restart frames, and the rate-safe runs; onsets
    without ``k`` frames before them are left out."""
    regs = voices(grid)
    t, v = onsets(regs[0])
    t, v = t[t >= k], v[t >= k]
    feat = lead(regs, t, v, k)
    cls, leads, frames = classify(feat)
    wrap, safe = wrap_frames(clock), run(feat["rate_safe"])
    counts = np.bincount(cls, minlength=len(CLASSES))
    restarts = int(counts[:4].sum())
    hr_leads = leads[cls < 4]
    return {
        "onsets": len(t),
        "voices": np.bincount(v, minlength=3).tolist(),
        "classes": dict(zip(CLASSES, counts.tolist())),
        "leads": {c: histogram(leads[cls == i], k) for i, c in enumerate(CLASSES)},
        "features": {f: feat[f].sum(axis=0).tolist() for f in FEATURES},
        "gate_off": histogram(run(feat["gate_off"]), k),
        "rate_safe": histogram(safe, k),
        "safe": dict(zip(CLASSES, np.bincount(cls[safe >= wrap], minlength=len(CLASSES)).tolist())),
        "test_at_onset": int(((regs[0][t, v] & TEST) > 0).sum()),
        "values": {f"{x:04X}": n for x, n in Counter(feat["values"][frames].tolist()).items()},
        "wrap": wrap,
        "restarts": restarts,
        "dominant_lead": int(np.bincount(hr_leads).argmax()) if restarts else None,
        "consistent": restarts > len(t) / 2,
    }


def measure(path: Path, subtune: int, frames: int, k: int, clock: str | None) -> dict:
    """:func:`analyse` of ``frames`` of the tune at ``path``'s subtune ``subtune``."""
    return analyse(register_grid(path.read_bytes(), frames, subtune=subtune), k, clock)


def timed(row: dict, hvsc, frames: int, k: int, timeout: float) -> dict:
    """:func:`measure` of a tune row within ``timeout`` seconds."""
    with deadline(timeout):
        path = shared.tune(row["path"], hvsc)
        return measure(path, int(row.get("subtune") or 0), frames, k, row.get("clock"))


def sample(rows: list, n: int, seed: int) -> list:
    """Up to ``n`` of each player's rows, drawn with ``seed``."""
    groups = defaultdict(list)
    for r in rows:
        groups[r.get("player", "")].append(r)
    rng = np.random.default_rng(seed)
    out = []
    for _, group in sorted(groups.items()):
        pick = rng.choice(len(group), min(n, len(group)), replace=False) if n else range(len(group))
        out += [group[i] for i in sorted(pick)]
    return out


def shares(counts: dict) -> str:
    """``counts`` as percentages of their total."""
    total = max(1, sum(counts.values()))
    return " ".join(f"{k} {n / total:.0%}" for k, n in counts.items())


def pooled(recs: list) -> dict:
    """The records' class counts, histograms and values summed."""
    out = {"classes": Counter(), "values": Counter(), "safe": Counter()}
    for key in ("gate_off", "rate_safe", "onsets", "test_at_onset"):
        out[key] = sum(np.asarray(r[key]) for r in recs)
    out["hr_leads"] = sum(np.asarray([r["leads"][c] for c in RESTARTS]).sum(0) for r in recs)
    for r in recs:
        out["classes"].update(r["classes"])
        out["values"].update(r["values"])
        out["safe"].update(r["safe"])
    return out


def summary(recs: list) -> str:
    """Per player, the tunes using hard restart on most onsets, the class shares, the restart
    leads, the commonest AD/SR values in restart frames and the onsets safe from the ADSR bug."""
    lines = []
    by = defaultdict(list)
    for r in recs:
        by[r.get("player", "")].append(r)
    for player, group in sorted(by.items()):
        ok = [r for r in group if "error" not in r and r["onsets"]]
        lines.append(
            f"{player}: {len(ok)} tunes with onsets, {len(group) - len(ok)} failed or silent"
        )
        if not ok:
            continue
        p = pooled(ok)
        wrap = Counter(r["wrap"] for r in ok).most_common(1)[0][0]
        lead_tunes = Counter(r["dominant_lead"] for r in ok if r["restarts"])
        lines += [
            f"  hard restart on most onsets: {sum(r['consistent'] for r in ok) / len(ok):.0%},"
            f" on any: {sum(r['restarts'] > 0 for r in ok) / len(ok):.0%}",
            f"  onsets: {shares(dict(p['classes']))}",
            "  rate-safe by class: "
            + " ".join(f"{c} {p['safe'][c] / max(1, p['classes'][c]):.0%}" for c in CLASSES),
            f"  test bit at onset: {p['test_at_onset'] / p['onsets']:.0%}",
            f"  restart lead 0..k: {p['hr_leads'].tolist()},"
            f" tunes' dominant lead {dict(lead_tunes)}",
            f"  gate-off run 0..k: {p['gate_off'].tolist()}",
            f"  rate-safe run 0..k: {p['rate_safe'].tolist()}, safe (>= {wrap} frames):"
            f" {p['rate_safe'][wrap:].sum() / max(1, p['rate_safe'].sum()):.0%}",
            "  AD/SR in restart frames: "
            + " ".join(f"{x}:{n}" for x, n in p["values"].most_common(8)),
        ]
    return "\n".join(lines)


def main(argv=None) -> int:
    """Parse the command line, measure the sampled tunes in parallel and print the summary."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("sources", nargs="+", help="tunes, directories or .csv tune lists")
    ap.add_argument("--hvsc", type=Path, help="HVSC root the lists' paths are under")
    ap.add_argument("--player", default="", help="regex of the lists' players to keep")
    ap.add_argument("--sample", type=int, default=0, help="tunes per player (0: all)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--subtune", type=int, default=0, help="0-based subtune")
    ap.add_argument("--frames", type=int, default=3000)
    ap.add_argument("--lead", type=int, default=4, help="lead frames examined before each onset")
    ap.add_argument("--timeout", type=float, default=300, help="seconds per tune")
    ap.add_argument("--jobs", type=int, default=max(1, len(os.sched_getaffinity(0)) - 2))
    ap.add_argument("--out", type=Path, help="write each tune's record as JSON")
    args = ap.parse_args(argv)
    rows = sample(
        shared.songs(shared.rows(args.sources, args.player), args.subtune), args.sample, args.seed
    )
    tasks = [(r, args.hvsc, args.frames, args.lead, args.timeout) for r in rows]
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
