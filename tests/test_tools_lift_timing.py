"""The lift timing tool: its table, its slow-tune profiles and its summary."""

import csv

import lift_timing
from pysidtracker.testing import resolve_tune

GLOW = "MUSICIANS/I/Ilkke/Glow_Worm.sid"


def test_a_tune_is_timed_and_profiled_when_slow(glow_worm):
    assert glow_worm
    root = resolve_tune(GLOW).parents[3]
    quick = lift_timing.timed(GLOW, "DefMon", root, 120, 1e9)
    assert quick["verdict"] == "ok" and quick["seconds"] > 0 and not quick["profile"]
    slow = lift_timing.timed(GLOW, "DefMon", root, 120, 0.0)
    assert "lift" in slow["profile"] and slow["profile"].count("\n") >= lift_timing.TOP


def test_a_lift_past_the_limit_is_a_timeout(glow_worm):
    assert glow_worm
    seconds, what = lift_timing.verdict(resolve_tune(GLOW).read_bytes(), 1)
    assert what in ("ok", "timeout") and seconds < 30
    assert lift_timing.verdict(b"not a tune", 5)[1].startswith("error")


def test_the_tool_writes_the_table_and_prints_the_slow_tunes(glow_worm, tmp_path, capsys):
    assert glow_worm
    root = resolve_tune(GLOW).parents[3]
    table = tmp_path / "players.csv"
    with open(table, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["path", "player"])
        writer.writerow([root / GLOW, "DefMon"])
        writer.writerow([root / GLOW, "DMC"])
    argv = [str(table), "--root", str(root), "--jobs", "1", "--slow", "0", "--out", str(tmp_path)]
    assert lift_timing.main(argv) == 0
    with open(tmp_path / "lift_times.csv", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [r["path"] for r in rows] == [GLOW] and rows[0]["verdict"] == "ok"
    assert GLOW in (tmp_path / "profiles.txt").read_text()
    assert "tunes 1 max" in capsys.readouterr().out
