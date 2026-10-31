"""A play routine is a plain ghost flush exactly when every SID store writes its ghost byte."""

from types import SimpleNamespace

import pytest
from pysidtracker.playspec.tv import TVError

from unitracker.lift import gt2wrap

PLAY, PLAYER, SINK = 0x1000, 0x2000, 0x40


def routine(code: bytes) -> tuple:
    """``(memory, wrap)`` of ``code`` at ``PLAY`` calling a player that returns at once."""
    memory = bytearray(0x10000)
    memory[PLAY : PLAY + len(code)] = code
    memory[PLAYER] = 0x60
    return bytes(memory), SimpleNamespace(play=PLAY, entries=frozenset({PLAYER}), sink=SINK)


JSR = bytes([0x20, PLAYER & 0xFF, PLAYER >> 8])
FLUSH = bytes([0xA2, 0x18, 0xB5, SINK, 0x9D, 0x00, 0xD4, 0xCA, 0x10, 0xF8, 0x60])


def test_a_loop_copying_the_ghost_block_is_a_plain_flush():
    assert not gt2wrap.own_writes(*routine(JSR + FLUSH))


def test_a_flush_before_the_player_call_is_plain_too():
    assert not gt2wrap.own_writes(*routine(FLUSH[:-1] + JSR + b"\x60"))


def test_a_constant_store_is_the_registers_own():
    code = bytes([0xA9, 0x0F, 0x8D, 0x18, 0xD4]) + JSR + FLUSH
    assert gt2wrap.own_writes(*routine(code)) == (24,)


def test_a_xor_with_a_cell_the_routine_rewrites_is_the_registers_own():
    # lda ghost+24; eor #$00; sta $d418; lda #$00; sta <the eor operand>
    eor = PLAY + 3
    code = bytes([0xA5, SINK + 24, 0x49, 0x00, 0x8D, 0x18, 0xD4])
    code += bytes([0xA9, 0x00, 0x8D, eor & 0xFF, eor >> 8, 0x60])
    assert gt2wrap.own_writes(*routine(code)) == (24,)


def test_an_eor_of_zero_is_the_ghost_byte_and_of_one_is_not():
    code = bytes([0xA5, SINK + 24, 0x49, 0x00, 0x8D, 0x18, 0xD4, 0x60])
    assert not gt2wrap.own_writes(*routine(code))
    code = bytes([0xA5, SINK + 24, 0x49, 0x01, 0x8D, 0x18, 0xD4, 0x60])
    assert gt2wrap.own_writes(*routine(code)) == (24,)


def test_a_store_into_an_opcode_is_outside_what_is_checked():
    code = bytes([0xA9, 0x60, 0x8D, PLAY & 0xFF, PLAY >> 8, 0x60])
    with pytest.raises(TVError, match="opcode"):
        gt2wrap.own_writes(*routine(code))


def test_a_store_through_a_computed_address_that_reaches_the_sid_is_refused():
    code = bytes([0xA5, SINK, 0xAA, 0xA9, 0x01, 0x9D, 0x00, 0xD4, 0x60])
    with pytest.raises(TVError, match="computed address"):
        gt2wrap.own_writes(*routine(code))
