"""The generic player: actions, lanes and their specs, cursors and the flush."""

import numpy as np
import pytest

from conftest import mini_song
from unitracker.expr import ExprError
from unitracker.vm import Cursor, SongError, SongPlayer, phrase_steps


def run(playroutine, ticks=1, **kw):
    player = SongPlayer(mini_song(playroutine, **kw))
    for _ in range(ticks):
        player.play_frame()
    return player


def ghost(*cells):
    return {"voice": dict(enumerate(cells))}


def lane_playroutine(tick, init=(), lanes=None, **extra):
    """One voice with lane ``x`` (8 bits) and a global lane ``g``."""
    pr = {
        "voice": {"x": {"width": 8}, "c": {}, "n": {"init": 3}, "h": {}},
        "global": {"g": {"width": 16}, "gc": {}},
        "lanes": lanes or {"voice": {"x": {}}, "global": {"g": {}}},
        "carry": {"x": "c", "g": "gc"},
        "pitch": {"note": "n * 10", "semi": {"args": ["k"], "expr": "n + k"}},
        "init": [{"for_voices": list(init)}] if init else [],
        "tick": [{"for_voices": tick}],
        "ghost": {"voice": {0: "x", 1: "c", 2: "h"}, "global": {0x15: "g & 0xFF", 0x16: "gc"}},
    }
    pr.update(extra)
    return pr


def test_records_apply_set_spec_off_and_live():
    records = {
        "r": {
            1: {"x": 5},
            2: {"x": {"step": 2}},
            3: {"x": "off"},
            4: {"x": {"live": "semi(1) + 100"}},
            5: {"x": {"from": 9, "step": "semi"}},
        }
    }
    pr = lane_playroutine(
        [
            {"set": {"h": "h + 1"}},
            {"run": {"record": "r", "index": "h", "field": "do"}},
            {"advance": "x"},
        ],
        record_types={
            "r": {"keys": {"x": "value set,spec,off,live"}, "do": [{"apply": {"x": "row.x"}}]}
        },
    )
    player = SongPlayer(mini_song(pr, records=records))
    seen = []
    for _ in range(6):
        player.play_frame()
        seen.append(player.regs[0])
    assert seen == [0, 5, 7, 7, 104, 12]


def test_start_once_stop_restart_and_tests():
    pr = lane_playroutine(
        [
            {"set": {"h": "h + 1"}},
            {"if": "h == 1", "then": [{"start": {"x": "S[1]"}}]},
            {"if": "h == 3", "then": [{"apply": {"x": "r[1].x"}, "once": True}]},
            {"if": "h == 4", "then": [{"stop": "x"}]},
            {"if": "h == 5", "then": [{"restart": "x"}, {"start": {"x": 0}}]},
            {"advance": "x"},
            {"set": {"c": "running(x) * 4 + on(x) * 2 + hit(x)"}},
        ],
        tables={"S": [0, 1]},
        record_types={"r": {"keys": {"x": "value spec"}}},
    )
    song = mini_song(pr, records={"r": {1: {"x": {"step": 10, "for": 1}}}}, tables={"S": [0, 1]})
    song["phrases"] = {"a": [{"command": ["go", {"step": 1}]}]}
    song["playroutine"]["commands"] = {"go": {"code": 1, "arg": "spec"}}
    song["playroutine"]["tick"][0]["for_voices"].insert(0, {"fetch": True})
    song["playroutine"]["tick"][0]["for_voices"][2] = {
        "if": "h == 1",
        "then": [{"start": {"x": "ev_arg"}}],
    }
    player = SongPlayer(song)
    seen = []
    for _ in range(6):
        player.play_frame()
        seen.append((player.regs[0], player.regs[1]))
    assert seen == [(0, 0), (1, 6), (2, 6), (13, 6), (13, 0), (13, 0)]


def test_set_assigns_a_lanes_stored_value():
    pr = lane_playroutine(
        [{"set": {"h": "h + 1"}}, {"if": "h == 2", "then": [{"set": {"x": 50}}]}, {"advance": "x"}],
        init=[{"start": {"x": "ev_arg"}}],
    )
    song = mini_song(pr)
    song["playroutine"]["init"][0]["for_voices"].insert(0, {"fetch": True})
    song["phrases"] = {"a": [{"command": ["go", {"step": 1}]}]}
    song["playroutine"]["commands"] = {"go": {"code": 1, "arg": "spec"}}
    player = SongPlayer(song)
    regs = [(player.play_frame(), player.regs[0])[1] for _ in range(4)]
    assert regs == [0, 1, 51, 52]


def test_global_lane_scale_and_carry():
    records = {"r": {1: {"g": {"from": "1/2", "step": "1/2", "width": 8}}}}
    pr = lane_playroutine(
        [],
        lanes={"voice": {"x": {}}, "global": {"g": {"scale": 256}}},
        record_types={"r": {"keys": {"g": "value spec"}, "do": [{"apply": {"g": "row.g"}}]}},
    )
    pr["init"] = [{"run": {"record": "r", "index": 1, "field": "do"}}]
    pr["tick"] = [{"advance": "g"}, {"apply": {"g": "2"}}, {"advance": "g"}]
    player = SongPlayer(mini_song(pr, records=records))
    player.play_frame()
    assert player.cell("g") == 128
    player.play_frame()
    assert player.cell("g") == 640 and player.cell("gc") == 0


def test_spec_names_semi_note_and_carry_from():
    pr = lane_playroutine(
        [{"advance": "x"}],
        init=[{"fetch": True}, {"start": {"x": "ev_arg"}}],
        commands={"go": {"code": 1, "arg": "spec"}},
    )
    song = mini_song(pr)
    song["phrases"] = {"a": [{"command": ["go", {"step": "semi(2)", "toward": "note"}]}]}
    player = SongPlayer(song)
    player.play_frame()
    regs = [(player.play_frame(), player.regs[0])[1] for _ in range(8)]
    assert regs[:6] == [5, 10, 15, 20, 25, 30] and regs[-1] == 30


def test_towards_and_has():
    pr = lane_playroutine(
        [{"fetch": True}, {"set": {"h": "towards(ev_arg) * 2 + has(r[1].x) + has(r[2].x)"}}],
        commands={"go": {"code": 1, "arg": "spec"}},
        record_types={"r": {"keys": {"x": "int"}}},
    )
    song = mini_song(pr, records={"r": {1: {"x": 0}}})
    song["phrases"] = {"a": [{"command": ["go", {"toward": "note"}]}]}
    player = SongPlayer(song)
    player.play_frame()
    player.play_frame()
    assert player.regs[2] == 3


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


def test_set_wraps_to_width_and_unbounded_cells_do_not():
    playroutine = {
        "global": {"g8": {"width": 8}, "big": {}},
        "voice": {"w4": {"width": 4}},
        "init": [{"set": {"g8": -1, "big": 0x1234}}, {"for_voices": [{"set": {"w4": "0x1F"}}]}],
        "ghost": {"global": {0x15: "g8", 0x16: "big >> 8"}, "voice": {0: "w4"}},
    }
    player = run(playroutine)
    assert (player.regs[0x15], player.regs[0x16], player.regs[0]) == (0xFF, 0x12, 0x0F)


def test_cursor_steps_transpose_repeat_loop():
    def rows(events):
        return [
            (
                (0, 0, 0, 0, 0, 0, 0)
                if e is None
                else (1 if "note" in e else 0, e.get("note", 0), 0, 0, 0, 0, 0)
            )
            for e in phrase_steps(events)
        ]

    phrases = {"a": rows([{"note": 1, "steps": 2}]), "b": rows([{"note": 5}])}
    cur = Cursor(
        {"plays": [{"phrase": "a", "transpose": 12, "repeat": 2}, {"phrase": "b"}], "loop": 1},
        phrases,
    )
    seen = [(cur.at_phrase_start, cur.fetch()[:2]) for _ in range(7)]
    assert seen == [
        (1, (1, 13)),
        (0, (0, 0)),
        (1, (1, 13)),
        (0, (0, 0)),
        (1, (1, 5)),
        (1, (1, 5)),
        (1, (1, 5)),
    ]


def test_cursor_end_and_fetch_pseudo_cells():
    playroutine = {
        "voice": {"k": {}, "n": {}, "e": {}, "a": {}},
        "commands": {"two": {"code": 4, "arg": "pair"}, "one": {"code": 5, "arg": "int"}},
        "tick": [
            {
                "for_voices": [
                    {"fetch": True},
                    {
                        "set": {
                            "k": "ev_kind",
                            "n": "ev_arg2 * 16 + ev_argn",
                            "e": "ended",
                            "a": "ev_cmd",
                        }
                    },
                ]
            }
        ],
        "ghost": ghost("k", "n", "e", "a"),
    }
    tracks = [{"plays": [{"phrase": "a"}], "loop": None}]
    phrases = {"a": [{"note": True, "command": ["two", [3, 4]]}]}
    player = run(playroutine, 2, tracks=tracks, phrases=phrases)
    assert player.regs[:4] == [3, 0x42, 1, 4]
    player.play_frame()
    assert player.regs[:4] == [0, 0, 1, 0]


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
    assert len(player.state()) == 3


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
        ({"lanes": {"voice": {"q": {}}}}, "not a voice cell"),
        ({"voice": {"q": {}}, "lanes": {"voice": {"q": {"scale": 3}}}}, "power of two"),
        ({"tick": [{"advance": "q"}]}, "unknown lane"),
        ({"tick": [{"apply": {"q": "row.x"}}]}, "unknown lane"),
    ],
)
def test_playroutine_errors(playroutine, match):
    with pytest.raises(SongError, match=match):
        SongPlayer(mini_song(playroutine, records={"ins": {}}))


@pytest.mark.parametrize(
    "records,match",
    [
        ({"r": {1: {"x": {"step": "nope"}}}}, "unknown name"),
        ({"r": {1: {"x": {"step": 1, "carry_from": "q"}}}}, "no carry"),
        ({"r": {1: {"x": [1]}}}, "bad value"),
    ],
)
def test_lane_value_errors(records, match):
    pr = lane_playroutine(
        [{"run": {"record": "r", "index": 1, "field": "do"}}],
        record_types={"r": {"do": [{"apply": {"x": "row.x"}}]}},
    )
    with pytest.raises(SongError, match=match):
        SongPlayer(mini_song(pr, records=records))


@pytest.mark.parametrize(
    "text,match",
    [
        ("t[0]", "unknown table"),
        ("r[0].f", "unknown record"),
        ("v_only", "global scope"),
        ("zzz", "unknown name"),
        ("has(1)", "has\\(\\)"),
        ("has(r[0].f)", "unknown record"),
        ("has(row.f)", "template"),
        ("running(1)", "lane name"),
        ("on(a, b)", "one argument"),
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
        ({"phrases": {"a": [{"command": ["nope", 1]}]}}, "no command"),
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


def test_spec_without_pitch_note():
    pr = lane_playroutine([{"advance": "x"}], init=[{"fetch": True}, {"start": {"x": "ev_arg"}}])
    del pr["pitch"]
    pr["commands"] = {"go": {"code": 1, "arg": "spec"}}
    song = mini_song(pr)
    song["phrases"] = {"a": [{"command": ["go", {"toward": "note"}]}]}
    with pytest.raises(SongError, match="pitch.note"):
        SongPlayer(song).play_frame()


def test_tables_read_zero_out_of_range():
    playroutine = {
        "global": {"a": {}, "b": {}},
        "init": [{"set": {"a": "t[1]", "b": "t[5] + t[-1]"}}],
        "ghost": {"global": {0x15: "a", 0x16: "b"}},
    }
    player = run(playroutine, tables={"t": [4, 9]})
    assert (player.regs[0x15], player.regs[0x16]) == (9, 0)
    assert np.array_equal(player.render_grid(1)[0], player.regs)
