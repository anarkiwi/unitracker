"""tools/divergence.py: register-grid divergence classes."""

import csv
import re

import numpy as np
import pytest

import divergence as dv

TICKS = 64


def song() -> np.ndarray:
    """A grid whose every register changes every tick, so any shift of it differs."""
    t = np.arange(TICKS)[:, None]
    return ((t * 7 + np.arange(25)[None, :] * 13) % 251 + 1).astype(np.uint8)


def edit(grid, ticks, regs, value) -> np.ndarray:
    """``grid`` with ``value`` xored into ``regs`` over ``ticks``."""
    out = grid.copy()
    out[np.ix_(ticks, regs)] ^= value
    return out


@pytest.mark.parametrize(
    "ours, std, native, cls",
    [
        (song(), song(), song(), "exact"),
        (song(), song(), edit(song(), range(5, 9), [0, 1], 3), "pitch-standard"),
        (song()[1:], song()[:-1], song()[:-1], "lag+1"),
        (song()[:-1], song()[1:], song()[1:], "lag-1"),
        (song(), edit(song(), range(0, 9), [21, 24], 0x10), song(), "init"),
        (song(), edit(song(), [0, 1], [3], 1), song(), "ticks0-1"),
        (song(), edit(song(), range(20, 30), [2, 3], 4), song(), "pulse"),
        (song(), edit(song(), range(20, 30), [21], 4), song(), "cutoff"),
        (song(), edit(song(), range(20, 30), [4, 11], 1), song(), "gate"),
        (song(), edit(song(), range(20, 30), [4, 11], 0x41), song(), "ctrl"),
        (song(), edit(song(), range(20, 40), [0, 2, 4, 5], 2), song(), "desync"),
        (song(), edit(song(), [20, 40], [0, 2, 4, 5], 2), song(), "adsr+ctrl+pitch+pulse"),
        (song(), edit(edit(song(), range(0, 5), [24], 1), [30], [9], 8), song(), "init/pulse"),
    ],
)
def test_classify(ours, std, native, cls):
    assert dv.classify(ours, std, native)["class"] == cls


def test_classify_counts():
    out = dv.classify(song(), edit(song(), range(20, 30), [2, 3], 4), song())
    assert (out["cells"], out["late_cells"], out["init_cells"], out["groups"]) == (
        20,
        20,
        0,
        "pulse",
    )
    assert out["first"] == "20 $02"


def test_group_repeats_per_chip():
    assert [dv.group(r) for r in (0, 2, 4, 5, 21, 23, 25, 46)] == [
        "pitch",
        "pulse",
        "ctrl",
        "adsr",
        "cutoff",
        "filter",
        "pitch",
        "cutoff",
    ]


def test_differing_rows_are_classed_into_a_table(metal_warrior, tmp_path, capsys):
    results = tmp_path / "results.csv"
    with results.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, ("doc", "path", "player", "verified", "gap"))
        writer.writeheader()
        row = {"path": metal_warrior, "verified": "no"}
        writer.writerow(row | {"doc": "gt2", "player": "goattracker2"})
        writer.writerow(row | {"doc": "unknown", "player": "unknown"})
        writer.writerow(row | {"doc": "gap", "player": "goattracker2", "gap": "x"})
        writer.writerow(row | {"doc": "exact", "player": "goattracker2", "verified": "yes"})
    assert dv.main([str(results), "--frames", "200", "--jobs", "2"]) == 0
    rows = {r["doc"]: r for r in csv.DictReader((tmp_path / "classes.csv").open())}
    assert set(rows) == {"gt2", "unknown"}
    assert rows["unknown"]["class"].startswith("error: KeyError")
    assert not rows["gt2"]["class"].startswith("error")
    assert "ghost=" in rows["gt2"]["flags"]
    assert "goattracker2" in capsys.readouterr().out


@pytest.mark.parametrize(
    "player, name",
    [
        ("goattracker2", "metal_warrior"),
        ("sidwizard", "mega_run"),
        ("defmon", "persei"),
        ("musicassembler", "space_invadarz"),
    ],
)
def test_classify_one_plays_each_formats_references(player, name, request):
    row = {"doc": name, "path": request.getfixturevalue(name), "player": player}
    opts = {"hvsc": None, "frames": 200, "subtune": 0, "timeout": 0}
    out = dv.classify_one(row, opts)
    known = r"exact|pitch-standard|ticks0-1|((lag[+-]1|init)/?)*([a-z]+(\+[a-z]+)*)?"
    assert re.fullmatch(known, out["class"]), out["class"]
    assert (out["class"] == "exact") == (out["cells"] == 0 and out["native_cells"] == 0)
