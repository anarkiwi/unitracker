"""SID-Wizard's code modulators, run by unitracker-player's 6502, fire as pysidwizard's
playroutine functions, trajectory by trajectory over random and boundary states."""

import copy
import random

import pytest
from pysidwizard import parse_sid_song
from pysidwizard.player import VoiceState
from unitracker_grammar import code
from unitracker_player.code import Machine

from unitracker.direct.sidwizard import CLOCK, Transcriber
from unitracker.direct.swcode import CODE, STATE

# pylint: disable=protected-access


def routine(name: str, values: dict, words: list):
    """Template ``name`` bound to ``values`` over the frequency table ``words``."""
    machine = Machine({"W": words}, {"freq": tuple(STATE["freq"].items())}, CLOCK[1])
    return machine.compile(name, code.read(CODE[name]), values, "freq")


@pytest.fixture(name="ref", scope="module")
def fixture_ref(mega_run):
    """A light-build player, its frequency table and its first instrument."""
    swm = parse_sid_song(mega_run.read_bytes(), 1)
    one = Transcriber(swm)
    return one.ref, one._words(), one


def state_of(v: VoiceState, held: bool) -> tuple:
    """The routines' state bytes of ``v``: o, s, dc, vc, fm."""
    o = v.vibrato_offset
    counters = (int(not held), v.vibrato_delay_counter & 0xFF, v.vibrato_freq_counter)
    return (o & 0xFF, o >> 8, *counters, v.vibrato_freqmod_lo, v.vibrato_freqmod_hi)


def voice(rng, ref) -> VoiceState:
    """A voice on a copy of instrument 1 with a random vibrato, counters and offset."""
    ins = copy.copy(ref.swm.instruments[0])
    ins.vibrato, ins.vibrato_delay = rng.randrange(256), rng.choice((0, 1, 5, 0x7F, 0x80, 0xFF))
    ins.control = rng.randrange(0x80) & 0x3F
    v = VoiceState(instrument=ins, note=rng.randrange(1, 0x60))
    v.vibrato_offset = rng.randrange(65536)
    v.vibrato_freq_counter = rng.randrange(256)
    lo = rng.randrange(256)
    v.vibrato_freqmod_lo, v.vibrato_freqmod_hi = lo, rng.randrange(256)
    v.vibrato_delay_counter = rng.randrange(-128, 128)
    return v


def test_every_template_is_canonical_code():
    for text in CODE.values():
        assert code.printed(code.read(text)) == text


@pytest.mark.parametrize("seed", range(40))
def test_vib_is_strtsnd_then_normvib(ref, seed):
    player, words, one = ref
    rng = random.Random(seed)
    v = voice(rng, player)
    ins = v.instrument
    one.swm.instruments[0], saved = ins, one.swm.instruments[0]
    try:
        call = one.vibrato(1)
    finally:
        one.swm.instruments[0] = saved
    fire = routine("vib", dict(zip(code.params(code.read(CODE["vib"])), call.args)), words)
    state = bytearray(rng.randrange(256) for _ in range(7))
    fire(0, 0, 0, 0, state)
    player._setup_vibrato(v)
    assert tuple(state) == state_of(v, True)
    for phi in range(1, 300):
        value = rng.randrange(65536)
        assert fire(value, phi, 0, 0, state) == value
        player._tick_vibrato(v)
        assert tuple(state) == state_of(v, True), phi


@pytest.mark.parametrize("seed", range(20))
def test_vibfx_is_setvibr_then_normvib(ref, seed):
    player, words, one = ref
    rng = random.Random(seed)
    v = voice(rng, player)
    note = rng.randrange(0x60, 0x78)
    saved = one.swm.instruments[0]
    one.swm.instruments[0] = v.instrument
    try:
        call = one.vibfx(1, note)
    finally:
        one.swm.instruments[0] = saved
    fire = routine("vibfx", dict(zip(code.params(code.read(CODE["vibfx"])), call.args)), words)
    state = bytearray(state_of(v, True))
    for phi in range(200):
        if not phi:
            player._apply_vibrato_byte(v, (note & 0x0F) << 4 | v.instrument.vibrato & 0x0F)
            v.vibrato_offset = 0
        fire(0, phi, 0, 0, state)
        player._tick_vibrato(v)
        assert tuple(state) == state_of(v, True), phi


@pytest.mark.parametrize("seed", range(40))
def test_leg_is_the_legato_seed_then_portame(ref, seed):
    player, words, _ = ref
    rng = random.Random(seed)
    v = voice(rng, player)
    prev = rng.choice((rng.randrange(65536), rng.randrange(256), 65535 - rng.randrange(256)))
    v.zp_freq_lo, v.zp_freq_hi = prev & 0xFF, prev >> 8
    fire = routine("leg", {}, words)
    state = bytearray(state_of(v, True))
    note = rng.randrange(1, 0x60)
    fire(prev, 0, 0, note - 1, state)
    v.vibrato_offset = (prev - player._dpitch_freq(note)) & 0xFFFF
    v.slide_vib, v.vibrato_freqmod_hi = 0x83, 0x7F
    assert tuple(state) == state_of(v, True)
    for phi in range(1, 40):
        fire(0, phi, 0, 0, state)
        player._tick_vibrato(v)
        assert tuple(state) == state_of(v, True), phi


@pytest.mark.parametrize("seed", range(40))
def test_tone_is_the_compose(ref, seed):
    player, words, _ = ref
    rng = random.Random(seed)
    fire = routine("tone", {}, words)
    v = voice(rng, player)
    held = rng.random() < 0.5
    for phi in range(30):
        prev = rng.randrange(65536)
        v.zp_freq_lo, v.zp_freq_hi = prev & 0xFF, prev >> 8
        v.note, v.wf_arp_pitch = rng.randrange(1, 0x60), rng.randrange(-20, 20)
        written = not phi or rng.random() < 0.2
        if written:
            v.vibrato_offset = 0
        v.wf_did_pitch_write, v.pitch_held, v.hr_timer = written, held, 0
        state = bytearray(state_of(v, held))
        out = fire(prev, 0 if written else phi, (v.note + v.wf_arp_pitch - 1) & 0xFF, 0, state)
        player._compose_sid_writes(v)
        held = v.pitch_held
        assert out == v.sid_freq_hi << 8 | v.sid_freq_lo, phi
        assert state[2] == int(not held)
