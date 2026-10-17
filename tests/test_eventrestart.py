"""Event restarts: a defMON note's restart, played on the steps before it by !run2."""

import numpy as np
import pytest

from unitracker import eventrestart, songio
from unitracker.vm import SongPlayer, compile_phrase

RUN2 = 1


def song(phrase, restart="[{hold: 3, ctrl: ^8}, {}, end]", sidtab="t1: [{ctrl: 64^1}, end]"):
    return f"""
unitracker: 1.15
playroutine: {{ref: defmon}}
song:
  - def:
      sidtab:
        {sidtab}
      restart:
        r1: {restart}
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
        (0,), (0, 0, 0, RUN2, 4), (0,), (1, 60, 2), (0, 0, 0, RUN2, 4), (1, 62, 2, RUN2, 5)
    )
    seen = []

    def restart_of(entry, gap):
        seen.append((entry, gap))
        return 7

    out = eventrestart.fold(phrase, RUN2, restart_of)
    assert seen == [(4, 2), (4, 1)]
    assert out[1].tolist() == [0] * 7 and out[4].tolist() == [0] * 7
    assert out[3].tolist() == [1, 60, 2, eventrestart.CODE, 7, 0, 0]
    assert out[5].tolist() == [1, 62, 2, RUN2, 5, eventrestart.CODE, 7]


@pytest.mark.parametrize(
    "step,note",
    [
        ((0, 0, 3, RUN2, 4), (1, 60)),
        ((0, 0, 0, RUN2, 4, RUN2, 5), (1, 60)),
        ((0, 0, 0, RUN2, 4), (2,)),
        ((0, 0, 0, RUN2, 4), (1, 60, 0, RUN2, 1, RUN2, 2)),
        ((0, 0, 0, 2, 4), (1, 60)),
    ],
)
def test_fold_leaves_what_is_not_a_restart(step, note):
    phrase = arr((0,), step, note)
    assert (eventrestart.fold(phrase, RUN2, lambda e, g: 1) == phrase).all()


def test_fold_leaves_a_command_with_no_restart():
    phrase = arr((0,), (0, 0, 0, RUN2, 4), (1, 60))
    assert (eventrestart.fold(phrase, RUN2, lambda e, g: None) == phrase).all()


def test_unfold_splits_the_steps_before_the_note():
    events = [
        {"note": 50, "steps": 5},
        {"note": 52, "restart": 1},
        {"steps": 3},
        {"note": 53, "restart": 1},
    ]
    out = eventrestart.unfold(events, RUN2, lambda index: (9, 3))
    assert out == [
        {"note": 50, "steps": 2},
        {"command": [RUN2, 9], "steps": 3},
        {"note": 52},
        {"command": [RUN2, 9], "steps": 3},
        {"note": 53},
    ]
    start = [{"steps": 3}, {"note": 1, "restart": 1}]
    assert eventrestart.unfold(start, RUN2, lambda index: (9, 3)) == [
        {"command": [RUN2, 9], "steps": 3},
        {"note": 1},
    ]
    one = [{"steps": 2}, {"note": 1, "restart": 1}]
    assert eventrestart.unfold(one, RUN2, lambda index: (9, 1)) == [
        {},
        {"command": [RUN2, 9]},
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
    with pytest.raises(eventrestart.RestartError, match=match):
        eventrestart.unfold(events, RUN2, lambda index: (9, 3))


def test_a_restart_reads_as_run2_before_its_note_and_writes_back():
    text = songio.dumps(songio.loads(song("[-*4, C3.t1!restart:@r1*8]")))
    ir = songio.loads(text)
    assert songio.dumps(ir) == text
    assert "!restart:@r1" in text and "run2" not in text
    steps = compile_phrase(ir["phrases"]["p1"])
    assert steps[1, 3] == RUN2 and steps[4, 0] == 1 and not steps[2:4].any()
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
    assert grid[1:, 4].tolist() == [65, 72, 72, 72, 65, 65, 65]


def test_a_written_run2_before_a_note_is_folded():
    text = songio.dumps(
        songio.loads(
            song(
                "[-, -!run2:@t2*3, C3.t1*8]",
                sidtab="t1: [{ctrl: 64^1}, end]\n        t2: [{ctrl: ^8}, end]",
            )
        )
    )
    assert "[-*4, C3.t1!restart:@r1*8]" in text and "r1: [{hold: 3, ctrl: ^8}, {}, end]" in text


@pytest.mark.parametrize(
    "phrase,restart,match",
    [
        ("[-*2, C3.t1!restart:@r1*8]", None, "3 empty steps"),
        ("[-*4, C3.t1!restart:@r1*8]", "[{}, end]", "a row before its note"),
        ("[-*4, C3.t1!restart:@r1*8]", "[{hold: 3}, {ctrl: 9}, end]", "can't express row"),
    ],
)
def test_the_reader_refuses_a_restart_it_cannot_play(phrase, restart, match):
    text = song(phrase, restart) if restart else song(phrase)
    with pytest.raises(songio.SongFormatError, match=match):
        songio.loads(text)


def test_an_instrument_restart_player_has_no_event_restart():
    text = song("[-*4, C3.t1!restart:@r1*8]").replace("defmon", "goattracker2")
    with pytest.raises(songio.SongFormatError):
        songio.loads(text)
