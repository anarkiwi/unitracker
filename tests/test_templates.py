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
        ({"ctrl": 0x41, "note": 0}, {"wave": 0x41, "lastnote": 24, "skip": 1, "ftab": 24}),
        ({"ctrl": 0x11, "note": 5}, {"wave": 0x11, "lastnote": 29, "ftab": 29}),
        ({"wait": 2, "noteabs": 1}, {"wave": 0x21, "lastnote": 1, "ftab": 1}),
        ({"ctrl": 1}, {"wave": 1, "skip": 0, "freq": 0x1234}),
        ({"ctrl": 0xE5}, {"wave": 0xE5, "skip": 1}),
        ({"ad": 0x40}, {"wave": 0x21, "ad": 0x40, "skip": 1, "freq": 0x1234}),
        ({"sr": 0x09}, {"sr": 0x09, "skip": 1}),
        ({"resfilt": 0}, {"filterctrl": 0, "fptr": 0, "skip": 1}),
        ({"cut": 0x50}, {"cut": 0x50, "skip": 1}),
        ({"vol": 5}, {"masterfader": 5, "skip": 1}),
        ({"pwprog": 3}, {"pptr": 3, "skip": 1}),
        ({"filtprog": 4}, {"fptr": 4, "skip": 1}),
        ({"freq": {"step": 16, "for": 1}}, {"freq": 0x1244, "skip": 1}),
        ({"freq": {"toward": "note", "for": 1}}, {"ftab": 24, "lastnote": 24, "skip": 1}),
    ],
)
def test_goattracker_control_rows(row, expect):
    player = run_row("goattracker2", "ctrlprog", row, GT_NOTE)
    ftab = songio.library("goattracker2")["tables"]["ftab"]
    for cell, value in expect.items():
        if cell == "ftab":
            assert player.cell("freq", 0) == ftab[value]
        elif cell in ("filterctrl", "fptr", "cut", "masterfader"):
            assert player.cell(cell) == value
        else:
            assert player.cell(cell, 0) == value


def test_goattracker_pulse_rows():
    assert run_row("goattracker2", "pwprog", {"pw": 0x420}).cell("pw", 1) == 0x420
    player = run_row("goattracker2", "pwprog", {"pw": {"step": -16, "for": 2}}, {"pw": 0x100})
    assert player.cell("pw", 1) == 0x100


@pytest.mark.parametrize(
    "row,expect",
    [
        ({"filtmode": 0x10, "resfilt": 0xF1, "cut": 0x40}, (0x10, 0xF1, 0x40)),
        ({"filtmode": 0x10, "resfilt": 0xF1}, (0x10, 0xF1, 0x33)),
        ({"cut": 0x22}, (0, 0, 0x22)),
    ],
)
def test_goattracker_filter_rows(row, expect):
    player = run_row("goattracker2", "filtprog", row, {"cut": 0x33}, voice=False)
    got = tuple(player.cell(c) for c in ("filtertype", "filterctrl", "cut"))
    assert got == expect


DM_STATE = {"tbase": 0x10, "res": 0x23, "rbit": 2, "rmask": 0xFD}


def test_defmon_every_key():
    row = {
        "ctrl": 0x41, "ctrlxor": 0x01, "ad": 0x0A, "sr": 0xF8, "note": 5, "bend": {"step": 4},
        "pw": {"from": 0x400, "up": 1, "down": -2, "dir": "up"}, "filtvoice": 0x98,
        "modevol": 0x2F, "cutbias": 0x07, "cut": {"step": "1/256"},
    }  # fmt: skip
    player = run_row("defmon", "prog", row, DM_STATE)
    voice = {"ctrl": 0x41, "eor": 0x01, "ad": 0x0A, "sr": 0xF8, "note": 0x15, "pw": 0x400}
    assert {c: player.cell(c, 2) for c in voice} == voice
    assert player.cell("res") == (0x03 | 0x98 | 2)
    assert (player.cell("fv"), player.cell("cp")) == (0x2F, 0x07)


@pytest.mark.parametrize(
    "row,expect",
    [
        ({"noteabs": 5}, {"note": 5}),
        ({"filtvoice": 0}, {"res": 0x23 & 0xFD}),
        ({"resfilt": 0x51}, {"res": 0x51}),
        ({"pw": 0x300}, {"pw": 0x300}),
        ({}, {"note": 0, "res": 0x23}),
    ],
)
def test_defmon_key_variants(row, expect):
    player = run_row("defmon", "prog", row, DM_STATE)
    for cell, value in expect.items():
        voice = 0 if cell in ("note", "pw") else None
        assert player.cell(cell, voice) == value
