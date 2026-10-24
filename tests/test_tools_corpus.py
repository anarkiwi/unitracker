"""tools/corpus.py: tune lists, corpus checks by cause and the survey renderer."""

import csv
from pathlib import Path

import corpus
import shared


def _csv(path: Path, fields: list, rows: list) -> Path:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _row(i, player, songs=1):
    return {"path": f"d/t{i}.sid", "md5": f"{i:032x}", "player": player, "songs": songs}


def test_tune_lists_filter_by_player_and_subtunes_and_name_their_outputs(tmp_path):
    rows = [_row(1, "GoatTracker_V2.x"), _row(2, "GoatTracker_V2.x", 3), _row(3, "DefMon")]
    tunes = _csv(tmp_path / "tunes.csv", ["path", "md5", "player", "songs"], rows)
    assert [r["path"] for r in shared.rows([tunes], "goat", 1)] == ["d/t1.sid"]
    assert len(shared.rows([tunes, "x/y.sid"])) == 4
    assert shared.name(rows[0]) == "00000000-t1" and shared.name({"path": "a/b.sid"}) == "b"
    assert shared.tune(tunes) == tunes


def test_rows_fall_into_categories():
    row = dict.fromkeys(corpus.FIELDS, "")
    assert corpus.category({**row, "verified": "yes"}) == "verified"
    assert corpus.category(row) == "mismatch"
    assert corpus.category({**row, "gap": "E: at 12 {a: 1}"}) == "gap: E: at N ..."


def test_a_tune_that_does_not_lift_is_a_gap(tmp_path):
    sid = tmp_path / "junk.sid"
    sid.write_bytes(b"not a sid")
    opts = {"out": str(tmp_path), "hvsc": None, "timeout": 0, "subtune": 0, "frames": 8}
    row = corpus.check_one({"path": str(sid)}, opts)
    assert row["verified"] == "no" and row["gap"].startswith("FormatError")
    assert (tmp_path / "junk.err").read_text() == row["gap"] + "\n"


def test_tunes_are_checked_and_reported(metal_warrior, tmp_path, capsys):
    tunes = _csv(
        tmp_path / "t.csv", ["path", "md5"], [{"path": metal_warrior.name, "md5": "0" * 32}]
    )
    out = tmp_path / "out"
    argv = ["check", str(tunes), str(tmp_path / "junk.sid"), "--hvsc", str(metal_warrior.parent)]
    assert corpus.main([*argv, "--jobs", "1", "--frames", "200", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "goattracker2: 1/1 verified" in printed and "gap: FileNotFoundError" in printed
    rows = {r["doc"]: r for r in csv.DictReader((out / "results.csv").open())}
    assert rows[f"00000000-{metal_warrior.stem}"]["verified"] == "yes"
    assert (out / f"00000000-{metal_warrior.stem}.yaml").read_text().startswith("unitracker: ")


def test_the_survey_renderer_plays_the_lifted_document(mega_run):
    played, info = corpus.render(mega_run.read_bytes(), 1, 50)
    assert played.shape == (50, 25) and info["backend"] == "sidwizard" and info["chars"] > 0
