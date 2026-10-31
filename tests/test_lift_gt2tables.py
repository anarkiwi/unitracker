"""Landings on table jump rows are stated as the ordinary rows the executors run."""

import pytest
from pygoattracker import typed as T
from pygoattracker.model import Orderlist, PlayPattern, Subtune

from unitracker.lift import gt2tables


def song(pulse=(), wave=(), filt=(), simple=False, **ins) -> T.TypedSong:
    """A one-pattern typed song whose instrument 1 has the given pointers."""
    return T.TypedSong(
        subtunes=(Subtune([Orderlist([PlayPattern(0)], 0)] * 3),),
        patterns=((T.Row(T.Note(24), 1, None, 1),),),
        instruments=(T.Instrument(**ins),),
        pulsetable=tuple(pulse),
        wavetable=tuple(wave),
        filtertable=tuple(filt),
        simplepulse=simple,
    )


PULSE = (T.PulseSet(0x800), T.Jump(3), T.PulseSet(0x900), T.Jump(2))


def test_a_jump_onto_a_jump_row_lands_on_its_ordinary_row():
    out = gt2tables.landings(song(PULSE, pulse_ptr=1))
    assert out.pulsetable[:4] == (PULSE[0], PULSE[1], PULSE[2], T.Jump(5))
    assert out.pulsetable[4:] == (T.PulseSet(0xF00 | 3), T.Jump(3))


def test_a_pointer_set_onto_a_jump_row_lands_on_its_ordinary_row():
    out = gt2tables.landings(song(PULSE, pulse_ptr=2))
    assert out.instruments[0].pulse_ptr == 5
    assert out.pulsetable[4:] == (T.PulseSet(0xF00 | 3), T.Jump(3))


def test_the_row_after_a_landing_is_followed_when_it_is_a_jump():
    table = (T.PulseSet(0x800), T.Jump(3), T.Jump(1), T.PulseSet(0x900), T.Jump(4))
    out = gt2tables.landings(song(table, pulse_ptr=2))
    at = out.instruments[0].pulse_ptr
    assert out.pulsetable[at - 1 : at + 1] == (T.PulseSet(0xF03), T.Jump(1))


def test_a_simple_pulse_table_lands_on_a_simple_set():
    table = (T.SimplePulseSet(0x80), T.Jump(1), T.SimplePulseSet(3), T.Jump(3))
    out = gt2tables.landings(song(table, simple=True, pulse_ptr=2))
    assert out.pulsetable[4] == T.SimplePulseSet(1)


def test_a_table_no_pointer_reaches_is_left_as_it_is():
    out = gt2tables.landings(song(PULSE))
    assert out.pulsetable == PULSE


def test_a_landing_the_typed_row_cannot_state_names_the_row_it_runs_as():
    with pytest.raises(gt2tables.Landing, match="tempo command F"):
        gt2tables.landings(song(wave=(T.Jump(1),), wave_ptr=1))
    with pytest.raises(gt2tables.Landing, match="every mode bit"):
        gt2tables.landings(song(filt=(T.Jump(1),), filter_ptr=1))


def test_a_landing_on_the_last_row_runs_on_past_the_table():
    with pytest.raises(gt2tables.Landing, match="last row"):
        gt2tables.landings(song((T.PulseSet(1), T.Jump(2)), pulse_ptr=2))


def test_a_table_running_on_past_its_end_beside_a_landing_is_refused():
    table = (T.PulseSet(1), T.Jump(3), T.PulseSet(2), T.PulseSet(3))
    with pytest.raises(gt2tables.Landing, match="runs on past"):
        gt2tables.landings(song(table, pulse_ptr=2))
