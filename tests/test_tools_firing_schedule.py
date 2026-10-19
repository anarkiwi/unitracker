"""tools/firing_schedule.py: each expression's native firing schedule over the generic
document's modulators."""

import importlib.util
from pathlib import Path

from test_document import gt_song

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "firing_schedule", ROOT / "tools" / "firing_schedule.py"
)
firing_schedule = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(firing_schedule)


def test_a_command_first_fired_a_tick_after_its_note_is_listed_with_its_ticks(capsys):
    firing_schedule.run(gt_song(), ("freq", 0), (41, 44))
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "freq 'F(N + 1) - F(N)': 2 modulators, agree"
    assert lines[1].split(" by ")[1] == "('p1', 1, 'note command'): skips [0, 7, 15, 23, 31]"
    assert lines[-3:] == [
        "41 native None None generic (φ, start) (0, 41)",
        "42 native None None generic (φ, start) (0, 42)",
        "43 native 1 F(N + 1) - F(N) generic (φ, start) (1, 42)",
    ]
    firing_schedule.run(gt_song(), timed=True)
    assert "6 modulators, agree" in capsys.readouterr().out


def test_a_tune_is_lifted_and_its_schedules_reported(references, capsys):
    ref = references["metal_warrior"]
    assert firing_schedule.main([str(ref.sid), "--cell", "pw:0", "--shown", "1"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines and all(line.startswith(("pw ", "    ")) for line in lines)
