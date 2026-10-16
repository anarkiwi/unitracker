"""defMON effects: each spec the lifter derives runs as the replay's arithmetic.

The reference functions transcribe the $1000 replay: the pulse sweep at $1495, the
pitch modes at $1405 and the ACID cutoff at $10B5.
"""

import random
from fractions import Fraction

import pytest

from conftest import mini_song
from unitracker import defmon, songio
from unitracker.spec import Compiled, Lane, parse
from unitracker.vm import SongPlayer

LIB = songio.library("defmon")
TABLES = LIB["tables"]
MEM = {defmon.RATE_LO + k: v for k, v in enumerate(TABLES["rate_lo"])}
MEM.update({defmon.RATE_HI + k: v for k, v in enumerate(TABLES["rate_hi"])})


def nfreq(n):
    return TABLES["note_lo"][n] | TABLES["note_hi"][n] << 8


def por(y):
    a = TABLES["por_lo"][y + 1] | TABLES["por_hi"][y + 1] << 8
    b = TABLES["por_lo"][y] | TABLES["por_hi"][y] << 8
    return (a - b) & 0xFFFF


def native_sweep(pw, ps, fc):
    """$1495: bit 7 adds; otherwise subtracts with the pitch carry; pins and reverses."""
    if ps == 0:
        return pw, ps
    if ps >= 0x80:
        value = (pw + (ps & 0x7F)) & 0xFFFF
        if value > (0x0FFF if pw >> 8 == 0x0F else 0xFFFF):
            return 0x0FF8, ps ^ 0x80
        return value, ps
    value = (pw - ps - (1 - fc)) & 0xFFFF
    if value > (0x00FF if pw >> 8 == 0 else 0xFFFF):
        return 1, ps ^ 0x80
    return value, ps


def compiled(spec, scale=1, carry=None):
    def compile_expr(text, _what):
        return lambda _c, v: eval(text, {"__builtins__": {}}, {"v": v})  # pylint: disable=eval-used

    return Compiled(parse(spec), scale, compile_expr, lambda _lane: carry)


def _carry(bits, tick):
    """The pitch carry of the current tick, from a fixed random sequence."""
    return lambda _c: bits[tick["t"]]


@pytest.mark.parametrize("ps", range(256))
def test_every_pulse_sweep_depth(ps):
    """Both directions, both bounds, the carry in, until two reversals or 9000 ticks."""
    rng = random.Random(ps)
    for start in (0x0FC0, 0x0040, 0x0800):
        bits = [rng.getrandbits(1) for _ in range(9000)]
        tick = {"t": 0}
        lane = Lane(0xFFFF)
        lane.set(start)
        value = defmon.pulse(None, ps)
        if value != "off":
            lane.start(compiled(value, carry=_carry(bits, tick)), "row")
        pw, state, flips = start, ps, 0
        for t in range(9000):
            tick["t"] = t
            pw, new = native_sweep(pw, state, bits[t])
            flips += new != state
            state = new
            lane.advance(None)
            assert lane.stored == pw, (ps, start, t)
            if flips >= 2:
                break


def test_pulse_set_with_and_without_a_sweep():
    assert defmon.pulse(0x100, None) == 0x100
    assert defmon.pulse(0x100, 0) == {"from": 0x100}
    assert defmon.pulse(None, 0) == "off"
    assert defmon.pulse(0x100, 0x81)["from"] == 0x100


def pitch_song(rows, note, voices=1):
    """The library defMON playroutine running only its pitch step, with program rows."""
    pr = songio.library("defmon")
    tables = pr.pop("tables")
    pr["voice"]["k"] = {}
    pr["init"] = [
        {"for_voices": [{"set": {"note": note, "tbase": note}}]},
    ]
    pr["tick"] = [
        {
            "for_voices": [
                {"set": {"k": "k + 1"}},
                {"if": "k == 1", "then": [{"run": {"record": "prog", "index": 0, "field": "do"}}]},
                {"if": "k == 70", "then": [{"run": {"record": "prog", "index": 1, "field": "do"}}]},
                {"if": "k == 140", "then": [{"set": {"bend": 0}}, {"stop": "bend"}]},
                {
                    "if": "k == 150",
                    "then": [{"run": {"record": "prog", "index": 0, "field": "do"}}],
                },
                {"call": "pitch"},
            ]
        }
    ]
    pr["ghost"] = {"voice": {0: "flo", 1: "fhi", 2: "fc"}}
    song = mini_song(pr, tables=tables, records={"prog": rows}, voices=voices)
    song["tracks"] = song["tracks"] * voices
    return song


def native_pitch(modes, note, ticks):
    """$1405 with the mode changes and the note's slide reset of :func:`pitch_song`."""
    sacc, mode, out = 0, 0, []
    for k in range(1, ticks + 1):
        if k == 1:
            mode = modes[0]
        if k == 70:
            mode = modes[1]
        if k == 140:
            sacc, mode = 0, 0
        if k == 150:
            mode = modes[0]
        if mode == 0:
            lo = TABLES["note_lo"][note]
            out.append((lo & 0xFF, TABLES["note_hi"][note], int(lo > 0xFF)))
            continue
        if mode >= 0x80:
            k_ = (mode & 0x3F) << 1
            rate = TABLES["rate_lo"][k_] | TABLES["rate_hi"][k_] << 8
            sacc = (sacc - rate if mode & 0x40 else sacc + rate) & 0xFFFF
            pf = sacc + nfreq(note)
        else:
            pf = nfreq(note) + por((note + mode) & 0xFF)
        out.append((pf & 0xFF, (pf >> 8) & 0xFF, int(pf > 0xFFFF)))
    return out


@pytest.mark.parametrize("mode", range(256))
def test_every_slide_mode(mode):
    """Each AF mode, a second mode taking over the stored slide, and a note's reset."""
    other = (mode * 37 + 0x91) & 0xFF
    rows = {
        0: {"bend": defmon.bend(MEM, mode), "next": -1},
        1: {"bend": defmon.bend(MEM, other), "next": -1},
    }
    for note in (12, 60):
        player = SongPlayer(pitch_song(rows, note))
        player.play_frame()
        got = []
        for _ in range(200):
            player.play_frame()
            got.append(tuple(player.regs[:3]))
        assert got == native_pitch((mode, other), note, 200)


def native_acid(acc, astep, sub, ticks, floor=2):
    out = []
    for _ in range(ticks):
        carry = int(acc - astep - 1 >= 0) if sub else int(acc + astep > 0xFFFF)
        acc = (acc - astep - 1) & 0xFFFF if sub else (acc + astep) & 0xFFFF
        if acc > 0x7FFF:
            acc = (floor << 8) | (acc & 0xFF)
        out.append((acc, carry))
    return out


@pytest.mark.parametrize(
    "word", list(range(0, 0x10000, 97)) + [0x7FFF, 0x8000, 0xBFFF, 0xC000, 0xFFFF]
)
def test_acid_words(word):
    """Slides, absolute resets keeping the direction, saturation and the carry out."""
    for acc, astep, sub in ((0x1234, 0x40, 0), (0x7F00, 0x100, 1), (0x0010, 0, 1)):
        lane = Lane(0xFFFF)
        init = defmon.acid(0, 2)
        init.update(
            {
                "from": Fraction(acc, 256),
                "up": Fraction(astep, 256),
                "down": Fraction(-(astep + 1), 256),
                "dir": "down" if sub else "up",
            }
        )
        lane.start(compiled(init, scale=256), "row")
        expected = native_acid(acc, astep, sub, 5)
        for value, carry in expected:
            lane.advance(None)
            assert (lane.stored, lane.carry) == (value, carry)
        state = expected[-1][0]
        lane.start(compiled(defmon.acid(word, 2), scale=256), "row")
        if word & 0x8000:
            astep, sub = (word & 0x3FFF, 1) if word & 0x4000 else (word & 0x7FFF, 0)
        else:
            state, astep = word, 0
        for value, carry in native_acid(state, astep, sub, 40):
            lane.advance(None)
            assert (lane.stored, lane.carry) == (value, carry)
