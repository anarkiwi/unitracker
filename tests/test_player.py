"""The generic player: actions, the bounded accumulator, cursors, the flush, canonical write
order, and the table entries a song can reach."""

import numpy as np
import pytest
from conftest import mini_song

from unitracker.playroutine.expr import ExprError
from unitracker.lift.verify import compare_grids
from unitracker.player.canonical import CANONICAL_ORDER, canonical_frames
from unitracker.player.reach import ReachabilityError, table_reads
from unitracker.player.vm import (
    Cursor,
    SongError,
    SongPlayer,
    compile_phrase,
    diff,
    first_mismatch,
    runs,
)


def run(playroutine, ticks=1, **kw):
    player = SongPlayer(mini_song(playroutine, **kw))
    for _ in range(ticks):
        player.play_frame()
    return player


HIT = [{"set": {"h": "h + 1"}}]

# (accumulate action, init, width, ticks, (value, on_bound hits))
ACCUMULATORS = [
    ({"step": 0x80}, 0x90, 8, 1, (0x10, 0)),
    ({"step": -1}, 0, 8, 2, (0xFE, 0)),
    ({"step": 4, "max": 10, "on_bound": HIT}, 0, 8, 4, (10, 2)),
    ({"step": 4, "max": 10, "clamp": False, "on_bound": HIT}, 0, 8, 3, (12, 1)),
    ({"step": -4, "min": 3, "on_bound": HIT}, 12, 8, 4, (0xFF, 1)),
    ({"step": 5, "toward": 12, "on_bound": HIT}, 0, 8, 5, (12, 1)),
    ({"step": 4, "toward": 12, "on_bound": HIT}, 0, 8, 5, (12, 0)),
    ({"step": 5, "toward": 2, "on_bound": HIT}, 20, 8, 5, (2, 1)),
    ({"step": 0x20, "toward": 0xFF, "on_bound": HIT}, 0xF0, 8, 1, (0x10, 0)),
    ({"step": 4, "max": 10, "clamp": "reflect", "on_bound": HIT}, 0, 8, 3, (4, 1)),
    ({"step": 3, "max": 3, "clamp": "reflect", "on_bound": HIT}, 2, 8, 1, (0xFF, 1)),
    ({"step": 1, "min": -3, "max": 1, "signed": True, "on_bound": HIT}, 0xFE, 8, 4, (1, 1)),
    ({"step": 1, "min": -3, "max": 1, "on_bound": HIT}, 0xFE, 8, 1, (1, 1)),
    ({"step": -1, "min": -2, "signed": True, "on_bound": HIT}, 0, 8, 3, (0xFE, 1)),
    ({"step": 5, "toward": 0, "signed": True, "on_bound": HIT}, 0xF0, 8, 4, (0, 1)),
    ({"step": 5, "toward": -1, "signed": True, "on_bound": HIT}, 0xFF, 8, 2, (0xFF, 0)),
    (
        {"step": 1, "min": 0, "signed": True, "clamp": "reflect", "on_bound": HIT},
        0x7E,
        8,
        2,
        (0x7E, 1),
    ),
    ({"step": 0x804, "max": 0x7FFF, "signed": True, "on_bound": HIT}, 0x7C00, 16, 1, (0x8404, 0)),
]


def test_bounded_accumulator():
    """Every case runs as its own pair of global cells in one playroutine."""
    cells, tick = {}, []
    for i, (acc, init, width, _, _) in enumerate(ACCUMULATORS):
        cells |= {f"x{i}": {"width": width, "init": init}, f"h{i}": {}}
        on_bound = [{"set": {f"h{i}": f"h{i} + 1"}}] if "on_bound" in acc else []
        tick.append({**acc, "accumulate": f"x{i}", "on_bound": on_bound})
    player = SongPlayer(mini_song({"global": cells, "tick": tick}))
    seen = []
    for _ in range(max(case[3] for case in ACCUMULATORS) + 1):
        player.play_frame()
        seen.append({name: player.cell(name) for name in cells})
    for i, (_, _, _, ticks, expected) in enumerate(ACCUMULATORS):
        assert (seen[ticks][f"x{i}"], seen[ticks][f"h{i}"]) == expected, ACCUMULATORS[i]


def test_cells_wrap_to_width_and_tables_read_zero_out_of_range():
    playroutine = {
        "global": {"g8": {"width": 8}, "big": {}, "a": {}, "b": {}},
        "voice": {"w4": {"width": 4}},
        "init": [
            {"set": {"g8": -1, "big": 0x1234, "a": "t[1]", "b": "t[5] + t[-1]"}},
            {"for_voices": [{"set": {"w4": "0x1F"}}]},
        ],
        "ghost": {
            "global": {0x15: "g8", 0x16: "big >> 8", 0x17: "a", 0x18: "b"},
            "voice": {0: "w4"},
        },
    }
    player = run(playroutine, tables={"t": [4, 9]})
    assert [player.regs[r] for r in (0x15, 0x16, 0x17, 0x18, 0)] == [0xFF, 0x12, 9, 0, 0x0F]
    assert (player.cell("big"), player.cell("w4", 0)) == (0x1234, 0x0F)


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
    seen = [(cur.at_phrase_start, cur.fetch()) for _ in range(9)]
    a = [(1, (1, 13, 0, 0, 0, 0, 0)), (0, (0,) * 7), (0, (2, 0, 0, 0, 0, 0, 0))]
    assert seen == a + a + [(1, (1, 5, 2, 3, 4, 6, 7))] * 3


def test_fetch_fills_the_event_pseudo_cells_until_the_track_ends():
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
        "ghost": {"voice": {0: "k", 1: "n", 2: "e"}},
    }
    tracks = [{"plays": [{"phrase": "a"}], "loop": None}]
    player = run(playroutine, 2, tracks=tracks, phrases={"a": [{"note": True}]})
    assert player.regs[:3] == [3, 0, 1]
    player.play_frame()
    assert player.regs[:3] == [0, 0, 1]


def test_flush_is_full_first_then_changes_only_in_canonical_order(tmp_path):
    playroutine = {
        "voice": {"t": {}},
        "tick": [{"for_voices": [{"set": {"t": "t + 1"}}]}],
        "ghost": {"voice": {4: "t", 0: "t * 2", 6: "5", 1: "0x20"}},
    }
    player = SongPlayer(mini_song(playroutine))
    assert [r for r, _ in player.play_frame()] == list(CANONICAL_ORDER)
    assert player.play_frame() == [(0, 2), (4, 1)]
    assert player.play_frame() == [(0, 4), (4, 2)]
    grid = player.render_grid(2)
    assert grid.shape == (2, 25) and np.array_equal(grid[-1], player.regs)
    assert player.starts == [0]
    assert player.render_wav(tmp_path / "a.wav", 0.1).stat().st_size > 1000


def test_starts_record_the_tick_each_voice_s_onset_cell_first_reads_one():
    playroutine = {
        "voice": {"onset": {"width": 1}},
        "tick": [
            {
                "for_voices": [
                    {"fetch": True},
                    {"if": "ev_kind == 1", "then": [{"set": {"onset": 1}}]},
                ]
            }
        ],
    }
    tracks = [{"plays": [{"phrase": name}], "loop": 0} for name in "abc"]
    phrases = {"a": [{}, {"note": 24}], "b": [{"note": 24}], "c": [{}]}
    player = run(playroutine, ticks=6, voices=3, tracks=tracks, phrases=phrases)
    assert player.starts == [2, 1, None]


def accumulating(width=8, **acc):
    tick = [{"for_voices": [dict(acc, accumulate="x")]}]
    return {"voice": {"x": {"width": width}}, "tick": tick}


def scoped(text):
    return {"voice": {"v_only": {}}, "global": {"g": {}}, "tick": [{"set": {"g": text}}]}


SONG_ERRORS = [
    ({"playroutine": {"tick": [{"set": {"nope": 1}}]}}, "unknown cell"),
    ({"playroutine": {"tick": [{"call": "nope"}]}}, "unknown proc"),
    ({"playroutine": {"tick": [{"fetch": True}]}}, "voice scope"),
    ({"playroutine": {"tick": [{"for_voices": [{"set": {"ev_kind": 1}}]}]}}, "read-only"),
    (
        {"playroutine": {"tick": [{"run": {"record": "r", "index": 0, "field": "do"}}]}},
        "unknown record",
    ),
    (
        {
            "playroutine": {
                "records": {"r": {0: {"do": []}}},
                "tick": [{"run": {"record": "r", "index": 0, "template": "do"}}],
            }
        },
        "unknown template",
    ),
    ({"playroutine": {"tick": ["x"]}}, "bad action"),
    ({"playroutine": {"tick": [{"jump": 1}]}}, "unknown action"),
    ({"playroutine": {"tick": {"set": {}}}}, "action list"),
    ({"playroutine": {"voice": {"ev_note": {}}}}, "reserved"),
    ({"playroutine": {"ghost": {"voice": {9: "1"}}}}, "offset"),
    ({"playroutine": {"records": {"ins": {}}}, "records": {"ins": {}}}, "twice"),
    ({"playroutine": accumulating(step=1, toward=3, clamp="reflect")}, "reflect with toward"),
    ({"playroutine": accumulating(step=1, clamp="bounce")}, "not 'bounce'"),
    ({"playroutine": accumulating(width=None, step=1, signed=True)}, "no width"),
    ({"playroutine": scoped("t[0]")}, "unknown table"),
    ({"playroutine": scoped("r[0].f")}, "unknown record"),
    ({"playroutine": scoped("v_only")}, "global scope"),
    ({"playroutine": scoped("zzz")}, "unknown name"),
    ({"phrases": {"a": []}}, "empty phrase"),
    ({"phrases": {"a": [{"steps": 0}]}}, "steps"),
    ({"phrases": {"a": [{"note": "x"}]}}, "bad note"),
    ({"phrases": {"a": [{"command": [1, 2], "commands": [[1, 2]]}]}}, "not both"),
    ({"phrases": {"a": [{"commands": [[1, 2], [3, 4], [5, 6]]}]}}, "at most 2"),
    ({"tracks": [{"plays": [{"phrase": "zz"}]}]}, "unknown phrase"),
    ({"tracks": [{"plays": []}]}, "no plays"),
    ({"tracks": [{"plays": [{"phrase": "a"}], "loop": 3}]}, "loop index"),
    ({"voices": 2}, "one track"),
    ({"unitracker": 1}, "expanded"),
]


def test_a_malformed_song_or_playroutine_is_refused():
    for change, match in SONG_ERRORS:
        song = mini_song(**{"playroutine": {}, **change})
        with pytest.raises((SongError, ExprError), match=match):
            SongPlayer(song)


def test_first_mismatch_counts_each_voice_from_its_start_and_globals_from_the_earliest():
    cases = [
        ({(1, 0), (4, 8)}, [2, 3, None], (4, 8)),
        ({(1, 0), (2, 6)}, [2, 3, None], (2, 6)),
        ({(1, 0), (2, 6)}, [3, 3, None], None),
        ({(0, 14), (7, 20)}, [0, 0, None], None),
        ({(0, 14), (7, 20)}, [0, 0, 5], (7, 20)),
        ({(0, 14), (7, 20)}, [None, None, None], None),
        ({(1, 24), (3, 23)}, [6, 2, None], (3, 23)),
        ({(1, 24), (3, 23)}, [6, 1, None], (1, 24)),
        ({(1, 24), (3, 23)}, [None, None, None], None),
        (set(), [0, 3, 5], None),
    ]
    theirs = np.zeros((8, 25), dtype=np.uint8)
    for cells, starts, expected in cases:
        ours = theirs.copy()
        for cell in cells:
            ours[cell] = 1
        assert first_mismatch(ours, theirs, starts) == expected, (cells, starts)


def test_diff_lists_the_differing_cells_with_their_values_under_the_criterion():
    zero = np.zeros((8, 25), dtype=np.uint8)
    ours = zero.copy()
    ours[1, 0] = 5
    ours[2, 6] = 9
    ours[4, 8] = 7
    assert diff(ours, zero, [2, 3, None]) == [(2, 6, 9, 0), (4, 8, 7, 0)]
    assert diff(ours, zero) == [(1, 0, 5, 0), (2, 6, 9, 0), (4, 8, 7, 0)]
    assert diff(ours[:3], zero) == [(1, 0, 5, 0), (2, 6, 9, 0)]


def test_diff_does_not_count_the_native_1_lsb_carry():
    theirs = np.zeros((2, 25), dtype=np.uint8)
    ours = np.zeros((2, 25), dtype=np.uint8)
    ours[0, 0], ours[0, 1] = 0x40, 0x10  # $1040: the native's $1041, its waveride carry
    theirs[0, 0], theirs[0, 1] = 0x41, 0x10
    ours[1, 21], ours[1, 22] = 0x05, 0x01  # cutoff 0x15: the native's 0x16
    theirs[1, 21], theirs[1, 22] = 0x06, 0x01
    assert diff(ours, theirs) == []
    ours[0, 0] = 0x44
    assert diff(ours, theirs) == [(0, 0, 0x44, 0x41)]


def test_runs_groups_the_cells_per_register_into_tick_runs_and_value_segments():
    cells = [
        (2, 0, 1, 0),
        (3, 0, 1, 0),
        (5, 0, 2, 0),
        (5, 21, 5, 6),
        (6, 21, 5, 6),
        (7, 21, 9, 1),
        (10, 0, 1, 3),
        (11, 0, 4, 3),
        (12, 0, 4, 3),
        (13, 0, 4, 8),
    ]
    assert runs(cells) == [
        {
            "reg": 0,
            "name": "v1 freq lo",
            "from": 2,
            "to": 13,
            "cells": 7,
            "runs": [
                {"from": 2, "to": 3, "values": [[2, 3, 1, 0]]},
                {"from": 5, "to": 5, "values": [[5, 5, 2, 0]]},
                {
                    "from": 10,
                    "to": 13,
                    "values": [[10, 10, 1, 3], [11, 12, 4, 3], [13, 13, 4, 8]],
                },
            ],
        },
        {
            "reg": 21,
            "name": "cut lo",
            "from": 5,
            "to": 7,
            "cells": 3,
            "runs": [{"from": 5, "to": 7, "values": [[5, 6, 5, 6], [7, 7, 9, 1]]}],
        },
    ]


def test_the_verdict_carries_the_differing_cells_and_a_machine_readable_summary():
    theirs = np.zeros((8, 25), dtype=np.uint8)
    ours = np.zeros((8, 25), dtype=np.uint8)
    ours[1, 0] = 5
    ours[2, 6] = 9
    verdict = compare_grids(ours, [2, 3, None], theirs)
    assert verdict.onset == (2, 6) and verdict.whole == (1, 0)
    assert verdict.cells == ((2, 6, 9, 0),)
    assert not verdict.ok
    assert verdict.summary() == {
        "ok": False,
        "ticks": 8,
        "played": 8,
        "ended": 0,
        "starts": [2, 3, None],
        "first": [2, 6],
        "whole": [1, 0],
        "registers": runs([(2, 6, 9, 0)]),
        "cells": [[2, 6, 9, 0]],
    }
    assert compare_grids(theirs, [2, 3, None], theirs).summary() == {
        "ok": True,
        "ticks": 8,
        "played": 8,
        "ended": 0,
        "starts": [2, 3, None],
        "first": None,
        "whole": None,
        "registers": [],
        "cells": [],
    }
    ended = compare_grids(ours, [2, 3, None], theirs[:4])
    assert ended.ticks == 4 and ended.cells == ((2, 6, 9, 0),)


def test_canonical_frames_cut_at_repeats_reorder_and_drop_unchanged():
    assert sorted(CANONICAL_ORDER) == list(range(25))
    zero = [0] * 25
    cases = [
        (
            [[(4, 0x41), (0, 1), (5, 9)], [(4, 0x41), (5, 9), (6, 3)]],
            zero,
            [[[(5, 9), (0, 1), (4, 0x41)]], [[(6, 3)]]],
        ),
        ([[(4, 0x40), (1, 2), (4, 0x41)]], zero, [[[(1, 2), (4, 0x40)], [(4, 0x41)]]]),
        ([[(4, 0), (4, 0x100)]], zero, [[]]),
        ([[(0, 7)]], None, [[[(0, 7)]]]),
        ([[(0x18, 1), (0, 0)]], None, [[[(0x18, 1), (0, 0)]]]),
    ]
    for frames, regs, expected in cases:
        assert list(canonical_frames(frames, regs=regs)) == expected


def counter_song(width) -> dict:
    """One voice reading ``t[1 + n % 2]`` while a global counts ticks in ``width`` bits."""
    playroutine = {
        "global": {"n": {} if width is None else {"width": width}},
        "tick": [{"set": {"n": "n + 1"}}],
        "ghost": {"global": {0x18: "t[1 + n % 2]"}},
    }
    return mini_song(playroutine, tables={"t": [10, 11, 12, 13]})


def test_table_reads_cover_the_reached_entries_over_one_recurrence():
    reads, ticks, period = table_reads(counter_song(4), progress=True)
    assert (reads, period) == ({"t": {1, 2}}, 16) and ticks >= period
    player = SongPlayer(counter_song(4))
    states = []
    for _ in range(18):
        player.play_frame()
        states.append(player.state())
    assert states[1] != states[2] and states[1] == states[17]
    assert player.table_reads is None
    with pytest.raises(ReachabilityError, match="did not recur within 50 ticks"):
        table_reads(counter_song(None), max_ticks=50)
