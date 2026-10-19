"""tools/sample_tunes.py: a balanced sample of lifted tunes, refilled per player until its quota."""

import csv
import importlib.util
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sample_tunes", ROOT / "tools" / "sample_tunes.py")
sample_tunes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sample_tunes)


def _tunes_csv(tmp_path, rows):
    path = tmp_path / "tunes.csv"
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, ["path", "md5", "player", "songs", "clock", "lengths"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def _row(i, player, songs=1):
    return {
        "path": f"d/t{i}.sid",
        "md5": f"{i:032x}",
        "player": player,
        "songs": songs,
        "clock": "PAL",
        "lengths": "60",
    }


def test_the_pools_keep_single_subtune_tunes_of_the_lifted_players(tmp_path):
    """Only tunes with at most the subtune limit, of the three lifted players, are drawn."""
    rows = [
        _row(1, "GoatTracker_V2.x"),
        _row(2, "GoatTracker_V2.x", songs=3),
        _row(3, "GoatTracker_V1.x"),
        _row(4, "Hermit/SidWizard_V1.x"),
        _row(5, "DefMon"),
        _row(6, "GoatTracker_V2/Mini"),
    ]
    pools = sample_tunes.pool_by_player(_tunes_csv(tmp_path, rows), 1)
    assert {k: [r["path"] for r in v] for k, v in pools.items()} == {
        "goattracker2": ["d/t1.sid"],
        "sidwizard": ["d/t4.sid"],
        "defmon": ["d/t5.sid"],
    }
    assert len(sample_tunes.pool_by_player(_tunes_csv(tmp_path, rows), 3)["goattracker2"]) == 2


def test_quotas_split_the_remaining_count_over_the_open_pools():
    """Closed pools' lifts come off the count; the rest splits evenly, less each pool's own."""
    assert sample_tunes.quotas(10, ["a", "b", "c"], {"a": 1, "b": 0, "c": 0}) == {
        "a": 3,
        "b": 3,
        "c": 3,
    }
    assert sample_tunes.quotas(4, ["a"], {"a": 2, "b": 1}) == {"a": 1}
    assert sample_tunes.quotas(12, ["a", "b"], {"a": 2, "b": 5, "c": 0}) == {"a": 4, "b": 1}
    assert sample_tunes.quotas(2, ["a", "b"], {"a": 2, "b": 1}) == {"a": 0, "b": 0}


def test_run_refills_each_pool_until_its_quota_and_hands_an_empty_pool_on(tmp_path, monkeypatch):
    """Refused lifts are redrawn; a pool that runs out leaves its share to the others."""
    pools = {
        "goattracker2": [_row(i, "GoatTracker_V2.x") for i in range(20)],
        "sidwizard": [_row(100 + i, "Hermit/SidWizard_V1.x") for i in range(20)],
        "defmon": [_row(200 + i, "DefMon") for i in range(3)],
    }
    attempts = []

    def lift_one(_hvsc, row, output):
        attempts.append(row["path"])
        if row["md5"].endswith(("1", "3", "5", "7", "9")) or int(row["md5"], 16) >= 200:
            return "LiftError: refused"
        Path(output).write_text("", encoding="utf-8")
        return ""

    monkeypatch.setattr(sample_tunes, "lift_one", lift_one)
    monkeypatch.setattr(sample_tunes, "ProcessPoolExecutor", ThreadPoolExecutor)
    rows = sample_tunes.run(tmp_path, pools, 12, tmp_path / "out", 4, random.Random(0))
    exported = [r for r in rows if not r["error"]]
    assert len(exported) == 12
    assert len(attempts) == len(rows) == len(set(attempts))
    assert all(r["error"] == "LiftError: refused" for r in rows if r["backend"] == "defmon")
    assert len([r for r in rows if r["backend"] == "defmon"]) == 3
    by_backend = {b: sum(1 for r in exported if r["backend"] == b) for b in pools}
    assert by_backend["defmon"] == 0 and sorted(by_backend.values())[1:] == [6, 6]
    assert all((tmp_path / "out" / r["output"]).exists() for r in exported)
    assert rows == sorted(rows, key=lambda r: (r["backend"], r["path"]))


def test_main_writes_the_sample_and_failure_tables(tmp_path, monkeypatch, capsys):
    """The CLI writes both tables and fails when the count is not reached."""
    tunes = _tunes_csv(tmp_path, [_row(1, "GoatTracker_V2.x"), _row(2, "GoatTracker_V2.x")])

    def lift_one(_hvsc, row, output):
        if row["path"].endswith("t1.sid"):
            return "LiftError: refused"
        Path(output).write_text("", encoding="utf-8")
        return ""

    monkeypatch.setattr(sample_tunes, "lift_one", lift_one)
    monkeypatch.setattr(sample_tunes, "ProcessPoolExecutor", ThreadPoolExecutor)
    out = tmp_path / "out"
    args = [str(tmp_path), str(tunes), str(out), "--count", "2", "--jobs", "1"]
    assert sample_tunes.main(args) == 1
    assert "goattracker2: 1/2 lifted (pool 2)" in capsys.readouterr().out
    failures = list(csv.DictReader((out / "failures.csv").open(encoding="utf-8")))
    assert [f["path"] for f in failures] == ["d/t1.sid"]
    assert len(list(csv.DictReader((out / "sample.csv").open(encoding="utf-8")))) == 2
    assert sample_tunes.main(args[:-4] + ["--count", "1", "--jobs", "1"]) == 0


def test_lift_one_writes_the_generic_document_or_returns_the_error(
    references, tmp_path, monkeypatch
):
    """A tune that lifts writes its lift's generic document; one that does not names its error."""
    monkeypatch.setenv("UNITRACKER_WORKERS", "1")
    ref = references["metal_warrior"]
    output = tmp_path / "mw.yaml"
    assert sample_tunes.lift_one(str(ref.sid.parent), {"path": ref.sid.name}, str(output)) == ""
    assert output.read_text(encoding="utf-8") == ref.generic
    (tmp_path / "junk.sid").write_bytes(b"not a sid")
    error = sample_tunes.lift_one(str(tmp_path), {"path": "junk.sid"}, str(tmp_path / "j.yaml"))
    assert error.startswith("UnknownFormat: ") and not (tmp_path / "j.yaml").exists()
