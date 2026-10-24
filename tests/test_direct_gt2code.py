"""GoatTracker 2's code modulators fire as pygoattracker's playroutine functions, trajectory by
trajectory over random and boundary states."""

import random

import pytest
from gt2code_harness import BRANCHES, MEMORY, Routine, RoutineError, assemble, params, parse
from pygoattracker import constants as GT
from pygoattracker.player import Player, _Channel

from unitracker.direct import goattracker2 as g
from unitracker.direct.gt2code import CODE, STATE

# pylint: disable=protected-access

RNG_G = random.Random(23)
TABLES = {
    "gt": list(GT.FREQ_TABLE),
    "random": [RNG_G.randrange(65536) for _ in range(128)],
}
STATE_NAMES = {name for cell in STATE.values() for name in cell}


def reference(freq=(), speed=(0, 0), pulse=((), ()), simplepulse=False) -> Player:
    """A playroutine over speed table entry 1 ``speed``, pulse table rows ``pulse`` from row 1
    and frequency table ``freq``."""
    ref = Player.__new__(Player)
    ref._freq_table = tuple(freq) or GT.FREQ_TABLE
    ref._ltable, ref._rtable = ([[0] * 256 for _ in range(4)] for _ in range(2))
    ref._ltable[GT.STBL][0], ref._rtable[GT.STBL][0] = speed
    ref._ltable[GT.PTBL][: len(pulse[0])] = pulse[0]
    ref._rtable[GT.PTBL][: len(pulse[1])] = pulse[1]
    ref.simplepulse, ref.pulse_step, ref.live_vibrato = simplepulse, 1, False
    return ref


def run_freq(name: str, values: dict, ref: Player, chan: _Channel, act, rng, firings=48):
    """Fire ``name`` beside ``act(phi)`` on the reference ``chan``, comparing the frequency and
    the vibrato phase after each firing; between firings, other slots sometimes write both."""
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    routine = Routine(CODE[name], values, {"G": ref._freq_table}, STATE["freq"])
    for phi in range(300 if rng.random() < 0.04 else firings):
        if phi and rng.random() < 0.1:
            chan.freq, chan.vibtime = rng.randrange(65536), rng.randrange(256)
        v, state = routine.fire(chan.freq, phi, chan.lastnote, chan.note, {"t": chan.vibtime})
        act(phi)
        assert (v, state["t"]) == (chan.freq, chan.vibtime), (name, values, phi)


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


def test_every_template_is_code():
    assert set(CODE) == set(g.TEMPLATES)
    for code in CODE.values():
        statements = parse(code)
        assert all(line == line.strip() for line in code.split("\n"))
        used = {
            match.group(1)
            for m, op in statements
            if op and m not in BRANCHES and (match := MEMORY.match(op))
        }
        assert used & STATE_NAMES <= set(STATE["freq"]) or used & STATE_NAMES <= set(STATE["pw"])


@pytest.mark.parametrize("calc", [False, True])
@pytest.mark.parametrize("depth", [0, 1, 5, 64, 126, 127])
def test_vibrato_every_phase(calc, depth):
    """Every phase, each depth, plain and calculated steps, after a delay or at once."""
    rng = random.Random(depth * 2 + calc)
    for t in range(256):
        lag = rng.choice((0, 0, 1, 2, rng.randrange(255)))
        step = shift(rng) if calc else rng.randrange(256)
        ref = reference(rng.choice(list(TABLES.values())), ((0x80 if calc else 0) | depth, step))
        command = GT.CMD_VIBRATO if not lag and rng.random() < 0.5 else GT.CMD_DONOTHING
        chan = channel(rng, vibtime=t, command=command, cmddata=1, vibdelay=lag + 1)
        values = {"c": depth, "l": lag, "k" if calc else "s": step}
        name = "vibn" if calc else "vib"
        run_freq(
            name, values, ref, chan, lambda _, c=chan, r=ref: r._tickn_command(c), rng, lag + 48
        )


def test_vibrato_delay_past_a_byte():
    """``phi`` past 255 keeps a long delay's vibrato running."""
    ref = reference(speed=(3, 40))
    chan = _Channel(freq=4000, command=GT.CMD_DONOTHING, cmddata=1, vibdelay=255)
    values = {"c": 3, "s": 40, "l": 254}
    routine = Routine(CODE["vib"], values, {"G": ref._freq_table}, STATE["freq"])
    v, state = chan.freq, {"t": 0}
    for phi in range(600):
        v, state = routine.fire(v, phi, 0, 0, state)
        ref._tickn_command(chan)
        assert (v, state["t"]) == (chan.freq, chan.vibtime)


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
    rng = random.Random(f"{once}{calc}{up}")
    for _ in range(300):
        speed, values = portamento_case(rng, calc)
        ref = reference(rng.choice(list(TABLES.values())), speed)
        command = GT.CMD_PORTAUP if up else GT.CMD_PORTADOWN
        chan = channel(rng, command=command, cmddata=1)
        name = ("portan" if calc else "porta") + once

        def act(phi, chan=chan, ref=ref):
            if not once or not phi:
                ref._tickn_command(chan)

        run_freq(name, values | {"g": 1 if up else -1}, ref, chan, act, rng)


@pytest.mark.parametrize("once", ["", "1"])
@pytest.mark.parametrize("form", ["snap", "tporta", "tportan"])
def test_toneporta(once, form):
    """Toward the event note's frequency from below, above, at it, and across 16-bit wraps."""
    rng = random.Random(f"{once}{form}")
    for _ in range(600):
        speed, values = portamento_case(rng, form == "tportan")
        ref = reference(rng.choice(list(TABLES.values())), speed)
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

        run_freq(form + once, values, ref, chan, act, rng)


def test_note():
    """A wavetable note row: the sounding note's frequency once, the phase restarted."""
    rng = random.Random(5)
    for _ in range(400):
        ref = reference(rng.choice(list(TABLES.values())))
        note = rng.randrange(256)
        chan = channel(rng, note=note, lastnote=note, wave_table_ptr=1)

        def act(phi, chan=chan, ref=ref):
            if not phi:
                ref._wave_exec(0, chan)

        run_freq("note", {}, ref, chan, act, rng)


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
        routine = Routine(CODE["ptabs" if simple else "ptab"], {"b": first}, tables, STATE["pw"])
        chan = _Channel(pulse=rng.randrange(4096))
        v, state = chan.pulse, {"r": rng.randrange(256), "q": rng.randrange(256)}
        for phi in range(700):
            if not phi:
                chan.pulse_table_ptr, chan.pulsetime = first, 0
            if chan.pulse_table_ptr:
                ref._pulse_exec(chan)
            v, state = routine.fire(v, phi, 0, 0, state)
            assert (v & 0xFFF, state["r"], state["q"]) == (
                chan.pulse & 0xFFF,
                chan.pulse_table_ptr,
                chan.pulsetime,
            ), (first, phi)


SYMBOLS = {"v": 512, "phi": 514, "nn": 516, "ne": 517, "t": 518, "G.lo": 600}


@pytest.mark.parametrize(
    "code",
    [
        "LDA v\nrts",
        "lda $10\nrts",
        "lda #$10\nrts",
        "jsr x\nrts",
        "lda (v),y\nrts",
        "lda v ; c\nrts",
        "lda v",
        "bne nowhere\nrts",
        "x:\nrts",
        "lp:\nlp:\nrts",
        "lda w\nrts",
        "lda #z\nrts",
        "lda #300\nrts",
        "stx v,x\nrts",
        "here:\n" + "nop\n" * 200 + "bne here\nrts",
    ],
)
def test_refused(code):
    with pytest.raises(RoutineError):
        assemble(code, {}, SYMBOLS)


def test_assemble_and_params():
    code = "lda #<p\nldx #>p\nldy #q\nsta G.lo+2,y\nfar:\njmp far\nrts"
    assert params(code) == ["p", "q"]
    machine = assemble(code, {"p": -2, "q": 7}, SYMBOLS, 0x1000)
    assert machine == bytes([0xA9, 0xFE, 0xA2, 0xFF, 0xA0, 7, 0x99, 90, 2, 0x4C, 0x09, 0x10, 0x60])


@pytest.mark.parametrize(
    "code",
    ["pla\nrts", "top:\npha\njmp top", "top:\njmp top\nrts", "lda #1\npha\npha\nrts"],
)
def test_refused_at_run(code):
    with pytest.raises(RoutineError):
        Routine(code, {}, {}, {}).fire(0, 0, 0, 0, {})


def test_writes_discarded_and_state_kept():
    """``phi`` and tables are restored each firing; ``v`` and state carry the routine's
    writes; the stack balances back to empty."""
    code = "lda phi\nadc G.lo\nsta v\nsta G.lo\nsta phi\npha\npla\ninc w+1\nlda #9\nsta v+1\nrts"
    routine = Routine(code, {}, {"G": [5]}, {"w": 2})
    assert routine.fire(0, 3, 0, 0, {"w": 0x00FF}) == (0x0908, {"w": 0x01FF})
    assert routine.fire(0, 3, 0, 0, {"w": 0}) == (0x0908, {"w": 0x0100})
