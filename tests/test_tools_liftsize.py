"""tools/liftsize.py: lifted documents' sizes against their native tunes."""

import json
import lzma

import pytest
from pysidtracker import SidImage

import optimiser_docs as D

import liftsize

DOC = D.doc(
    "      ins:\n        i1:{ad:9,sr:0,ctrl:c1}\n"
    "      ctrl:\n        c1:[{wait:0,ctrl:65},end]\n"
    "      phrase:\n        p1:[C4.i1,-]\n",
    head="voices:1\ntempo:6\n",
)


def test_sections_split_the_bytes_by_top_level_key():
    shares = liftsize.sections(b"a:1\nbb:\n x\n y\nc:[2]\n")
    assert shares == pytest.approx({"a": 0.2, "bb": 0.5, "c": 0.3})
    assert liftsize.sections(DOC.encode())["ctrl"] > 0
    assert not liftsize.sections(b"")


def corpus(tmp_path, tune, docs: dict):
    """A ``forms`` corpus of ``docs`` (name: text) all lifted from ``tune``, and its tune list."""
    for sub in ("lifted", "meta"):
        (tmp_path / sub).mkdir()
    for doc, text in docs.items():
        (tmp_path / "lifted" / f"{doc}.yaml").write_text(text)
        (tmp_path / "meta" / f"{doc}.json").write_text('{"player": "goattracker2", "ticks": 9}')
    (tmp_path / "meta" / "stray.json").write_text("{}")
    (tmp_path / "meta" / "unlifted.json").write_text("{}")
    (tmp_path / "lifted" / "stray.yaml").write_text(DOC)
    lst = tmp_path / "tunes.csv"
    lst.write_text(f"path,md5,songs\n{tune},abcdef0123,2\n")
    return lst


def test_each_song_is_measured_against_its_tune(tmp_path, metal_warrior):
    doc = f"abcdef01-{metal_warrior.stem}"
    lst = corpus(tmp_path, metal_warrior, {doc: DOC, f"{doc}-s1": DOC})
    todo, missing = liftsize.tasks(tmp_path, [str(lst)])
    assert missing == ["stray"] and len(todo) == 1 and len(todo[0][1]) == 2
    recs = {r["doc"]: r for r in liftsize.measure(*todo[0])}
    one = recs[doc]
    native = len(SidImage.from_sid(metal_warrior.read_bytes()).image)
    assert one["native"] == native and recs[f"{doc}-s1"]["lifted"] == one["lifted"]
    assert one["lifted"] == len(DOC) and one["ratio"]["lifted"] == len(DOC) / native
    assert one["xz"] == len(lzma.compress(DOC.encode())) and one["tokens"] < one["lifted"]
    assert sum(one["sections"].values()) == pytest.approx(1)
    assert one["sections"][one["dominant"]] == max(one["sections"].values())


def record(doc: str, ratio: float, player: str = "p") -> dict:
    """A measured record of ``player`` whose sizes are ``ratio`` times its tune's."""
    return {
        "doc": doc,
        "player": player,
        "ratio": dict.fromkeys(liftsize.MEASURES, ratio),
        "sections": {"ctrl": 0.75, "song": 0.25},
        "dominant": "ctrl",
    }


def test_the_summary_ranks_the_largest_first():
    recs = [record(f"d{i}", 0.5 + i / 10) for i in range(9)] + [record("big", 30)]
    recs += [record("lone", 2, "q"), {"doc": "bad", "error": "x"}]
    lines = liftsize.summary(recs, 2).splitlines()
    assert lines[0].startswith("11 songs (1 failed)") and lines[1] == "p: 10 songs"
    assert lines[2].startswith("  lifted/native bytes: ") and lines[2].endswith(" 30")
    tops = [i for i, line in enumerate(lines) if "top 2 by xz" in line]
    assert lines[tops[0] + 1].split() == ["30", "ctrl", "75%", "big"]
    assert lines[tops[0] + 2].split()[-1] == "d8" and lines[tops[1] + 1].split()[-1] == "lone"


def test_the_command_line_writes_records_and_summarises(tmp_path, metal_warrior, capsys):
    doc = f"abcdef01-{metal_warrior.stem}"
    lst = corpus(tmp_path, metal_warrior, {doc: DOC})
    out = tmp_path / "sizes.json"
    assert liftsize.main([str(tmp_path), str(lst), "--jobs", "1", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "1 lifted songs not in the tune lists" in printed and "goattracker2: 1 songs" in printed
    assert json.loads(out.read_text())[0]["doc"] == doc
