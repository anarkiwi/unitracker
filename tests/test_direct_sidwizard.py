"""The direct SID-Wizard transcription plays the reference player's registers."""

import dataclasses

import pytest
from pysidwizard import (
    End,
    Loop,
    Pattern,
    PlayPattern,
    Row,
    SWMPlayer,
    TempoOverride,
    Transpose,
    parse_sid_song,
)
from unitracker_grammar.document import VERSION, printed, read

from unitracker.direct import sidwizard as s
from unitracker.direct.swtrace import standard
from unitracker.direct.cli import summary

TICKS = 800
EMPTY = Row()
META = (("title", "t"),)


def song(raw: bytes, rows: list, others=(), **instruments):
    """Tune ``raw`` reduced to a loop of ``rows`` on voice 1 (``others`` on voices 2 and 3,
    else rests), its instrument ``iN`` fields (and the module's, ``swm``) set from
    ``instruments``."""
    swm = parse_sid_song(raw, 1)
    lines = [rows, *others] + [[EMPTY] * len(rows)] * (2 - len(others))
    swm.patterns = [Pattern(rows=list(r)) for r in lines]
    swm.sequences = [[PlayPattern(k + 1), Loop(0)] for k in range(3)]
    for name, fields in instruments.items():
        for key, value in fields.items():
            if name == "swm":
                setattr(swm, key, value)
            else:
                setattr(swm.instruments[int(name[1:]) - 1], key, value)
    return swm


def exact(swm, ticks: int = TICKS) -> str:
    """The tune's document, asserting it reads back to itself and its grid is the reference
    player's at the standard note table."""
    text = s.Transcriber(swm, META).document()
    assert printed(read(text)) == text
    cells = s.compare(swm, text, ticks)
    assert not cells.size, summary(cells, 3) + "\n" + text
    return text


def notes(*rows) -> list:
    """Pattern rows from ``(note, instrument)`` pairs, each followed by an empty row."""
    out = []
    for note, ins in rows:
        out += [Row(note=note, instrument=ins), EMPTY]
    return out


@pytest.mark.parametrize(
    "name",
    [
        "mega_run",
        "lykia_boss",
        "border_odyssey",
        "whammer",
        "csillag",
        "samwise",
        "flowing_slowly_extended",
    ],
)
def test_tunes_play_exactly(name, request):
    """Light and bare builds, a normal build with octave shifts, a medium build with
    portamentos and slides, a normal build with chords and chord FX, extra builds (one of a
    pre-1.8 driver, one with staccato restarts)."""
    raw = request.getfixturevalue(name).read_bytes()
    cells, text = s.verify(raw, frames=3000)
    assert not cells.size, summary(cells, 3)
    assert "{loop: " in text or text.endswith("  - end\n")
    assert "\ntables:" not in text and "F.lo-1,x" in text


@pytest.mark.parametrize("name", ["mega_run", "lykia_boss"])
def test_the_native_table_is_the_standard_one_but_for_note_0(name, request):
    """FREQTBL is F rounded down at CLOCK but for note 0, a word under C0, and past its 96
    notes: a tune playing neither plays the native player exactly."""
    one = s.transcriber(request.getfixturevalue(name).read_bytes())
    native, ref = SWMPlayer(one.swm), standard(one.swm)
    words = [[p.freq_hi[k] << 8 | p.freq_lo[k] for k in range(96)] for p in (native, ref)]
    assert [k for k, (u, w) in enumerate(zip(*words)) if u != w] == [0]
    assert (words[0][0], words[1][0]) == (263, 262)
    cells = s.compare(one.swm, one.document(), 3000, native=True)
    assert not cells.size, summary(cells, 3)


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
    "a pulse table that starts with a sweep, its count left from before": (notes((37, 1))),
    "rests before the first note": [EMPTY, EMPTY, Row(note=30, instrument=1)] + [EMPTY] * 5,
    "waveform FX and note-column sync and ring": [
        Row(note=37, instrument=1),
        Row(note=0x79),
        Row(note=0x7B, fx=0x41),
        Row(note=0x7A, fx=0x04, fx_value=0x40),
        Row(note=0x7C),
        Row(note=0x7E),
        Row(note=0x7D),
        EMPTY,
    ],
    "instruments selected without a note": [
        Row(note=37, instrument=1),
        Row(instrument=3),
        EMPTY,
        Row(note=0x6A, instrument=13),
    ],
}


@pytest.mark.parametrize("case", BARE)
def test_bare_build_rows_play_exactly(case, lykia_boss):
    swm = song(lykia_boss.read_bytes(), BARE[case])
    if "sweep" in case:
        swm.instruments[0].pw_table = bytes.fromhex("100100")
    exact(swm)


NORMAL = {
    "hard restarts on TICK_1 only, and none": (
        notes((37, 1), (40, 1), (44, 3), (45, 3)),
        {"i1": {"control": 0x19}, "i3": {"control": 0x1B, "hr_attack": 2, "hr_release": 5}},
    ),
    "a staccato restart over a walking WF table": (
        notes((37, 5), (40, 1), (None, None), (45, 1)),
        {"i1": {"control": 0x1E}},
    ),
    "a staccato first note": (
        notes((37, 1), (40, 1)),
        {"i1": {"control": 0x1E, "wf_table": bytes.fromhex("410000400000")}},
    ),
    "a first frame without the test bit": (
        notes((37, 1), (40, 1), (44, 1)),
        {"i1": {"control": 0x12, "wf_table": bytes.fromhex("41000040fe00")}},
    ),
    "detuned WF rows, a down arp past DPITCH, a NOP and an end": (
        notes((37, 3), (2, 3), (None, None)),
        {"i3": {"wf_table": bytes.fromhex("410507"), "arp_speed": 0}},
    ),
    "held WF rows on a detune build": (
        notes((37, 5), (40, 5), (None, None), (44, 5)),
        {"i5": {"wf_table": bytes.fromhex("2100030341f4ff4003fe40fc0041000a"), "arp_speed": 1}},
    ),
    "chords that return and loop, and a chord FX": (
        notes((37, 3), (40, 5), (44, 3)) + [Row(fx=0x72), EMPTY, Row(note=37, instrument=3)],
        {
            "i3": {"wf_table": bytes.fromhex("417f00217f00410c00"), "default_chord": 1},
            "i5": {"wf_table": bytes.fromhex("117f00"), "default_chord": 2, "arp_speed": 1},
            "swm": {"chord_table": bytes.fromhex("0003077e0004070c7f")},
        },
    ),
    "keyboard-tracked pulse and filter rows": (
        notes((37, 1), (60, 1), (None, None), (13, 3), (90, 1)),
        {
            "i1": {
                "pw_table": bytes.fromhex("84000c20400320c0f0fe1700"),
                "filter_table": bytes.fromhex("9f08100410fe0200fc06"),
            },
            "i3": {"pw_table": bytes.fromhex("8400000a1002")},
            "i5": {"arp_speed": 0, "wf_table": bytes.fromhex("210000")},
        },
    ),
    "held WF rows over a tracked pulse: WRPULS's carry": (
        notes((37, 5), (13, 5), (None, None), (60, 5)),
        {
            "i5": {
                "wf_table": bytes.fromhex("2100000341f4ff4003fe40fc0041000a"),
                "arp_speed": 1,
                "pw_table": bytes.fromhex("8f0001108003fe2600"),
            }
        },
    ),
    "gate-off pointers move the WF and pulse walks": (
        notes((37, 1), (0x7E, None), (40, 3), (0x7E, None), (44, 1)),
        {
            "i1": {"wf_table": bytes.fromhex("410000400000"), "gateoff_wf": 19, "gateoff_pw": 26},
            "i3": {"gateoff_pw": 26},
        },
    ),
    "cutoff FX on the filter's voice, before and after its walk": (
        [Row(note=37, instrument=1), Row(fx=0x0F, fx_value=0x30), EMPTY, Row(fx=0x0F, fx_value=9)],
        {"i1": {"filter_table": bytes.fromhex("9f0800051000")}},
    ),
    "reset switches keep the pulse and filter walking": (
        notes((37, 1), (40, None), (44, None), (40, 1)),
        {"i1": {"control": 0xDA, "pw_table": bytes.fromhex("8400000c2000"), "arp_speed": 0}},
    ),
    "arp speed FX": (
        [Row(note=37, instrument=5), Row(fx=0xC3), EMPTY, Row(fx=0x0C, fx_value=0), EMPTY, EMPTY],
        {"i5": {"wf_table": bytes.fromhex("210000410c00110700fe1000")}},
    ),
    "typed vibratos": (
        notes((37, 1), (40, 3), (44, 5)),
        {
            "i1": {"control": 0x0A, "vibrato": 0x84, "vibrato_delay": 2},
            "i3": {"control": 0x2A, "vibrato": 0x63},
            "i5": {"control": 0x3A, "vibrato": 0x35, "vibrato_delay": 0x81},
        },
    ),
}
FX_ROWS = [
    Row(note=37, instrument=3),
    Row(fx=0x01, fx_value=0x20),
    Row(fx=0x02, fx_value=0x00),
    Row(note=40, fx=0x03, fx_value=0x30),
    Row(fx=0x03, fx_value=0x10),
    Row(fx=0x08, fx_value=0x84),
    Row(fx=0x16, fx_value=0x20),
    Row(fx=0x0D, fx_value=0x07),
    Row(note=44, instrument=0x3F, fx=0x01, fx_value=0x40),
    Row(note=0x78),
    Row(note=37),
    Row(fx=0x85),
    Row(fx=0x93),
    Row(fx=0xA9),
    Row(fx=0xB2),
    Row(fx=0xD3),
    Row(note=40, instrument=3, fx=0x87),
    Row(note=0x7E, fx=0x05, fx_value=0x22),
    Row(note=0x7D, fx=0x06, fx_value=0x84),
    Row(note=0x65, fx=0x10, fx_value=0x05),
    Row(fx=0x11, fx_value=0x43),
    Row(note=44, instrument=3, fx=0x14, fx_value=0x34),
]


@pytest.mark.parametrize("case", NORMAL)
def test_normal_build_rows_play_exactly(case, border_odyssey):
    rows, instruments = NORMAL[case]
    exact(song(border_odyssey.read_bytes(), rows, **instruments))


def test_normal_build_fx_play_exactly(border_odyssey):
    """Slides, portamentos, legato, vibrato and detune FX, small FX, tempo FX."""
    text = exact(song(border_odyssey.read_bytes(), FX_ROWS + [EMPTY] * 10), 1500)
    for name in ("sld", "por", "vfx", "kty", "dfx", "vfq"):
        assert f"  {name}(" in text


def test_orderlist_commands_play_exactly(border_odyssey):
    """A transpose, a tempo, an end: timing and notes come from the player's orderlists."""
    swm = song(border_odyssey.read_bytes(), notes((37, 1), (40, 1)))
    swm.sequences[0] = [TempoOverride(4), Transpose(3), PlayPattern(1), Loop(1)]
    swm.sequences[1] = [PlayPattern(2), End()]
    exact(swm)


def test_a_lower_voice_takes_the_filter_claimed_in_the_same_tick(mega_run):
    """The player runs the voices last to first: the later voice's note keeps the filter."""
    rows = notes((37, 3), (None, None))
    text = exact(song(mega_run.read_bytes(), notes((37, 4), (None, None)), [rows, rows]))
    assert ".i3!filter" in text


REFUSED = {
    "FX \\$09": [Row(note=37, instrument=1, fx=9, fx_value=1), EMPTY],
    "a note before any instrument": [Row(note=37), EMPTY],
    "a waveform FX on a note": [Row(note=37, instrument=1, fx=0x41), EMPTY],
    "an instrument selected before any note": [Row(note=0x7E, instrument=1), EMPTY],
    "two FX of one cell": [Row(note=37, instrument=1), Row(note=0x65, fx=0x01, fx_value=1)],
}


@pytest.mark.parametrize("match", REFUSED)
def test_rows_refused(match, mega_run):
    swm = song(mega_run.read_bytes(), REFUSED[match])
    with pytest.raises(s.TranscribeError, match=match):
        s.Transcriber(swm, META).document()


def test_a_filter_voice_note_that_routes_the_filter_leaves_it_walking(mega_run):
    """A note of the filter's voice whose instrument does not take the filter: the walk goes on
    through the new instrument's bytes."""
    rows = notes((37, 4), (40, 1), (None, None), (42, 4))
    exact(song(mega_run.read_bytes(), rows, i1={"filter_table": b"\x00\x00\x00"}))


@pytest.mark.parametrize("third", [0x84, 0x20])
def test_a_filter_row_routes_by_its_fswitch_column(samwise, third):
    """From 1.4 a filter row's $8x third column is FSWITCH: the routing of every voice, and no
    keyboard track; any other byte is the track."""
    rows = notes((37, 1), (40, 1))
    text = exact(song(samwise.read_bytes(), rows, i1={"filter_table": bytes((0x91, 0x40, third))}))
    assert ("route: 4" in text) == (third == 0x84)


def test_song_refusals(lykia_boss, csillag):
    swm = song(csillag.read_bytes(), notes((37, 1)))
    swm.instruments[0].arp_speed = 2
    swm.player = dataclasses.replace(
        swm.player, features=dataclasses.replace(swm.player.features, multispeedsupp=False)
    )
    with pytest.raises(s.TranscribeError, match="without STRTSND's ARPSCNT reset"):
        s.Transcriber(swm, META)
    swm = song(lykia_boss.read_bytes(), notes((37, 1)))
    swm.frame_speed = 2
    with pytest.raises(s.TranscribeError, match="multispeed"):
        s.Transcriber(swm, META)


def test_cli(mega_run, capsys):
    assert s.main([str(mega_run)]) == 0
    assert capsys.readouterr().out.startswith(f"unitracker: {VERSION}\nmeta: {{title: Mega-Run")
    assert s.main(["--verify", "--frames", "400", str(mega_run)]) == 0
    assert f"{mega_run}: exact" in capsys.readouterr().out


def test_a_cutoff_fx_of_an_earlier_voice_lands_after_the_filter_walk(border_odyssey):
    """The player runs voice 3, 2, 1: voice 1's BIGFX0F lands after voice 2's filter walk."""
    raw = border_odyssey.read_bytes()
    first = [EMPTY, EMPTY, Row(fx=0x0F, fx_value=0x30), EMPTY]
    second = [Row(note=37, instrument=1), EMPTY, EMPTY, EMPTY]
    swm = song(raw, first, [second], i1={"filter_table": bytes.fromhex("9f0800051000")})
    swm.patterns[0].rows[0] = Row(note=40, instrument=3)
    assert "late.cut:fhi(48)" in exact(swm)
