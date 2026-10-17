"""The token studies: sections of a document and the context-efficiency mock-ups."""

import pytest
from test_optimise import PULSE, doc, ins_line

from unitracker import songio, tokenstudy

I2 = ins_line("i2", "ad: 4, ctrl: [{ctrl: 33, note: +0}, end]")
X = (
    "      x:\n        x1: (v + 32*[(φ - 3) % 12 >= 6] - 16) % 65536\n"
    "        x2: (v + 64*[(φ - 3) % 12 >= 6] - 32) % 65536\n"
)
PHRASE = (
    "[C4.i1*2, D#4, G4!freq:@x1, C5, C4.i2*2, D#4, G4!freq:@x2, C5, F4.i1, G#4, C5, F5,"
    " C4*2, D#4, G4, C5, C4*2, D#4, G4, C5]"
)
SECOND = (
    "  - def:\n      phrase:\n        p3: [C4.i2*2, D#4, G4, C5, -*19]\n    play: [p3, p2, p2]\n"
)


def song():
    pulse = ins_line("i1", f"ad: 9, ctrl: [{{ctrl: 65, note: +0}}, end], pw: {PULSE}")
    text = doc(f"{pulse}\n{I2}", PHRASE, X, steps=24).replace("loop: 0\n", SECOND + "loop: 0\n")
    text = text.replace("\nsong:", "\nhints: {ticks_per_step: 6}\nsong:")
    return songio.loads(text)


@pytest.fixture(scope="module", name="written")
def fixture_written():
    holder = {}
    songio.dumps(song(), respell=lambda d: holder.setdefault("doc", d))
    return holder["doc"]


def test_sections_split_a_document():
    text = songio.dumps(song())
    split = tokenstudy.tokenizers()["cl100k_base"]
    counts = tokenstudy.sections(text, split)
    assert all(counts[s] > 0 for s in tokenstudy.SECTIONS)
    assert abs(sum(counts[s] for s in tokenstudy.SECTIONS) - counts["total"]) <= len(
        text.splitlines()
    )
    moved = tokenstudy.sections(songio.dumps(song(), respell=tokenstudy.plays_first), split)
    assert (
        moved["phrases"] > 0
        and moved["definitions"] > 0
        and moved["structure"] < counts["structure"]
    )


def test_mockups_rewrite_what_they_name():
    texts = tokenstudy.mockups(song())
    assert "forms: {f1: '(a, b) (v + a*[(φ - 3) % 12 >= 6] - b) % 65536'}" in texts["forms"]
    assert "f1(32, 16)" in texts["forms"] and "f1(64, 32)" in texts["forms"]
    assert "tri(φ - 32, 128)" in texts["primitives"]
    assert "&s1" in texts["sub-phrases"] and "sub:" in texts["sub-phrases"]
    assert texts["grouped definitions"].count("def:") == 1
    assert "about: {i1: 'pul wave; ad, ctrl, pw, restart'" in texts["summaries"]
    assert (
        "bpm:" in texts["tempo"] and "voices: [[p1, p3], [p2, p2], [p2, p2]]" in texts["contents"]
    )
    assert "\ndefs:\n" in texts["plays first"] and "i1_pul" in texts["mnemonics"]
    assert texts["hoisted instruments"] == texts["baseline"]


def test_hoisted_instruments_merge_phrases_alike_but_for_instruments():
    doc_ = {
        "song": [
            {"def": {"phrase": {"p1": ["C4.i1", "D4"], "p2": ["C4.i2", "D4"]}}, "play": ["p1"]}
        ]
    }
    doc_["song"].append({"play": ["p2+3"]})
    out = tokenstudy.hoisted_instruments(doc_)
    assert "p2" not in out["song"][0]["def"]["phrase"] and out["song"][1]["play"] == ["p1(i1=i2)+3"]


def test_rests_and_primitive_uses(written):
    split = tokenstudy.tokenizers()["cl100k_base"]
    rest, total = tokenstudy.rests(written, split)
    assert 0 < rest < total
    assert tokenstudy.primitive_uses(written) == {"tri": 1, "clamp": 0}


def test_report_tables(tmp_path):
    path = tmp_path / "song.yaml"
    path.write_text(songio.dumps(song()), encoding="utf-8")
    report = tokenstudy.report([str(path)])
    assert "| variant | document | total cl100k_base |" in report
    assert (
        "| sub-phrases | " in report
        and "| total o200k_base |" in report
        and "expression primitives" in report
    )
