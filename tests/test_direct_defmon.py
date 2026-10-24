"""The direct defMON transcription plays the replay's registers, and refuses what it cannot."""

import pytest
from unitracker_grammar import parse

from unitracker.direct import cli
from unitracker.direct import defmon as dm

TICKS = 3000


@pytest.fixture(name="cross", scope="module")
def fixture_cross(crosseyed):
    """A transcriber of Crosseyed, its song sequenced."""
    tune = dm.Transcriber(crosseyed.read_bytes())
    tune.song()
    return tune


@pytest.mark.parametrize("name", ["crosseyed", "tractorbeam"])
def test_the_document_plays_the_replay(request, name):
    cells, text = dm.compare(request.getfixturevalue(name).read_bytes(), frames=TICKS)
    parse(text)
    assert not cells.size, cli.summary(cells, 3)


def test_helpers():
    assert [dm.note_name(k) for k in (0, 48, 127)] == ["C0", "C4", "G10"]
    assert dm.route(0) == [("route", "'-'")]
    assert dm.route(0xF8) == [("res", 15), ("route", "+ 8")]
    assert dm.route(0xF7) == [("res", 15), ("route", 7)]
    assert str(dm.pitch(0)) == "pitch()"
    assert str(dm.pitch(0xD8)) == "slide(216)"
    assert str(dm.pitch(3)) == "por(3)"
    assert str(dm.acid({"cp": 4})) == "acid(1,4,0)"
    assert str(dm.acid({"acid": 0x4000})) == "acid(2,0,16384)"
    assert str(dm.acid({"acid": 0xC200})) == "acid(8,0,512)"
    assert str(dm.acid({"acid": 0x8A00})) == "acid(4,0,2560)"
    assert dm.text("Crosseyed") == "Crosseyed"
    assert dm.text("it's [x]") == "'it''s [x]'"
    assert dm.event_at(bytes([0x70, 3, 4, 48]), 0) == (3, 4, 48)
    assert dm.event_at(bytes([0x90, 48]), 0) == (None, None, 48)
    assert cli.summary(dm.np.zeros((0, 4))) == "exact"


def test_a_frozen_program_ends_and_a_cycling_one_loops(cross):
    rows, loop = cross.joint((2, 0, 0, 0xFF))
    assert loop is None and [h for h, _ in rows] == [2, 1]
    rows, loop = cross.joint((16, 0, 0, 0xFF))
    assert loop == 2 and [h for h, _ in rows] == [1, 1, 1, 1]


@pytest.mark.parametrize(
    "addr, value, message",
    [
        (0x10BF, 0x69, "ACID opcodes"),
        (0x10D4, 0x60, "cutoff output"),
        (0x101F, 9, "detune and routing"),
        (0x1019, 1, "slide accumulator"),
    ],
)
def test_init_state_it_cannot_write_is_refused(cross, addr, value, message):
    keep = cross.mem[addr]
    cross.mem[addr] = value
    try:
        with pytest.raises(dm.TranscribeError, match=message):
            cross.check()
    finally:
        cross.mem[addr] = keep


def test_a_row_without_body_is_refused(cross):
    keep = cross.mem[dm.SIDTAB_HI + 40], cross.mem[dm.SIDTAB_LO + 40]
    cross.mem[dm.SIDTAB_HI + 40], cross.mem[dm.SIDTAB_LO + 40] = 0, 40
    try:
        with pytest.raises(dm.TranscribeError, match="no body"):
            cross.resolve(40)
    finally:
        cross.mem[dm.SIDTAB_HI + 40], cross.mem[dm.SIDTAB_LO + 40] = keep


@pytest.mark.parametrize(
    "cols, message",
    [
        ([{"wgh": 0x41}, {"wgl": 1}], "both wgh and wgl"),
        ([{"af": 0x80}, {"ps": 4}], "pulse sweep beside a pitch mode"),
    ],
)
def test_columns_it_cannot_write_are_refused(cross, cols, message):
    joints = cross.joints
    cross.joints = {"c1": ([(1, c) for c in cols], None, 0)}
    try:
        with pytest.raises(dm.TranscribeError, match=message):
            cross.gate()
    finally:
        cross.joints = joints


def test_notes_it_cannot_write_are_refused(cross):
    cross.gate()
    assert not cross.gated
    with pytest.raises(dm.TranscribeError, match="past the note column"):
        cross.event((None, None, 0x80), None)
    with pytest.raises(dm.TranscribeError, match="does not set the gate"):
        cross.event((None, None, 48), None)
    assert cross.event((None, None, 48), "c2").note == "C4"


def test_both_layers_writing_one_unit_in_a_call_are_refused(cross):
    with pytest.raises(dm.TranscribeError, match="both layers"):
        cross.tick((17, 0, 17, 0))


def test_a_song_that_does_not_loop_in_time_is_refused(cross):
    with pytest.raises(dm.TranscribeError, match="does not loop within 50 calls"):
        dm.Transcriber(cross.raw).song(50)


@pytest.mark.parametrize(
    "name, message",
    [("rent_a_cop", r"not the \$1000 build"), ("persei", r"a play routine at \$17C1")],
)
def test_other_builds_are_refused(request, name, message):
    with pytest.raises(dm.TranscribeError, match=message):
        dm.transcribe(request.getfixturevalue(name).read_bytes())


def test_a_multi_sid_tune_is_refused(crosseyed):
    raw = bytearray(crosseyed.read_bytes())
    raw[0x7A] = 0x50
    with pytest.raises(dm.TranscribeError, match="multi-SID"):
        dm.Transcriber(bytes(raw))


def test_main_transcribes_and_verifies(crosseyed, persei, capsys):
    assert dm.main([str(crosseyed)]) == 0
    assert capsys.readouterr().out.startswith("unitracker: 2.0\n")
    assert dm.main(["--verify", "--frames", "300", "--diff", "2", str(crosseyed), str(persei)]) == 0
    out = capsys.readouterr().out
    assert f"{crosseyed}: exact" in out and f"{persei}: TranscribeError" in out
