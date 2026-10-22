"""Expressions re-timed from a native unit's firings to the generic player's ticks: the firing
count and the hold factor against a tick trace, their spellings, the schedules modulators
share, the events that play their own, and a lift whose native unit skips ticks verifying on
unitracker-player."""

import copy
import random

import irs
import pytest
from conftest import gt_tune
from test_document import HARD_RESTART
from unitracker_player import play
from unitracker_grammar import expr as xexpr

from unitracker.document import retime, tree, writer
from unitracker.document.retime import Part, Schedule
from unitracker.lift.goattracker import GoatTrackerLift
from unitracker.player import oracle

TABLES = {"_T": {}, "_F": lambda k: 0}


def ticks(text: str, v: int, n: int) -> list[int]:
    """The values expression ``text`` plays over ``n`` ticks from ``v``, one firing a tick."""
    fn, out = xexpr.compile_expr(text, TABLES), []
    for phi in range(n):
        v = fn(v, phi, 0)
        out.append(v)
    return out


def firings(text: str, v: int, schedule: Schedule) -> list[int]:
    """The values a native unit plays over ``schedule``'s ticks: the expression's ``k``-th
    firing on each tick it fires, the value it holds on the others."""
    fn, out, k = xexpr.compile_expr(text, TABLES), [], 0
    for phi in range(schedule.length):
        if phi not in schedule.skips:
            v, k = fn(v, k, 0), k + 1
        out.append(v)
    return out


def test_a_closed_form_reads_the_firing_count_and_holds_on_a_skipped_tick():
    text = retime.retimed("16*φ + 2048", Schedule(frozenset({2, 5}), 8))
    assert ticks(text, 0, 8) == [2048, 2064, 2064, 2080, 2096, 2096, 2112, 2128]
    assert "v" not in text


def test_a_recurrence_holds_its_update_on_a_skipped_tick():
    text = retime.retimed("v + 3", Schedule(frozenset({0, 4}), 7))
    assert ticks(text, 100, 7) == [100, 103, 106, 109, 109, 112, 115]


def test_a_closed_form_first_fired_late_holds_its_cell_or_starts_at_zero():
    late = Schedule(frozenset({0, 1}), 5)
    assert ticks(retime.retimed("16*φ + 2048", late), 7, 5) == [7, 7, 2048, 2064, 2080]
    zero = retime.retimed("16*φ + 2048", late, zero=True)
    assert ticks(zero, 7, 5) == [0, 0, 2048, 2064, 2080] and "v" not in zero


def test_no_skips_leaves_the_text():
    assert retime.retimed("v + 3", Schedule(frozenset(), 9)) == "v + 3"


def test_a_skip_every_step_is_spelled_with_floor_division():
    schedule = Schedule(frozenset(range(3, 40, 5)), 40)
    text = retime.retimed("16*φ + 2048", schedule)
    assert text == "16*φ - 16*((φ + 2) // 5) + 2048"
    assert ticks(text, 0, 40) == firings("16*φ + 2048", 0, schedule)
    held = retime.retimed("v + 3", schedule)
    assert held == "v - 3*(φ % 5 == 3) + 3"


def test_a_progression_that_stops_before_the_end_is_capped():
    schedule = Schedule(frozenset({3, 8, 13, 18}), 30)
    assert retime.parts(schedule) == [Part(3, 5, 19)]
    assert Part(3, 5, 19).count() == "min((φ + 2) // 5, 4)"
    assert retime.parts(Schedule(frozenset({3, 8, 13, 18}), 20)) == [Part(3, 5, None)]


@pytest.mark.parametrize("text", ["16*φ + 2048", "(φ % 4)*9", "v + 5*φ - 2", "2*v + 1"])
def test_every_spelling_plays_the_native_firings_over_its_ticks(text):
    rng = random.Random(183)
    for _ in range(60):
        length = rng.randint(1, 60)
        step, start = rng.randint(2, 7), rng.randint(0, 9)
        skips = {s for s in range(length) if rng.random() < 0.15}
        skips |= set(range(start, length, step)) if rng.random() < 0.6 else set()
        skips |= set(range(rng.randint(0, length), length)) if rng.random() < 0.3 else set()
        schedule = Schedule(frozenset(skips), length)
        want = firings(text, 100, schedule)
        assert ticks(retime.retimed(text, schedule), 100, length) == want
        for periodic in (True, False):
            found = retime.parts(schedule, periodic)
            assert sum(len(range(p.start, p.end or length, p.step)) for p in found) == len(skips)


def test_parts_spell_runs_progressions_and_single_ticks():
    schedule = Schedule(frozenset({0, 1, 2, 7, 11, 15, 19, 21}), 23)
    found = retime.parts(schedule)
    assert found == [Part(0, 1, 3), Part(7, 4, None), Part(21, 1, 22)]
    assert [p.count() for p in found] == ["min(φ + 1, 3)", "max(0, (φ - 3) // 4)", "(φ >= 21)"]
    assert [p.skipped() for p in found] == [
        "1 - (φ >= 3)",
        "(φ % 4 == 3)*(φ >= 7)",
        "(φ == 21)",
    ]
    assert (
        Part(9, 1, None).skipped() == "(φ >= 9)"
        and Part(4, 1, 6).count() == "min(max(0, φ - 3), 2)"
    )


def test_a_schedule_whose_sum_no_player_compiles_has_no_spelling():
    rng = random.Random(7)
    skips = frozenset(rng.sample(range(5000), 600))
    assert retime.retimed("v + 1", Schedule(skips, 5000)) is None


def test_schedules_merge_when_one_extends_the_others_and_agree_by_consensus():
    short, long_ = Schedule(frozenset({2}), 5), Schedule(frozenset({2, 7}), 9)
    other = Schedule(frozenset({3}), 5)
    assert long_.covers(short) and not short.covers(long_) and not long_.covers(other)
    assert retime.merged([short, long_]) == long_ and retime.merged([]) is None
    assert retime.merged([short, other]) is None
    assert retime.consensus([short, long_, other]) == long_
    assert retime.consensus([other, other, long_]) == other and retime.consensus([]) is None
    assert Schedule(frozenset({0, 1, 3}), 5).lead == 2 and short.lead == 0


def gt_song() -> dict:
    """A goattracker2 song: two instruments sharing pulse program ``16*φ + 1024``, a note
    running pulse program ``32*φ + 512`` and one playing frequency expression ``v + 8``, each
    voice looping it."""
    freq = {"command": [16, 3]}
    return irs.song(
        "goattracker2",
        records={
            "ctrltab": {1: {"n": 0, "next": 0, "w": 65}, 2: {"n": 0, "next": 0, "w": 65}},
            "ins": {
                1: {"ctrl": 1, "pw": 1, "restart": 1, "sr": 240},
                2: {"ctrl": 2, "pw": 1, "restart": 1, "sr": 200},
            },
            "pulsetab": {1: {"next": 0, "x": 1}, 2: {"next": 0, "x": 2}},
            "restart": copy.deepcopy(HARD_RESTART),
        },
        phrases={
            "p1": [
                {"instrument": 1, "note": 36, "steps": 4},
                {"note": 38, "steps": 4},
                {"command": [9, 2], "note": 40, "steps": 4},
                {**freq, "instrument": 2, "note": 41},
                *[dict(freq) for _ in range(3)],
                {"note": 43, "steps": 2},
            ]
        },
        play=["p1"] * 3,
        loop=0,
        exprs={1: "16*φ + 1024", 2: "32*φ + 512", 3: "v + 8"},
        meta={"title": "T"},
    )


def respelled(events: dict) -> str:
    """The generic document of :func:`gt_song` with ``events`` playing their own schedules."""
    ir = gt_song()
    native = writer.native_state(ir, 200, 200)
    timing = writer.Timing(events=events)
    return tree.respelled(
        ir, writer._Respell(ir, native, 0, timing)  # pylint: disable=protected-access
    )


def test_events_play_their_own_schedules_in_copies_and_commands():
    skip = Schedule(frozenset({1}), 4)
    text = respelled(
        {
            ("p1", 0): {"pw": ("16*φ + 1024", skip, writer.COPY)},
            ("p1", 2): {"pw": ("32*φ + 512", skip, "run")},
            ("p1", 3): {"freq": ("v + 8", skip, "command")},
        }
    )
    assert "p1: [C3.i3*4, D3.i1*4, E3!run:pw2*4, F3.i2!freq:x2*4, G3*2]" in text
    assert "pw2: 32*φ - 32*(φ >= 1) + 512" in text and "pw1:" not in text and "x1:" not in text
    assert "x2: (v - G[N]) % 65536 - 8*(φ == 1) + G[N] + 8" in text
    assert "i3: {ad: 0, sr: 240, ctrl: c1, pw: 16*φ - 16*(φ >= 1) + 1024" in text
    grid = play(text, 40)
    fired = next(
        f for cell, f, origin in oracle.firings(text, 40) if origin == ("p1", 0, "instrument")
    )
    assert [int(grid[t][2]) | int(grid[t][3]) << 8 for t in fired[:4]] == [1024, 1024, 1040, 1056]


def test_an_event_whose_expression_or_instrument_is_not_its_own_keeps_its_atom():
    skip = Schedule(frozenset({1}), 4)
    text = respelled(
        {
            ("p1", 0): {"pw": ("2*φ", skip, writer.COPY)},
            ("p1", 2): {"pw": ("2*φ", skip, "run")},
            ("p1", 4): {"pw": ("16*φ + 1024", skip, writer.COPY)},
            ("p1", 1): {"pw": ("16*φ + 1024", skip, "run")},
        }
    )
    assert "p1: [C3.i1*4, D3*4, E3!run:pw1*4, F3.i2!freq:x1*4, G3*2]" in text


def test_a_lift_whose_native_pulse_skips_ticks_verifies_on_the_generic_player():
    pulse = [(0x84, 0), (0x20, 0x10), (0x40, 0xF0), (0x20, 0x10), (0xFF, 2)]
    ins = {"gateoff_timer": 2, "sustain_release": 0xF0, "wave_ptr": 1, "pulse_ptr": 1}
    rest = [(None, 0, 0, 0)]
    rows = [(24, 1, 0, 0)] + rest * 7 + [(26, 1, 0, 0)] + rest * 7
    lift = GoatTrackerLift(gt_tune([(0x41, 0), (0xFF, 0)], [ins], rows, pulse=pulse)[0])
    lift.run()
    assert lift.verdict.ok, lift.verdict.line()
    assert "(φ + 3) // 6" in lift.document and "D2!pw:x1*8" in lift.document
    mods = [(f[0], len(f), o) for c, f, o in oracle.firings(lift.document, 100) if c == ("pw", 0)]
    assert mods == [(8, 48, ("p1", 0, "instrument")), (56, 44, ("p1", 1, "note command"))]
