"""tools/corpus.py: corpus lifts by cause, samples, censuses and one document's grid diff."""

import csv
import json
import random
from pathlib import Path

import corpus
import shared

from unitracker.lift import verify

VERDICT = verify.Verdict(
    ticks=8,
    played=8,
    starts=(2, 3, None),
    onset=(0, 6),
    whole=(1, 0),
    cells=((1, 0, 5, 0), (2, 6, 9, 0), (5, 6, 9, 2)),
)
OPTS = {
    "plain": False,
    "ticks": 0,
    "registers": "",
    "forbid": ["sr"],
    "diff": True,
    "hvsc": None,
    "timeout": 0,
}


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
    assert corpus.category(row) == "lifted"
    gap = "ins i12: fields ['vibrato'] have no generic form"
    assert corpus.category({**row, "gap": gap}) == "gap: ins iN: fields ... have no generic form"
    mismatch = {**row, "onset_reg": "$09", "motion": "native holds"}
    assert corpus.category(mismatch) == "mismatch: pw, native holds"
    assert corpus.category({**row, "onset_reg": "$18"}) == "mismatch: mode/vol, first tick"
    assert corpus.motion([3, 3], [3, 4]) == "native holds"
    assert corpus.motion([3, 4], [3, 3]) == "document holds"
    assert corpus.motion([3, 4], [3, 5]) == "both move"


def install_fake_lift(monkeypatch, verdict=VERDICT, document="song: [x1: sr]\n", gap=None):
    """A lift that already holds its generic document and verdict."""

    class FakeLift:  # pylint: disable=too-few-public-methods
        """A lift that already holds its generic document and verdict."""

        playroutine = "fake"

        def __init__(self):
            self.verdict, self.document, self.gap = verdict, document, gap

        def run(self):
            return {}

    monkeypatch.setattr(corpus.backends, "lifter", lambda raw, expressions: FakeLift())


def test_a_mismatch_row_names_its_registers_values_and_forbidden_words(monkeypatch, tmp_path):
    install_fake_lift(monkeypatch)
    sid = tmp_path / "t.sid"
    sid.write_bytes(b"PSID\x01")
    out = tmp_path / "out"
    out.mkdir()
    row = corpus.check_one({"path": str(sid)}, {**OPTS, "out": str(out)})
    assert (row["onset"], row["onset_reg"], row["whole_reg"], row["forbidden"]) == (
        0,
        "$06",
        "$00",
        "sr",
    )
    assert row["registers"] == (
        "$00 v1 freq lo: 1 tick 1 (doc 05, native 00); $06 v1 sr: 2 ticks 2-5 (doc 09, native 00)"
    )
    assert (out / "t.yaml").read_text() == "song: [x1: sr]\n"
    assert json.loads((out / "t.diff.json").read_text())["first"] == [0, 6]
    install_fake_lift(monkeypatch, None, None, "ins i1: no generic form")
    row = corpus.check_one({"path": str(sid)}, {**OPTS, "out": str(out)})
    assert row["gap"] == "gap: ins i1: no generic form" and (out / "t.err").exists()
    assert not corpus.documented(row)


def test_a_tune_that_does_not_lift_is_a_gap(tmp_path):
    sid = tmp_path / "junk.sid"
    sid.write_bytes(b"not a sid")
    row = corpus.check_one({"path": str(sid)}, {**OPTS, "out": str(tmp_path)})
    assert row["verified"] == "no" and row["gap"].startswith("UnknownFormat")
    assert corpus.category(row).startswith("gap: UnknownFormat")


def test_a_reference_tune_is_checked_reported_and_compared(references, tmp_path, capsys):
    ref = references["metal_warrior"]
    tunes = _csv(tmp_path / "t.csv", ["path", "md5"], [{"path": ref.sid.name, "md5": "0" * 32}])
    out = tmp_path / "out"
    argv = ["check", str(tunes), "--hvsc", str(ref.sid.parent), "--jobs", "1", "--out", str(out)]
    assert corpus.main(argv) == 0
    printed = capsys.readouterr().out
    assert "goattracker2: 0/1 verified" in printed and "mismatch: freq, " in printed
    (row,) = csv.DictReader((out / "results.csv").open())
    assert (row["onset"], row["onset_reg"]) == ("1550", "$0E")
    assert row["motion"] in ("document holds", "both move")
    name = f"00000000-{ref.sid.stem}"
    assert (out / f"{name}.yaml").read_text() == ref.generic
    earlier = _csv(
        tmp_path / "before.csv", corpus.FIELDS, [{**row, "onset": "105", "onset_reg": "$0B"}]
    )
    assert corpus.main([*argv, "--against", str(earlier)]) == 0
    assert capsys.readouterr().out.splitlines()[-1].split() == [name, "105", "$0B", "1550", "$0E"]
    for only, count in (("^verified", 0), ("mismatch: ctrl", 1)):
        assert corpus.main(["check", str(earlier), *argv[2:], "--only", only]) == 0
        assert len(list(csv.DictReader((out / "results.csv").open()))) == count


def test_a_plain_lift_replays_its_vm_against_the_native_grid(references, tmp_path):
    ref = references["metal_warrior"]
    opts = {**OPTS, "plain": True, "ticks": 64, "out": str(tmp_path)}
    row = corpus.check_one({"path": str(ref.sid)}, opts)
    assert row["verified"] == "yes" and row["player"] == "goattracker2"
    assert (tmp_path / f"{ref.sid.stem}.yaml").read_text().startswith("{")
    masked = corpus.check_one({"path": str(ref.sid)}, {**opts, "registers": "0x18"})
    assert masked["verified"] == "yes" and corpus.documented(masked)


def test_quotas_split_the_remaining_count_over_the_open_pools():
    assert corpus.quotas(10, ["a", "b", "c"], {"a": 1, "b": 0, "c": 0}) == {"a": 3, "b": 3, "c": 3}
    assert corpus.quotas(4, ["a"], {"a": 2, "b": 1}) == {"a": 1}
    assert corpus.quotas(12, ["a", "b"], {"a": 2, "b": 5, "c": 0}) == {"a": 4, "b": 1}
    assert corpus.quotas(2, ["a", "b"], {"a": 2, "b": 1}) == {"a": 0, "b": 0}


def test_a_sample_refills_each_pool_until_its_quota_and_hands_an_empty_pool_on():
    rows = [_row(i, "GoatTracker_V2.x") for i in range(20)]
    rows += [_row(100 + i, "Hermit/SidWizard_V1.x") for i in range(20)]
    rows += [_row(200 + i, "DefMon") for i in range(3)] + [_row(300, "GoatTracker_V1.x")]
    pools = corpus.pools(rows)
    assert [len(pools[p]) for p in ("goattracker2", "sidwizard", "defmon")] == [20, 20, 3]
    attempts = []

    def check_rows(batch):
        attempts.extend(r["path"] for r in batch)
        refused = [int(r["md5"], 16) % 2 or int(r["md5"], 16) >= 200 for r in batch]
        return [
            {"path": r["path"], "md5": r["md5"], "player": "p", "gap": "gap: no" if no else ""}
            for r, no in zip(batch, refused)
        ]

    out = corpus.sample(pools, 12, random.Random(0), check_rows)
    assert len(attempts) == len(out) == len(set(attempts))
    lifted = [int(r["md5"], 16) // 100 for r in out if corpus.documented(r)]
    assert sorted(lifted) == [0] * 6 + [1] * 6
    assert sum(int(r["md5"], 16) >= 200 for r in out) == 3


def test_the_census_counts_documents_and_native_commands(references, tmp_path, capsys):
    doc = tmp_path / "t.yaml"
    doc.write_text(references["metal_warrior"].generic, encoding="utf-8")
    js = tmp_path / "census.json"
    sid = str(references["metal_warrior"].sid)
    assert corpus.main(["census", str(doc), sid, "--json", str(js)]) == 0
    assert "## 2 documents" in capsys.readouterr().out
    found = json.loads(js.read_text(encoding="utf-8"))
    assert found["backend|goattracker2"] == [1, [sid]]
    assert any(k.startswith("goattracker2 command|") for k in found)
    assert found["timeline|loop"][1] == [str(doc)]


def test_grid_diffs_the_diverging_reference_tune_for_the_eye_and_the_machine(
    references, tmp_path, capsys
):
    ref = references["csillag"]
    doc = tmp_path / "csillag.yaml"
    doc.write_text(ref.generic, encoding="utf-8")
    out = tmp_path / "diff.json"
    argv = ["grid", str(doc), str(ref.sid), "--ticks", "64"]
    assert corpus.main([*argv, "--json", str(out), "--grids", str(tmp_path)]) == 1
    summary = json.loads(out.read_text(encoding="utf-8"))
    assert summary["ok"] is False and summary["first"] == [2, 0] and summary["starts"] == [2, 2, 2]
    assert (tmp_path / "doc.csv.zst").is_file() and (tmp_path / "native.csv.zst").is_file()
    assert corpus.main([*argv, "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["first"] == [2, 0]
    assert corpus.main(argv) == 1
    text = capsys.readouterr().out
    assert "mismatch: tick 2, register $00 v1 freq lo" in text and "\n     2 O " in text


def test_grid_agrees_over_the_ticks_the_grids_share(references, tmp_path):
    ref = references["metal_warrior"]
    doc = tmp_path / "metal_warrior.yaml"
    doc.write_text(ref.generic, encoding="utf-8")
    assert corpus.main(["grid", str(doc), str(ref.sid), "--ticks", "32"]) == 0
