"""tools/liftsize.py: lifted document sizes against their native tunes, ranked per player."""

import json

import pytest
from pysidtracker import SidImage

import optimiser_docs as D

import liftsize

DOC = D.doc("      ctrl:\n        c1:[{wait:0,ctrl:65},end]\n", head="voices:1\ntempo:6\n")


def test_sections_split_the_bytes_by_top_level_key():
    shares = liftsize.sections(b"a:1\nbb:\n x\n y\nc:[2]\n")
    assert shares == pytest.approx({"a": 0.2, "bb": 0.5, "c": 0.3})
    assert liftsize.sections(DOC.encode())["ctrl"] > 0
    assert not liftsize.sections(b"")


@pytest.mark.parametrize(
    "name,player",
    [
        ("mega_run", "sidwizard"),
        ("crosseyed", "defmon"),
        ("space_invadarz", "musicassembler"),
    ],
)
def test_song_data_is_part_of_the_native_image(name, player, request):
    raw = request.getfixturevalue(name).read_bytes()
    data = liftsize.native_data(player, raw)
    assert 0 < data < len(SidImage.from_sid(raw).image)


def test_song_data_is_none_without_an_encoder_or_a_parse(metal_warrior):
    assert liftsize.native_data("goattracker2", metal_warrior.read_bytes()) is None
    assert liftsize.native_data("defmon", metal_warrior.read_bytes()) is None


def corpus(tmp_path, tune, docs: dict):
    """A ``forms`` corpus of ``docs`` (name: text) all lifted from ``tune``, and its tune list."""
    for sub in ("lifted", "meta"):
        (tmp_path / sub).mkdir()
    for doc, text in docs.items():
        (tmp_path / "lifted" / f"{doc}.yaml").write_text(text)
        (tmp_path / "meta" / f"{doc}.json").write_text('{"player": "defmon", "ticks": 9}')
    (tmp_path / "meta" / "stray.json").write_text("{}")
    (tmp_path / "meta" / "unlifted.json").write_text("{}")
    (tmp_path / "lifted" / "stray.yaml").write_text(DOC)
    lst = tmp_path / "tunes.csv"
    lst.write_text(f"path,md5,songs\n{tune},abcdef0123,2\n")
    return lst


def test_each_song_is_measured_against_its_tune(tmp_path, crosseyed):
    doc = f"abcdef01-{crosseyed.stem}"
    lst = corpus(tmp_path, crosseyed, {doc: DOC, f"{doc}-s1": DOC * 3})
    todo, missing = liftsize.tasks(tmp_path, [str(lst)])
    assert missing == ["stray"] and len(todo) == 1 and len(todo[0][1]) == 2
    recs = {r["doc"]: r for r in liftsize.measure(*todo[0])}
    one, three = recs[doc], recs[f"{doc}-s1"]
    native = len(SidImage.from_sid(crosseyed.read_bytes()).image)
    assert one["native"] == native
    assert 0 < one["native_data"] < native
    assert three["lifted"] == 3 * one["lifted"] == 3 * len(DOC)
    assert three["ratio"]["lifted"] == pytest.approx(3 * one["ratio"]["lifted"])
    assert three["xz"] < 3 * one["xz"] and one["tokens"] < one["lifted"]
    assert one["data_ratio"]["xz"] == one["xz"] / one["native_data"]
    assert sum(one["sections"].values()) == pytest.approx(1)
    assert one["sections"][one["dominant"]] == max(one["sections"].values())


def record(doc: str, ratio: float, player: str = "p") -> dict:
    """A measured record of ``player`` whose sizes are ``ratio`` times its native bytes."""
    return {
        "doc": doc,
        "player": player,
        "lifted": int(ratio * 100),
        "ratio": dict.fromkeys(liftsize.MEASURES, ratio),
        "data_ratio": None,
        "sections": {"ctrl": 0.75, "song": 0.25},
        "dominant": "ctrl",
    }


def test_the_summary_ranks_the_outlier_first():
    recs = [record(f"d{i}", 1 + i / 10) for i in range(9)] + [record("big", 300)]
    recs += [record("lone", 2, "q"), {"doc": "bad", "error": "x"}]
    text = liftsize.summary(recs, 2)
    lines = text.splitlines()
    assert lines[0].startswith("11 songs (1 failed)")
    tops = [i for i, line in enumerate(lines) if "top by" in line]
    assert [lines[i + 1].split()[-1] for i in tops] == ["big", "big", "lone", "lone"]
    assert float(lines[tops[0] + 1].split()[2]) > 10 and "ctrl 75%" in lines[tops[0] + 1]
    assert "xz/data" not in text


def test_the_command_line_writes_records_and_summarises(tmp_path, crosseyed, capsys):
    doc = f"abcdef01-{crosseyed.stem}"
    lst = corpus(tmp_path, crosseyed, {doc: DOC})
    out = tmp_path / "sizes.json"
    assert liftsize.main([str(tmp_path), str(lst), "--jobs", "1", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "1 lifted songs not in the tune lists" in printed and "defmon: 1 songs" in printed
    assert json.loads(out.read_text())[0]["doc"] == doc
