"""Lifting GoatTracker 2 bytes to unitracker records, phrases and tracks; synthetic tunes
play their native player."""

from types import SimpleNamespace

import numpy as np
import pytest
import yaml
from conftest import gt_tune, table
from pygoattracker import Player
from pygoattracker import constants as C
from pygoattracker.model import (
    Instrument,
    Orderlist,
    PlayPattern,
    Repeat,
    Row,
    Song,
    Transpose,
)

from unitracker.lift import goattracker as gt
from unitracker.playroutine.codec import CodecError, CommandCodec, RowCodec, row_codecs
from unitracker.playroutine.library import library
from unitracker.playroutine.records import record_types
from unitracker.modfit.convert import ConversionError
from unitracker.player.vm import SongPlayer, event_commands


# Each table's rows and the records it lifts to, as ``keys`` values (None: absent).
@pytest.mark.parametrize(
    "records,rows,keys,expected",
    [
        (
            gt.wave_records,
            [(0x41, 0x00), (0x02, 0x81), (0xE1, 0x80), (0xF5, 0x40), (0xFF, 0x01)],
            ("w", "n", "next"),
            [(0x41, 0x00, 2), (0x02, 0x81, 3), (0xE1, 0x80, 4), (0xF5, 0x40, 1), (0x00, 0x01, 6)],
        ),
        (
            gt.pulse_records,
            [(0x84, 0x00), (0x20, 0xF0), (0x10, 0x10), (0xFF, 0x02)],
            ("l", "r", "next"),
            [(0x84, 0x00, 2), (0x20, 0xF0, 3), (0x10, 0x10, 2), (0x8F, 0x02, 5)],
        ),
        (
            gt.filter_records,
            [(0x90, 0xF1), (0x00, 0x40), (0x10, 0x02), (0x00, 0x22), (0xA0, 0x10), (0xFF, 0x00)],
            ("l", "r", "cut", "next"),
            [(0x90, 0xF1, 0x40, 3), (0, 0x40, None, 3), (0x10, 0x02, None, 4)]
            + [(0, 0x22, None, 5), (0xA0, 0x10, None, 0), (0xF0, 0x00, 0x00, 8)],
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
    assert gt.native_start(Player(song)) == {"tempo": 5, "funk0": 8, "funk1": 5}
    song.instruments[62] = Instrument(attack_decay=7)
    assert gt.native_start(Player(song))["tempo"] == 6


@pytest.mark.parametrize(
    "start,tempo",
    [((5, 8, 5), 6), ((0, 8, 5), [9, 6]), ((1, 8, 5), [6, 9]), ((0, 6, 6), 7)],
)
def test_the_initial_tempo_is_the_native_tempo_cells(start, tempo):
    assert gt.initial_tempo(dict(zip(("tempo", "funk0", "funk1"), start))) == tempo


def test_a_song_starting_on_its_swings_second_tempo_plays_the_player():
    dec, native = gt_tune([(0x41, 0), (0xFF, 0)])
    dec.info.build.deftempo = 1
    native.default_tempo = 1
    text, _, _, _ = lifted(dec, native)
    assert "tempo: [6, 9]" in text


def tempo_rows(*commands):
    """A rising scale on voice 1 with each ``(row, command, argument)`` set."""
    rows = [(24 + k, 1 if k == 0 else 0, 0, 0) for k in range(16)]
    for k, code, arg in commands:
        rows[k] = (*rows[k][:2], code, arg)
    return rows


@pytest.mark.parametrize(
    "commands,written",
    [
        ([(0, 14, 1)], ["C2.i1!tempo:6/9,"]),
        ([(0, 14, 1), (8, 15, 0)], ["C2.i1!tempo:6/9,", "G#2!tempo:6/9,"]),
        ([(0, 15, 1)], ["C2.i1!tempo:6/9,"]),
        ([(0, 14, 0)], ["C2.i1!tempo:9/6,"]),
        ([(0, 15, 0x80)], ["C2.i1!vtempo:9/6,"]),
        ([(0, 15, 0x81)], ["C2.i1!vtempo:6/9,"]),
        ([(0, 15, 0), (4, 15, 0x81)], ["C2.i1!tempo:9/6,", "E2!vtempo:6/9,"]),
    ],
)
def test_a_swing_names_its_pair_and_plays_the_player(commands, written):
    text, _, _, _ = lifted(
        *gt_tune([(0x41, 0), (0xFF, 0)], rows=tempo_rows(*commands), speed=[(6, 9)])
    )
    assert all(w in text for w in written) and "speedtab" not in text


def test_a_swing_meeting_another_pair_on_each_pass_is_written_out_until_the_passes_repeat():
    dec, native = gt_tune(
        [(0x41, 0), (0xFF, 0)], rows=tempo_rows((4, 15, 0), (8, 14, 1)), speed=[(6, 9)]
    )
    text, _, _, _ = lifted(dec, native, 2000)
    played = [e for e in yaml.safe_load(text)["song"] if isinstance(e, dict) and "play" in e]
    assert "!tempo:9/6" in text and "!tempo:6/9" in text and len(played) >= 2


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
        gt.GoatTrackerLift(SimpleNamespace(info=info)).run()


def test_the_reference_lift_holds_the_tunes_constants(references):
    song = references["metal_warrior"].ir()
    assert song["meta"]["title"] == "Metal Warrior Title Remix"
    assert song["clock"] == gt.PAL_CLOCK
    assert not song["playroutine"].get("constants") and song["tempo"] == 12
    assert (song["records"]["restart"][1]["ad"], song["records"]["restart"][1]["sr"]) == (15, 0)
    assert [len(t["plays"]) for t in song["tracks"]] == [5, 5, 5]
    assert len(song["tables"]["ftab"]) == 128
    assert song["records"]["ins"][1]["sr"] == 0x88


REST = (None, 0, 0, 0)
SPEEDS = [(0, 0x20), (0x80, 1), (0x02, 0x10)]
TWO_NOTES = [(24, 1, 0, 0)] + [REST] * 7 + [(31, 1, 0, 0)] + [REST] * 7
FIRST = {"ctrl": 65, "note": 0}
RESTARTED = {"hold": 0, "gate": 0}
COMMANDS = {
    "wave": [(0x41, 0), (0xF5, 25), (0xF6, 200), (0xF9, 1), (0xFA, 1), (0xFB, 0xC2), (0xFC, 0x80)]
    + [(0xFD, 9), (0xFF, 0)],
    "pulse": [(0x88, 0), (0xFF, 0)],
    "filt": [(0x90, 0x31), (0x00, 0x40), (0xFF, 0)],
}


def lifted(dec, native, ticks=400) -> tuple[str, dict, SongPlayer, np.ndarray]:
    """``(generic document, the lifter's song, its player, native grid)`` of a synthetic tune's
    lift, asserting the song replays the native grid on the VM and the document is written."""
    lift = gt.GoatTrackerLift(dec)
    song = lift.run()
    theirs = np.asarray(native.render_grid(ticks), dtype=np.uint8)
    player = SongPlayer(song)
    assert player.mismatch(theirs) is None
    assert lift.document is not None, lift.gap
    return lift.document, song, player, theirs


def song_def(text: str) -> dict:
    """Every ``def`` section of a generic document, merged per kind."""
    out = {}
    for entry in yaml.safe_load(text)["song"]:
        for kind, names in entry.get("def", {}).items():
            out.setdefault(kind, {}).update(names)
    return out


# Each case's program as written: ``...`` a row of any form, a string value a fragment of the
# written value.
WAVETABLE_TUNES = {
    "commands are their shared keys": (
        COMMANDS,
        ("ins", "i1", "ctrl"),
        [RESTARTED, FIRST, {"ad": 25}, {"sr": 200}, {"run": "pw1"}, {"run": "f1"}]
        + [{"res": 12, "route": 2}, {"cut": 1024}, {"vol": 9}, "end"],
    ),
    "steps are their rows' freq": (
        {
            "wave": [(0x41, 0), (0xF4, 3), (0xF4, 3), (0xF3, 1), (0xF2, 2), (0xF1, 1)]
            + [(0x41, 0x80), (0xFF, 7)],
            "rows": TWO_NOTES,
            "speed": SPEEDS,
        },
        ("ins", "i1", "ctrl"),
        [RESTARTED, FIRST, {"freq": "+ 16"}, {"freq": "+ 16"}, ...]
        + [{"freq": "(F((N + 1) % 128) - F(N)) % 65536 // 2"}]
        + [{"freq": "+ 32"}, {"ctrl": 65}, {"loop": 7}],
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
        [RESTARTED, FIRST, {"wait": 1}, {"ctrl": 33}, {"loop": 3}],
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
        [RESTARTED, FIRST, {"run": "pw1"}, {"run": "f1"}, {"wait": 3}, {"pw": "none"}]
        + [{"wait": 3}, {"filter": "none"}, {"wait": 3}, {"run": "f1"}, {"wait": 3}]
        + [{"res": 0, "route": 0, "filter": "none"}, {"ctrl": 65}, {"loop": 12}],
    ),
}


@pytest.mark.parametrize("tune,path,rows", WAVETABLE_TUNES.values(), ids=WAVETABLE_TUNES)
def test_wavetable_rows_lift_to_their_shared_form(tune, path, rows):
    text, _, _, _ = lifted(*gt_tune(**tune))
    got = song_def(text)
    for key in path:
        got = got[key]
    assert len(got) == len(rows) and "speedtab" not in text
    assert all(is_written(g, r) for g, r in zip(got, rows))


def is_written(row, want) -> bool:
    """Whether written program row ``row`` is ``want`` (see :data:`WAVETABLE_TUNES`)."""
    if want is ...:
        return isinstance(row, dict)
    if not isinstance(want, dict):
        return row == want
    return row.keys() == want.keys() and all(
        v in row[k] if isinstance(v, str) else row[k] == v for k, v in want.items()
    )


def test_wavetable_commands_set_their_registers():
    _, _, _, theirs = lifted(*gt_tune(**COMMANDS))
    last = theirs[-1].astype(int)
    assert (last[5], last[6], last[2] | last[3] << 8) == (25, 200, 2048)
    assert (last[22], last[23], last[24]) == (0x80, 0xC2, 0x10 | 9)


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
    _, _, player, theirs = lifted(*gt_tune(**tune))
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
        gt.GoatTrackerLift(dec).run()


VIBRATO = {"wave_ptr": 1, "gateoff_timer": 2, "sustain_release": 0xF0, "vibrato_param": 1}
NOTES = [(24, 1, 0, 0)] + [REST] * 7 + [(31, 1, 0, 0), REST, (36, 1, 0, 0)] + [REST] * 5
RESUMED = [(24, 1, 0, 0)] + [REST] * 3 + [(None, 0, 1, 1)] + [REST] * 3 + [(31, 1, 0, 0)]
CHANGED = [(24, 1, 0, 0)] + [REST] * 3 + [(None, 2, 0, 0)] + [REST] * 3 + [(36, 2, 0, 0)]
RESUMED_ATOMS = ["C2.i1*4", "-!freq:x1", "-!freq:x2*3", "G2*8"]
CHANGED_ATOMS = ["C2.i1*4", "-.i2!freq:x1*4", "C3*8"]


# Each case: instrument 1's vibrato delay, the rows, a second instrument, a fragment of each
# instrument's freq (None: it has none), and the first phrase.
@pytest.mark.parametrize(
    "delay,rows,second,freqs,atoms",
    [
        (1, NOTES, None, {1: "((φ - 2) % 8 >= 4)"}, ["C2.i1*8", "G2*2", "C3*6"]),
        (3, NOTES, None, {1: "(φ >= 2)"}, ["C2.i1*8", "G2*2", "C3*6"]),
        (40, NOTES, None, {1: "(φ >= 38)"}, ["C2.i1*8", "G2*2", "C3*6"]),
        (0, TWO_NOTES, None, {1: None}, ["C2.i1*8", "G2*8"]),
        (3, RESUMED + [REST] * 7, None, {}, RESUMED_ATOMS),
        (
            3,
            CHANGED + [REST] * 7,
            {**VIBRATO, "vibrato_param": 2, "vibrato_delay": 1},
            {1: "(φ >= 2)", 2: "F((N + 1) % 128)"},
            CHANGED_ATOMS,
        ),
    ],
    ids=["delay 1", "delay 3", "delay 40", "no delay is none", "resumed", "instrument change"],
)
def test_the_instrument_vibrato_is_the_instrument_freq(delay, rows, second, freqs, atoms):
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    ins = [{**VIBRATO, "vibrato_delay": delay}] + ([second] if second else [])
    text, song, _, _ = lifted(
        *gt_tune([(0x41, 0), (0xFF, 0)], ins, rows, speed=[(2, 0x20), (0x82, 3)])
    )
    assert "speed" not in text and "vd:" not in text
    for number, fragment in freqs.items():
        freq = song["records"]["ins"][number].get("freq")
        assert freq is None if fragment is None else fragment in song["exprs"][freq]
    assert song_def(text)["phrase"]["p1"] == atoms


def test_a_swing_alternates_its_tempos():
    rows = tempo_rows((0, 14, 1))
    _, song, _, _ = lifted(*gt_tune([(0x41, 0), (0xFF, 0)], rows=rows, speed=[(6, 9)]))
    grid = SongPlayer(song).render_grid(120).astype(int)
    steps = np.diff([t for t in range(1, 120) if grid[t, 0] != grid[t - 1, 0]])
    assert set(steps[::2]) == {6} and set(steps[1::2]) == {9}


def test_a_cutoff_byte_writes_its_top_eight_bits():
    pr = library("goattracker2")
    rows, cmds = row_codecs(pr, None), CommandCodec(pr["commands"], record_types(pr))
    assert cmds.encode(12, 0x21) == ("cut", 0x108) and cmds.decode("cut", 0x108) == (12, 0x21)
    assert rows["ctrltab"].decode({"cut": 0x108}) == {"w": 0xFC, "n": 0x21}
    with pytest.raises(CodecError, match="can't express !cut:265"):
        cmds.decode("cut", 0x109)
    with pytest.raises(CodecError, match=r"values of \['cut'\]"):
        rows["ctrltab"].decode({"cut": 0x109})
    with pytest.raises(CodecError, match="a byte form has no value"):
        RowCodec(record_types(pr)["ctrltab"], [{"keys": {"cut": {"byte": "n", "value": "n"}}}], 0)


@pytest.mark.parametrize(
    "commands",
    [[(4, 13, 0x20)], [(4, 4, 1), (8, 13, 0x20), (9, 13, 0xFF)], [(4, 1, 1), (6, 13, 0x10)]],
    ids=["after no command", "during a vibrato", "during a portamento"],
)
def test_a_d10_to_dff_is_the_command_it_keeps_running(commands):
    rows = [(24, 1, 0, 0)] + [REST] * 15
    for k, code, arg in commands:
        rows[k] = (None, 0, code, arg)
    dec, native = gt_tune([(0x41, 0), (0xFF, 0)], rows=rows, speed=[(2, 0x20)])
    _, song, _, _ = lifted(dec, native)
    events = [e for phrase in song["phrases"].values() for e in phrase]
    assert all(code != C.CMD_SETMASTERVOL for e in events for code, _ in event_commands(e))


def test_a_toneporta_of_speed_0_is_freq_at_the_notes_frequency():
    rows = [(24, 1, 0, 0)] + [REST] * 3 + [(31, 0, 3, 0)] + [REST] * 3
    rows += [(36, 1, 0, 0)] + [REST] * 7
    dec, native = gt_tune([(0x41, 0), (0xFF, 0)], rows=rows)
    out = gt.snapped(gt.GoatTrackerLift(dec, expressions=False).run())
    cmds = [list(c) for e in out["phrases"]["p00"] for c in event_commands(e)]
    assert [3, 0] not in cmds and [gt.LEGATO, 0] in cmds
    (index,) = [a for c, a in cmds if c == gt.FREQ]
    assert out["exprs"][index] == gt.AT_NOTE
    theirs = np.asarray(native.render_grid(400), dtype=np.uint8)
    assert SongPlayer(out).mismatch(theirs) is None
    assert gt.snapped(out) is out


def test_an_arpeggio_under_a_toneporta_of_speed_0_keeps_one_freq():
    rows = [(24, 1, 0, 0)] + [REST] * 3 + [(31, 0, 3, 0)] + [REST] * 11
    arpeggio = [(0x41, 0), (0x04, 0x00), (0x04, 0x03), (0xFF, 2)]
    text, _, _, _ = lifted(*gt_tune(arpeggio, rows=rows))
    first = song_def(text)
    assert first["phrase"]["p1"] == ["C2.i1*4", "G2!freq:x1!legato", "-!freq:none*11"]
    assert first["x"] == {"x1": 0}


ZERO_SWEEPS = {
    "a native zero sweep": ([(0x90, 0xF1), (0x00, 0x40), (0x05, 0x00), (0xA0, 0xF2)], {"hold": 5}),
    "a sweep the expression replaces": (
        [(0x90, 0xF1), (0x00, 0x40), (0x06, 0x03), (0xA0, 0xF2)],
        {"hold": 6},
    ),
}


@pytest.mark.parametrize("filt,held", ZERO_SWEEPS.values(), ids=ZERO_SWEEPS)
def test_a_zero_sweep_holds_its_ticks(filt, held):
    instruments = [{"wave_ptr": 1, "filter_ptr": 1, "gateoff_timer": 2, "sustain_release": 0xF0}]
    text, _, _, theirs = lifted(
        *gt_tune([(0x41, 0), (0xFF, 0)], instruments, filt=filt + [(0xFF, 0)])
    )
    assert held in song_def(text)["ins"]["i1"]["filter"]
    assert 0xF2 in theirs[:, 0x17]


def test_a_set_cutoff_row_before_a_mode_row_holds_its_expression():
    instruments = [{"wave_ptr": 1, "filter_ptr": 1, "gateoff_timer": 2, "sustain_release": 0xF0}]
    filt = [(0x00, 0x60), (0x90, 0xF1), (0x04, 0x02), (0xFF, 0)]
    text, _, _, theirs = lifted(*gt_tune([(0x41, 0), (0xFF, 0)], instruments, filt=filt))
    first, mode, end = song_def(text)["ins"]["i1"]["filter"]
    assert list(first) == ["cut"] and "φ" in first["cut"]
    assert mode == {"res": 15, "route": 1, "mode": 16} and end == "end"
    assert 0x68 in theirs[:, 0x16]


JUMPS_TO_JUMPS = {
    "wave": {"wave": [(0x41, 0), (0xFF, 3), (0xFF, 0x05), (0x21, 0x80), (0xFF, 4)]},
    "pulse": {
        "wave": [(0x41, 0), (0xFF, 0)],
        "pulse": [(0x84, 0), (0x08, 0x7F), (0xFF, 3), (0x87, 0x00), (0xFF, 0)],
    },
    "filter": {
        "wave": [(0x41, 0), (0xFF, 0)],
        "filt": [(0x90, 0xF1), (0x00, 0x40), (0x04, 0x02), (0xFF, 4), (0xA0, 0xF2), (0xFF, 0)],
    },
}


@pytest.mark.parametrize("tune", JUMPS_TO_JUMPS.values(), ids=JUMPS_TO_JUMPS)
def test_a_jump_to_a_jump_row_reads_that_row_as_data(tune):
    ins = {"wave_ptr": 1, "pulse_ptr": 1 if "pulse" in tune else 0, "gateoff_timer": 2}
    ins.update(sustain_release=0xF0, filter_ptr=1 if "filt" in tune else 0)
    lifted(*gt_tune(instruments=[ins], **tune))
