"""Lifting GoatTracker 2 bytes to unitracker records, phrases and tracks; synthetic tunes
play their native player."""

from types import SimpleNamespace

import numpy as np
import pytest
import yaml
from conftest import gt_tune, table
from pygoattracker.model import (
    Instrument,
    Orderlist,
    PlayPattern,
    Repeat,
    Row,
    Song,
    Transpose,
)

from unitracker.format import songio
from unitracker.format.codec import CodecError, CommandCodec, RowCodec
from unitracker.format.records import record_types
from unitracker.lift import goattracker as gt
from unitracker.modfit.convert import ConversionError
from unitracker.player.vm import SongPlayer


# Each table's rows and the records it lifts to, as ``keys`` values (None: absent).
@pytest.mark.parametrize(
    "records,rows,keys,expected",
    [
        (
            gt.wave_records,
            [(0x41, 0x00), (0x02, 0x81), (0xE1, 0x80), (0xF5, 0x40), (0xFF, 0x01)],
            ("w", "n", "next"),
            [(0x41, 0x00, 2), (0x02, 0x81, 3), (0xE1, 0x80, 4), (0xF5, 0x40, 1)],
        ),
        (
            gt.pulse_records,
            [(0x84, 0x00), (0x20, 0xF0), (0x10, 0x10), (0xFF, 0x02)],
            ("l", "r", "next"),
            [(0x84, 0x00, 2), (0x20, 0xF0, 3), (0x10, 0x10, 2)],
        ),
        (
            gt.filter_records,
            [(0x90, 0xF1), (0x00, 0x40), (0x10, 0x02), (0x00, 0x22), (0xA0, 0x10), (0xFF, 0x00)],
            ("l", "r", "cut", "next"),
            [(0x90, 0xF1, 0x40, 3), (0, 0x40, None, 3), (0x10, 0x02, None, 4)]
            + [(0, 0x22, None, 5), (0xA0, 0x10, None, 0)],
        ),
        (gt.speed_records, [(0x04, 0x20), (0x81, 0x02)], ("l", "r"), [(4, 32), (0x81, 2)]),
    ],
    ids=["wave", "pulse", "filter absorbs its cutoff row", "speed"],
)
def test_table_records(records, rows, keys, expected):
    assert records(table(*rows)) == {
        k: {key: v for key, v in zip(keys, row) if v is not None}
        for k, row in enumerate(expected, start=1)
    }


@pytest.mark.parametrize(
    "timer,first,rows",
    [
        (0x04, 0x09, [{"hold": 4, "gate": 0, "ad": 0x0F, "sr": 0}, {"ctrl": 9, "gate": 1}]),
        (0x83, 0xFE, [{"hold": 3, "gate": 0}, {"gate": 0}]),
        (0xC1, 0xFF, [{"hold": 1}, {"gate": 1}]),
        (0x42, 0x00, [{"hold": 2}, {}]),
    ],
)
def test_restart_rows_follow_the_gate_timer_and_first_wave(timer, first, rows):
    assert gt.restart_rows(timer, first, 0x0F00) == rows


def test_instruments_and_default_tempo():
    instruments = [
        Instrument(attack_decay=0x12, wave_ptr=3, gateoff_timer=0x82),
        Instrument(gateoff_timer=0x82),
        Instrument(),
        Instrument(sustain_release=0xF0, gateoff_timer=0x40),
        Instrument(gateoff_timer=0x82, first_wave=0x41),
    ]
    recs, restarts = gt.instrument_records(instruments, 0x0F00)
    assert recs == {
        1: {"ad": 0x12, "ctrl": 3, "restart": 1},
        2: {"restart": 1},
        3: {},
        4: {"sr": 0xF0},
        5: {"restart": 3},
    }
    assert restarts == {
        1: {"hold": 2, "gate": 0, "next": 2},
        2: {"next": 0},
        3: {"hold": 2, "gate": 0, "next": 4},
        4: {"ctrl": 0x41, "gate": 1, "next": 0},
    }
    for bad in (Instrument(attack_decay=1), Instrument(first_wave=9, gateoff_timer=0x40)):
        with pytest.raises(gt.LiftError, match="gate timer 0"):
            gt.instrument_records([bad], 0x0F00)
    song = Song(instruments=[Instrument() for _ in range(63)])
    assert gt._default_tempo(song) == 5  # pylint: disable=protected-access
    song.instruments[62] = Instrument(attack_decay=7)
    assert gt._default_tempo(song) == 6  # pylint: disable=protected-access


def _tempo_phrases(*commands):
    return {"a": [{"command": list(c)} for c in commands]}


@pytest.mark.parametrize(
    "commands,deftempo,expected",
    [
        ([], 5, 6),
        ([], 1, None),
        ([(0xF, 16)], 11, 16),
        ([(0xF, 16), (0xF, 16)], 11, 16),
        ([(0xF, 16), (0xF, 8)], 11, None),
        ([(0xF, 0x90)], 11, None),
        ([(0xE, 1)], 11, None),
        ([(0x1, 1), (0xF, 16)], 11, None),
        ([(0x1, 1), (0xF, 12)], 11, 12),
    ],
)
def test_ticks_per_step(commands, deftempo, expected):
    tracks = [{"plays": [{"phrase": "a"}]}]
    assert gt.ticks_per_step(_tempo_phrases(*commands), tracks, deftempo) == expected


def test_phrase_folds_empty_rows():
    rows = [
        Row(),
        Row(note=0x60 + 24, instrument=2, command=1, data=3),
        Row(),
        Row(),
        Row(note=0xBE),
        Row(note=0xBF),
    ]
    assert gt.phrase(rows) == [
        {},
        {"note": 24, "instrument": 2, "command": [1, 3], "steps": 3},
        {"note": "off"},
        {"note": "on"},
    ]
    with pytest.raises(gt.LiftError, match="empty"):
        gt.phrase([])
    with pytest.raises(gt.LiftError, match="note byte"):
        gt.phrase([Row(note=0x10)])


def test_track_transpose_repeat_loop():
    ol = Orderlist(
        entries=[
            Transpose(2),
            Repeat(1),
            PlayPattern(3),
            PlayPattern(4),
            Transpose(0),
            PlayPattern(3),
        ],
        restart=4,
    )
    assert gt.track(ol) == {
        "plays": [
            {"phrase": "p03", "transpose": 2, "repeat": 2},
            {"phrase": "p04", "transpose": 2},
            {"phrase": "p03"},
        ],
        "loop": 2,
    }


@pytest.mark.parametrize(
    "entries,restart,match",
    [
        ([Transpose(2), PlayPattern(0), Transpose(0), PlayPattern(1)], 1, "different transpose"),
        ([PlayPattern(0)], 5, "outside"),
        ([Repeat(1), Transpose(1), PlayPattern(0)], 0, "not \\[transpose\\]"),
    ],
)
def test_track_errors(entries, restart, match):
    with pytest.raises(gt.LiftError, match=match):
        gt.track(Orderlist(entries=entries, restart=restart))


def test_unsupported_builds_are_named():
    info = SimpleNamespace(
        simplepulse=True, state=object(), init_regs=((0, 1),), live_vibrato=False
    )
    with pytest.raises(gt.LiftError, match="SIMPLEPULSE.*full-ghosting.*init-routine"):
        gt.lift(SimpleNamespace(info=info))


def test_the_reference_lift_holds_the_tunes_constants(references):
    song = references["metal_warrior"].ir()
    assert song["meta"]["title"] == "Metal Warrior Title Remix"
    assert song["clock"] == gt.PAL_CLOCK
    assert song["playroutine"]["constants"] == {"adparam": 0x0F00, "deftempo": 11}
    assert [len(t["plays"]) for t in song["tracks"]] == [5, 5, 5]
    assert len(song["tables"]["ftab"]) == 128
    assert song["records"]["ins"][1]["sr"] == 0x88


COMMANDS_SONG = """
unitracker: 1.20
playroutine: {ref: goattracker2, constants: {deftempo: 6}}
song:
  - def:
      pw:
        pw1: [{pw: 2048}, end]
      filter:
        f1: [{cut: 512, res: 3, route: 1, mode: 16}, end]
      ins:
        i1: {sr: 240, pw: pw1, ctrl: [{ctrl: 65, note: +0}, {ad: 25}, {sr: 200}, {run: pw1},
            {run: f1},
            {res: 12, route: 2}, {cut: 1024}, {vol: 9}, end]}
      phrase:
        p1: [C4.i1*8]
        p2: [off*8]
    play: [p1, p2, p2]
  - end
"""


def test_wavetable_commands_are_their_shared_keys():
    ir = songio.loads(COMMANDS_SONG)
    rows = ir["records"]["ctrltab"]
    assert [(r["w"], r["n"]) for r in rows.values()][1:] == [
        (0xF5, 25), (0xF6, 200), (0xF9, 1), (0xFA, 1), (0xFB, 0xC2), (0xFC, 128), (0xFD, 9)
    ]  # fmt: skip
    grid = SongPlayer(ir).render_grid(20).astype(int)
    last = grid[-1]
    assert (last[5], last[6], last[2] | last[3] << 8) == (25, 200, 2048)
    assert (last[22], last[23], last[24]) == (128, 0xC2, 0x10 | 9)
    text = songio.dumps(ir)
    assert "{run: pw1}, {run: f1}" in text and songio.dumps(songio.loads(text)) == text
    assert np.array_equal(SongPlayer(songio.loads(text)).render_grid(20), grid)


def test_a_wavetable_command_names_a_program_of_its_kind():
    with pytest.raises(songio.SongFormatError, match="'i1' names no filter or pw"):
        songio.loads(COMMANDS_SONG.replace("{run: pw1}", "{run: i1}"))


REST = (None, 0, 0, 0)
SPEEDS = [(0, 0x20), (0x80, 1), (0x02, 0x10)]
TWO_NOTES = [(24, 1, 0, 0)] + [REST] * 7 + [(31, 1, 0, 0)] + [REST] * 7
FIRST = {"ctrl": 65, "note": 0}


def lifted(dec, native, ticks=400) -> tuple[str, SongPlayer, np.ndarray]:
    """``(canonical text, its player, native grid)`` of a synthetic tune's lift, asserting the
    text round-trips and plays as the native player."""
    text = songio.dumps(gt.lift(dec))
    song = songio.loads(text)
    assert songio.dumps(song) == text
    theirs = np.asarray(native.render_grid(ticks), dtype=np.uint8)
    player = SongPlayer(song)
    assert player.mismatch(theirs) is None
    return text, player, theirs


def song_def(text: str) -> dict:
    return yaml.safe_load(text)["song"][0]["def"]


# Each case's program as written, ``...`` for a row of any form.
WAVETABLE_TUNES = {
    "steps are their rows' freq": (
        {
            "wave": [(0x41, 0), (0xF4, 3), (0xF4, 3), (0xF3, 1), (0xF2, 2), (0xF1, 1)]
            + [(0x41, 0x80), (0xFF, 7)],
            "rows": TWO_NOTES,
            "speed": SPEEDS,
        },
        ("ins", "i1", "ctrl"),
        [FIRST, {"freq": "(v + 16) % 65536"}, {"freq": "(v + 16) % 65536"}, ...]
        + [{"freq": "(v - ((-F(N) + F((N + 1) % 128)) % 65536) // 2) % 65536"}]
        + [{"freq": "(v + 32) % 65536"}, {"ctrl": 65}, {"loop": 6}],
    ),
    "an unheard waveform pause is a waveform row": (
        {
            "wave": [(0x41, 0), (0xFF, 0), (0xF7, 0x21), (0xF7, 0x05), (0xFF, 3)],
            "rows": [(24, 1, 0, 0)] + [REST] * 7 + [(None, 0, 8, 3)] + [REST] * 7,
        },
        ("ctrl", "c1"),
        [{"ctrl": 33}, {"ctrl": 5}, {"loop": 0}],
    ),
    "a volume from 16 is an empty row": (
        {"wave": [(0x41, 0), (0xFD, 0x10), (0xFD, 0xFF), (0x21, 0x80), (0xFF, 4)]},
        ("ins", "i1", "ctrl"),
        [FIRST, {}, {}, {"ctrl": 33}, {"loop": 3}],
    ),
    "program stops name the program kind": (
        {
            "wave": [(0x41, 0), (0xF9, 1), (0xFA, 1), (0x03, 0x80), (0xF9, 0), (0x03, 0x80)]
            + [(0xFA, 0), (0x03, 0x80), (0xFA, 1), (0x03, 0x80), (0xFB, 0), (0x41, 0x80)]
            + [(0xFF, 12)],
            "pulse": [(0x88, 0), (0x20, 0x10), (0xFF, 2)],
            "filt": [(0x90, 0xF1), (0x00, 0x40), (0x10, 0x02), (0xFF, 3)],
        },
        ("ins", "i1", "ctrl"),
        [FIRST, {"run": "pw1"}, {"run": "f1"}, {"wait": 3}, {"pw": "none"}, {"wait": 3}]
        + [{"filter": "none"}, {"wait": 3}, {"run": "f1"}, {"wait": 3}]
        + [{"res": 0, "route": 0, "filter": "none"}, {"ctrl": 65}, {"loop": 11}],
    ),
}


@pytest.mark.parametrize("tune,path,rows", WAVETABLE_TUNES.values(), ids=WAVETABLE_TUNES)
def test_wavetable_rows_lift_to_their_shared_form(tune, path, rows):
    text, _, _ = lifted(*gt_tune(**tune))
    got = song_def(text)
    for key in path:
        got = got[key]
    assert len(got) == len(rows) and "speedtab" not in text
    assert all(isinstance(g, dict) if r is ... else g == r for g, r in zip(got, rows))


GT_FILTER_TUNES = {
    "wavetable": (
        {"wave": [(0x41, 0), (0xFB, 0xA1), (0xFC, 0x40), (0xFD, 0x07), (0x21, 0x80), (0xFF, 5)]},
        {0x16: 0x40, 0x17: 0xA1, 0x18: 0x07},
    ),
    "pattern": (
        {
            "wave": [(0x41, 0), (0xFF, 0)],
            "rows": [(24, 1, 0xB, 0xA1), *[REST] * 3, (None, 0, 0xC, 0x50), *[REST] * 3]
            + [(None, 0, 0xD, 0x09), *[REST] * 7],
        },
        {0x16: 0x50, 0x17: 0xA1, 0x18: 0x09},
    ),
    "program-stop": (
        {
            "wave": [(0x41, 0), (0xFA, 1), (0x03, 0x80), (0xFB, 0), (0x41, 0x80), (0xFF, 5)],
            "filt": [(0x90, 0xF1), (0x00, 0x40), (0x10, 0x02), (0xFF, 3)],
        },
        {0x17: 0xF1, 0x18: 0x1F},
    ),
}


@pytest.mark.parametrize("tune,regs", GT_FILTER_TUNES.values(), ids=GT_FILTER_TUNES)
def test_filter_writes_sound_after_the_filter_step(tune, regs):
    _, player, theirs = lifted(*gt_tune(**tune))
    begun = min(s for s in player.starts if s is not None)
    for reg, value in regs.items():
        assert value in theirs[begun:, reg]


@pytest.mark.parametrize(
    "wave,rows,speed,error,match",
    [
        (
            [(0x41, 0), (0xF4, 3), (0xFF, 2)],
            None,
            SPEEDS,
            ConversionError,
            "its runs need several freq",
        ),
        (
            [(0x41, 0), (0xF7, 0x21), (0xFF, 2)],
            [(24, 1, 1, 1)] + [REST] * 15,
            [(0, 0x20)],
            gt.LiftError,
            r"ctrltab rows \[2\] pause the tick's continuous effect, which the song hears at tick",
        ),
        *(
            ([(0x41, 0), row, (0xFF, 2)], None, [(0, 0x20)], gt.LiftError, match)
            for row, match in [
                ((0xF7, 0xE1), r"row 2: \$F7 sets waveform \$E1, which no"),
                ((0xF0, 0), r"row 2: wavetable command \$F0 stops the"),
                ((0xF8, 1), r"row 2: wavetable command \$F8 stops the"),
                ((0xFE, 1), r"row 2: wavetable command \$FE stops the"),
            ]
        ),
    ],
)
def test_wavetable_rows_without_a_form_are_named(wave, rows, speed, error, match):
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    dec, _ = gt_tune(wave, rows=rows, speed=speed)
    with pytest.raises(error, match=match):
        gt.lift(dec)


VIBRATO = {"wave_ptr": 1, "gateoff_timer": 2, "sustain_release": 0xF0, "vibrato_param": 1}
NOTES = [(24, 1, 0, 0)] + [REST] * 7 + [(31, 1, 0, 0), REST, (36, 1, 0, 0)] + [REST] * 5
RESUMED = [(24, 1, 0, 0)] + [REST] * 3 + [(None, 0, 1, 1)] + [REST] * 3 + [(31, 1, 0, 0)]
CHANGED = [(24, 1, 0, 0)] + [REST] * 3 + [(None, 2, 0, 0)] + [REST] * 3 + [(36, 2, 0, 0)]
RESUMED_ATOMS = ["C2.i1*4", "-!freq:x1", "-!freq:x2", "-!freq:x2", "-!freq:x2", "G2.i1*8"]
CHANGED_ATOMS = ["C2.i1*4", "-.i2!freq:x1", "-!freq:x1", "-!freq:x1", "-!freq:x1", "C3.i2*8"]


# Each case: instrument 1's vibrato delay, the rows, a second instrument, a fragment of each
# instrument's freq (None: it has none), and the first phrase.
@pytest.mark.parametrize(
    "delay,rows,second,freqs,atoms",
    [
        (1, NOTES, None, {"i1": "[(φ - 2) % 8 >= 4]"}, ["C2.i1*8", "G2.i1*2", "C3.i1*6"]),
        (3, NOTES, None, {"i1": "[φ >= 2]"}, ["C2.i1*8", "G2.i1*2", "C3.i1*6"]),
        (40, NOTES, None, {"i1": "[φ >= 38]"}, ["C2.i1*8", "G2.i1*2", "C3.i1*6"]),
        (0, TWO_NOTES, None, {"i1": None}, ["C2.i1*8", "G2.i1*8"]),
        (3, RESUMED + [REST] * 7, None, {}, RESUMED_ATOMS),
        (
            3,
            CHANGED + [REST] * 7,
            {**VIBRATO, "vibrato_param": 2, "vibrato_delay": 1},
            {"i1": "[φ >= 2]", "i2": "F((N + 1) % 128)"},
            CHANGED_ATOMS,
        ),
    ],
    ids=["delay 1", "delay 3", "delay 40", "no delay is none", "resumed", "instrument change"],
)
def test_the_instrument_vibrato_is_the_instrument_freq(delay, rows, second, freqs, atoms):
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    ins = [{**VIBRATO, "vibrato_delay": delay}] + ([second] if second else [])
    text, _, _ = lifted(*gt_tune([(0x41, 0), (0xFF, 0)], ins, rows, speed=[(2, 0x20), (0x82, 3)]))
    assert "speed" not in text and "vd:" not in text
    first = song_def(text)
    for name, fragment in freqs.items():
        freq = first["ins"][name].get("freq")
        assert freq is None if fragment is None else fragment in freq
    assert first["phrase"]["p1"] == atoms


def test_funk_carries_its_tempos():
    rows = [(24 + k, 1, 14 if k == 0 else 0, 1 if k == 0 else 0) for k in range(16)]
    text, _, _ = lifted(*gt_tune([(0x41, 0), (0xFF, 0)], rows=rows, speed=[(6, 9)]))
    assert "C2.i1!funk:6/9," in text and "speedtab" not in text
    grid = SongPlayer(songio.loads(text)).render_grid(120).astype(int)
    steps = np.diff([t for t in range(1, 120) if grid[t, 0] != grid[t - 1, 0]])
    assert set(steps[::2]) == {6} and set(steps[1::2]) == {9}


def test_a_cutoff_byte_writes_its_top_eight_bits():
    pr = songio.library("goattracker2")
    rows, cmds = songio.codecs(pr, None), CommandCodec(pr["commands"], record_types(pr))
    assert cmds.encode(12, 0x21) == ("cut", 0x108) and cmds.decode("cut", 0x108) == (12, 0x21)
    assert rows["ctrltab"].decode({"cut": 0x108}) == {"w": 0xFC, "n": 0x21}
    with pytest.raises(CodecError, match="can't express !cut:265"):
        cmds.decode("cut", 0x109)
    with pytest.raises(CodecError, match=r"values of \['cut'\]"):
        rows["ctrltab"].decode({"cut": 0x109})
    with pytest.raises(CodecError, match="a byte form has no value"):
        RowCodec(record_types(pr)["ctrltab"], [{"keys": {"cut": {"byte": "n", "value": "n"}}}], 0)
