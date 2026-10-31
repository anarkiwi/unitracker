"""tools/lift_why.py: the first differing cell and the two players' steps beside it."""

import lift_why
from conftest import gt_tune
from unitracker.lift import goattracker2 as g

WAVE = [(0x41, 0x00), (0xFF, 0x00)]


def test_the_document_trace_lists_each_calls_events(capsys):
    core = gt_tune(WAVE)
    trace = lift_why.document_trace(g.document_text(core), 12)
    assert any(kind == "event" for steps in trace.values() for kind, _ in steps)
    lift_why.window(trace, [(n, [(4, 1)], {"gate": [255]}) for n in range(12)], 3, 5)
    out = capsys.readouterr().out
    assert "-- call 3" in out and "spec writes [(4, 1)]" in out and "{'gate': [255]}" in out


def test_main_reports_no_difference_for_an_exact_tune(metal_warrior, capsys):
    assert lift_why.main([str(metal_warrior), "--frames", "300"]) == 0
    assert "frame -1" in capsys.readouterr().out


def test_main_prints_the_window_around_a_frame(metal_warrior, capsys):
    argv = [str(metal_warrior), "--frame", "40", "--before", "1", "--after", "0", "--vars", "gate"]
    assert lift_why.main(argv) == 0
    out = capsys.readouterr().out
    assert "-- call 39" in out and "-- call 40" in out and "-- call 41" not in out


def test_first_cell_names_the_frame_and_register(metal_warrior):
    text, frame, reg = lift_why.first_cell(metal_warrior.read_bytes(), 0, 100)
    assert text.startswith("unitracker:2.5") and (frame, reg) == (-1, -1)
