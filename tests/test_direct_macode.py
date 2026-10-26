"""Music Assembler's code modulators, run by unitracker-player's 6502, fire as pymusicassembler's
playroutine functions, firing by firing over random instruments and states."""

import random

import pymusicassembler
import pytest
from unitracker_grammar import code
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
from unitracker.direct.musicassembler import CLOCK, Transcriber

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


@pytest.mark.parametrize("seed", range(40))
def test_vib_is_the_arpeggio(one, seed):
    rng = random.Random(seed)
    speed = rng.choice((0, 1, 0x08, 0x0F, 0x1A, rng.randrange(256)))
    rec = record(one, rng, {5: speed, 6: rng.choice((0, 1, 0x80, rng.randrange(256)))})
    call = one.arpeggio(1)
    ref, lay = one.ref, one.lay
    value = rng.choice((rng.randrange(65536), rng.randrange(0x200), 0xFF00 | rng.randrange(256)))
    ref._wr(lay.freq_lo_w, value & 0xFF)
    ref._wr(lay.freq_hi_w, value >> 8)
    ref._trigger(0, rec, 0)
    ref._wr(lay.arp_substep, 0)
    ref._wr(lay.arp_phase, 0)
    fire, state = routine(call, "freq"), bytearray(rng.randrange(256) for _ in range(3))
    for phi in range(300):
        value = fire(value, phi, 0, 0, state) & 0xFFFF
        ref._arp(0, REC)
        assert value == word(one, lay.freq_lo_w, lay.freq_hi_w), phi
        cells = (lay.porta_ctr, lay.arp_substep, lay.arp_phase)
        assert tuple(state) == tuple(one._rd(c) for c in cells), phi


@pytest.mark.parametrize("seed", range(30))
def test_sweep_is_the_owners_trigger_then_its_cutoff_sweep(one, seed):
    rng = random.Random(seed)
    ref, lay = one.ref, one.lay
    ref._wr(lay.trigger_owner, 0)
    ref._wr(lay.trigger_acc, rng.randrange(256))
    ref._wr(lay.trigger_ctr, rng.choice((1, 2, 0xFF, rng.randrange(1, 256))))
    ref._wr(lay.sweep_step, rng.randrange(256))
    call = one.init()[0].commands[-1][1]
    rec = record(one, rng, {7: 0})
    ref._trigger(0, rec, 0)
    fire, state = routine(call, "cut"), bytearray(rng.randrange(256) for _ in range(2))
    value = fire(0, 0, 0, 0, state)
    for phi in range(1, 300):
        assert tuple(state) == (one._rd(lay.sweep_acc), one._rd(lay.sweep_ctr)), phi
        assert value >> 3 == ref.snapshot()[22], phi
        value = fire(value, phi, 0, 0, state) & 0x7FF
        ref._sustain(0, rec, 0x40)


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
