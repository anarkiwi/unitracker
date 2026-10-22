"""tools/modulators.py: firing schedules, cell writers, occurrences and expression pairs."""

import modulators
from test_document import gt_song


def test_a_command_first_fired_a_tick_after_its_note_is_listed_with_its_ticks():
    lines = modulators.firings(gt_song(), ("freq", 0), (41, 44))
    assert lines[0] == "freq 'F(N + 1) - F(N)': 2 modulators, agree"
    assert lines[1].split(" by ")[1] == "('p1', 1, 'note command'): skips [0, 7, 15, 23, 31]"
    assert lines[-3:] == [
        "41 native None None generic (φ, start) (0, 41)",
        "42 native None None generic (φ, start) (0, 42)",
        "43 native 1 F(N + 1) - F(N) generic (φ, start) (1, 42)",
    ]
    assert "6 modulators, agree" in "\n".join(modulators.firings(gt_song(), timed=True))


def test_a_tune_is_lifted_and_its_schedules_and_occurrences_reported(references, capsys):
    sid = str(references["metal_warrior"].sid)
    assert modulators.main(["firings", sid, "--cell", "pw:0", "--shown", "1"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines and all(line.startswith(("pw ", "    ")) for line in lines)
    assert modulators.main(["occurrences", sid, "--unit", "pulse", "--cells", "pw"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines and all(line.startswith("pulse v") and " pw=" in line for line in lines)


def test_each_units_cell_writers_and_whether_its_starts_write_it(capsys):
    assert modulators.main(["writers", "goattracker2"]) == 0
    text = capsys.readouterr().out
    pulse = text.split("## unit pulse")[1].split("## unit")[0]
    assert "  init " in pulse and "accumulate" in pulse
    assert "start " in pulse and ("writes the cell" in pulse or "inherits the cell" in pulse)
    assert modulators.hook_path({"type": "t", "template": "each"}) == "record_types.t.each"
    assert modulators.hook_path({"record": "r", "field": "f"}) == "records.r."


def test_expression_pairs_are_proved_equal_where_they_are(tmp_path, capsys):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    doc = "song:\n  - def:\n      x:\n        x1: '{}'\n        x2: '{}'\n"
    (old / "t.yaml").write_text(doc.format("v + 256", "abs(v)"))
    (new / "t.yaml").write_text(doc.format("v", "v"))
    assert modulators.main(["exprs", str(old), str(new), "--bits", "8"]) == 0
    text = capsys.readouterr().out
    assert "song/0/def/x/x1: equal for v < 2**8 modulo 2**8" in text
    assert "song/0/def/x/x2: equal for v < 2**8\n" in text
    assert "  before (4 tokens, 7 chars): v + 256" in text and "0 pairs not in fewer tokens" in text
