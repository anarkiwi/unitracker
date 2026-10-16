"""The bounded accumulator: the spec grammar and the engine's semantics."""

from fractions import Fraction

import pytest

from unitracker.spec import Compiled, Lane, SpecError, moves, number, parse, render
from unitracker.spec import spec_key as key


def engine(spec, start=0, mask=0xFFFF, scale=1, carry=0):
    def compile_expr(text, _what):
        # pylint: disable=eval-used
        return lambda _c, v: eval(text, {"__builtins__": {}}, {"v": v, "k": 3})

    lane = Lane(mask)
    lane.set(start)
    return lane, Compiled(parse(spec), scale, compile_expr, lambda _lane: lambda _c: carry)


def run(spec, ticks, **kw):
    lane, compiled = engine(spec, **kw)
    lane.start(compiled, "row")
    out = []
    for _ in range(ticks):
        lane.advance(None)
        out.append(lane.stored)
    return lane, out


def test_parse_and_render_round_trip():
    spec = parse(
        {
            "from": "1/4",
            "up": 1,
            "down": {"step": -2, "carry_from": "freq"},
            "dir": "down",
            "lo": 0,
            "at_lo": {"clamp": 1, "reverse": True},
            "hi": 10,
            "at_hi": {"wrap": "on", "stop": True},
            "every": 2,
            "for": 0,
            "width": 8,
            "reset": ["command", "row"],
        }
    )
    assert spec["from"] == Fraction(1, 4) and spec["reset"] == ["row", "command"]
    assert parse(render(spec)) == spec
    assert render({"reset": ["row"], "step": Fraction(4, 2)}) == {"step": 2, "reset": "row"}
    assert key(spec) == key(parse(render(spec)))
    assert not moves({"from": 1}) and moves({"toward": "note"})


@pytest.mark.parametrize(
    "spec,match",
    [
        ([], "mapping"),
        ({"nope": 1}, "unknown"),
        ({"step": 1, "up": 1, "down": 2}, "use step"),
        ({"up": 1}, "together"),
        ({"flip": [1], "up": 1, "down": 1}, "flip"),
        ({"step": True}, "number"),
        ({"step": ""}, "number"),
        ({"up": {"x": 1}, "down": 1}, "phase step"),
        ({"at_hi": {"clamp": 1}}, "needs hi"),
        ({"hi": 1, "at_hi": {"bounce": 1}}, "options"),
        ({"hi": 1, "at_hi": {"reverse": 2}}, "must be on"),
        ({"every": 0}, ">= 1"),
        ({"flip": []}, "periods"),
        ({"reset": "note"}, "triggers"),
        ({"dir": "left"}, "up or down"),
        ({"from": "x"}, "number"),
    ],
)
def test_parse_errors(spec, match):
    with pytest.raises(SpecError, match=match):
        parse(spec)


def test_number():
    assert number("-3/6", "x") == Fraction(-1, 2) and number(Fraction(4, 2), "x") == 2


def test_step_wraps_to_the_lane():
    assert run({"step": 0x80}, 2, start=0x90, mask=0xFF)[1] == [0x10, 0x90]


def test_flip_schedule_and_restart():
    lane, out = run({"step": 1, "flip": [2, 3]}, 9)
    assert out == [1, 2, 1, 0, -1 & 0xFFFF, 0, 1, 2, 1]
    lane.restart()
    lane.advance(None)
    assert lane.stored == 2


def test_bounds_cross_and_options():
    assert run({"step": 4, "hi": 10}, 4)[1] == [4, 8, 10, 10]
    assert run({"step": 4, "hi": 10, "at_hi": {"clamp": 3, "reverse": 1}}, 4)[1] == [
        4,
        8,
        3,
        0xFFFF,
    ]
    assert run({"step": 4, "lo": 0, "hi": 9, "at_hi": {"wrap": 1}}, 3)[1] == [4, 8, 2]
    assert run({"step": -4, "lo": 0, "hi": 9, "at_lo": {"wrap": 1}}, 1, start=2)[1] == [8]
    lane, out = run({"step": 4, "hi": 6, "at_hi": {"stop": 1}}, 4)
    assert out == [4, 8, 8, 8] and lane.driver == Lane.OFF
    assert run({"step": 4, "hi": 10, "at_hi": {"clamp": "v - 1"}}, 3)[1] == [4, 8, 11]


def test_bound_starting_beyond_is_not_crossed():
    assert run({"step": 4, "hi": 10}, 2, start=20)[1] == [24, 28]


def test_up_down_dir_and_carry():
    spec = {"up": 2, "down": {"step": -3, "carry_from": "x"}, "dir": "down"}
    assert run(spec, 2, start=10, carry=1)[1] == [8, 6]
    lane, compiled = engine({"up": 2, "down": -3}, start=10)
    lane.dir = -1
    lane.start(compiled, "row")
    lane.advance(None)
    assert lane.stored == 7
    assert run({"step": 1, "carry_from": "x"}, 1, carry=1)[1] == [2]


def test_constant_step_sets_the_direction():
    lane, compiled = engine({"step": -1})
    lane.start(compiled, "row")
    assert lane.dir == -1


def test_for_every_and_stall():
    lane, out = run({"step": 1, "for": 3}, 5)
    assert out == [1, 2, 3, 3, 3] and lane.driver == Lane.OFF
    assert run({"step": 1, "every": 3}, 6)[1] == [0, 0, 1, 1, 1, 2]
    lane, out = run({"step": 1, "for": 0}, 3)
    assert out == [0, 0, 0] and lane.driver == Lane.ACC


def test_width_carry_out():
    lane, out = run({"step": 0x30, "width": 8}, 2, start=0xE0)
    assert out == [0x10, 0x40] and lane.carry == 0
    lane, _ = run({"step": -1, "width": 8}, 1, start=0)
    assert lane.carry == 0
    lane, _ = run({"step": -1, "width": 8}, 1, start=1)
    assert lane.carry == 1


def test_scaled_lane():
    _, out = run({"from": "1/2", "step": "1/256", "width": 8}, 2, scale=256)
    assert out == [129, 130]
    with pytest.raises(SpecError, match="multiple"):
        engine({"step": "1/512"}, scale=256)


def test_toward():
    lane, out = run({"step": 5, "toward": "12"}, 4)
    assert out == [5, 10, 12, 12] and lane.hit == 0
    lane, out = run({"step": 5, "toward": "2"}, 5, start=20)
    assert out[-1] == 2
    lane, out = run({"step": 0x20, "toward": "0xFF", "width": 8}, 1, start=0xF0, mask=0xFF)
    assert out == [0x10] and not lane.hit
    lane, out = run({"toward": "7"}, 2)
    assert out == [7, 7] and lane.hit


def test_from_only_sets_and_stops():
    lane, compiled = engine({"from": 9})
    lane.start(compiled, "row")
    assert lane.stored == 9 and lane.driver == Lane.OFF


def test_reset_trigger_on_start():
    lane, compiled = engine({"step": 1, "flip": [1, 1], "reset": "command"})
    lane.start(compiled, "command")
    lane.advance(None)
    assert lane.dir == -1
    lane.start(compiled, "row")
    assert lane.dir == -1
    lane.start(compiled, "command")
    assert lane.dir == 1


def test_once_leaves_the_running_spec():
    lane, running = engine({"step": 1})
    _, once = engine({"step": 10, "for": 1})
    lane.start(running, "row")
    assert lane.once(once, None) == 10
    lane.advance(None)
    assert lane.stored == 11 and lane.spec is running
    _, setter = engine({"from": 3})
    assert lane.once(setter, None) == 3


def test_live_and_stop():
    lane = Lane(0xFF)
    lane.set_live(lambda _c, _v: 0x123)
    assert lane.advance(None) == 0x23
    lane.stop()
    assert lane.advance(None) is None
    assert len(lane.state()) == 12
