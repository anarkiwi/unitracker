"""defMON's ACID offset cp: a sidTAB row that sets it plays as its program's cut expression."""

import numpy as np
import pytest
from pydefmon import DefmonPlayer
from pysidtracker import SidImage

from unitracker import defmon, songio
from unitracker.vm import SongPlayer

TICKS = 95 + 2 * 1152 + 100


def row_body(fields: dict) -> bytes:
    """The sidTAB row body the $16B0 bitmap walk reads as ``fields``."""
    out, half, bitmap, values = bytearray(), 0, 0, bytearray()
    for col_half, bit, size, field in defmon.COLUMNS:
        if col_half != half:
            out += bytes([bitmap]) + values
            half, bitmap, values = col_half, 0, bytearray()
        if field in fields:
            bitmap |= bit
            values += int(fields[field]).to_bytes(size, "little")
    return bytes(out + bytes([bitmap]) + values)


def patched(raw: bytes, columns: dict) -> bytes:
    """``raw`` with each sidTAB row of ``columns`` given those extra columns, its body moved to
    the end of the image."""
    mem = SidImage.from_bytes(raw).mem
    data = int.from_bytes(raw[6:8], "big") + 2
    base = int.from_bytes(raw[data - 2 : data], "little")
    out = bytearray(raw)
    for row, extra in columns.items():
        eff = defmon.resolve_row(mem, row)
        addr = mem[defmon.SIDTAB_LO + eff] | (mem[defmon.SIDTAB_HI + eff] << 8)
        new = base + len(out) - data
        out += row_body({**defmon.row_fields(mem, addr), **extra})
        out[data + defmon.SIDTAB_LO + eff - base] = new & 0xFF
        out[data + defmon.SIDTAB_HI + eff - base] = new >> 8
    return bytes(out)


def test_row_body_reads_back(crosseyed):
    raw = crosseyed.read_bytes()
    mem = SidImage.from_bytes(raw).mem
    fields = {"wgh": 0x41, "ad": 9, "af": 0x80, "ps": 3, "cp": 0xD0, "acid": 0x4000}
    mem[0x3000:0x3010] = row_body(fields)
    assert defmon.row_fields(mem, 0x3000) == fields
    got = SidImage.from_bytes(patched(raw, {16: {"cp": 7}})).mem
    assert defmon.sidtab_records(got, {16})[16]["cp"] == 7


@pytest.fixture(name="offset", scope="module")
def offset_fixture(crosseyed) -> tuple:
    """Crosseyed with offsets on its two ACID programs, one wrapping the cutoff byte past $FF."""
    raw = patched(crosseyed.read_bytes(), {16: {"cp": 0xD0}, 13: {"cp": 0x05}})
    return raw, np.asarray(DefmonPlayer(raw).render_grid(TICKS), dtype=np.uint8)


def test_the_offset_changes_the_cutoff(crosseyed, offset):
    plain = np.asarray(DefmonPlayer(crosseyed.read_bytes()).render_grid(TICKS), dtype=np.uint8)
    cutoff = offset[1][:, 0x16]
    assert not np.array_equal(plain[:, 0x16], cutoff)
    assert len(np.unique(cutoff)) > 20


def test_the_transcription_plays_the_offset(offset):
    raw, native = offset
    ir = defmon.lift(raw, expressions=False)
    assert {r.get("cp") for r in ir["records"]["sidtab"].values()} == {None, 5, 0xD0}
    assert SongPlayer(ir).mismatch(native) is None


def test_the_offset_is_folded_into_the_cut_expressions(offset):
    raw, native = offset
    ir = defmon.lift(raw, expressions=False)
    text = songio.dumps(defmon.lift(raw))
    song = songio.loads(text)
    assert songio.dumps(song) == text
    assert "cp:" not in text and "acid:" not in text
    assert np.array_equal(SongPlayer(song).render_grid(TICKS), SongPlayer(ir).render_grid(TICKS))
    assert SongPlayer(song).mismatch(native) is None
