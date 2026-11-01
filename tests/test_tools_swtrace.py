"""The SID-Wizard state tracer prints the layout cells and registers frame by frame."""

import swtrace


def test_the_trace_follows_cells_of_a_voice_and_its_registers(must_dash):
    raw = must_dash.read_bytes()
    rows = list(swtrace.trace(raw, 6, ["spdcnt@0", "curins"], voice=0))
    assert [r[0] for r in rows] == list(range(6)) and len(rows[0]) == 1 + 2 + 7
    assert [r[1] for r in rows] == [1, 2, 3, 4, 5, 6]


def test_the_tool_prints_a_header_and_a_row_a_frame(must_dash, capsys):
    argv = [str(must_dash), "3", "spdcnt@1", "--regs", "0"]
    assert swtrace.main(argv) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split()[:2] == ["frame", "spdcnt@1"] and len(lines) == 4
