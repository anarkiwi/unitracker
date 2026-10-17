"""Note and duration spellings: how tokenizers split them, and each spelling's exact inverse."""

import pytest
from test_tokenstudy import fixture_written, song  # pylint: disable=unused-import

from unitracker.analysis import spelling, tokenstudy
from unitracker.format import songio


def test_tokenizers_split_note_names_differently():
    toks = tokenstudy.tokenizers()
    if "llama2" not in toks:
        pytest.skip("Llama 2 tokenizer not in the local Hugging Face cache; tests do not fetch it")
    assert set(toks) == {"cl100k_base", "o200k_base", "llama2"}
    assert toks["cl100k_base"]("C#4") == ["C", "#", "4"]
    assert toks["llama2"]("C10") == ["▁C", "1", "0"]


@pytest.mark.parametrize(
    "name", ["relative", "degrees", "delta", "expanded", "slashed", "absolute"]
)
def test_every_spelling_has_an_exact_inverse(name, written):
    key = spelling.best_key(written)
    variants = spelling.respelt(song())
    text, respelt = variants[name]
    pairs = zip(tokenstudy.phrase_defs(respelt), tokenstudy.phrase_defs(written))
    for (table, pname), (base, _) in pairs:
        assert spelling.absolute(list(table[pname]), name, key) == list(base[pname])
    assert (text != variants["absolute"][0]) == (name != "absolute")


def test_spellings_read_as_intended(written):
    atoms = list(written["song"][0]["def"]["phrase"]["p1"])
    assert spelling.relative(atoms)[:4] == ["=C4", "+0.i1*2", "+3", "+7!freq:@x1"]
    assert spelling.delta(atoms)[:4] == ["C4.i1*2", "+3", "+4!freq:@x1", "+5"]
    assert spelling.best_key(written) == (0, "minor")
    assert spelling.degree_text(27, (0, "minor")) == "3o2"
    assert spelling.degree_text(28, (0, "minor")) == "3#o2"
    assert spelling.expanded(["C4*3"]) == ["C4", "-", "-"]
    assert spelling.slashed(["C4.i1*3"]) == ["C4.i1/3"]
    assert spelling.relative(["-*4"]) == ["-*4"]


def test_shapes_and_identical_phrases(written):
    windows, other_keys = spelling.shapes(written)
    assert 0 < other_keys <= windows
    assert spelling.identical(written) == 0


def test_report_tables(tmp_path):
    path = tmp_path / "song.yaml"
    path.write_text(songio.dumps(song()), encoding="utf-8")
    report = spelling.report([str(path)])
    assert "| `C#4` | `C` `#` `4` |" in report and "| delta | " in report
    assert "phrase definitions identical to an earlier one: absolute 0" in report
