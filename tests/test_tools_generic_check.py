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


def test_documents_map_to_tunes_by_output_or_md5_prefix(tmp_path):
    docs = [tmp_path / "0123abcd-Tune.yaml", tmp_path / "ffffffff-Other.yaml", tmp_path / "x.yaml"]
    sample = _csv(
        tmp_path / "sample.csv", ["path", "output"], [{"path": "A/x.sid", "output": "x.yaml"}]
    )
    assert generic_check.tune_paths(docs, sample) == {docs[2]: "A/x.sid"}
    tunes = _csv(
        tmp_path / "tunes.csv", ["path", "md5"], [{"path": "B/t.sid", "md5": "0123abcd99"}]
    )
    assert generic_check.tune_paths(docs, tunes) == {docs[0]: "B/t.sid"}


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


def test_a_reference_document_is_checked_and_reported(references, tmp_path, capsys):
    ref = references["metal_warrior"]
    doc = tmp_path / "mw.yaml"
    doc.write_text(ref.text)
    tunes = _csv(
        tmp_path / "s.csv", ["path", "output"], [{"path": ref.sid.name, "output": doc.name}]
    )
    out = tmp_path / "out"
    argv = [str(doc), "--csv", str(tunes), "--hvsc", str(ref.sid.parent), "--jobs", "1"]
    assert generic_check.main([*argv, "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "goattracker2: 0/1 verified" in printed and "mismatch: pw, native holds" in printed
    (row,) = csv.DictReader((out / "results.csv").open())
    assert (row["onset"], row["onset_reg"], row["motion"]) == ("25", "$02", "native holds")
    assert (out / doc.name).read_text() == ref.generic
