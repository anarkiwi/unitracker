"""tools/grid_diff.py: a document's divergence from the native grid, for the eye and the machine."""

import importlib.util
import json
from pathlib import Path

from unitracker.lift import verify

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("grid_diff", ROOT / "tools" / "grid_diff.py")
grid_diff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(grid_diff)


def test_the_report_names_each_differing_register_its_runs_and_both_grids_values():
    verdict = verify.Verdict(
        ticks=12,
        played=12,
        starts=(1, 4, None),
        onset=(3, 5),
        whole=(2, 11),
        cells=(
            (2, 11, 0, 7),
            (3, 11, 5, 7),
            (3, 5, 1, 9),
            (7, 5, 8, 0),
            (7, 8, 0, 2),
            (8, 8, 4, 2),
        ),
    )
    assert grid_diff.report(verdict) == (
        "mismatch: tick 3, register $05 v1 ad (from tick 0: tick 2, $0B v2 ctrl)\n"
        "  $05 v1 ad: 2 cells, ticks 3-7\n"
        "    3: doc 01, native 09\n"
        "    7: doc 08, native 00\n"
        "  $08 v2 freq hi: 2 cells, ticks 7-8\n"
        "    7: doc 00, native 02\n"
        "    8: doc 04, native 02\n"
        "  $0B v2 ctrl: 2 cells, ticks 2-3\n"
        "    2: doc 00, native 07\n"
        "    3: doc 05, native 07"
    )


def test_the_report_counts_the_value_lines_it_omits():
    verdict = verify.Verdict(
        ticks=10,
        played=10,
        starts=(0,),
        onset=(0, 0),
        whole=(0, 0),
        cells=tuple((t, 0, 1 if t % 2 else 2, 0) for t in range(10)),
    )
    text = grid_diff.report(verdict)
    assert "  $00 v1 freq lo: 10 cells, ticks 0-9" in text
    assert "    0: doc 02, native 00" in text
    assert "    ... 4 more value lines" in text


def test_a_verified_and_a_ended_document_are_not_mismatches():
    assert (
        grid_diff.report(verify.Verdict(8, 8, (0, 0, 0), None, None, ()))
        == "verified: 8 ticks play the native grid"
    )
    assert (
        grid_diff.report(verify.Verdict(8, 4, (0, 0, 0), None, (2, 0), ()))
        == "mismatch: the document ends at tick 4 of 8"
    )


def test_main_diffs_the_diverging_reference_tune_for_the_eye_and_the_machine(
    references, tmp_path, capsys
):
    ref = references["csillag"]
    doc = tmp_path / "csillag.yaml"
    doc.write_text(ref.generic, encoding="utf-8")
    out = tmp_path / "diff.json"
    assert grid_diff.main([str(doc), str(ref.sid), "--ticks", "64", "--json", str(out)]) == 1
    summary = json.loads(out.read_text(encoding="utf-8"))
    assert summary["ok"] is False and summary["first"] == [2, 0]
    assert summary["played"] == 64 and summary["ended"] == 0 and summary["starts"] == [2, 2, 2]
    entry = next(e for e in summary["registers"] if e["reg"] == 0)
    assert summary["cells"][0][0] == 2 and summary["cells"][0][1] == 0
    assert entry["name"] == "v1 freq lo" and entry["cells"] == len(
        [c for c in summary["cells"] if c[1] == 0]
    )
    assert grid_diff.main([str(doc), str(ref.sid), "--ticks", "64", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["first"] == [2, 0]
    assert grid_diff.main([str(doc), str(ref.sid), "--ticks", "64"]) == 1
    text = capsys.readouterr().out
    assert "mismatch: tick 2, register $00 v1 freq lo" in text
    assert "doc " in text and "native " in text


def test_main_agrees_over_the_ticks_the_grids_share(references, tmp_path):
    ref = references["metal_warrior"]
    doc = tmp_path / "metal_warrior.yaml"
    doc.write_text(ref.generic, encoding="utf-8")
    out = tmp_path / "diff.json"
    assert grid_diff.main([str(doc), str(ref.sid), "--ticks", "32", "--json", str(out)]) == 0
    summary = json.loads(out.read_text(encoding="utf-8"))
    assert summary["ok"] is True and summary["first"] is None and summary["cells"] == []
