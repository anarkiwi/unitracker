"""The generic report: step basis, rows, grouping and the CLI."""

from unitracker.modfit import generic
from unitracker.modfit.cli import main
from unitracker.modfit.factor import basis
from unitracker.modfit.form import Form
from unitracker.modfit.generic import Report, Row, grouping, table

TOY = {"voice": {"x": {"width": 8}, "note": {"width": 8}}, "tables": {"t": [1, 2, 3]}}


def test_step_basis():
    f = Form(TOY)
    assert basis(3, {"note"}) == "constant"
    assert basis(f.expr("t[note + 1] - t[note]", {}), {"note"}) == "note-relative t"
    assert basis(f.expr("t[arg] + 1", {}), {"note"}) == "decoded arg, t"
    assert basis(f.expr("x * 2", {}), {"note"}) == "cells x"


def row(player, label, role, kind, temporal) -> Row:
    return Row(player, label, "c", role, "1", "constant", "-", kind, kind, temporal, temporal, "ok")


def test_grouping_counts_players():
    rows = [
        row("a", "!x", "FREQ", "accumulate", "timer"),
        row("b", "!y", "FREQ", "accumulate", "timer"),
    ]
    rows.append(row("a", "!z", "PW", "bounce", "row duration"))
    lines = grouping(rows)
    assert lines[2] == "FREQ | accumulate | timer | 2 | a:!x, b:!y"
    assert table(rows)[2].startswith("a | !x | c | FREQ")


def test_defmon_rows_and_timer_gates():
    rep = Report("defmon")
    rows = rep.record_rows("procs.pulse", "pw")
    assert [r.kind for r in rows] == ["bounce", "bounce"] and all(
        r.status == "proved" for r in rows
    )
    assert (
        rep.summary("cnt")
        == "timer cnt (-1): counts [0..127: p + 1] [128..255: 1] ticks for start cnt = p in 0..255"
    )
    [acid] = rep.record_rows("procs.acid", "acc")
    assert acid.role == "CUT[3:10]" and acid.kind == "reads v" and acid.basis == "cells astep, sub"


def test_cli_generic(tmp_path, monkeypatch):
    monkeypatch.setattr(generic, "keywords", lambda pr: [])
    out = tmp_path / "generic.txt"
    main(["--out", str(out), "generic", "defmon"])
    text = out.read_text()
    assert "# Grouping by role, respond and temporal kind" in text and "== defmon timers" in text
    assert "defmon | procs.acid [always] | acc | CUT[3:10]" in text
