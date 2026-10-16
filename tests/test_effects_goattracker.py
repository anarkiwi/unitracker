"""GoatTracker 2 effects: each spec the lifter derives runs as the player's arithmetic.

The reference functions transcribe gplay.c / player.s (pygoattracker's ``Player``):
``_speed_value``, ``_toneporta`` and ``_vibrato``.
"""

import itertools

import pytest
from pygoattracker.constants import FREQ_TABLE

from unitracker.goattracker import Effects
from unitracker.spec import Compiled, Lane, parse


class Speed:  # pylint: disable=too-few-public-methods
    """A one-row speedtable."""

    def __init__(self, left, right):
        self.left, self.right = [left], [right]


class Voice:  # pylint: disable=too-few-public-methods
    """The voice state the player's effects read: note, lastnote, the vibrato phase."""

    def __init__(self, note=24, lastnote=24, freq=0x1000):
        self.note, self.lastnote, self.freq, self.vibtime = note, lastnote, freq, 0


def speed_value(row, voice):
    """``_speed_value``: a fixed 16-bit speed, or a semitone interval shifted right."""
    left, right = row
    speed = left << 8 | right
    if speed >= 0x8000:
        semi = (
            FREQ_TABLE[(voice.lastnote + 1) & 0x7F] - FREQ_TABLE[voice.lastnote & 0x7F]
        ) & 0xFFFF
        speed = semi >> right
    return speed & 0xFFFF


def toneporta(row, voice, tie=False):
    """``_toneporta``; True when it reaches the note (freq, vibtime and lastnote latch)."""
    target = FREQ_TABLE[voice.note & 0x7F]
    reached = tie
    if not tie:
        speed = speed_value(row, voice)
        if voice.freq < target:
            voice.freq = (voice.freq + speed) & 0xFFFF
            reached = voice.freq > target
        if not reached and voice.freq > target:
            voice.freq = (voice.freq - speed) & 0xFFFF
            reached = voice.freq < target
    if reached:
        voice.freq, voice.vibtime, voice.lastnote = target, 0, voice.note
    return reached


def vibrato(row, voice):
    """``_vibrato``: vibtime's turns decide the direction of each step."""
    left, right = row
    cmp_, speed = left, right
    if cmp_ >= 0x80:
        cmp_ &= 0x7F
        speed = speed_value(row, voice)
    if cmp_ < voice.vibtime < 0x80:
        voice.vibtime ^= 0xFF
    voice.vibtime = (voice.vibtime + 2) & 0xFF
    step = -speed if voice.vibtime & 1 else speed
    voice.freq = (voice.freq + step) & 0xFFFF


def compile_for(voice):
    """A compile function evaluating spec expressions against ``voice`` like the playroutine."""

    def compile_expr(text, _what):
        def fn(_cells, value):
            semi = (
                FREQ_TABLE[(voice.lastnote + 1) & 0x7F] - FREQ_TABLE[voice.lastnote & 0x7F]
            ) & 0xFFFF
            names = {"semi": semi, "note": FREQ_TABLE[voice.note & 0x7F], "v": value}
            return eval(text, {"__builtins__": {}}, names)  # pylint: disable=eval-used

        return fn

    return compile_expr


def lane_for(spec, voice):
    lane = Lane(0xFFFF)
    lane.set(voice.freq)
    compiled = Compiled(parse(spec), 1, compile_for(voice), None)
    return lane, compiled


@pytest.mark.parametrize("left", range(256))
def test_every_slide(left):
    for right, sign, start in itertools.product(range(256), (1, -1), (0x10, 0xFFF0)):
        voice = Voice(lastnote=(left * 7 + right) & 0x7F, freq=start)
        spec = Effects(Speed(left, right)).slide(1, sign)
        lane, compiled = lane_for(spec, voice)
        lane.start(compiled, "command")
        for _ in range(3):
            voice.freq = (voice.freq + sign * speed_value((left, right), voice)) & 0xFFFF
            assert lane.advance(None) == voice.freq


@pytest.mark.parametrize("left", range(256))
def test_every_portamento(left):
    for right, start, note in itertools.product(range(0, 256, 3), (0x200, 0x9000), (30, 60)):
        voice = Voice(note=note, lastnote=40, freq=start)
        ours = Voice(note=note, lastnote=40, freq=start)
        spec = Effects(Speed(left, right)).porta(1)
        lane, compiled = lane_for(spec, ours)
        lane.start(compiled, "command")
        for _ in range(40):
            reached = toneporta((left, right), voice)
            value = lane.advance(None)
            assert bool(lane.hit) == reached
            if lane.hit:
                ours.lastnote = ours.note
            assert value == voice.freq


def test_tie_reaches_every_tick():
    voice, ours = Voice(note=50, freq=123), Voice(note=50, freq=123)
    lane, compiled = lane_for(Effects(Speed(0, 0)).porta(0), ours)
    lane.start(compiled, "command")
    for _ in range(3):
        assert toneporta((0, 0), voice, tie=True)
        assert lane.advance(None) == voice.freq and lane.hit


@pytest.mark.parametrize("cmp_", range(128))
def test_every_vibrato_compare_value(cmp_):
    """Direction periods for each compare value, odd and even, over two full swings, and
    a phase reset (a note or a reached portamento) partway through."""
    for left, right in ((cmp_, 0x21), (cmp_ | 0x80, 2)):
        fx = Effects(Speed(left, right))
        voice = Voice(lastnote=33)
        ours = Voice(lastnote=33)
        lane, compiled = lane_for(fx.vibrato(1), ours)
        lane.start(compiled, "command")
        period = 2 * (cmp_ // 2 + 1) + 2
        for tick in range(3 * period + 5):
            if tick == period + 1:
                voice.vibtime = 0
                lane.restart()
            vibrato((left, right), voice)
            assert lane.advance(None) == voice.freq


@pytest.mark.parametrize("right", range(0, 256, 1))
def test_every_vibrato_depth(right):
    for left in (0, 7, 0x7D, 0x7E, 0x7F, 0x84):
        voice, ours = Voice(lastnote=12), Voice(lastnote=12)
        lane, compiled = lane_for(Effects(Speed(left, right)).vibrato(1), ours)
        lane.start(compiled, "command")
        for _ in range(140):
            vibrato((left, right), voice)
            assert lane.advance(None) == voice.freq


def test_vibrato_restarted_by_command_keeps_its_phase():
    """A vibrato command each row restarts the spec but not the phase; a slide resets it."""
    fx = Effects(Speed(6, 9))
    voice, ours = Voice(), Voice()
    lane, compiled = lane_for(fx.vibrato(1), ours)
    _, slide = lane_for(fx.slide(1, 1), ours)
    for tick in range(60):
        if tick % 5 == 0:
            lane.start(compiled, "command")
        if tick == 33:
            lane.start(slide, "command")
            voice.vibtime = 0
            lane.start(compiled, "command")
        vibrato((6, 9), voice)
        assert lane.advance(None) == voice.freq


def test_one_tick_wavetable_effects_share_the_phase():
    """A wavetable vibrato row steps the running vibrato's phase without replacing it."""
    fx = Effects(Speed(4, 3))
    voice, ours = Voice(), Voice()
    lane, compiled = lane_for(fx.vibrato(1), ours)
    _, once = lane_for(fx.vibrato(1, row=True), ours)
    lane.start(compiled, "command")
    for tick in range(40):
        vibrato((4, 3), voice)
        if tick % 7 == 3:
            lane.once(once, None)
        else:
            lane.advance(None)
        assert lane.stored == voice.freq


def test_flip_schedules_must_agree():
    fx = Effects(Speed(4, 3))
    fx.vibrato(1)
    fx.vibrato(0)
    with pytest.raises(ValueError, match="flip schedules"):
        fx.check_flips()
