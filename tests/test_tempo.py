"""The initial tempo: the cells it starts in the VM, the swings the VM plays, and the generic
document's spelling of a pair of equal tempos."""

import irs
import numpy as np
import pytest
from conftest import grid_diff_of
from unitracker_grammar import parse

from unitracker import document
from unitracker.lift import verify
from unitracker.player import oracle
from unitracker.player.vm import SongError, SongPlayer
from unitracker.playroutine import tempo
from unitracker.playroutine.library import library

RECORDS = {
    "goattracker2": {
        "ctrltab": {1: {"n": 0, "next": 0, "w": 65}},
        "ins": {1: {"ctrl": 1, "restart": 1, "sr": 240}},
        "restart": {1: {"ad": 15, "gate": 0, "hold": 2, "next": 2, "sr": 0}, 2: {"next": 0}},
    },
    "sidwizard": {
        "ins": {1: {"ctrl": 1, "sr": 240}},
        "wftab": {1: {"a": 0, "d": 0, "h": 0, "next": 0, "w": 65}},
    },
}
SCALE = (0, 2, 4, 5, 7, 9, 11, 12)
LF185 = pytest.mark.xfail(
    strict=True, reason="the sidwizard tempo step still plays a tick after the native's"
)


def scale_song(player: str, value=None) -> dict:
    """Three voices looping a one-instrument C major scale from C4, at initial tempo ``value``."""
    c4 = 48 + (player == "sidwizard")
    events = [{"note": c4 + k} for k in SCALE]
    events[0]["instrument"] = 1
    extra = {} if value is None else {"tempo": value}
    return irs.song(
        player, records=RECORDS[player], phrases={"p1": events}, play=["p1"] * 3, loop=0, **extra
    )


@pytest.mark.parametrize(
    "player,value,cells",
    [
        ("goattracker2", None, {"tempo": 5, "funk0": 5, "funk1": 5}),
        ("goattracker2", 12, {"tempo": 11, "funk0": 11, "funk1": 11}),
        ("goattracker2", [6, 9], {"tempo": 0, "funk0": 5, "funk1": 8}),
        ("goattracker2", 2, {"tempo": 0, "funk0": 1, "funk1": 1}),
        ("goattracker2", 129, {"tempo": 0, "funk0": 128, "funk1": 128}),
        ("sidwizard", None, {"tl": 6, "tr": 6}),
        ("sidwizard", [4, 7], {"tl": 4, "tr": 7}),
    ],
)
def test_the_tempo_starts_the_cells_the_playroutine_declares(player, value, cells):
    played = SongPlayer(scale_song(player, value))
    voice = {n: played.cell(n, 0) for n in cells if n in played.codegen.vindex}
    glob = {n: played.cell(n) for n in cells if n in played.codegen.gindex}
    assert {**voice, **glob} == cells


@pytest.mark.parametrize("value", [[6, 9], [4, 9], 7, 3])
def test_a_swing_alternates_its_two_step_lengths(value):
    first, second = value if isinstance(value, list) else (value, value)
    grid = SongPlayer(scale_song("goattracker2", value)).render_grid(120)
    steps = np.diff([t for t in range(1, 120) if grid[t, 0] != grid[t - 1, 0]])
    assert {frozenset(steps[::2]), frozenset(steps[1::2])} == {
        frozenset({first}),
        frozenset({second}),
    }


def test_a_tempo_cell_must_be_a_cell():
    pr = library("sidwizard")
    assert tempo.cells(pr, [3, 4]) == {"tl": 3, "tr": 4} and tempo.written(5, 5) == 5
    ir = scale_song("sidwizard")
    with pytest.raises(SongError, match="unknown cell 'nope'"):
        SongPlayer({**ir, "playroutine": {**pr, "tempo": {"cells": {"nope": "a"}}}})
    assert tempo.cells({}, 6) == {} and tempo.pair(True) is None


def three_voices(*commands, ticks: int = 300) -> np.ndarray:
    """The register grid of a goattracker2 song whose voices each play a rising run once, the
    first event of voice ``v`` carrying ``vtempo`` speedtab row ``commands[v]`` (None: none)."""
    speeds, phrases = {}, {}
    for v, pair in enumerate(commands):
        first = {"note": 48, "instrument": 1}
        if pair is not None:
            speeds[len(speeds) + 1] = dict(zip("lr", pair))
            first["command"] = [18, len(speeds)]
        phrases[f"p{v + 1}"] = [first] + [{"note": n} for n in (50, 52, 53, 55, 57, 59, 60, 62)]
    records = {**RECORDS["goattracker2"], "speedtab": speeds}
    ir = irs.song("goattracker2", records=records, phrases=phrases, play=list(phrases))
    return SongPlayer(ir).render_grid(ticks)


def test_a_voice_swing_swings_that_voice_alone():
    both = three_voices((5, 4), (7, 3), None)
    one = three_voices((5, 4), None, None)
    two = three_voices(None, (7, 3), None)
    assert (both[:, :7] == one[:, :7]).all() and (both[:, 7:] == two[:, 7:]).all()
    assert (one[:, :7] != two[:, :7]).any()


def note_ticks(grid: np.ndarray) -> list[int]:
    """The ticks voice 1's frequency high byte changes on: its notes' steps."""
    return np.flatnonzero(np.diff(grid[:, 1])).tolist()


@pytest.mark.parametrize(
    "player,code,arg,written",
    [
        ("goattracker2", 14, (6, 6), "!tempo:6"),
        ("goattracker2", 18, (129, 129), "!vtempo:129"),
        pytest.param("sidwizard", 17, 0x55, "!tempo:5", marks=LF185),
        pytest.param("sidwizard", 20, 0x00, "!vtempo:6", marks=LF185),
    ],
)
def test_a_pair_of_equal_tempos_is_written_as_one(player, code, arg, written, request):
    ir = scale_song(player)
    if isinstance(arg, tuple):
        ir["records"]["speedtab"] = {1: dict(zip("lr", arg))}
        arg = 1
    ir["phrases"]["p1"][0]["command"] = [code, arg]
    text = document.write(ir)
    parse(text)
    assert f"C4.i1{written}" in text and "/" not in written
    doc, starts = oracle.play(text, 300)
    vm = SongPlayer(ir).render_grid(300)
    if note_ticks(doc) != note_ticks(vm):
        grid_diff_of(request, verify.compare_grids(doc, starts, vm))
    assert note_ticks(doc) == note_ticks(vm)
