"""The direct SID-Wizard transcription plays the reference player's registers."""

import pytest
from pysidwizard import Loop, Pattern, PlayPattern, Row, TempoOverride, parse_sid_song
from pysidtracker import gridcmp
from unitracker_grammar.document import VERSION, printed, read
from unitracker_player.player import play

from unitracker.direct import sidwizard as s
from unitracker.direct.cli import summary

TICKS = 800
EMPTY = Row()
META = (("title", "t"),)


def song(raw: bytes, rows: list, others=(), **instruments):
    """Tune ``raw`` reduced to a loop of ``rows`` on voice 1 (``others`` on voices 2 and 3,
    else rests), its instrument ``iN`` fields set from ``instruments``."""
    swm = parse_sid_song(raw, 1)
    lines = [rows, *others] + [[EMPTY] * len(rows)] * (2 - len(others))
    swm.patterns = [Pattern(rows=list(r)) for r in lines]
    swm.sequences = [[PlayPattern(k + 1), Loop(0)] for k in range(3)]
    for name, fields in instruments.items():
        for key, value in fields.items():
            setattr(swm.instruments[int(name[1:]) - 1], key, value)
    return swm


def standard_cells(swm, text: str, ticks: int):
    """The cells where ``text``'s grid differs from the reference player's at the standard
    note table."""
    ours = play(text, ticks=ticks)
    return gridcmp.cell_diff(ours, s.standard(swm).render_grid(len(ours)))


def exact(swm, ticks: int = TICKS) -> str:
    """The tune's document, asserting it reads back to itself and its grid is the reference
    player's at the standard note table."""
    text = s.Transcriber(swm, META).document()
    assert printed(read(text)) == text
    cells = standard_cells(swm, text, ticks)
    assert not cells.size, summary(cells, 3) + "\n" + text
    return text


def notes(*rows) -> list:
    """Pattern rows from ``(note, instrument)`` pairs, each followed by an empty row."""
    out = []
    for note, ins in rows:
        out += [Row(note=note, instrument=ins), EMPTY]
    return out


@pytest.mark.parametrize("name", ["mega_run", "lykia_boss"])
def test_tunes_play_exactly(name, request):
    """A light build with shared filter claims and a bare one with legatos, vibrato note FX and
    a release small FX."""
    raw = request.getfixturevalue(name).read_bytes()
    one = s.transcriber(raw, frames=4000)
    text = one.document()
    cells = standard_cells(one.swm, text, 4000)
    assert not cells.size, summary(cells, 3)
    assert "{loop: " in text and "\ntables:" not in text and "F.lo,x" in text


@pytest.mark.parametrize("name", ["mega_run", "lykia_boss"])
def test_against_the_native_table_only_the_pitch_differs(name, request):
    """FREQTBL is no clock's F: the native player differs in the frequency registers alone."""
    cells, _ = s.verify(request.getfixturevalue(name).read_bytes(), frames=4000)
    assert cells.size and set(cells[:, 1].tolist()) <= {0, 1, 7, 8, 14, 15}


LIGHT = {
    "a WF loop, absolute, down and NOP arps": {
        "i1": {"wf_table": bytes.fromhex("41000021c00011f400108000fe1300")}
    },
    "a WF jump past $7F rests": {"i1": {"wf_table": bytes.fromhex("410c00fe8000")}},
    "an empty WF table holds the first-tick control": {"i1": {"wf_table": b""}},
    "a pulse jump to a sweep and a sweep of none": {
        "i1": {"pw_table": bytes.fromhex("880000041000000000fe1700")}
    },
    "a pulse jump to a set row": {"i1": {"pw_table": bytes.fromhex("84000002f000fe1400")}},
    "a pulse self jump rests": {"i1": {"pw_table": bytes.fromhex("880000fe1900")}},
    "a filter loop": {
        "i1": {"filter_table": bytes.fromhex("9a2000021000fe2400"), "wf_table": b"\x41\x00\x00"}
    },
    "a filter jump to a set row": {"i1": {"filter_table": bytes.fromhex("9a200004f000fe2100")}},
    "a routed voice that takes no filter": {"i1": {"filter_table": b"\x00\x00\x00"}},
    "a delayed vibrato": {"i1": {"vibrato": 0x56, "vibrato_delay": 3}},
}


@pytest.mark.parametrize("case", LIGHT)
def test_light_build_tables_play_exactly(case, mega_run):
    rows = notes((37, 1), (None, None), (44, None), (None, None))
    exact(song(mega_run.read_bytes(), rows, **LIGHT[case]))


BARE = {
    "small FX on a note and between": [
        Row(note=37, instrument=1),
        Row(instrument=0x5C),
        Row(note=40, instrument=0x52),
        Row(instrument=0x6C),
        Row(note=42, instrument=0x61),
        EMPTY,
    ],
    "a vibrato note FX and a gate off": [
        Row(note=37, instrument=13),
        EMPTY,
        Row(note=0x6A),
        EMPTY,
        Row(note=0x7E),
        EMPTY,
    ],
    "a legato over ended tables": [
        Row(note=37, instrument=13),
        EMPTY,
        Row(note=44, instrument=0x3F),
        EMPTY,
        Row(note=0x6B),
        EMPTY,
        Row(note=37, instrument=13),
        EMPTY,
    ],
    "rests before the first note": [EMPTY, EMPTY, Row(note=30, instrument=1)] + [EMPTY] * 5,
}


@pytest.mark.parametrize("case", BARE)
def test_bare_build_rows_play_exactly(case, lykia_boss):
    exact(song(lykia_boss.read_bytes(), BARE[case]))


def test_a_lower_voice_takes_the_filter_claimed_in_the_same_tick(mega_run):
    """The player runs the voices last to first: the later voice's note keeps the filter."""
    rows = notes((37, 3), (None, None))
    text = exact(song(mega_run.read_bytes(), notes((37, 4), (None, None)), [rows, rows]))
    assert ".i3!filter" in text


REFUSED = {
    "FX \\$05": [Row(note=37, instrument=1, fx=5, fx_value=1), EMPTY],
    "an instrument selected without a note": [Row(note=37, instrument=1), Row(instrument=2)],
    "a note column before any instrument": [Row(note=37), EMPTY],
    "instrument selected without a note": [Row(note=0x7E, instrument=1), EMPTY],
    "note column \\$7D": [Row(note=37, instrument=1), Row(note=0x7D)],
    "small FX \\$41": [Row(note=37, instrument=1), Row(instrument=0x41)],
}


@pytest.mark.parametrize("match", REFUSED)
def test_rows_refused(match, mega_run):
    swm = song(mega_run.read_bytes(), REFUSED[match])
    with pytest.raises(s.TranscribeError, match=match):
        s.Transcriber(swm, META).document()


def test_a_legato_over_a_walking_table_is_refused(lykia_boss):
    rows = [Row(note=37, instrument=1), Row(note=40, instrument=0x3F), EMPTY, EMPTY]
    swm = song(lykia_boss.read_bytes(), rows)
    with pytest.raises(s.TranscribeError, match="legato note at frame"):
        s.Transcriber(swm, META).document()


def test_a_filter_voice_note_that_leaves_the_filter_walking_is_refused(mega_run):
    rows = notes((37, 4), (40, 1))
    swm = song(mega_run.read_bytes(), rows, i1={"filter_table": b"\x00\x00\x00"})
    with pytest.raises(s.TranscribeError, match="does not take it"):
        s.Transcriber(swm, META).document()


def test_song_refusals(mega_run, lykia_boss, csillag):
    raw = mega_run.read_bytes()
    with pytest.raises(s.TranscribeError, match="normal build: arpspeedsupp"):
        s.transcriber(csillag.read_bytes())
    swm = song(raw, notes((37, 1)))
    swm.subtune_tempos = [(0x05, 0x03)]
    with pytest.raises(s.TranscribeError, match="tempo \\$05"):
        s.Transcriber(swm, META)
    swm = song(raw, notes((37, 1)))
    swm.sequences[0] = [TempoOverride(4), PlayPattern(1), Loop(0)]
    with pytest.raises(s.TranscribeError, match="orderlist TempoOverride"):
        s.Transcriber(swm, META).document()
    swm.sequences[0] = [PlayPattern(1)]
    with pytest.raises(s.TranscribeError, match="an orderlist that ends"):
        s.Transcriber(swm, META).document()
    swm = song(lykia_boss.read_bytes(), notes((37, 1)))
    swm.instruments[0].pw_table = bytes.fromhex("100100")
    with pytest.raises(s.TranscribeError, match="pw table that starts with a sweep"):
        s.Transcriber(swm, META).document()


def test_cli(mega_run, capsys):
    assert s.main([str(mega_run)]) == 0
    assert capsys.readouterr().out.startswith(f"unitracker: {VERSION}\nmeta: {{title: Mega-Run")
    assert s.main(["--verify", "--frames", "400", str(mega_run)]) == 0
    assert f"{mega_run}: first (" in capsys.readouterr().out
