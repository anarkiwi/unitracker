"""tools/liftsize.py: lifted documents' objects and sizes against their native tunes."""

import json
import lzma

import pytest
from pysidtracker import SidImage

import optimiser_docs as D

import liftsize
from unitracker.direct import invariants

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


@pytest.mark.parametrize(
    "name,player",
    [
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
    lst = corpus(tmp_path, crosseyed, {doc: DOC, f"{doc}-s1": DOC})
    todo, missing = liftsize.tasks(tmp_path, [str(lst)])
    assert missing == ["stray"] and len(todo) == 1 and len(todo[0][1]) == 2
    recs = {r["doc"]: r for r in liftsize.measure(*todo[0])}
    one, three = recs[doc], recs[f"{doc}-s1"]
    native = len(SidImage.from_sid(crosseyed.read_bytes()).image)
    assert one["native"] == native
    assert 0 < one["native_data"] < native
    assert one["lifted"] == len(DOC) and one["ratio"]["lifted"] == len(DOC) / native
    assert one["xz"] == len(lzma.compress(DOC.encode())) and one["tokens"] < one["lifted"]
    assert one["data_ratio"]["xz"] == one["xz"] / one["native_data"]
    assert sum(one["sections"].values()) == pytest.approx(1)
    assert one["sections"][one["dominant"]] == max(one["sections"].values())
    assert one["counts"]["lifted"] == invariants.lifted(DOC) and one["exceeded"] is None
    entries = one["counts"]["native"]["sidtab"]
    assert one["kinds"] == {"sidtab": 2 / entries} and entries >= 2
    assert three["counts"] == one["counts"]


def test_a_native_count_that_fails_leaves_the_kinds_unchecked():
    got = liftsize.counted(b"not a sid", 0, DOC)
    assert got["counts"]["error"].startswith("FormatError") and got["counts"]["native"] == {}
    assert got["kinds"] == {} and got["exceeded"] is None


def record(doc: str, ratio: float, player: str = "p") -> dict:
    """A measured record of ``player`` with ``ratio`` times its tune's control programs and
    native bytes."""
    have, native = {"ctrl": round(ratio * 10)}, {"ctrl": 10}
    return {
        "doc": doc,
        "player": player,
        "ratio": dict.fromkeys(liftsize.MEASURES, ratio),
        "data_ratio": None,
        "sections": {"ctrl": 0.75, "song": 0.25},
        "dominant": "ctrl",
        "counts": {"lifted": have, "native": native},
        "kinds": liftsize.ratios(have, native),
        "exceeded": invariants.exceeded(have, native),
    }


def test_kind_ratios_take_a_voice_at_most_and_an_empty_native_kind_as_unbounded():
    have = {"song": (3, 8), "ctrl": 2, "pw": 0}
    got = liftsize.ratios(have, {"song": (3, 4), "ctrl": 0, "pw": 0})
    assert got == {"song": 2.0, "ctrl": float("inf"), "pw": 0.0}


def test_the_summary_counts_broken_invariants_and_ranks_the_worst_first():
    recs = [record(f"d{i}", 0.5 + i / 10) for i in range(9)] + [record("big", 30)]
    recs += [record("lone", 2, "q"), {"doc": "bad", "error": "x"}]
    lines = liftsize.summary(recs, 2).splitlines()
    assert lines[0].startswith("11 songs (1 failed)")
    assert lines[1] == "p: 10 songs, 4 break an invariant"
    assert lines[2] == "  first kind exceeded: {'ctrl': 4}; 0 unread"
    tops = [i for i, line in enumerate(lines) if "top 2 by kind ratio" in line]
    assert lines[tops[0] + 1].split() == ["ctrl", "300/10", "30", "ctrl", "75%", "big"]
    assert lines[tops[1] + 1].split()[-1] == "lone" and "xz/data" not in "\n".join(lines)


def test_the_command_line_writes_records_and_summarises(tmp_path, crosseyed, capsys):
    doc = f"abcdef01-{crosseyed.stem}"
    lst = corpus(tmp_path, crosseyed, {doc: DOC})
    out = tmp_path / "sizes.json"
    assert liftsize.main([str(tmp_path), str(lst), "--jobs", "1", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "1 lifted songs not in the tune lists" in printed and "defmon: 1 songs" in printed
    assert json.loads(out.read_text())[0]["doc"] == doc
