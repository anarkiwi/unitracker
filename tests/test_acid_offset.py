"""defMON's ACID offset cp: a sidTAB row that sets it plays as its program's cut expression."""

import numpy as np
import pytest
import z3
from pydefmon import DefmonPlayer
from pysidtracker import SidImage

from unitracker import defmon, songio
from unitracker.modfit.form import Form
from unitracker.songio import library
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


def replay(z: dict, wrap: bool = True) -> tuple:
    """``(accumulator, $D416 before the doubling)`` after the replay's $10B5-$10D4 code, in its
    8-bit add-with-carry, from the folded accumulator ``acc`` and the offset ``cp``; without
    ``wrap`` the cutoff byte is the unwrapped sum."""
    acc = (z["acc"] - 256 * z["cp"]) & 0xFFFF
    step = [z3.If(z["sub"] == 1, 0xFF - b, b) for b in (z["astep"] & 0xFF, z["astep"] >> 8)]
    low = (acc & 0xFF) + step[0]
    high = (acc >> 8) + step[1] + (low >> 8)
    stored = z3.If(high & 0x80 == 0, high & 0xFF, z["floor"])
    cut = stored + z["cp"] + (high >> 8)
    cut = cut & 0xFF if wrap else cut
    out = z3.If(z3.Or(z3.UGE(cut, 0x80), z3.ULT(cut, z["floor"])), z["floor"], cut)
    return stored * 256 + (low & 0xFF), out


def refuted(actions: list, claim) -> list:
    """The paths of defMON's ``actions`` on which ``claim(cells, after)`` fails for some
    accumulator, step, direction, offset, floor and row offset."""
    pr = library("defmon")
    pr["constants"] = {"dbl": 0}
    form = Form(pr)
    names = ("acc", "astep", "sub", "cp", "floor", ("fld", "cp", "row"))
    out = []
    for conds, state in form.paths(actions, (), {}):
        smt = form.solver()
        z = {n if isinstance(n, str) else "row.cp": smt.term(n) for n in names}
        smt.solver.add(z3.ULE(z["floor"], 0xFF), z3.ULE(z["row.cp"], 0xFF))
        smt.solver.add(*(smt.cond(c) for c in conds))
        after = {k: smt.term(v) for k, v in state.items()}
        after["ghost"] = smt.term(form.expr(pr["ghost"]["global"][0x16], state)) & 0xFF
        smt.solver.add(z3.Not(claim(z, after)))
        if smt.solver.check() != z3.unsat:
            out.append(smt.solver.model())
    return out


def test_the_folded_step_is_the_replays_add_with_carry_for_every_state():
    def claim(z, after, wrap=True):
        stored, out = replay(z, wrap)
        return z3.And((after["acc"] - 256 * z["cp"]) & 0xFFFF == stored, after["ghost"] == out)

    steps = [{"call": "acid_step"}, {"call": "acid_out"}]
    assert not refuted(steps, claim)
    assert refuted(steps, lambda z, after: claim(z, after, wrap=False))


def test_an_offset_row_keeps_the_accumulator_and_its_carry_for_every_state():
    do = library("defmon")["record_types"]["sidtab"]["do"]
    [row] = [a for a in do if a.get("if") == "row.cp >= 0"]

    def claim(z, after, shift=0):
        before = (z["acc"] - 256 * z["cp"] + shift) & 0xFFFF
        moved = (after["acc"] - 256 * after["cp"]) & 0xFFFF
        return z3.And(moved == before, after["acc"] >> 16 == z["acc"] >> 16)

    assert not refuted([row], claim)
    assert refuted([row], lambda z, after: claim(z, after, 256))
