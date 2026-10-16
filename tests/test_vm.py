"""The generic player: actions, the bounded accumulator, cursors and the flush."""

import numpy as np
import pytest

from conftest import mini_song
from unitracker.expr import ExprError
from unitracker.vm import Cursor, SongError, SongPlayer, compile_phrase


def run(playroutine, ticks=1, **kw):
    player = SongPlayer(mini_song(playroutine, **kw))
    for _ in range(ticks):
        player.play_frame()
    return player


def ghost(*cells):
    return {"voice": dict(enumerate(cells))}


def acc_playroutine(acc, width=8, init=0):
    return {
        "voice": {"x": {"width": width, "init": init}, "hits": {}},
        "tick": [{"for_voices": [dict(acc, accumulate="x")]}],
        "ghost": ghost("x", "hits"),
    }


HIT = [{"set": {"hits": "hits + 1"}}]


def regs(player):
    return player.regs[0], player.regs[1]


@pytest.mark.parametrize(
    "acc,init,width,ticks,expected",
    [
        ({"step": 3}, 0, 8, 4, (12, 0)),
        ({"step": 0x80}, 0x90, 8, 1, (0x10, 0)),
        ({"step": -1}, 0, 8, 2, (0xFE, 0)),
        ({"step": 4, "max": 10, "on_bound": [{"set": {"hits": "hits + 1"}}]}, 0, 8, 4, (10, 2)),
        (
            {"step": 4, "max": 10, "clamp": False, "on_bound": [{"set": {"hits": 1}}]},
            0,
            8,
            3,
            (12, 1),
        ),
        ({"step": -4, "min": 3, "on_bound": [{"set": {"hits": "hits + 1"}}]}, 12, 8, 3, (3, 1)),
        ({"step": -4, "min": 3, "on_bound": [{"set": {"hits": "hits + 1"}}]}, 12, 8, 4, (0xFF, 1)),
        ({"step": 5, "toward": 12, "on_bound": [{"set": {"hits": "hits + 1"}}]}, 0, 8, 5, (12, 1)),
        ({"step": 4, "toward": 12, "on_bound": [{"set": {"hits": 1}}]}, 0, 8, 5, (12, 0)),
        ({"step": 5, "toward": 2, "on_bound": [{"set": {"hits": 1}}]}, 20, 8, 5, (2, 1)),
        ({"step": 0x30, "toward": 0x20}, 0x30, 8, 1, (0x20, 0)),
        ({"step": 4, "max": 10, "clamp": "reflect", "on_bound": HIT}, 0, 8, 3, (4, 1)),
        ({"step": -4, "min": 3, "clamp": "reflect", "on_bound": HIT}, 12, 8, 3, (8, 1)),
        ({"step": 3, "max": 3, "clamp": "reflect", "on_bound": HIT}, 2, 8, 1, (0xFF, 1)),
        ({"step": 1, "min": -3, "max": 1, "signed": True, "on_bound": HIT}, 0xFE, 8, 4, (1, 1)),
        ({"step": 1, "min": -3, "max": 1, "on_bound": HIT}, 0xFE, 8, 1, (1, 1)),
        ({"step": -1, "min": -2, "signed": True, "on_bound": HIT}, 0, 8, 3, (0xFE, 1)),
        ({"step": 5, "toward": 0, "signed": True, "on_bound": HIT}, 0xF0, 8, 4, (0, 1)),
        ({"step": 5, "toward": 0, "on_bound": HIT}, 0xF0, 8, 2, (0xE6, 0)),
        ({"step": 5, "toward": -1, "signed": True, "on_bound": HIT}, 0xFF, 8, 2, (0xFF, 0)),
        (
            {"step": 1, "min": 0, "signed": True, "clamp": "reflect", "on_bound": HIT},
            0x7E,
            8,
            2,
            (0x7E, 1),
        ),
        ({"step": 0x804, "max": 0x7FFF, "signed": True, "on_bound": HIT}, 0x7C00, 16, 1, (4, 0)),
    ],
)
def test_bounded_accumulator(acc, init, width, ticks, expected):
    assert regs(run(acc_playroutine(acc, width, init), ticks + 1)) == expected


@pytest.mark.parametrize(
    "acc,width",
    [
        ({"step": 1, "toward": 3, "clamp": "reflect"}, 8),
        ({"step": 1, "max": 3, "signed": True}, None),
        ({"step": 1, "max": 3, "clamp": "bounce"}, 8),
    ],
)
def test_bad_accumulator_is_a_compile_error(acc, width):
    with pytest.raises(SongError):
        SongPlayer(mini_song(acc_playroutine(acc, width)))


def test_toward_wrapped_overshoot_is_not_a_hit():
    playroutine = acc_playroutine(
        {"step": 0x20, "toward": 0xFF, "on_bound": [{"set": {"hits": 1}}]}, 8, 0xF0
    )
    assert regs(run(playroutine, 2)) == (0x10, 0)


def test_set_wraps_to_width_and_unbounded_cells_do_not():
    playroutine = {
        "global": {"g8": {"width": 8}, "big": {}},
        "voice": {"w4": {"width": 4}},
        "init": [{"set": {"g8": -1, "big": 0x1234}}, {"for_voices": [{"set": {"w4": "0x1F"}}]}],
        "ghost": {"global": {0x15: "g8", 0x16: "big >> 8"}, "voice": {0: "w4"}},
    }
    player = run(playroutine)
    assert (player.regs[0x15], player.regs[0x16], player.regs[0]) == (0xFF, 0x12, 0x0F)


def test_control_flow_procs_records_and_return():
    playroutine = {
        "constants": {"k": 7},
        "global": {"count": {}},
        "voice": {"a": {}, "b": {}, "arg": {}},
        "records": {
            "ops": {1: {"do": [{"set": {"b": "b + arg"}}, {"return": True}, {"set": {"b": 99}}]}}
        },
        "procs": {
            "bump": [
                {"set": {"a": "a + arg"}},
                {"if": "a > 5", "then": [{"return": True}]},
                {"set": {"a": "a + 100"}},
            ]
        },
        "tick": [
            {"set": {"count": "count + 1"}},
            {
                "if": "count == 2",
                "then": [{"return": True}],
                "else": [{"set": {"count": "count + 0"}}],
            },
            {
                "for_voices": [
                    {"call": "bump", "with": {"arg": "k"}},
                    {"run": {"record": "ops", "index": 1, "field": "do"}},
                    {"run": {"record": "ops", "index": 5, "field": "do"}},
                ]
            },
        ],
        "ghost": {"global": {0x15: "count"}, "voice": {0: "a", 1: "b"}},
    }
    player = run(playroutine, 4)
    assert (player.regs[0x15], player.regs[0], player.regs[1]) == (3, 14, 14)


def test_cursor_steps_transpose_repeat_loop():
    phrases = {
        "a": compile_phrase([{"note": 1, "steps": 2}, {"note": "off"}]),
        "b": compile_phrase([{"note": 5, "instrument": 2, "commands": [[3, 4], [6, 7]]}]),
    }
    cur = Cursor(
        {"plays": [{"phrase": "a", "transpose": 12, "repeat": 2}, {"phrase": "b"}], "loop": 1},
        phrases,
    )
    seen = []
    for _ in range(9):
        seen.append((cur.at_phrase_start, cur.fetch()))
    assert seen == [
        (1, (1, 13, 0, 0, 0, 0, 0)),
        (0, (0, 0, 0, 0, 0, 0, 0)),
        (0, (2, 0, 0, 0, 0, 0, 0)),
        (1, (1, 13, 0, 0, 0, 0, 0)),
        (0, (0, 0, 0, 0, 0, 0, 0)),
        (0, (2, 0, 0, 0, 0, 0, 0)),
        (1, (1, 5, 2, 3, 4, 6, 7)),
        (1, (1, 5, 2, 3, 4, 6, 7)),
        (1, (1, 5, 2, 3, 4, 6, 7)),
    ]


@pytest.mark.parametrize(
    "event",
    [
        {"command": [1, 2], "commands": [[1, 2]]},
        {"commands": [[1, 2], [3, 4], [5, 6]]},
    ],
)
def test_event_commands_rejects(event):
    with pytest.raises(SongError):
        compile_phrase([event])


def test_cursor_end_and_fetch_pseudo_cells():
    playroutine = {
        "voice": {"k": {}, "n": {}, "e": {}},
        "tick": [
            {
                "for_voices": [
                    {"fetch": True},
                    {"set": {"k": "ev_kind", "n": "ev_note", "e": "ended"}},
                ]
            }
        ],
        "ghost": ghost("k", "n", "e"),
    }
    tracks = [{"plays": [{"phrase": "a"}], "loop": None}]
    player = run(playroutine, 2, tracks=tracks, phrases={"a": [{"note": True}]})
    assert player.regs[:3] == [3, 0, 1]
    player.play_frame()
    assert player.regs[:3] == [0, 0, 1]


def test_first_flush_is_full_then_changes_only_in_canonical_order():
    playroutine = {
        "voice": {"t": {}},
        "tick": [{"for_voices": [{"set": {"t": "t + 1"}}]}],
        "ghost": {"voice": {4: "t", 0: "t * 2", 6: "5"}},
    }
    player = SongPlayer(mini_song(playroutine))
    assert len(player.play_frame()) == 25
    assert player.play_frame() == [(0, 2), (4, 1)]
    assert player.play_frame() == [(0, 4), (4, 2)]
    assert player.render_grid(2).shape == (2, 25)


def test_render_wav(tmp_path):
    playroutine = {"ghost": {"voice": {4: "0x11", 1: "0x20", 6: "0xF0"}}}
    path = SongPlayer(mini_song(playroutine)).render_wav(tmp_path / "a.wav", 0.1)
    assert path.stat().st_size > 1000


@pytest.mark.parametrize(
    "playroutine,match",
    [
        ({"tick": [{"set": {"nope": 1}}]}, "unknown cell"),
        ({"tick": [{"call": "nope"}]}, "unknown proc"),
        ({"tick": [{"fetch": True}]}, "voice scope"),
        ({"tick": [{"for_voices": [{"set": {"ev_kind": 1}}]}]}, "read-only"),
        ({"tick": [{"run": {"record": "r", "index": 0, "field": "do"}}]}, "unknown record"),
        ({"tick": ["x"]}, "bad action"),
        ({"tick": [{"jump": 1}]}, "unknown action"),
        ({"tick": {"set": {}}}, "action list"),
        ({"voice": {"ev_note": {}}}, "reserved"),
        ({"ghost": {"voice": {9: "1"}}}, "offset"),
        ({"records": {"ins": {}}}, "twice"),
    ],
)
def test_playroutine_errors(playroutine, match):
    with pytest.raises(SongError, match=match):
        SongPlayer(mini_song(playroutine, records={"ins": {}}))


@pytest.mark.parametrize(
    "text,match",
    [
        ("t[0]", "unknown table"),
        ("r[0].f", "unknown record"),
        ("v_only", "global scope"),
        ("zzz", "unknown name"),
    ],
)
def test_expression_scope_errors(text, match):
    playroutine = {"voice": {"v_only": {}}, "global": {"g": {}}, "tick": [{"set": {"g": text}}]}
    with pytest.raises(ExprError, match=match):
        SongPlayer(mini_song(playroutine))


@pytest.mark.parametrize(
    "song_kw,match",
    [
        ({"phrases": {"a": []}}, "empty phrase"),
        ({"phrases": {"a": [{"steps": 0}]}}, "steps"),
        ({"phrases": {"a": [{"note": "x"}]}}, "bad note"),
        ({"tracks": [{"plays": [{"phrase": "zz"}]}]}, "unknown phrase"),
        ({"tracks": [{"plays": []}]}, "no plays"),
        ({"tracks": [{"plays": [{"phrase": "a"}], "loop": 3}]}, "loop index"),
        ({"voices": 2}, "one track"),
        ({"registers": [0]}, "registers"),
        ({"unitracker": 1}, "expanded"),
    ],
)
def test_song_errors(song_kw, match):
    with pytest.raises(SongError, match=match):
        SongPlayer(mini_song({}, **song_kw))


def test_tables_read_zero_out_of_range():
    playroutine = {
        "global": {"a": {}, "b": {}},
        "init": [{"set": {"a": "t[1]", "b": "t[5] + t[-1]"}}],
        "ghost": {"global": {0x15: "a", 0x16: "b"}},
    }
    player = run(playroutine, tables={"t": [4, 9]})
    assert (player.regs[0x15], player.regs[0x16]) == (9, 0)
    assert np.array_equal(player.render_grid(1)[0], player.regs)
