"""The library playroutines' record templates, each row run once through the player."""

import pytest

from unitracker import songio
from unitracker.vm import SongPlayer


def run_row(name, rtype, row, cells=None, **where):
    """A player after one tick that sets ``cells`` then runs ``field`` of ``row``."""
    pr = songio.library(name)
    tables = pr.pop("tables")
    voice = where.get("voice", True)
    run = {"run": {"record": rtype, "index": 1, "field": where.get("field", "do")}}
    pre = [{"set": cells}] if cells else []
    pr["init"] = [{"for_voices": pre + [run]}] if voice else pre + [run]
    pr["tick"] = []
    song = {
        "unitracker": "ir",
        "clock": {"cycles_per_tick": 19656, "clock_hz": 985248},
        "voices": 3,
        "tracks": [{"plays": [{"phrase": "a"}], "loop": 0}] * 3,
        "phrases": {"a": [{}]},
        "tables": tables,
        "playroutine": pr,
        "records": {rtype: {1: row}},
    }
    player = SongPlayer(song)
    player.play_frame()
    return player


GT_NOTE = {"note": 24, "wave": 0x21, "freq": 0x1234}


@pytest.mark.parametrize(
    "row,expect",
    [
        ({"w": 0x41, "n": 0}, {"wave": 0x41, "lastnote": 24, "skip": 1, "ftab": 24}),
        ({"w": 0x11, "n": 5}, {"wave": 0x11, "lastnote": 29, "ftab": 29}),
        ({"w": 0x02, "n": 0x81}, {"wave": 0x21, "lastnote": 1, "ftab": 1}),
        ({"w": 0xE1, "n": 0x80}, {"wave": 1, "skip": 0, "freq": 0x1234}),
        ({"w": 0xF5, "n": 0x40}, {"wave": 0x21, "ad": 0x40, "skip": 1, "freq": 0x1234}),
    ],
)
def test_goattracker_wave_rows(row, expect):
    player = run_row("goattracker2", "wavetab", row, GT_NOTE)
    ftab = songio.library("goattracker2")["tables"]["ftab"]
    for cell, value in expect.items():
        if cell == "ftab":
            assert player.cell("freq", 0) == ftab[value]
        else:
            assert player.cell(cell, 0) == value


def test_goattracker_pulse_rows():
    assert run_row("goattracker2", "pulsetab", {"l": 0x84, "r": 0x20}).cell("pulse", 1) == 0x420
    each = run_row(
        "goattracker2", "pulsetab", {"l": 0x20, "r": 0xF0}, {"pulse": 0x100}, field="each"
    )
    assert each.cell("pulse", 2) == 0xF0


@pytest.mark.parametrize(
    "row,field,expect",
    [
        ({"l": 0x90, "r": 0xF1, "cut": 0x40}, "do", (0x10, 0xF1, 0x40)),
        ({"l": 0x90, "r": 0xF1}, "do", (0x10, 0xF1, 0x33)),
        ({"l": 0x00, "r": 0x22}, "do", (0, 0, 0x22)),
        ({"l": 0x10, "r": 0x02}, "each", (0, 0, 0x35)),
    ],
)
def test_goattracker_filter_rows(row, field, expect):
    player = run_row(
        "goattracker2", "filttab", row, {"filtercutoff": 0x33}, field=field, voice=False
    )
    got = tuple(player.cell(c) for c in ("filtertype", "filterctrl", "filtercutoff"))
    assert got == expect


DM_STATE = {"tbase": 0x10, "res": 0x23, "rbit": 2, "rmask": 0xFD}


def test_defmon_every_column():
    row = {
        "wgh": 0x41, "wgl": 0x01, "ad": 0x0A, "sr": 0xF8, "tr": 0x05, "af": 0x83, "pw": 0x4C,
        "ps": 0x91, "re": 0x9F, "fv": 0x2F, "cp": 0x07, "acid": 0x8110,
    }  # fmt: skip
    player = run_row("defmon", "sidtab", row, DM_STATE)
    voice = {
        "ctrl": 0x41, "eor": 0x01, "ad": 0x0A, "sr": 0xF8, "note": 0x15, "mode": 0x83,
        "pw": 0x4C40, "ps": 0x91,
    }  # fmt: skip
    assert {c: player.cell(c, 2) for c in voice} == voice
    assert player.cell("res") == (0x03 | 0x9F | 2)
    assert (player.cell("fv"), player.cell("cp")) == (0x2F, 0x07)
    assert (player.cell("astep"), player.cell("sub")) == (0x0110, 0)


@pytest.mark.parametrize(
    "row,expect",
    [
        ({"tr": 0x85}, {"note": 5}),
        ({"re": 0}, {"res": 0x23 & 0xFD}),
        ({"re": 0x51}, {"res": 0x51}),
        ({"acid": 0x1234}, {"acc": 0x1234, "astep": 0}),
        ({"acid": 0xC102}, {"astep": 0x0102, "sub": 1}),
        ({}, {"note": 0, "res": 0x23}),
    ],
)
def test_defmon_column_variants(row, expect):
    player = run_row("defmon", "sidtab", row, DM_STATE)
    for cell, value in expect.items():
        voice = 0 if cell == "note" else None
        assert player.cell(cell, voice) == value
