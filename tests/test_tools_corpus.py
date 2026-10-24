"""tools/corpus.py: tune lists, corpus checks by cause and the survey renderer."""

import csv
from pathlib import Path

import corpus
import pytest
import shared
from conftest import gt_tune


def _sid(raw: bytes, version: int, at: int = 0, value: int = 0) -> bytes:
    """``raw`` with header version ``version`` and byte ``at`` set to ``value``."""
    out = bytearray(raw)
    out[4:6], out[at] = version.to_bytes(2, "big"), value or out[at]
    return bytes(out)


@pytest.mark.parametrize(
    "version, at, multi",
    [(2, 0x7A, False), (3, 0x7A, True), (3, 0x7B, False), (4, 0x7B, True), (4, 0x7C, False)],
)
def test_a_tune_is_multi_sid_by_its_header_sid_addresses(metal_warrior, version, at, multi):
    assert shared.multi_sid(_sid(metal_warrior.read_bytes(), version, at, 0x42)) == multi


@pytest.mark.parametrize("voices, multi", [((0, 7, 14), False), ((0, 7, 0x20), True)])
def test_a_goattracker_build_past_three_voices_is_multi_sid(
    metal_warrior, monkeypatch, voices, multi
):
    dec = gt_tune([(0x41, 0)], voices=voices)[0]
    monkeypatch.setattr(shared, "decompile_sid", lambda raw: dec)
    assert shared.multi_sid(metal_warrior.read_bytes()) == multi


def test_other_formats_and_unreadable_tunes_are_single_sid(mega_run):
    assert not shared.multi_sid(mega_run.read_bytes()) and not shared.multi_sid(b"PSID" + bytes(9))


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


def test_multi_sid_tunes_are_excluded_unless_asked_for(metal_warrior, tmp_path, capsys):
    sid = tmp_path / "multi.sid"
    sid.write_bytes(_sid(metal_warrior.read_bytes(), 3, 0x7A, 0x50))
    out = tmp_path / "out"
    argv = ["check", str(sid), "--jobs", "1", "--frames", "100", "--out", str(out)]
    assert corpus.main(argv) == 0 and "1 multi-SID tunes excluded" in capsys.readouterr().out
    assert corpus.main([*argv, "--multi-sid"]) == 0
    assert "goattracker2: 1/1 verified" in capsys.readouterr().out
    argv = ["forms", str(sid), "--out", str(out), "--jobs", "1", "--forms", "lifted"]
    assert corpus.main(argv) == 0 and "1 multi-SID tunes excluded" in capsys.readouterr().out
    assert not (out / "lifted" / "multi.err").exists()
    with pytest.raises(ValueError, match="multi-SID"):
        corpus.render(sid.read_bytes(), 1, 10)
