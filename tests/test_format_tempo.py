"""The initial tempo: the document's ``tempo`` key, the cells it starts and its refusals."""

import numpy as np
import pytest
from test_format_codec import atom_doc

from unitracker.format import songio, tempo
from unitracker.player.vm import SongError, SongPlayer


def tempo_song(player: str, value=None, ins="{sr: 240, ctrl: [{ctrl: 65, note: +0}, end]}") -> str:
    initial = "" if value is None else f"tempo: {value}\n"
    return (
        f"unitracker: {songio.VERSION}\nplayroutine: {{ref: {player}}}\n{initial}song:\n"
        f"  - def:\n      ins:\n        i1: {ins}\n"
        "      phrase:\n        p1: [C4.i1, D4, E4, F4, G4, A4, B4, C5]\n"
        "    play: [p1, p1, p1]\n  - {loop: 0}\n"
    )


@pytest.mark.parametrize(
    "player,value,cells",
    [
        ("goattracker2", None, {"tempo": 5, "funk0": 5, "funk1": 5}),
        ("goattracker2", 12, {"tempo": 11, "funk0": 11, "funk1": 11}),
        ("goattracker2", "[6, 9]", {"tempo": 0, "funk0": 5, "funk1": 8}),
        ("goattracker2", 2, {"tempo": 0, "funk0": 1, "funk1": 1}),
        ("goattracker2", 129, {"tempo": 0, "funk0": 128, "funk1": 128}),
        ("sidwizard", None, {"tl": 6, "tr": 6}),
        ("sidwizard", "[4, 7]", {"tl": 4, "tr": 7}),
    ],
)
def test_the_tempo_starts_the_cells_the_playroutine_declares(player, value, cells):
    ir = songio.loads(tempo_song(player, value))
    text = songio.dumps(ir)
    assert songio.dumps(songio.loads(text)) == text
    assert (f"tempo: {value}\n" in text) == (value is not None)
    played = SongPlayer(ir)
    voice = {n: played.cell(n, 0) for n in cells if n in played.codegen.vindex}
    glob = {n: played.cell(n) for n in cells if n in played.codegen.gindex}
    assert {**voice, **glob} == cells


@pytest.mark.parametrize("value,ticks", [("[6, 9]", 15), ("[4, 9]", 13), (7, 14), (2, 4), (3, 6)])
def test_a_swing_alternates_its_two_step_lengths(value, ticks):
    grid = SongPlayer(songio.loads(tempo_song("goattracker2", value))).render_grid(120)
    steps = np.diff([t for t in range(1, 120) if grid[t, 0] != grid[t - 1, 0]])
    assert set(steps) == {ticks}


@pytest.mark.parametrize(
    "player,value,match",
    [
        ("goattracker2", 130, "goattracker2: the player can't express tempo 130"),
        ("goattracker2", "[1, 6]", r"can't express tempo \[1, 6\]"),
        ("goattracker2", "[6, 6]", "two different tempos"),
        ("goattracker2", "[6, 7, 8]", "two different tempos"),
        ("goattracker2", "fast", "two different tempos"),
        ("sidwizard", 2, "sidwizard: the player can't express tempo 2"),
    ],
)
def test_the_reader_refuses_a_tempo_the_player_cannot_play(player, value, match):
    with pytest.raises(songio.SongFormatError, match=match):
        songio.loads(tempo_song(player, value))
    with pytest.raises(songio.SongFormatError, match="defmon: the player has no tempo"):
        songio.loads(tempo_song("defmon", 6, "{ctrl: [{ctrl: 65, gate: 1}, end]}"))


def test_the_writer_refuses_a_tempo_the_player_cannot_play():
    ir = songio.loads(tempo_song("goattracker2"))
    with pytest.raises(songio.SongFormatError, match="two different tempos"):
        songio.dumps({**ir, "tempo": [6, 6]})
    with pytest.raises(songio.SongFormatError, match="can't express tempo 200"):
        songio.dumps({**ir, "tempo": 200})


def test_a_tempo_cell_must_be_a_cell_and_its_expressions_must_read():
    pr = songio.library("sidwizard")
    assert tempo.cells(pr, [3, 4]) == {"tl": 3, "tr": 4} and tempo.written(5, 5) == 5
    bad = {**pr, "tempo": {"cells": {"nope": "a"}}}
    ir = songio.loads(tempo_song("sidwizard"))
    with pytest.raises(SongError, match="unknown cell 'nope'"):
        SongPlayer({**ir, "playroutine": bad})
    assert "unknown table" in tempo.problem({**pr, "tempo": {"when": "t[0]"}}, 6)
    assert tempo.cells({}, 6) == {} and tempo.pair(True) is None


def three_voices(*firsts: str, ticks: int = 300) -> np.ndarray:
    """The register grid of a goattracker2 song whose voices play one phrase each, the first
    event of voice ``v`` being ``firsts[v]``."""
    notes = "D4, E4, F4, G4, A4, B4, C5, D5, E5, F5, G5"
    phrases = "".join(f"        p{v + 1}: [{e}, {notes}]\n" for v, e in enumerate(firsts))
    text = (
        f"unitracker: {songio.VERSION}\nplayroutine: {{ref: goattracker2}}\nsong:\n"
        "  - def:\n      ins:\n        i1: {sr: 240, ctrl: [{ctrl: 65, note: +0}, end]}\n"
        f"      phrase:\n{phrases}    play: [p1, p2, p3]\n  - end\n"
    )
    return SongPlayer(songio.loads(text)).render_grid(ticks)


def test_a_voice_swing_swings_that_voice_alone():
    both = three_voices("C4.i1!vtempo:5/4", "C4.i1!vtempo:7/3", "C4.i1")
    one = three_voices("C4.i1!vtempo:5/4", "C4.i1", "C4.i1")
    two = three_voices("C4.i1", "C4.i1!vtempo:7/3", "C4.i1")
    assert (both[:, :7] == one[:, :7]).all() and (both[:, 7:] == two[:, 7:]).all()
    assert (one[:, :7] != two[:, :7]).any()


@pytest.mark.parametrize("player", ["goattracker2", "sidwizard"])
def test_a_swing_of_two_equal_tempos_is_refused(player):
    with pytest.raises(songio.SongFormatError, match=r"!vtempo:5/5 is written !vtempo:5 in"):
        songio.loads(atom_doc(player, "C4!vtempo:5/5"))


@pytest.mark.parametrize(
    "player,code,arg,written",
    [
        ("goattracker2", 14, (6, 6), "!tempo:6"),
        ("goattracker2", 14, (2, 2), "!tempo:2"),
        ("goattracker2", 18, (129, 129), "!vtempo:129"),
        ("sidwizard", 17, 0x55, "!tempo:5"),
        ("sidwizard", 20, 0x00, "!vtempo:6"),
    ],
)
def test_a_pair_of_equal_tempos_is_written_as_one(player, code, arg, written):
    ir = songio.loads(atom_doc(player, "C4!tempo:4/5"))
    if isinstance(arg, tuple):
        ir["records"]["speedtab"] = {1: dict(zip("lr", arg))}
        arg = 1
    ir["phrases"]["p1"][0]["command"] = [code, arg]
    text = songio.dumps(ir)
    assert f"p1: [C4{written}]" in text and songio.dumps(songio.loads(text)) == text
    assert (
        SongPlayer(ir).render_grid(200) == SongPlayer(songio.loads(text)).render_grid(200)
    ).all()


def test_goattracker2_plays_the_tempos_command_f_cannot_through_a_row_of_equal_tempos():
    ir = songio.loads(atom_doc("goattracker2", "C4!tempo:2"))
    (code, index), rows = ir["phrases"]["p1"][0]["command"], ir["records"]["speedtab"]
    assert code == 14 and rows[index] == {"l": 2, "r": 2}
    assert songio.loads(atom_doc("goattracker2", "C4!tempo:6"))["phrases"]["p1"][0]["command"] == [
        15,
        6,
    ]
