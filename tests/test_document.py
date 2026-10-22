"""The generic document: the lifter's song in unitracker-grammar's language with no player, the
native note table where it differs from ``F``, the init row, its refusals, and its proof on
unitracker-player against the native grid."""

import copy

import irs
import numpy as np
import pytest
from conftest import gt_tune, mini_song
from unitracker_grammar import NTSC_HZ, frequency, parse
from unitracker_grammar import table as f_table
from unitracker_player import read

from unitracker import cli, document
from unitracker.document import timeline, writer
from unitracker.document.timeline import END, TimelineError
from unitracker.lift import backends, base, verify
from unitracker.lift.goattracker import GoatTrackerLift
from unitracker.player import oracle, reach
from unitracker.player.vm import SongPlayer
from unitracker.playroutine.library import SongFormatError, library

TUNES = ("metal_warrior", "crosseyed", "csillag")
HARD_RESTART = {
    1: {"ad": 15, "ctrl": -1, "gate": 0, "hold": 2, "next": 2, "sr": 0},
    2: {"ad": -1, "ctrl": 9, "gate": 1, "next": 0, "sr": -1},
}


def gt_song() -> dict:
    """A goattracker2 song at NTSC timing and tempo 8: an instrument with a hard restart and a
    frequency expression ``F(N + 1) - F(N)`` running under a note, each voice looping it."""
    held = {"command": [16, 1]}
    return irs.song(
        "goattracker2",
        records={
            "ctrltab": {1: {"n": 0, "next": 0, "w": 65}},
            "ins": {1: {"ctrl": 1, "restart": 1, "sr": 240}},
            "restart": copy.deepcopy(HARD_RESTART),
        },
        phrases={
            "p1": [
                {"instrument": 1, "note": 36, "steps": 4},
                {**held, "note": 38},
                *[dict(held) for _ in range(3)],
            ]
        },
        play=["p1"] * 3,
        loop=0,
        exprs={1: "F(N + 1) - F(N)"},
        tempo=8,
        meta={"title": "T"},
        clock={"cycles_per_tick": 17095, "clock_hz": 1022727},
    )


def written(ir: dict, ticks: int = 400):
    """``(Native, generic text)`` of song ``ir``."""
    native = document.native_state(ir, ticks, ticks)
    return native, document.write(ir, native)


@pytest.mark.parametrize("name", TUNES)
def test_the_generic_documents_are_canonical(references, name):
    text = references[name].generic
    parse(text)
    assert "playroutine" not in text


def test_the_header_names_no_player_and_what_differs_from_pal():
    native, text = written(gt_song())
    lines = [line for line in text.splitlines() if not line.startswith("    ")]
    assert lines[:7] == [
        "unitracker: 1.20",
        "meta: {title: T}",
        "clock: {cycles_per_tick: 17095, clock_hz: 1022727}",
        "tempo: 8",
        "tables:",
        lines[5],
        "init: [-!vol:15!tempo:2, -, -]",
    ]
    assert "playroutine" not in text
    ftab = library("goattracker2")["tables"]["ftab"]
    table = list(read(text).tables["G"])
    assert table == ftab[: len(f_table(NTSC_HZ))] and len(table) > max(native.notes) + 1
    assert any(table[k] != frequency(k, NTSC_HZ) for k in native.notes)


def test_the_native_note_table_is_read_for_every_f():
    _, text = written(gt_song())
    assert "F(" not in text
    assert "x2: (G[N + 1] - G[N])*(φ >= 1) + G[N]" in text and "freq: 'G[N]'" in text
    assert "freq: '(v - G[N]) % 65536 + G[N]'" in text


def test_the_note_table_stays_f_where_the_native_one_agrees(references):
    text = references["crosseyed"].generic
    assert "G[" not in text and "F(N)" in text


def test_the_tables_an_expression_indexes_are_document_tables(references):
    doc = read(references["csillag"].generic)
    lib = library("sidwizard")["tables"]
    words = [hi << 8 | lo for hi, lo in zip(lib["freqhi"], lib["freqlo"])]
    assert list(doc.tables) == ["G", "W"]
    assert list(doc.tables["W"]) == [*lib["exptab"][:107], *words[:97]]


def test_the_lead_in_starts_each_voice_on_its_native_tick():
    native, text = written(gt_song())
    assert oracle.play(text, 40)[1] == list(native.starts) == [10, 10, 10]


def test_a_lead_shorter_than_a_step_restores_the_tempo_on_the_first_step(references):
    text = references["csillag"].generic
    assert "init: [-!vol:15!tempo:3, -, -]" in text
    lead = text.split("play: ", 1)[1].split("\n", 1)[0]
    assert lead == "[p40, p40, p40]" and "p40: [-]" in text
    first = text.split("play: ", 2)[2].split("\n", 1)[0]
    assert first == "[p39, p2, p3]" and "p39: [C5.i1!pw:x28!tempo:6*8, off*24, " in text
    assert "p1: [C5.i1!pw:x28*8, off*24, " in text


def test_with_command_keeps_the_grammar_order_and_an_existing_command():
    assert writer.with_command("C3.i1!freq:x1*4", "tempo", "6") == "C3.i1!tempo:6!freq:x1*4"
    assert writer.with_command("-!run:c1!pw", "tempo", "6/7") == "-!run:c1!tempo:6/7!pw"
    assert writer.with_command("-!tempo:5", "tempo", "6") == "-!tempo:5"


def dm_song(vinit: dict, ginit: dict, exprs: dict) -> dict:
    """A defMON song of rests whose state after init is ``vinit`` and ``ginit``."""
    return irs.song(
        "defmon",
        records={"vinit": {0: vinit}, "ginit": {0: ginit}},
        phrases={"p1": [{"steps": 4}]},
        play=["p1"] * 3,
        loop=0,
        exprs=exprs,
    )


def init_song() -> dict:
    """A one-voice song of a playroutine whose ``init`` record type has no generic form."""
    pr = {
        "name": "mini",
        "instrument": {"type": "ins", "base": 0},
        "record_types": {
            "ins": {"prefix": "i", "none": 0, "form": "map", "fields": {"a": 0}},
            "init": {"fixed": True, "form": "map", "fields": ["o"]},
        },
        "voice": {"out": {"width": 8}},
        "tick": [{"for_voices": [{"fetch": True}, {"set": {"out": "ins[1].a + init[0].o"}}]}],
        "ghost": {"voice": {0: "out"}},
    }
    records = {"ins": {1: {"a": 1}}, "init": {0: {"o": 5}}}
    return mini_song(pr, phrases={"a": [{"note": 24, "instrument": 1}]}, records=records)


def test_the_state_after_init_is_the_init_row():
    ir = dm_song({"pw": 0x0567, "tbase": 60, "sacc": 3}, {"xc": 1}, {1: "(v - 1) % 65536"})
    _, text = written(ir)
    assert "init: [C3!pw:1383!vol:15!cut:x1!freq:3!legato, -, -]" in text
    assert "x1: (v - 1) % 65536" in text and "vinit" not in text and "ginit" not in text
    grid, starts = oracle.play(text, 3)
    assert starts == [None, None, None]
    assert list(grid[0][2:5]) == [0x67, 0x05, 0] and grid[0][24] == 15
    assert [int(grid[t][22]) << 3 | int(grid[t][21]) for t in range(3)] == [2047, 2046, 2045]


def test_a_row_with_no_shared_key_is_a_named_gap():
    ir = gt_song()
    native = document.native_state(ir, 50, 50)
    respell = writer._Respell(ir, native)  # pylint: disable=protected-access
    with pytest.raises(document.GenericError, match=r"ctrl c1 row 0: row \[4, 32\] is in its type"):
        respell.row([4, 32], "ctrl c1 row 0")
    with pytest.raises(document.GenericError, match=r"ins i1: fields \['a'\] have no generic form"):
        respell.instrument({"a": 1}, "ins i1")
    with pytest.raises(document.GenericError, match=r"record type init \(0\) has no generic form"):
        document.write(init_song())


def test_a_document_that_plays_the_native_grid_verifies():
    lift = GoatTrackerLift(gt_tune([(0x41, 0), (0xFF, 0)])[0])
    names = [name for name, _ in lift.stages()]
    assert names[-1] == "verify" and lift.gap is None
    parse(lift.document)
    assert lift.verdict.ok and lift.verdict.n == lift.ticks
    assert lift.verdict.line() == f"verified: {lift.ticks} ticks play the reference grid"
    assert "ctrl: [{hold: 0, gate: 0}, {ctrl: 65, note: +0}, end]" in lift.document


def test_a_grid_that_differs_names_its_first_difference():
    _, text = written(gt_song())
    grid, starts = oracle.play(text, 60)
    assert verify.compare(text, grid).ok
    changed = grid.copy()
    changed[30, 4] ^= 1
    verdict = verify.compare(text, changed)
    assert (verdict.onset, verdict.whole) == ((30, 4), (30, 4)) and not verdict.ok
    assert verdict.line() == "mismatch: tick 30, register $04 (from tick 0: tick 30, $04)"
    early = grid.copy()
    early[starts[0] - 1, 5] ^= 1
    verdict = verify.compare(text, early)
    assert verdict.ok and verdict.whole == (starts[0] - 1, 5)
    ending = text.replace("  - {loop: 1}\n", "  - end\n")
    played = oracle.play(ending, 200)[0]
    verdict = verify.compare(ending, np.vstack([played, np.repeat(played[-1:], 10, axis=0)]))
    assert verdict.onset is None and verdict.played == len(played) and not verdict.ok
    assert (
        verdict.line() == f"mismatch: the render ends at tick {len(played)} of {len(played) + 10}"
    )


def test_the_lift_names_a_gap_and_a_song_whose_state_does_not_recur(monkeypatch):
    def gap(*_):
        raise document.GenericError("ins i1: fields ['vibrato'] have no generic form")

    monkeypatch.setattr(document, "write", gap)
    lift = GoatTrackerLift(gt_tune([(0x41, 0), (0xFF, 0)])[0])
    lift.run()
    assert (lift.document, lift.verdict) == (None, None) and "vibrato" in lift.gap

    def unbounded(_):
        raise reach.ReachabilityError("playback state did not recur")

    monkeypatch.setattr(base, "window", unbounded)
    lift.run()
    assert lift.verdict is None and "does not recur" in lift.gap


def test_backends_verify_a_document_against_its_tune(references):
    ref = references["metal_warrior"]
    assert backends.verify(ref.generic, ref.raw, ticks=1550).ok
    assert backends.verify(ref.generic, ref.raw, ticks=1600).onset == (1550, 0x0E)


class _Gap:  # pylint: disable=too-few-public-methods
    """A lift whose document has a gap."""

    document, verdict, gap = None, None, "record type speedtab (s1) has no generic form"

    @staticmethod
    def run():
        return {}


def test_the_lift_command_reports_a_gap(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(backends, "lifter", lambda *_: _Gap())
    sid = tmp_path / "t.sid"
    sid.write_bytes(b"")
    assert cli.lift_main([str(sid)]) == 1
    assert capsys.readouterr().err.strip().endswith("no generic document: " + _Gap.gap)


def play(name, transpose=0, repeat=1):
    return {"phrase": name, "transpose": transpose, "repeat": repeat}


def expand(track, ticks, lengths):
    """The ``(time, phrase, transpose)`` starts of a track over ``ticks`` steps."""
    intro, cycle = timeline._items(track)  # pylint: disable=protected-access
    out, t, k = [], 0, 0
    while t < ticks and (k < len(intro) or cycle):
        item = intro[k] if k < len(intro) else cycle[(k - len(intro)) % len(cycle)]
        out.append((t, *item))
        t += lengths[item[0]]
        k += 1
    return out


def row_starts(rows, loop, lengths, voices, ticks):
    """Each voice's ``(time, phrase, transpose)`` starts over ``ticks`` steps of timeline
    ``rows``: a row starts once the voice playing longest so far ends, wrapping to ``loop``."""
    out, ends, t, k = [[] for _ in range(voices)], [0] * voices, 0, 0
    while t < ticks and k < len(rows):
        for v, entry in enumerate(rows[k]):
            if entry == END:
                ends[v] = None
            elif entry is not None:
                out[v].append((t, *entry))
                ends[v] = t + lengths[entry[0]]
        live = [e for e in ends if e is not None]
        if not live:
            break
        t, k = min(live), k + 1
        if k == len(rows) and loop is not None:
            k = loop
    return out


def column(rows, voice):
    return [row[voice] for row in rows]


@pytest.mark.parametrize(
    "lengths,tracks,check",
    [
        (
            {"a": 4, "b": 4},
            [{"plays": [play("a"), play("b", 2)], "loop": 0}, {"plays": [play("b")], "loop": 0}],
            lambda rows, loop: (rows, loop) == ([[("a", 0), ("b", 0)], [("b", 2), ("b", 0)]], 0),
        ),
        (
            {"a": 2, "b": 3},
            [{"plays": [play("a")], "loop": 0}, {"plays": [play("b")], "loop": 0}],
            lambda rows, loop: loop == 0
            and column(rows, 0).count(("a", 0)) == 3
            and column(rows, 1).count(("b", 0)) == 2,
        ),
        (
            {"i": 3, "a": 2, "b": 4},
            [
                {"plays": [play("i"), play("a", repeat=2)], "loop": 1},
                {"plays": [play("b", 1)], "loop": 0},
            ],
            lambda rows, loop: True,
        ),
        (
            {"a": 2, "b": 5},
            [{"plays": [play("a")], "loop": None}, {"plays": [play("b")], "loop": 0}],
            lambda rows, loop: rows[1][0] == END,
        ),
        (
            {"a": 2, "b": 5},
            [{"plays": [play("a")], "loop": None}, {"plays": [play("b")], "loop": None}],
            lambda rows, loop: (rows, loop) == ([[("a", 0), ("b", 0)]], None),
        ),
        (
            {"a": 2, "b": 5},
            [{"plays": [play("a")], "loop": None}, {"plays": [play("b"), play("a")], "loop": None}],
            lambda rows, loop: rows[1:] == [[END, None], [None, ("a", 0)]],
        ),
    ],
)
def test_the_timeline_rows_play_each_track(lengths, tracks, check):
    rows, loop = timeline.to_rows(tracks, lengths)
    assert check(rows, loop)
    played = row_starts(rows, loop, lengths, len(tracks), 400)
    assert played == [expand(track, 400, lengths) for track in tracks]


def test_unaligned_loops_beyond_the_row_limit(monkeypatch):
    monkeypatch.setattr(timeline, "MAX_ROWS", 3)
    with pytest.raises(TimelineError, match="align"):
        timeline.to_rows(
            [{"plays": [play("a")], "loop": 0}, {"plays": [play("b")], "loop": 0}],
            {"a": 2, "b": 3},
        )


@pytest.mark.parametrize(
    "seq,loop,expect",
    [
        ("ab", 0, (0, 2)),
        ("abab", 0, (0, 2)),
        ("xabab", 1, (1, 2)),
        ("babab", 1, (0, 2)),
        ("abcbc", 3, (1, 2)),
        ("aaaa", 2, (0, 1)),
        ("abcabd", 3, (3, 3)),
    ],
)
def test_minimal_loop(seq, loop, expect):
    assert timeline.minimal_loop(list(seq), loop) == expect


FILTER_INS = [{"wave_ptr": 1, "filter_ptr": 1, "gateoff_timer": 2, "sustain_release": 0xF0}]
NO_NOTE = 0x80
IDLE_ROWS = {
    "waits one after another are one row": (
        {"wave": [(0x41, 0), (0x02, NO_NOTE), (0x03, NO_NOTE), (0x21, 12), (0xFF, 0)]},
        "{ctrl: 65, note: +0}, {wait: 6}, {ctrl: 33, note: +12}, end]",
    ),
    "a wait longer than one row is two": (
        {"wave": [(0x41, 0), (0x0F, NO_NOTE), (0x0F, NO_NOTE), (0x21, 12), (0xFF, 0)]},
        "{ctrl: 65, note: +0}, {wait: 15}, {wait: 15}, {ctrl: 33, note: +12}, end]",
    ),
    "a last wait is left out": (
        {"wave": [(0x41, 0), (0x21, 12), (0x05, NO_NOTE), (0xFF, 0)]},
        "{ctrl: 65, note: +0}, {ctrl: 33, note: +12}, end]",
    ),
    "zero sweeps one after another are one hold": (
        {
            "wave": [(0x41, 0), (0xFF, 0)],
            "instruments": FILTER_INS,
            "filt": [(0x90, 0xF1), (0x00, 0x40), (0x05, 0), (0x03, 0), (0xA0, 0xF2), (0xFF, 0)],
        },
        "{hold: 8}",
    ),
}


@pytest.mark.parametrize("tune,program", IDLE_ROWS.values(), ids=IDLE_ROWS)
def test_rows_that_only_last_are_written_as_the_fewest_rows(tune, program):
    lift = GoatTrackerLift(gt_tune(**tune)[0])
    lift.run()
    assert lift.verdict.ok, lift.verdict.line()
    assert program in " ".join(lift.document.split())


def first_event(ir: dict, **event) -> dict:
    """``ir`` with the first event of its first phrase replaced by ``event`` of as many steps."""
    events = ir["phrases"][next(iter(ir["phrases"]))]
    events[0] = {"steps": events[0].get("steps", 1), **event}
    return ir


REFUSED = {
    "two commands in sidwizard's one FX column": (
        "csillag",
        lambda ir: first_event(ir, note=40, instrument=1, commands=[[18, 4], [16, 0]]),
        "sidwizard: the player can't play !tempo and !freq on one event",
    ),
    "an envelope byte off its column": (
        "csillag",
        lambda ir: first_event(ir, note=40, instrument=1, command=[336, 12]),
        "!sr in the instrument column has no written form",
    ),
    "defmon's layer b armed twice": (
        "crosseyed",
        lambda ir: first_event(ir, note=72, commands=[[1, 2], [1, 2]]),
        "defmon: an event arms the instrument's filter twice",
    ),
}


@pytest.mark.parametrize("tune,change,match", REFUSED.values(), ids=REFUSED)
def test_the_writer_refuses_an_event_the_player_cannot_play(references, tune, change, match):
    ir = change(references[tune].ir())
    native = document.native_state(ir, 50, 50)
    with pytest.raises(SongFormatError, match=match):
        document.write(ir, native)


@pytest.fixture(scope="module", name="gt_lifted")
def fixture_gt_lifted() -> dict:
    """The lifter's song of a one-note goattracker2 tune."""
    return GoatTrackerLift(gt_tune([(0x41, 0), (0xFF, 0)])[0]).run()


def test_voices_that_end_and_loop_apart_play_as_the_vm_plays_them(gt_lifted):
    ir = copy.deepcopy(gt_lifted)
    ir["phrases"]["p02"] = [{"note": 31, "instrument": 1, "steps": 6}]
    ir["tracks"] = [
        {"plays": irs.plays("p00", "p02"), "loop": None},
        {"plays": irs.plays("p02", "p02", ("p02", 2)), "loop": 1},
        {"plays": irs.plays("p01"), "loop": None},
    ]
    text = document.write(ir)
    assert "  - play: [~, p2+2, ~]\n" in text and "  - play: [end, ~, ~]\n" in text
    assert verify.compare(text, SongPlayer(ir).render_grid(300)).ok


def test_a_song_whose_voices_all_end_ends_with_its_last_note(gt_lifted):
    ir = copy.deepcopy(gt_lifted)
    ir["phrases"]["p02"] = [{"note": 31, "instrument": 1, "steps": 6}]
    ir["tracks"] = [
        {"plays": irs.plays("p00", "p02"), "loop": None},
        {"plays": irs.plays("p02"), "loop": None},
        {"plays": irs.plays("p01"), "loop": None},
    ]
    text = document.write(ir)
    assert text.endswith("  - play: [p2, ~, end]\n  - end\n")
    played = verify.compare(text, SongPlayer(ir).render_grid(300)).played
    assert verify.compare(text, SongPlayer(ir).render_grid(played)).ok and played < 300


def test_the_restart_preset_is_one_per_song(gt_lifted):
    ir = copy.deepcopy(gt_lifted)
    restarts = ir["records"]["restart"]
    restarts[3], restarts[4] = {**restarts[1], "ad": 14, "next": 4}, dict(restarts[2])
    ir["records"]["ins"][2] = {**ir["records"]["ins"][1], "restart": 3}
    ir["phrases"]["p01"] = [{"note": 26, "instrument": 2, "steps": 16}]
    with pytest.raises(SongFormatError, match="the restart rows set ad to different values"):
        document.write(ir)
