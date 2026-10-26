"""Music Assembler's code modulators, run by unitracker-player's 6502, fire as pymusicassembler's
playroutine functions, firing by firing over random instruments and states."""

import random

import pymusicassembler
import pytest
from unitracker_grammar import code, frequency
from unitracker_grammar.document import (
    NONE,
    Call,
    Code,
    Document,
    Event,
    Header,
    Instrument,
    Once,
    Play,
    Program,
    Row,
    Section,
    Slot,
    SongRow,
    printed,
    signature,
)
from unitracker_player.code import Machine
from unitracker_player.player import play

from unitracker.direct.macode import CODE, STATE
from unitracker.direct.musicassembler import CLOCK, Transcriber, Voice

# pylint: disable=protected-access

REC = 8


def routine(call, cell: str):
    """Template call ``call`` as the player compiles it for ``cell``."""
    machine = Machine({}, {cell: tuple(STATE[cell].items())}, CLOCK[1])
    statements = code.read(CODE[call.name])
    return machine.compile(
        call.name, statements, dict(zip(code.params(statements), call.args)), cell
    )


@pytest.fixture(name="one")
def fixture_one(space_invadarz):
    """A transcriber of a fresh player, whose memory a test may write."""
    return Transcriber(pymusicassembler.read(space_invadarz.read_bytes()))


def record(one, rng, fields: dict) -> int:
    """Instrument 1's record, random but for ``fields`` (offset: value); its address."""
    at = one.lay.instr + REC
    for k in range(REC):
        one.ref._wr(at + k, fields.get(k, rng.randrange(256)))
    return at


def word(one, lo: int, hi: int) -> int:
    return one._rd(lo) | one._rd(hi) << 8


def test_every_template_is_canonical_code():
    for text in CODE.values():
        assert code.printed(code.read(text)) == text


@pytest.mark.parametrize("seed", range(30))
def test_the_pulse_is_the_trigger_then_the_sweep(one, seed):
    rng = random.Random(seed)
    flags = rng.choice((0x40, 0x60, 0x20, 0x00))
    rec = record(one, rng, {7: flags, 4: rng.choice((0, 1, 0x10, 0x3F, rng.randrange(256)))})
    pw = one.ins[one.instrument(1)].get("pw")
    ref, lay = one.ref, one.lay
    ref._trigger(0, rec, 0)
    if isinstance(pw, int):
        assert pw == word(one, lay.pw_lo, lay.pw_hi) & 0xFFF
        return
    fire, state = routine(pw, "pw"), bytearray(rng.randrange(256) for _ in range(2))
    value = fire(rng.randrange(4096), 0, 0, 0, state) & 0xFFF
    for phi in range(1, 600):
        assert value == word(one, lay.pw_lo, lay.pw_hi) & 0xFFF, phi
        if pw.name == "pong":
            assert tuple(state) == (one._rd(lay.pw_dir), one._rd(lay.pw_substep)), phi
        value = fire(value, phi, 0, 0, state) & 0xFFF
        ref._pulse(0, rec, flags)


def record_n(one, rng, n: int, fields: dict) -> int:
    """Instrument ``n``'s record, random but for ``fields``; its address."""
    at = one.lay.instr + n * REC
    for k in range(REC):
        one.ref._wr(at + k, fields.get(k, rng.randrange(256)))
    return at


def table(one, note: int) -> None:
    """The voice's working frequency, as the player's table reads at ``note``."""
    value = frequency(note, CLOCK[1])
    one.ref._wr(one.lay.freq_lo_w, value & 0xFF)
    one.ref._wr(one.lay.freq_hi_w, value >> 8)


@pytest.mark.parametrize("seed", range(40))
def test_vib_is_the_arpeggio(one, seed):  # pylint: disable=too-many-locals
    """``vib`` from the sounding note; ``vibs`` on another instrument's step and speed, and
    ``vibl`` from a legato note at phase 0, each with the delay left."""
    rng = random.Random(seed)

    def speed():
        return rng.choice((0, 1, 0x08, 0x0F, 0x1A, rng.randrange(256)))

    def step():
        return rng.choice((0, 1, 0x80, rng.randrange(256)))

    recs = [record_n(one, rng, n, {5: speed(), 6: step()}) for n in (1, 2)]
    ref, lay, note = one.ref, one.lay, rng.randrange(96)
    voice = Voice(1, trig=1)
    table(one, note)
    ref._trigger(0, recs[0], 0)
    ref._wr(lay.arp_substep, 0)
    ref._wr(lay.arp_phase, 0)
    plan = sorted(rng.sample(range(1, 200), 2))
    fire, state, x8 = routine(one.arpeggio(voice), "freq"), bytearray(6), REC
    phi, value = 0, rng.randrange(65536)
    for tick in range(300):
        if tick == plan[0]:
            voice.ins, x8, phi = 2, 2 * REC, 0
            fire = routine(one.arpeggio(voice, "vibs"), "freq")
        if tick == plan[1]:
            note, phi = rng.randrange(96), 0
            table(one, note)
            ref._wr(lay.arp_substep, 0)
            ref._wr(lay.arp_phase, 0)
            fire = routine(one.arpeggio(voice, "vibl"), "freq")
        value = fire(value, phi, note, 0, state) & 0xFFFF
        phi += 1
        ref._arp(0, x8)
        assert value == word(one, lay.freq_lo_w, lay.freq_hi_w), tick
        cells = (lay.porta_ctr, lay.arp_substep, lay.arp_phase)
        assert tuple(state[:3]) == tuple(one._rd(c) for c in cells), tick


@pytest.mark.parametrize("seed", range(30))
def test_a_selected_instruments_pulse_step_sweeps_on(one, seed):
    """``pstep`` and ``pongs``: the sweep of the trigger's mode at another instrument's step."""
    rng = random.Random(seed)
    flags = rng.choice((0x40, 0x20))
    recs = [record_n(one, rng, n, {7: flags}) for n in (1, 2)]
    pw = one.ins[one.instrument(1)].get("pw")
    ref, lay = one.ref, one.lay
    ref._trigger(0, recs[0], 0)
    fire, state = routine(pw, "pw"), bytearray(2)
    value, switch, rec = fire(0, 0, 0, 0, state) & 0xFFF, rng.randrange(1, 100), recs[0]
    for phi in range(1, 300):
        if phi == switch:
            voice = Voice(2, flags=flags, live=True, pws=-1)
            fire, rec = routine(one._select(voice, 2)["pw"], "pw"), recs[1]
        ref._pulse(0, rec, flags)
        value = fire(value, phi, 0, 0, state) & 0xFFF
        assert value == word(one, lay.pw_lo, lay.pw_hi) & 0xFFF, phi


@pytest.mark.parametrize("seed", range(30))
def test_sweep_is_the_owners_trigger_then_its_cutoff_sweep(one, seed):
    rng = random.Random(seed)
    ref, lay = one.ref, one.lay
    ref._wr(lay.trigger_owner, 0)
    ref._wr(lay.trigger_acc, rng.randrange(256))
    ref._wr(lay.trigger_ctr, rng.choice((1, 2, 0xFF, rng.randrange(1, 256))))
    ref._wr(lay.sweep_step, rng.randrange(256))
    cells = (lay.trigger_acc, lay.trigger_ctr, lay.sweep_step)
    call = one.call("csweep", **dict(zip("ucs", (one._rd(c) for c in cells))))
    rec = record(one, rng, {7: 0})
    ref._trigger(0, rec, 0)
    fire, state = routine(call, "cut"), bytearray(rng.randrange(256) for _ in range(2))
    value = fire(0, 0, 0, 0, state)
    for phi in range(1, 300):
        assert tuple(state) == (one._rd(lay.sweep_acc), one._rd(lay.sweep_ctr)), phi
        assert value >> 3 == ref.snapshot()[22], phi
        value = fire(value, phi, 0, 0, state) & 0x7FF
        ref._sustain(0, rec, 0x40)


@pytest.mark.parametrize("seed", range(40))
def test_vslide_is_sid_finals_word(seed):
    """``_sid_final``'s slid: the event note's frequency plus the step each tick, an odd step's
    every other tick sounding the sounding note's frequency."""
    rng = random.Random(seed)
    lo, hi, event = rng.randrange(256), rng.randrange(256), rng.randrange(96)
    machine = Machine({}, {"freq": tuple(STATE["freq"].items())}, CLOCK[1])
    fire = machine.compile("vslide", code.read(CODE["vslide"]), {"l": lo, "h": hi}, "freq")
    state, slid, toggle = bytearray(sum(STATE["freq"].values())), frequency(event, CLOCK[1]), 0xFF
    for phi in range(64):
        note, toggled = rng.randrange(96), 0
        if lo & 1:
            toggled = toggle = toggle ^ 0xFF
        if not toggled:
            slid = (slid + (hi << 8 | lo)) & 0xFFFF
        want = frequency(note, CLOCK[1]) if toggled else slid
        assert fire(rng.randrange(0x10000), phi, note, event, state) == want, phi


def test_a_poke_writes_the_accumulator_after_the_sweep_steps():
    """A program row's filter byte: the sweep steps on its tick, then continues from the byte."""
    ins = Instrument({"ad": 9, "sr": 9, "pw": 2048, "freq": NONE})
    mods = [
        (signature(t, code.params(c)), Code(c))
        for t, c in ((t, code.read(CODE[t])) for t in ("csweep", "poke"))
    ]
    rows = [Row({"ctrl": 0x41}) for _ in range(5)]
    rows[2] = Row({"ctrl": 0x41, "post.cut": Once(Call("poke", (0x10,)))})
    head = Header(
        (("title", "t"),),
        clock=CLOCK,
        tempo=1,
        slots={"cut": [Slot("cut", hold=("row.ad",), reset=("note",)), Slot("post")]},
        state={"cut": [("acc", 1), ("ctr", 1)]},
    )
    note = Event("C4", "i1", {"cut": Call("csweep", (0x20, 6, 2)), "run": "c1"}, steps=6)
    doc = Document(
        head,
        mods=mods,
        defs=[
            Section("ins", [("i1", ins)]),
            Section("ctrl", [("c1", Program(rows))]),
            Section("phrase", [("p0", [Event("-", steps=6)]), ("p1", [note])]),
        ],
        init=[Event("-", commands={"vol": 15}), Event("-"), Event("-")],
        rows=[SongRow((Play("p1"), Play("p0"), Play("p0")))],
        loop=0,
    )
    cut = [int(x) for x in play(printed(doc), ticks=7)[:, 22]]
    assert cut == [0, 34, 36, 18, 20, 22, 22]
