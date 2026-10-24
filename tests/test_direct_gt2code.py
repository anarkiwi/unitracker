"""GoatTracker 2's code modulators, run by unitracker-player's 6502, fire as pygoattracker's
playroutine functions, trajectory by trajectory over random and boundary states."""

import random

import pytest
from pygoattracker import constants as GT
from pygoattracker.player import Player, _Channel
from unitracker_grammar import code, frequency
from unitracker_player.code import Machine

from unitracker.direct.goattracker2 import CLOCK
from unitracker.direct.gt2code import CODE, STATE

# pylint: disable=protected-access

CLOCK_HZ = CLOCK["clock_hz"]
FREQ = tuple(frequency(k, CLOCK_HZ) for k in range(128))


def routine(name: str, values: dict, tables: dict, cell: str):
    """Template ``name`` bound to ``values`` on a machine of its own over ``tables``, as the
    player compiles it for ``cell``."""
    machine = Machine(tables, {cell: tuple(STATE[cell].items())}, CLOCK_HZ)
    return machine.compile(name, code.read(CODE[name]), values, cell)


def reference(speed=(0, 0), pulse=((), ()), simplepulse=False) -> Player:
    """A playroutine over speed table entry 1 ``speed`` and pulse table rows ``pulse`` from row
    1, its frequency table the code's ``F``."""
    ref = Player.__new__(Player)
    ref._freq_table = FREQ
    ref._ltable, ref._rtable = ([[0] * 256 for _ in range(4)] for _ in range(2))
    ref._ltable[GT.STBL][0], ref._rtable[GT.STBL][0] = speed
    ref._ltable[GT.PTBL][: len(pulse[0])] = pulse[0]
    ref._rtable[GT.PTBL][: len(pulse[1])] = pulse[1]
    ref.simplepulse, ref.pulse_step, ref.live_vibrato = simplepulse, 1, False
    return ref


def run_freq(name: str, values: dict, chan: _Channel, act, rng, firings=48):
    """Fire ``name`` beside ``act(phi)`` on the reference ``chan``, comparing the frequency and
    the state (the vibrato phase and the latched note) after each firing; between firings, other
    slots sometimes write them."""
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    fire = routine(name, values, {}, "freq")
    for phi in range(300 if rng.random() < 0.04 else firings):
        if phi and rng.random() < 0.1:
            chan.freq, chan.vibtime = rng.randrange(65536), rng.randrange(256)
            chan.lastnote = rng.randrange(256)
        state = bytearray((chan.vibtime, chan.lastnote))
        v = fire(chan.freq, phi, chan.note, chan.note, state)
        act(phi)
        assert (v, *state) == (chan.freq, chan.vibtime, chan.lastnote), (name, values, phi)


def channel(rng, **fields) -> _Channel:
    """A channel with a random frequency (often at a wrap), phase and notes."""
    freq = rng.choice((rng.randrange(65536), rng.randrange(256), 65535 - rng.randrange(256)))
    chan = _Channel(freq=freq, vibtime=rng.randrange(256), lastnote=rng.randrange(256))
    chan.note = rng.randrange(256)
    for key, value in fields.items():
        setattr(chan, key, value)
    return chan


def shift(rng) -> int:
    """A calculated speed's right shift: mostly in range, sometimes past 16 bits."""
    return rng.choice((rng.randrange(17), rng.randrange(17, 256)))


def test_every_template_is_canonical_code():
    """Each text is its routine's one text, naming the state of one cell only."""
    owners = {name: cell for cell, names in STATE.items() for name in names}
    for text in CODE.values():
        statements = code.read(text)
        assert code.printed(statements) == text
        assert len({owners[s] for s in code.symbols(statements) if s in owners}) <= 1


@pytest.mark.parametrize("calc", [False, True])
@pytest.mark.parametrize("depth", [0, 1, 5, 64, 126, 127])
def test_vibrato_every_phase(calc, depth):
    """Every phase, each depth, plain and calculated steps, after a delay or at once."""
    rng = random.Random(depth * 2 + calc)
    for t in range(256):
        lag = rng.choice((0, 0, 1, 2, rng.randrange(255)))
        step = shift(rng) if calc else rng.randrange(256)
        ref = reference(((0x80 if calc else 0) | depth, step))
        command = GT.CMD_VIBRATO if not lag and rng.random() < 0.5 else GT.CMD_DONOTHING
        chan = channel(rng, vibtime=t, command=command, cmddata=1, vibdelay=lag + 1)
        values = {"c": depth, "l": lag, "k" if calc else "s": step}
        name = "vibn" if calc else "vib"
        run_freq(name, values, chan, lambda _, c=chan, r=ref: r._tickn_command(c), rng, lag + 48)


def test_vibrato_delay_past_a_byte():
    """``phi`` past 255 keeps a long delay's vibrato running."""
    ref = reference((3, 40))
    chan = _Channel(freq=4000, command=GT.CMD_DONOTHING, cmddata=1, vibdelay=255)
    values = {"c": 3, "s": 40, "l": 254}
    fire = routine("vib", values, {}, "freq")
    v, state = chan.freq, bytearray(2)
    for phi in range(600):
        v = fire(v, phi, 0, 0, state)
        ref._tickn_command(chan)
        assert (v, *state) == (chan.freq, chan.vibtime, chan.lastnote)


def portamento_case(rng, calc: bool) -> tuple:
    """A speed table entry and its code parameters."""
    if calc:
        right = shift(rng)
        return (0x80 | rng.randrange(128), right), {"k": right}
    speed = rng.choice((rng.randrange(0x8000), rng.randrange(64), 0x7FFF - rng.randrange(8), 0))
    return (speed >> 8, speed & 255), {"s": speed}


@pytest.mark.parametrize("once", ["", "1"])
@pytest.mark.parametrize("calc", [False, True])
@pytest.mark.parametrize("up", [False, True])
def test_portamento(once, calc, up):
    """A pattern portamento's rows each zero the phase on tick 0 (``_tick0_command``); a
    wavetable step leaves it."""
    rng = random.Random(f"{once}{calc}{up}")
    for _ in range(300):
        speed, values = portamento_case(rng, calc)
        ref = reference(speed)
        command = GT.CMD_PORTAUP if up else GT.CMD_PORTADOWN
        chan = channel(rng, command=command, cmddata=1)
        name = ("portan" if calc else "porta") + once

        def act(phi, chan=chan, ref=ref):
            if not once:
                chan.vibtime = 0
            if not once or not phi:
                ref._tickn_command(chan)

        run_freq(name, values | {"g": 1 if up else -1}, chan, act, rng)


@pytest.mark.parametrize("once", ["", "1"])
@pytest.mark.parametrize("form", ["snap", "tporta", "tportan"])
def test_toneporta(once, form):
    """Toward the event note's frequency from below, above, at it, and across 16-bit wraps."""
    rng = random.Random(f"{once}{form}")
    for _ in range(600):
        speed, values = portamento_case(rng, form == "tportan")
        ref = reference(speed)
        chan = channel(rng, command=GT.CMD_TONEPORTA, cmddata=0 if form == "snap" else 1)
        near = rng.choice(
            (0, rng.randrange(-300, 300), rng.randrange(-20, 20) * values.get("s", 1))
        )
        if rng.random() < 0.5:
            chan.freq = (ref._freq(chan.note) + near) % 65536
        values = {} if form == "snap" else values

        def act(phi, chan=chan, ref=ref):
            if not once or not phi:
                ref._tickn_command(chan)

        run_freq(form + once, values, chan, act, rng)


def test_note():
    """A wavetable note row: the sounding note's frequency once, the phase restarted, the
    note latched for calculated speeds."""
    rng = random.Random(5)
    for _ in range(400):
        ref = reference()
        note = rng.randrange(256)
        chan = channel(rng, note=note, wave_table_ptr=1)

        def act(phi, chan=chan, ref=ref):
            if not phi:
                ref._wave_exec(0, chan)

        run_freq("note", {}, chan, act, rng)


def pulse_table(rng) -> tuple:
    """Random pulse table rows: set rows, modulation rows (some of 256 steps), jumps."""
    left, right = [], []
    for _ in range(256):
        kind = rng.random()
        if kind < 0.3:
            left.append(rng.randrange(0x80, 0xFF))
        elif kind < 0.85:
            left.append(rng.choice((0, rng.randrange(1, 8), rng.randrange(1, 0x80))))
        else:
            left.append(0xFF)
        right.append(rng.choice((rng.randrange(256), 0, rng.randrange(1, 4))))
    return left, right


@pytest.mark.parametrize("simple", [False, True])
def test_pulse_table(simple):
    """Trajectories of the pulse table from random first rows over random tables, the row
    and time compared with the reference's pointer and pulse time."""
    rng = random.Random(simple)
    for _ in range(150):
        left, right = pulse_table(rng)
        ref = reference(pulse=(left, right), simplepulse=simple)
        tables = {"PL": [0] + left[:255], "PR": [0] + right[:255]}
        first = rng.choice((rng.randrange(1, 256), rng.randrange(1, 256), 0, 255))
        fire = routine("ptabs" if simple else "ptab", {"b": first}, tables, "pw")
        chan = _Channel(pulse=rng.randrange(4096))
        v, state = chan.pulse, bytearray((rng.randrange(256), rng.randrange(256)))
        for phi in range(700):
            if not phi:
                chan.pulse_table_ptr, chan.pulsetime = first, 0
            if chan.pulse_table_ptr:
                ref._pulse_exec(chan)
            v = fire(v, phi, 0, 0, state)
            assert (v & 0xFFF, state[0], state[1]) == (
                chan.pulse & 0xFFF,
                chan.pulse_table_ptr,
                chan.pulsetime,
            ), (first, phi)
