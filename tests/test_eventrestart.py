"""Event restarts: a defMON note's restart, played on layer B on the steps before it."""

import numpy as np
import pytest

from unitracker.format import songio
from unitracker.format.events import restart
from unitracker.player.vm import SongPlayer, compile_phrase

LAYER_B = 1


def song(phrase, program="[{hold: 3, ctrl: ^8}, {}, end]", ins="i1: {ctrl: [{ctrl: 64^1}, end]}"):
    return f"""
unitracker: 1.19
playroutine: {{ref: defmon}}
song:
  - def:
      ins:
        {ins}
      restart:
        r1: {program}
      phrase:
        p1: {phrase}
        p2: [-*12]
    play: [p1, p2, p2]
loop: ~
"""


def arr(*rows):
    return np.array([list(r) + [0] * (7 - len(r)) for r in rows], dtype=np.int64)


def test_fold_moves_a_lone_command_onto_the_note_it_precedes():
    phrase = arr(
        (0,), (0, 0, 0, LAYER_B, 4), (0,), (1, 60, 2), (0, 0, 0, LAYER_B, 4), (1, 62, 2, LAYER_B, 5)
    )
    seen = []

    def restart_of(entry, gap):
        seen.append((entry, gap))
        return 7

    out = restart.fold(phrase, LAYER_B, restart_of)
    assert seen == [(4, 2), (4, 1)]
    assert out[1].tolist() == [0] * 7 and out[4].tolist() == [0] * 7
    assert out[3].tolist() == [1, 60, 2, restart.CODE, 7, 0, 0]
    assert out[5].tolist() == [1, 62, 2, LAYER_B, 5, restart.CODE, 7]


@pytest.mark.parametrize(
    "step,note",
    [
        ((0, 0, 3, LAYER_B, 4), (1, 60)),
        ((0, 0, 0, LAYER_B, 4, LAYER_B, 5), (1, 60)),
        ((0, 0, 0, LAYER_B, 4), (2,)),
        ((0, 0, 0, LAYER_B, 4), (1, 60, 0, LAYER_B, 1, LAYER_B, 2)),
        ((0, 0, 0, 2, 4), (1, 60)),
    ],
)
def test_fold_leaves_what_is_not_a_restart(step, note):
    phrase = arr((0,), step, note)
    assert (restart.fold(phrase, LAYER_B, lambda e, g: 1) == phrase).all()


def test_fold_leaves_a_command_with_no_restart():
    phrase = arr((0,), (0, 0, 0, LAYER_B, 4), (1, 60))
    assert (restart.fold(phrase, LAYER_B, lambda e, g: None) == phrase).all()


def test_unfold_splits_the_steps_before_the_note():
    events = [
        {"note": 50, "steps": 5},
        {"note": 52, "restart": 1},
        {"steps": 3},
        {"note": 53, "restart": 1},
    ]
    out = restart.unfold(events, LAYER_B, lambda index: (9, 3))
    assert out == [
        {"note": 50, "steps": 2},
        {"command": [LAYER_B, 9], "steps": 3},
        {"note": 52},
        {"command": [LAYER_B, 9], "steps": 3},
        {"note": 53},
    ]
    start = [{"steps": 3}, {"note": 1, "restart": 1}]
    assert restart.unfold(start, LAYER_B, lambda index: (9, 3)) == [
        {"command": [LAYER_B, 9], "steps": 3},
        {"note": 1},
    ]
    one = [{"steps": 2}, {"note": 1, "restart": 1}]
    assert restart.unfold(one, LAYER_B, lambda index: (9, 1)) == [
        {},
        {"command": [LAYER_B, 9]},
        {"note": 1},
    ]


@pytest.mark.parametrize(
    "events,match",
    [
        ([{"note": 1, "restart": 1}], "3 empty steps"),
        ([{"note": 50, "steps": 3}, {"note": 52, "restart": 1}], "3 empty steps"),
        ([{"steps": 4}, {"note": "off", "restart": 1}], "needs a note"),
    ],
)
def test_unfold_refuses_a_restart_without_room(events, match):
    with pytest.raises(restart.RestartError, match=match):
        restart.unfold(events, LAYER_B, lambda index: (9, 3))


def test_a_restart_reads_as_layer_b_before_its_note_and_writes_back():
    text = songio.dumps(songio.loads(song("[-*4, C3.i1!restart:@r1*8]")))
    ir = songio.loads(text)
    assert songio.dumps(ir) == text
    assert "!restart:@r1" in text and "filter" not in text
    steps = compile_phrase(ir["phrases"]["p1"])
    assert steps[1, 3] == LAYER_B and steps[4, 0] == 1 and not steps[2:4].any()
    assert ir["records"]["sidtab"][steps[1, 4]] == {
        "dl": 0x80,
        "wgh": -1,
        "wgl": 8,
        "ad": -1,
        "sr": -1,
        "next": 0,
    }
    assert "restart" not in ir["records"]
    grid = SongPlayer(ir).render_grid(8)
    assert grid[1:, 4].tolist() == [0, 8, 8, 8, 65, 65, 65]


def test_a_layer_b_program_before_a_note_is_folded():
    text = songio.dumps(
        songio.loads(
            song(
                "[-, -.i2*3, C3.i1*8]",
                ins="i1: {ctrl: [{ctrl: 64^1}, end]}\n        i2: {filter: [{ctrl: ^8}, end]}",
            )
        )
    )
    assert "i2" not in text
    assert "[-*4, C3.i1!restart:@r1*8]" in text and "r1: [{hold: 3, ctrl: ^8}, {}, end]" in text


@pytest.mark.parametrize(
    "phrase,program,match",
    [
        ("[-*2, C3.i1!restart:@r1*8]", None, "3 empty steps"),
        ("[-*4, C3.i1!restart:@r1*8]", "[{}, end]", "a row before its note"),
        ("[-*4, C3.i1!restart:@r1*8]", "[{hold: 3}, {ctrl: 9}, end]", "can't express row"),
        ("[-*4, C3.i1!restart:@r1*8]", "[{hold: 3, ctrl: ^8}, {}, {loop: 0}]", "can't loop"),
    ],
)
def test_the_reader_refuses_a_restart_it_cannot_play(phrase, program, match):
    text = song(phrase, program) if program else song(phrase)
    with pytest.raises(songio.SongFormatError, match=match):
        songio.loads(text)


def test_an_instrument_restart_player_has_no_event_restart():
    text = song("[-*4, C3.i1!restart:@r1*8]").replace("defmon", "goattracker2")
    with pytest.raises(songio.SongFormatError):
        songio.loads(text)
