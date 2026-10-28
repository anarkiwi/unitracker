"""The attribution of a defMON transliteration's differing registers to its sidTAB rows."""

import json

import dmgaps


def test_a_gap_rows_runs_are_attributed_to_it(defmon_gap):
    lift, _, cells, runs = dmgaps.attribute(defmon_gap)
    (entry, row, _), *_ = lift.gaps
    assert cells.size and runs and all(cause == "gap" for *_, cause, _ in runs)
    assert {at for *_, at in runs} == {(0, row)} and entry == row
    assert sum(ticks for _, _, ticks, _, _ in runs) == len(cells)


def test_a_record_names_its_verdict_and_sizes(defmon_gap, defmon_song, tmp_path):
    gap, song = tmp_path / "gap.sid", tmp_path / "song.sid"
    gap.write_bytes(defmon_gap)
    song.write_bytes(defmon_song)
    rec = dmgaps.record(str(gap))
    assert rec["verdict"] == "gaps" and rec["causes"] == {"gap": rec["cells"]}
    assert rec["other"] == {} and rec["exceeded"] is None and rec["bytes"] > rec["data"] > 0
    assert dmgaps.record(str(song))["verdict"] == "exact"


def test_the_command_line_attributes_tunes_and_runs_a_corpus(defmon_gap, tmp_path, capsys):
    (tmp_path / "gap.sid").write_bytes(defmon_gap)
    (tmp_path / "bad.sid").write_bytes(b"PSID")
    tunes = tmp_path / "tunes.csv"
    tunes.write_text("path,player\ngap.sid,DefMon\nbad.sid,DefMon\nother.sid,GoatTracker\n")
    out = tmp_path / "corpus.jsonl"
    argv = [str(tmp_path / "gap.sid"), "--corpus", str(tunes), "--hvsc", str(tmp_path)]
    assert dmgaps.main(argv + ["--jobs", "1", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "{'gaps': 1, 'failed': 1}" in printed and "layer A row" in printed
    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert sorted(r["verdict"] for r in records) == ["failed", "gaps"]
