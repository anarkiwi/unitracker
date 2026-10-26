"""Locating defMON replay builds and reading their sidTAB rows."""

import pytest
from pydefmon import DefmonPlayer

from unitracker.direct import dmimage as dm


def build_mem(*names):
    """A memory holding each region's code at its $1000 build address, each varying part's build
    named in ``names``, else its $1000 build's."""
    mem = bytearray(0x10000)
    for addr, _name, *parts in dm.REGIONS:
        code = " ".join(
            (
                p
                if isinstance(p, str)
                else next((p[n] for n in names if n in p), next(iter(p.values())))
            )
            for p in parts
        )
        for i, tok in enumerate(code.split()):
            mem[addr + i] = 0 if tok == "??" else int(tok.split("|")[-1], 16)
    return mem


def test_locate_finds_each_build():
    at, variants = dm.locate(build_mem())
    assert variants == {"counted", "absolute"}
    assert all(at[a] == a for a in (0x137F, 0x159C, 0x1800, 0x10D4, 0x14EC))
    at, variants = dm.locate(build_mem("plain", "relative"))
    assert variants == {"plain", "relative"} and 0x14EC not in at
    assert at[0x1146] == 0x1146 - 33 and at[0x137F] == 0x137F
    mem = build_mem()
    mem[0x16B4] ^= 1
    with pytest.raises(dm.LiftError, match=r"no sidTAB walk code \(\$16B0"):
        dm.locate(mem)
    mem = build_mem()
    mem[mem.index(bytes([0x8C, 0xEF, 0x12]), 0x12DF) + 1] = 0xF0
    with pytest.raises(dm.LiftError, match=r"places \$12EF of the \$1000 build at both"):
        dm.locate(mem)


@pytest.mark.parametrize("name", ["uncut", "fixed", "direct"])
def test_region_matches_a_variant_build(name):
    mem = build_mem(name)
    found = [
        addr
        for addr, _name, *parts in dm.REGIONS
        for names, code, _ in dm.region_builds(parts)
        if name in names
        and any(m.addr == addr for m in dm.find_code_all(mem, dm.code_pattern(code)[0]))
    ]
    assert found == [{"uncut": 0x10A9, "fixed": 0x117F, "direct": 0x10A9}[name]]


def body(mem, addr, *data):
    mem[addr : addr + len(data)] = bytes(data)
    return addr


@pytest.mark.parametrize(
    "data,fields",
    [
        (
            [0xFE, 0x41, 0x01, 0x0A, 0xF8, 0x05, 0x83, 0x4C, 0xF8, 0x91, 0x9F, 0x2F, 0x07, 0x10]
            + [0x81],
            dict(
                zip(
                    "wgh wgl ad sr tr af pw ps re fv cp acid".split(),
                    (0x41, 0x01, 0x0A, 0xF8, 0x05, 0x83, 0x4C, 0x91, 0x9F, 0x2F, 0x07, 0x8110),
                )
            ),
        ),
        ([0x00, 0x00], {}),
        ([0x08, 0x85, 0x40, 0x00], {"tr": 0x85, "re": 0}),
        ([0x00, 0x48, 0x51, 0x34, 0x12], {"re": 0x51, "acid": 0x1234}),
    ],
    ids=["every column", "empty", "transpose and resonance", "resonance and acid"],
)
def test_row_fields(data, fields):
    mem = build_mem()
    assert dm.row_fields(mem, body(mem, 0x4000, *data)) == fields


@pytest.mark.parametrize(
    "tune,variants,cells",
    [
        ("tractorbeam", {"counted", "absolute"}, {0x137F: 0x137F}),
        ("ngc1277", {"counted", "absolute"}, {0x137F: 0x077F, 0x00FB: 0x02, 0x00FC: 0x03}),
    ],
)
def test_locate_reads_each_build_at_its_places(request, tune, variants, cells):
    raw = request.getfixturevalue(tune).read_bytes()
    at, found = dm.locate(DefmonPlayer(raw)._mem)  # pylint: disable=protected-access
    assert found == variants and {a: at[a] for a in cells} == cells
