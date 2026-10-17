"""Event instruments: a defMON event names one instrument for the programs its columns arm."""

import numpy as np
import pytest

from unitracker import eventins, songio
from unitracker.records import record_types
from unitracker.vm import SongPlayer, compile_phrase

DM = songio.library("defmon")
SPEC = eventins.spec(DM)
INS = record_types(DM)["ins"]
LAYER_B = next(int(code) for code in SPEC["commands"])


def song(phrase, ins="i1: {ctrl: c1, filter: c2}"):
    return f"""
unitracker: 1.19
playroutine: {{ref: defmon}}
meta: {{title: instruments}}
song:
  - def:
      ctrl:
        c1: [{{ctrl: 65, hold: 4}}, {{ctrl: 64}}, end]
        c2: [{{res: 15, route: 7, mode: 16}}, end]
      ins:
        {ins}
      phrase:
        p1: {phrase}
        p2: [-*8]
    play: [p1, p2, p2]
loop: ~
"""


def test_the_playroutine_writes_its_layers_as_one_instrument():
    assert eventins.document(DM) == ("ins", 0)
    assert eventins.document(songio.library("goattracker2")) == ("ins", 0)
    assert eventins.written_as(DM, LAYER_B) == "ins.filter"
    assert eventins.written_as(DM, 2) is None
    assert eventins.written_as(songio.library("sidwizard"), 112) is None


def test_fold_names_one_instrument_per_pair_of_columns():
    arr = np.array(
        [
            [1, 60, 3, LAYER_B, 7, 0, 0],
            [0, 0, 0, LAYER_B, 7, 0, 0],
            [1, 62, 3, 0, 0, -1, 2],
            [0, 0, 0, 0, 0, 0, 0],
        ]
    )
    seen = []

    def entry(fields):
        seen.append(fields)
        return len(seen)

    out = eventins.fold(arr, SPEC, 1, entry)
    assert seen == [{"ctrl": 2, "filter": 7}, {"filter": 7}, {"ctrl": 2}]
    assert out[:, 2].tolist() == [1, 2, 3, 0] and not out[:2, 3:5].any()
    assert out[2, 5:].tolist() == [-1, 2]
    twice = np.array([[0, 0, 0, LAYER_B, 1, LAYER_B, 2]])
    with pytest.raises(eventins.InstrumentError, match="arms the instrument's filter twice"):
        eventins.fold(twice, SPEC, 1, entry)


def test_unfold_arms_the_columns_an_instrument_holds():
    note = {"note": 60, "instrument": 1, "restart": 4, "steps": 2}
    assert eventins.unfold(note, {"ctrl": 2, "filter": 7}, SPEC, INS, 1) == {
        "note": 60,
        "instrument": 3,
        "command": [LAYER_B, 7],
        "restart": 4,
        "steps": 2,
    }
    assert eventins.unfold({"instrument": 1}, {"filter": 0}, SPEC, INS, 1) == {
        "command": [LAYER_B, 0]
    }
    both = {"instrument": 1, "commands": [[9, 1], [8, 2]]}
    assert eventins.unfold(both, {"ctrl": 0}, SPEC, INS, 1)["commands"] == [[9, 1], [8, 2]]
    with pytest.raises(eventins.InstrumentError, match="arms no program"):
        eventins.unfold({"instrument": 1}, {}, SPEC, INS, 1)


def test_an_instrument_reads_as_both_layers_and_writes_back():
    ir = songio.loads(
        song("[C4.i1*4, -.i2*4]", ins="i1: {ctrl: c1, filter: c2}\n        i2: {filter: c2}")
    )
    assert "ins" not in ir["records"]
    steps = compile_phrase(ir["phrases"]["p1"])
    assert steps[0, :5].tolist() == [1, 72, 1, LAYER_B, 2]
    assert steps[4, :5].tolist() == [0, 0, 0, LAYER_B, 2]
    text = songio.dumps(ir)
    assert "p1: [C4.i1*4, -.i2*4]" in text and "run2" not in text
    assert "i1: {ctrl: [{hold: 4, ctrl: 65}, {ctrl: 64}, end], filter: c1}" in text
    assert songio.dumps(songio.loads(text)) == text
    grid = SongPlayer(ir).render_grid(8)
    assert grid[1:5, 4].tolist() == [65, 65, 65, 65] and grid[2, 0x17] == 0xF7


def test_the_writer_names_the_layers_of_a_native_event():
    ir = songio.loads(song("[C4.i1*8]"))
    ir["phrases"]["p1"] = [{"note": 72, "instrument": 1, "steps": 4}, {"command": [LAYER_B, 2]}]
    ir["phrases"]["p1"].append({"note": 72, "instrument": 1, "command": [LAYER_B, 2], "steps": 3})
    text = songio.dumps(ir)
    assert "p1: [C4.i1*4, -.i2, C4.i3*3]" in text
    assert "i1: {ctrl: c1}" in text and "i2: {filter: c2}" in text
    assert "i3: {ctrl: c1, filter: c2}" in text


@pytest.mark.parametrize(
    "phrase,ins,match",
    [
        ("[C4.i1!run2:@c1*8]", "i1: {ctrl: c1}", "!run2 is retired in v1.17"),
        ("[C4.i1*8]", "i1: {ctrl: c1, pw: 256}", "unknown field 'pw'"),
        ("[C4.i1*8]", "i1: {ctrl: c1, restart: c2}", "unknown field 'restart'"),
        ("[C4.i1*8]", "i1: {}", "phrase p1: an instrument arms no program"),
        ("[C4.c1*8]", "i1: {ctrl: c1}", "'c1' is a ctrl, not a ins"),
    ],
)
def test_the_reader_refuses_what_the_two_layers_cannot_hold(phrase, ins, match):
    with pytest.raises(songio.SongFormatError, match=match):
        songio.loads(song(phrase, ins))


def test_the_writer_refuses_an_event_that_arms_layer_b_twice():
    ir = songio.loads(song("[C4.i1*8]"))
    ir["phrases"]["p1"] = [{"note": 72, "commands": [[LAYER_B, 2], [LAYER_B, 2]], "steps": 8}]
    with pytest.raises(
        songio.SongFormatError, match="defmon: an event arms the instrument's filter"
    ):
        songio.dumps(ir)
