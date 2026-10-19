"""tools/generic_check.py: lifted documents verified on unitracker-player, one row per tune."""

import csv
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("generic_check", ROOT / "tools" / "generic_check.py")
generic_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generic_check)


def _csv(path: Path, fields: list, rows: list) -> Path:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_tunes_come_from_a_sample_or_a_survey_csv(tmp_path):
    sample = _csv(
        tmp_path / "sample.csv",
        ["path", "output", "error"],
        [
            {"path": "A/x.sid", "output": "x.yaml", "error": ""},
            {"path": "A/y.sid", "output": "", "error": "LiftError: refused"},
        ],
    )
    assert generic_check.tunes(sample) == {"x.yaml": "A/x.sid"}
    survey = _csv(
        tmp_path / "tunes.csv", ["path", "md5"], [{"path": "B/t.sid", "md5": "0123abcd99"}]
    )
    assert generic_check.tunes(survey) == {"0123abcd-t.yaml": "B/t.sid"}


def test_rows_fall_into_categories():
    row = dict.fromkeys(generic_check.FIELDS, "")
    assert generic_check.category({**row, "verified": "yes"}) == "verified"
    gap = "ins i12: fields ['vibrato'] have no generic form"
    assert (
        generic_check.category({**row, "gap": gap})
        == "gap: ins iN: fields ... have no generic form"
    )
    mismatch = {**row, "onset_reg": "$09", "motion": "native holds"}
    assert generic_check.category(mismatch) == "mismatch: pw, native holds"
    assert generic_check.category({**row, "onset_reg": "$18"}) == "mismatch: mode/vol, first tick"
    assert generic_check.motion([3, 3], [3, 4]) == "native holds"
    assert generic_check.motion([3, 4], [3, 3]) == "document holds"
    assert generic_check.motion([3, 4], [3, 5]) == "both move"


def test_a_reference_tune_is_lifted_checked_and_reported(references, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("UNITRACKER_WORKERS", "1")
    ref = references["metal_warrior"]
    tunes = _csv(tmp_path / "t.csv", ["path", "md5"], [{"path": ref.sid.name, "md5": "0" * 32}])
    out = tmp_path / "out"
    argv = ["--csv", str(tunes), "--hvsc", str(ref.sid.parent), "--jobs", "1", "--out", str(out)]
    assert generic_check.main(argv) == 0
    printed = capsys.readouterr().out
    assert "goattracker2: 0/1 verified" in printed and "mismatch: freq, " in printed
    (row,) = csv.DictReader((out / "results.csv").open())
    assert (row["onset"], row["onset_reg"]) == ("1550", "$0E")
    assert row["motion"] in ("document holds", "both move")
    name = f"00000000-{ref.sid.stem}.yaml"
    assert (out / name).read_text() == ref.generic
    earlier = _csv(
        tmp_path / "before.csv", generic_check.FIELDS, [{**row, "onset": "105", "onset_reg": "$0B"}]
    )
    assert generic_check.main([*argv, "--against", str(earlier)]) == 0
    assert capsys.readouterr().out.splitlines()[-1].split() == [name, "105", "$0B", "1550", "$0E"]


def test_a_tune_that_does_not_lift_is_a_gap(tmp_path, monkeypatch):
    monkeypatch.setenv("UNITRACKER_WORKERS", "1")
    sid = tmp_path / "junk.sid"
    sid.write_bytes(b"not a sid")
    row = generic_check.check("junk.yaml", str(sid), None)
    assert row["verified"] == "no" and row["gap"].startswith("UnknownFormat")
    assert generic_check.category(row).startswith("gap: UnknownFormat")
