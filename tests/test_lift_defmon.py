"""Lifting the defMON replay: build check, state, sidTAB rows and song timing."""

import pytest

from unitracker.lift import defmon as dm


def build_mem():
    mem = bytearray(0x10000)
    for addr, pattern in dm.ANCHORS:
        for i, tok in enumerate(pattern.split()):
            mem[addr + i] = 0 if tok == "??" else int(tok, 16)
    for v, off in enumerate(dm.VOICE_OFFSETS):
        mem[0x101F + off], mem[0x1020 + off], mem[0x1021 + off] = v, 1 << v, 0xFF ^ (1 << v)
        mem[0x12E0 + off] = mem[0x1373 + off] = 0xFF
    mem[0x10B8] = mem[0x10BF] = 0xE9
    mem[0x10D4] = 0xEA
    mem[0x10D9] = 0x80
    return mem


def test_check_build():
    mem = build_mem()
    dm.check_build(mem)
    mem[0x16B5] ^= 1
    with pytest.raises(dm.LiftError, match=r"\$16B5"):
        dm.check_build(mem)


def test_voice_init():
    mem = build_mem()
    mem[0x1023 + 0x31], mem[0x1025 + 0x31], mem[0x12ED + 0x62] = 0x40, 0x08, 60
    recs = dm.voice_init(mem)
    assert recs[1]["pw"] == 0x0840 and recs[2]["tbase"] == 60
    assert set(recs[0]) == {"pw", "flo", "fhi", "sacc", "tbase"}
    for cell, addr in (("note", 0x137F), ("cntB", 0x1373)):
        mem[addr] ^= 1
        with pytest.raises(dm.LiftError, match=f"init leaves voice 0's {cell} at"):
            dm.voice_init(mem)
        mem[addr] ^= 1
    mem[0x101F + 0x62] = 7
    with pytest.raises(dm.LiftError, match="voice 2"):
        dm.voice_init(mem)


def test_global_init_and_model():
    mem = build_mem()
    assert dm.global_init(mem) == {"sub": 1}
    mem[0x10B6], mem[0x10BE] = 0x34, 0x12
    with pytest.raises(dm.LiftError, match=r"init leaves acc at \$1234, not 0"):
        dm.global_init(mem)
    mem[0x10B6] = mem[0x10BE] = 0
    mem[0x10B8] = mem[0x10BF] = 0x69
    assert dm.global_init(mem)["sub"] == 0
    mem[0x10CA] = 0x07
    with pytest.raises(dm.LiftError, match=r"ACID offset cp at \$07"):
        dm.global_init(mem)
    mem[0x10BF] = 0xE9
    with pytest.raises(dm.LiftError, match="ACID opcodes"):
        dm.global_init(mem)
    mem[0x10CE] = 2
    assert dm.model_constants(mem) == {"floor": 2, "dbl": 0}
    mem[0x10D4] = 0x0A
    assert dm.model_constants(mem)["dbl"] == 1
    mem[0x10D4] = 0x00
    with pytest.raises(dm.LiftError, match="opcode"):
        dm.model_constants(mem)


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


def test_sidtab_records_follow_rows_and_jumps():
    mem = build_mem()
    for row, addr, dl in ((5, 0x4200, 2), (6, 0x4210, 0x80), (9, 0x4220, 0)):
        mem[dm.SIDTAB_LO + row], mem[dm.SIDTAB_HI + row] = addr & 0xFF, addr >> 8
        mem[dm.SIDTAB_DL + row] = dl
        body(mem, addr, 0x40, 0x11, 0x00)
    mem[dm.SIDTAB_LO + 10] = 5
    recs = dm.sidtab_records(mem, {9})
    assert set(recs) == {6, 9, 10}
    assert recs[10] == {"dl": 2, "next": 6, "wgh": 0x11}
    assert recs[6]["dl"] == 0x80 and 7 not in recs
    assert dm.resolve_row(mem, 10) == 5
    with pytest.raises(dm.LiftError, match="no body"):
        dm.sidtab_records(mem, {20})


def test_decode_event():
    mem = build_mem()
    body(mem, 0x5000, 0x73, 4, 5, 60)
    assert dm.decode_event(mem, 0x5000) == (0x73, 4, 5, 60, 4)
    body(mem, 0x5010, 0x22, 9)
    assert dm.decode_event(mem, 0x5010) == (0x22, None, 9, None, 2)


def test_arranger_step():
    mem = build_mem()
    a1, a2, a3 = dm.ARRANGER
    mem[a1 + 0], mem[a2 + 0], mem[a3 + 0] = 1, 2, 3
    assert dm.arranger_step(mem, 0, 0, 0) == ((1, 2, 3), 1, 0, 0)
    mem[a1 + 4], mem[a2 + 4], mem[a3 + 4] = 0xFF, 0, 0
    assert dm.arranger_step(mem, 4, 0, 0)[1:] == (1, 0, 0)
    mem[a3 + 4] = 2
    assert dm.arranger_step(mem, 4, 0, 0)[1:] == (1, 4, 2)
    assert dm.arranger_step(mem, 4, 4, 2)[1:] == (1, 4, 1)
    pats, y, jr, jc = dm.arranger_step(mem, 4, 4, 1)
    assert (y, jr, jc) == (6, 0, 0) and pats == (mem[a1 + 5], mem[a2 + 5], mem[a3 + 5])


def put_pattern(mem, num, addr, *events):
    mem[dm.PATTERN_LO + num], mem[dm.PATTERN_HI + num] = addr & 0xFF, addr >> 8
    flat = [b for event in events for b in event]
    mem[addr : addr + len(flat)] = bytes(flat)


def test_simulate_step_times_events():
    mem = build_mem()
    put_pattern(mem, 1, 0x6000, (0x51, 7, 40), (0x02,), (0x80,))
    put_pattern(mem, 2, 0x6100, (0x20 | 0x0F, 3))
    put_pattern(mem, 3, 0x6200, (0x00,))
    applied, length, e = dm.simulate_step(mem, (1, 2, 3), 0)
    assert [t for t, *_ in applied[0]] == [1, 4, 8]
    assert applied[0][0] == (1, 0x51, 7, None, 40)
    assert [t for t, *_ in applied[1]] == [1]
    assert [t for t, *_ in applied[2]] == [1, 3, 5, 7]
    assert (length, e) == (9, 0)
    mem = build_mem()
    put_pattern(mem, 1, 0x6000, (0x00,))
    with pytest.raises(dm.LiftError, match="ALT"):
        dm.simulate_step(mem, (1, 1, 1), 0)


def test_phrase_events():
    assert dm.phrase_events([], 5) == [{"steps": 5}]
    applied = [(2, 0x51, 7, None, 40), (4, 0x00, None, None, None), (6, 0x20, None, 3, None)]
    assert dm.phrase_events(applied, 9) == [
        {"steps": 2},
        {"note": 40, "instrument": 8, "steps": 4},
        {"command": [1, 3], "steps": 3},
    ]
    assert dm.phrase_events([(0, 0x10, None, None, 1)], 1) == [{"note": 1}]


def test_compress_keeps_the_loop_boundary():
    assert dm._compress(["a", "a", "a", "b"], 2) == {  # pylint: disable=protected-access
        "plays": [{"phrase": "a", "repeat": 2}, {"phrase": "a"}, {"phrase": "b"}],
        "loop": 1,
    }


def test_song_steps_loop_and_errors(monkeypatch):
    mem = build_mem()
    put_pattern(mem, 1, 0x6000, (0x80,))
    a1, a2, a3 = dm.ARRANGER
    mem[a1 + 0], mem[a2 + 0], mem[a3 + 0] = 1, 1, 1
    mem[a1 + 1], mem[a2 + 1], mem[a3 + 1] = 0xFF, 0, 0
    steps, loop = dm.song_steps(mem, 0)
    assert len(steps) == 2 and loop == 1 and steps[0][1] == 2
    monkeypatch.setattr(dm, "MAX_SONG_STEPS", 1)
    with pytest.raises(dm.LiftError, match="does not loop"):
        dm.song_steps(mem, 0)
    mem[0x10D9] = 0
    with pytest.raises(dm.LiftError, match="arm"):
        dm.song_steps(mem, 0)


def test_the_reference_lift_holds_the_replay_state(references):
    ref = references["crosseyed"]
    assert dm.lift(ref.raw, expressions=False)["meta"]["source"] == "defmon subtune 0"
    song = ref.ir()
    assert set(song["tables"]) == {name for name, _, _ in dm.TABLES}
    assert set(song["records"]) == {"vinit", "ginit", "sidtab"}
    assert song["playroutine"]["name"] == "defmon"
    assert song["clock"]["cycles_per_tick"] == 19656
    assert [t["loop"] for t in song["tracks"]] == [1, 1, 1]
