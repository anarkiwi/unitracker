"""The generic document: the internal song in unitracker-grammar's language with no player, the
native note table where it differs from ``F``, the init row, its refusals, and its proof on
unitracker-player against the native grid."""

import numpy as np
import pytest
from conftest import doc_text, gt_tune
from test_format_document import song as mini
from unitracker_grammar import NTSC_HZ, frequency, parse
from unitracker_player import read

from unitracker import cli
from unitracker.format import generic, header, songio
from unitracker.lift import backends, base, verify
from unitracker.lift.goattracker import GoatTrackerLift
from unitracker.player import oracle, reach

GT = """unitracker: 1.20
playroutine: {ref: goattracker2}
meta: {title: T}
clock: {cycles_per_tick: 17095, clock_hz: 1022727}
tempo: 8
song:
  - def:
      x:
        x1: F(N + 1) - F(N)
      restart:
        r1: [{hold: 2, gate: 0, ad: 15, sr: 0}, {ctrl: 9}, end]
      ins:
        i1: {sr: 240, ctrl: [{ctrl: 65, note: +0}, end], restart: r1}
      phrase:
        p1: [C3.i1*4, D3!freq:x1*4]
    play: [p1, p1, p1]
  - {loop: 0}
"""


def written(text: str, ticks: int = 400):
    """``(internal song, Native, generic text)`` of internal document ``text``."""
    ir = songio.loads(text)
    native = generic.native_state(ir, ticks, ticks)
    return ir, native, generic.dumps(ir, native)


def test_the_header_names_no_player_and_what_differs_from_pal():
    _, native, text = written(GT)
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
    ftab = header.library("goattracker2")["tables"]["ftab"]
    table = list(read(text).tables["G"])
    assert table == [ftab[k] if k in native.notes else 0 for k in range(max(native.notes) + 1)]
    assert any(table[k] != frequency(k, NTSC_HZ) for k in native.notes)


def test_the_native_note_table_is_read_for_every_f():
    _, _, text = written(GT)
    assert "F(" not in text
    assert "x2: (G[N + 1] - G[N])*(φ >= 1) + G[N]" in text and "freq: 'G[N]'" in text
    assert "freq: '(v - G[N]) % 65536 + G[N]'" in text


def test_the_note_table_stays_f_where_the_native_one_agrees(references):
    text = references["crosseyed"].generic
    assert "G[" not in text and "F(N)" in text


def test_the_tables_an_expression_indexes_are_document_tables(references):
    doc = read(references["csillag"].generic)
    lib = header.library("sidwizard")["tables"]
    words = [hi << 8 | lo for hi, lo in zip(lib["freqhi"], lib["freqlo"])]
    assert list(doc.tables) == ["G", "W"]
    assert list(doc.tables["W"]) == [*lib["exptab"][:107], *words[:97]]


def test_the_lead_in_starts_each_voice_on_its_native_tick():
    _, native, text = written(GT)
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
    assert generic.with_command("C3.i1!freq:x1*4", "tempo", "6") == "C3.i1!tempo:6!freq:x1*4"
    assert generic.with_command("-!run:c1!pw", "tempo", "6/7") == "-!run:c1!tempo:6/7!pw"
    assert generic.with_command("-!tempo:5", "tempo", "6") == "-!tempo:5"


def dm_song(vinit: dict, ginit: dict) -> dict:
    """A defMON song of rests whose state after init is ``vinit`` and ``ginit``."""
    row = {"def": {"vinit": {0: vinit}, "ginit": {0: ginit}, "phrase": {"p1": ["-*4"]}}}
    row["play"] = ["p1"] * 3
    doc = {"unitracker": songio.VERSION, "playroutine": {"ref": "defmon"}, "song": [row]}
    doc["song"].append({"loop": 0})
    return songio.loads(doc_text(doc))


def test_the_state_after_init_is_the_init_row():
    ir = dm_song({"pw": 0x0567, "note": "C3", "freq": 3}, {"cut": "(v - 1) % 65536"})
    text = generic.dumps(ir, generic.native_state(ir, 400, 400))
    assert "init: [C3!pw:1383!vol:15!cut:x1!freq:3!legato, -, -]" in text
    assert "x1: (v - 1) % 65536" in text and "vinit" not in text and "ginit" not in text
    grid, starts = oracle.play(text, 3)
    assert starts == [None, None, None]
    assert list(grid[0][2:5]) == [0x67, 0x05, 0] and grid[0][24] == 15
    assert [int(grid[t][22]) << 3 | int(grid[t][21]) for t in range(3)] == [2047, 2046, 2045]


def test_a_row_with_no_shared_key_is_a_named_gap():
    ir = songio.loads(GT)
    native = generic.native_state(ir, 50, 50)
    respell = generic._Respell(ir, native)  # pylint: disable=protected-access
    with pytest.raises(generic.GenericError, match=r"ctrl c1 row 0: row \[4, 32\] is in its type"):
        respell.row([4, 32], "ctrl c1 row 0")
    with pytest.raises(generic.GenericError, match=r"ins i1: fields \['a'\] have no generic form"):
        respell.instrument({"a": 1}, "ins i1")
    with pytest.raises(generic.GenericError, match=r"record type init \(0\) has no generic form"):
        generic.dumps(mini())


def test_a_document_that_plays_the_native_grid_verifies():
    lift = GoatTrackerLift(gt_tune([(0x41, 0), (0xFF, 0)])[0])
    names = [name for name, _ in lift.stages()]
    assert names[-1] == "verify" and lift.gap is None
    parse(lift.document)
    assert lift.verdict.ok and lift.verdict.ticks == lift.ticks
    assert lift.verdict.line() == f"verified: {lift.ticks} ticks play the native grid"
    assert "ctrl: [{hold: 0, gate: 0}, {ctrl: 65, note: +0}, end]" in lift.document


def test_a_grid_that_differs_names_its_first_difference():
    _, _, text = written(GT)
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
        verdict.line() == f"mismatch: the document ends at tick {len(played)} of {len(played) + 10}"
    )


def test_the_lift_names_a_gap_and_a_song_whose_state_does_not_recur(monkeypatch):
    def gap(*_):
        raise generic.GenericError("ins i1: fields ['vibrato'] have no generic form")

    monkeypatch.setattr(generic, "dumps", gap)
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
    assert backends.verify(ref.generic, ref.raw, ticks=105).ok
    assert backends.verify(ref.generic, ref.raw, ticks=120).onset == (105, 0x0B)


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
