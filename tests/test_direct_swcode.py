"""SID-Wizard's code modulators, run by unitracker-player's 6502, fire as pysidwizard's
playroutine functions at the standard note table, trajectory by trajectory over random and
boundary states, for a light build and for a normal build (calculated and typed vibratos)."""

import copy
import random

import pytest
from pysidwizard import parse_sid_song
from pysidwizard.player import VoiceState
from unitracker_grammar import code
from unitracker_player.code import Machine

from unitracker.direct.sidwizard import CLOCK, Transcriber
from unitracker.direct.swcode import STATE

# pylint: disable=protected-access

NAMES = tuple(STATE["freq"])


@pytest.fixture(name="builds", scope="module")
def fixture_builds(mega_run, csillag):
    """Transcribers of a light and a normal build, each over the standard note table."""
    return [
        Transcriber(parse_sid_song(p.read_bytes(), 1), check=False) for p in (mega_run, csillag)
    ]


def routine(one, call):
    """The routine of ``call`` as the player compiles it for the freq cell."""
    machine = Machine({}, {"freq": tuple(STATE["freq"].items())}, CLOCK[1])
    params = code.params(one.routines[call.name])
    return machine.compile(call.name, one.routines[call.name], dict(zip(params, call.args)), "freq")


def state_of(v: VoiceState, held: bool = True) -> dict:
    """The routines' state of ``v``, by name."""
    o, z = v.vibrato_offset, v.zp_freq_hi << 8 | v.zp_freq_lo
    fm = v.vibrato_freqmod_hi << 8 | v.vibrato_freqmod_lo
    return {
        "o": o,
        "s": int(not held),
        "z": z,
        "dc": v.vibrato_delay_counter & 0xFF,
        "vc": v.vibrato_freq_counter,
        "p": v.vibrato_period,
        "fm": fm,
        "k": v.slide_vib,
        "dd": v.detune & 0xFF,
    }


def pack(values: dict) -> bytearray:
    """``values`` laid out as the state line declares them."""
    out = bytearray()
    for name, size in STATE["freq"].items():
        value = values.get(name, 0)
        out += bytes((value >> (8 * i)) & 0xFF for i in range(size))
    return out


def unpack(state: bytearray) -> dict:
    """The state bytes by name."""
    out, at = {}, 0
    for name, size in STATE["freq"].items():
        out[name] = sum(state[at + i] << (8 * i) for i in range(size))
        at += size
    return out


def voice(rng, one) -> VoiceState:
    """A voice on a copy of instrument 1 with a random vibrato, type, counters and offset."""
    ins = copy.copy(one.swm.instruments[0])
    ins.vibrato, ins.vibrato_delay = rng.randrange(256), rng.choice((0, 1, 5, 0x7F, 0x80, 0xFF))
    ins.control = rng.choice((0x00, 0x10, 0x20, 0x30, 0x18, 0x08))
    ins.ram = b""
    one.ref._zp_images.clear()
    v = VoiceState(instrument=ins, note=rng.randrange(1, 0x60))
    v.vibrato_offset = rng.randrange(65536)
    v.vibrato_freq_counter, v.vibrato_period = rng.randrange(256), rng.randrange(0, 32, 2)
    v.vibrato_freqmod_lo, v.vibrato_freqmod_hi = rng.randrange(256), rng.randrange(256)
    v.vibrato_delay_counter = rng.randrange(-128, 128)
    v.zp_freq_lo, v.zp_freq_hi = rng.randrange(256), rng.randrange(256)
    v.slide_vib = rng.choice((0x00, 0x10, 0x20, 0x30, 0x81, 0x82, 0x83, 0xFF, 0x90))
    return v


def compare(state: bytearray, v: VoiceState, keys: str, phi: int) -> None:
    got, want = unpack(state), state_of(v)
    assert {k: got[k] for k in keys.split()} == {k: want[k] for k in keys.split()}, phi


@pytest.mark.parametrize("seed", range(30))
@pytest.mark.parametrize("build", [0, 1])
def test_stp_is_vibslide(builds, build, seed):
    one, rng = builds[build], random.Random(seed)
    v = voice(rng, one)
    fire, state = routine(one, one.call("stp")), pack(state_of(v))
    for phi in range(200):
        value = rng.randrange(65536)
        assert fire(value, phi, 0, 0, state) == value
        one.ref._tick_vibrato(v)
        compare(state, v, "o dc vc fm k", phi)


@pytest.mark.parametrize("seed", range(30))
@pytest.mark.parametrize("build", [0, 1])
def test_snd_is_strtsnd(builds, build, seed):
    one, rng = builds[build], random.Random(seed)
    v = voice(rng, one)
    call = one.strtsnd(v.instrument, rng.randrange(3))
    state = pack(state_of(v) | {"s": 1})
    value = rng.randrange(65536)
    out = routine(one, call)(value, 0, 0, v.note - 1, state)
    one.ref._setup_vibrato(v)
    v.detune = 0
    compare(state, v, "o s dc vc p fm k dd", 0)
    high = dict(zip(code.params(one.routines["snd"]), call.args))["h"]
    expect = value if not high else one.ref.freq_hi[v.note] << 8 | value & 0xFF
    assert out == expect


@pytest.mark.parametrize("seed", range(30))
@pytest.mark.parametrize("build", [0, 1])
def test_vfx_is_setvibr_then_vibslide(builds, build, seed):
    one, rng = builds[build], random.Random(seed)
    v = voice(rng, one)
    byte, clear = rng.randrange(256), rng.random() < 0.5
    call = one.vibrato_byte(v.instrument, byte, clear)
    fire, state = routine(one, call), pack(state_of(v))
    for phi in range(100):
        if not phi:
            one.ref._apply_vibrato_byte(v, byte)
            v.vibrato_offset = 0 if clear else v.vibrato_offset
        fire(0, phi, 0, v.note - 1, state)
        one.ref._tick_vibrato(v)
        compare(state, v, "o dc vc p fm k", phi)


@pytest.mark.parametrize("seed", range(30))
@pytest.mark.parametrize("build", [0, 1])
def test_por_and_leg_are_portame(builds, build, seed):
    one, rng = builds[build], random.Random(seed)
    v = voice(rng, one)
    value, legato = rng.randrange(256), rng.random() < 0.4
    note = rng.randrange(1, 0x60)
    call = one.call("leg") if legato else one.call("por", m=0x83, **one.amp(value, False))
    fire, state = routine(one, call), pack(state_of(v))
    fire(0, 0, 0, note - 1, state)
    v.note = note
    if legato:
        prev = v.zp_freq_hi << 8 | v.zp_freq_lo
        v.vibrato_offset = (prev - one.ref._dpitch_freq(note)) & 0xFFFF
        v.slide_vib, v.vibrato_freqmod_hi = 0x83, 0x7F
    else:
        one.ref._begin_portamento(v, value)
    compare(state, v, "o fm k", 0)
    for phi in range(1, 60):
        fire(0, phi, 0, note - 1, state)
        one.ref._tick_vibrato(v)
        compare(state, v, "o fm k", phi)


@pytest.mark.parametrize("seed", range(30))
@pytest.mark.parametrize("build", [0, 1])
def test_sld_is_setslid(builds, build, seed):
    one, rng = builds[build], random.Random(seed)
    v = voice(rng, one)
    fx, value = rng.choice((1, 2, 3)), rng.choice((0, rng.randrange(256)))
    fire = routine(one, one.call("sld", m=0x80 + fx, **one.amp(value, fx != 3)))
    state = pack(state_of(v))
    one.ref._apply_fx_columns(v, None, fx, value, False)
    for phi in range(40):
        fire(0, phi, 0, v.note - 1, state)
        one.ref._tick_vibrato(v)
        compare(state, v, "o fm k", phi)


@pytest.mark.parametrize("seed", range(40))
@pytest.mark.parametrize("build", [0, 1])
def test_tone_and_det_are_wrpitch(builds, build, seed):
    """FREQLO/FREQHI of the masked note (held while the pitch is), then the detune and the
    carry of the WF row: none, set, or a relative note down past DPITCH."""
    # pylint: disable=too-many-locals
    one, rng = builds[build], random.Random(seed)
    v = voice(rng, one)
    tone = routine(one, one.call("tone"))
    held = rng.random() < 0.5
    for phi in range(30):
        v.note = rng.randrange(1, 0x60)
        v.wf_arp_pitch = rng.randrange(-v.note, 0x100 - v.note)
        written = not phi or rng.random() < 0.2
        if written:
            v.vibrato_offset = 0
        v.wf_did_pitch_write, v.pitch_held, v.hr_timer = written, held, 0
        state = pack(state_of(v, held))
        nn = (v.note + v.wf_arp_pitch - 1) & 0xFF
        zp = tone(0, 0 if written else phi, nn, 0, state)
        one.ref._compose_sid_writes(v)
        held = v.pitch_held
        assert zp == v.zp_freq_hi << 8 | v.zp_freq_lo == unpack(state)["z"], phi
        if not one.feats.detunesupport:
            continue
        arp = rng.choice((0, rng.randrange(0xE0, 0x100)))
        mode = rng.choice((0, 1)) if arp < 0xE0 else 2
        v.wf_pitch_carry = bool(mode) if mode < 2 else arp + v.note > 0xFF
        v.detune = rng.randrange(-128, 128)
        det = routine(one, one.call("det", f=1, d=v.detune & 0xFF, c=mode, b=arp))
        one.ref._compose_sid_writes(v)
        assert det(0, 0, 0, v.note - 1, state) == v.sid_freq_hi << 8 | v.sid_freq_lo, phi


def test_every_template_reads_only_its_cell_and_round_trips(builds):
    allowed = set(NAMES) | {"v", "phi", "nn", "ne", "F.lo", "F.hi"}
    for one in builds:
        for text in one.code.values():
            assert code.printed(code.read(text)) == text
            assert set(code.symbols(code.read(text))) <= allowed
