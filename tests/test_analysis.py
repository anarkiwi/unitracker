"""The analyses: the orthogonality report, the token studies and the note and duration spellings."""

import pytest
from conftest import OPTIMISED
from test_optimise import C1, I1, PULSE, RUN, RUN_TWICE, doc, ins_line, run

from unitracker.analysis import orthogonality, spelling, tokenstudy
from unitracker.format import songio

TOY = {
    "name": "toy",
    "constants": {"k": 3, "unused": 4},
    "voice": {"a": {"width": 8}, "b": {"width": 8}, "c": {"width": 8}, "m": {"width": 8}},
    "procs": {"p": [{"set": {"c": 1}}], "q": []},
    "record_types": {
        "prog": {"chain": "next", "fields": ["v", "w", "next"], "do": [{"set": {"a": "row.v"}}]},
        "ins": {"fields": ["vib"]},
    },
    "modulators": {"units": [{"name": "u", "retire": {"record": "ins", "clear": {"vib": 0}}}]},
    "init": [{"set": {"m": 0}}],
    "tick": [
        {"for_voices": [{"set": {"a": "a + k", "b": "a + 1"}}, {"call": "p"}]},
        {"if": "m == 1", "then": [{"set": {"a": 0}}]},
        {"if": "c > 5", "then": [{"set": {"b": 2}}]},
        {"accumulate": "a", "step": 1, "on_bound": [{"set": {"m": 0}}]},
    ],
    "ghost": {"voice": {0: "a"}},
}


def test_dead_state_of_a_playroutine():
    found = {(ln.where, ln.forms): ln.resolution for ln in orthogonality.dead_state(TOY)}
    for where, forms in [
        ("cell `b`", "written, never read"),
        ("cell `c`", "read only by dead cells"),
        ("proc `q`", "never called"),
        ("constant `unused`", "never named"),
        ("test `c > 5`", "always false for every value its cells take"),
        ("field `prog.w`", "never read by the VM"),
    ]:
        assert (where, forms) in found
    assert all(w not in ("cell `a`", "cell `m`") for w, _ in found)
    assert found[("field `ins.vib`", "never read by the VM")].startswith("retired")
    resolution = found[("field `prog.next`", "never read by the VM")]
    assert resolution.startswith("read by the format's program walk")


@pytest.mark.parametrize("name", ["goattracker2", "sidwizard", "defmon"])
def test_library_playroutines_have_no_dead_cells_or_procs(name):
    lines = orthogonality.dead_state(songio.library(name))
    assert {ln.where.split()[0] for ln in lines} <= {"field"}
    if name == "defmon":
        restart = {ln.where: ln.resolution for ln in lines}["field `restart.ad`"]
        assert restart.startswith("format-side")


def test_vocabulary_lists_shapes_and_commands_no_row_reaches():
    ir = songio.loads(doc(I1, RUN, C1))
    found = [(ln.where, ln.forms) for ln in orthogonality.vocabulary([("toy", ir)])]
    assert any(w.startswith("toy codec shape ctrl[") and w.endswith("`{vol}`") for w, _ in found)
    assert ("toy command `!tempo`", "no lifted event plays it") in found
    assert all("command `!run`" not in w for w, _ in found)
    uses = orthogonality.shape_uses(ir)
    assert uses[("ctrltab", 0)] >= 2 and orthogonality.command_uses(ir) == {"run": 4}


def test_report_lists_a_known_redundancy_once_per_form():
    result = run(doc(I1, "[C4.i1*2, D4.i1*2, E4!ad:9*2, F4!ad:9*2]"), ["commands"])
    report = orthogonality.report([("toy", result)])
    assert report.startswith("# Orthogonality and redundancy")
    key = report.split("## Non-orthogonal key")[1].split("##")[0]
    assert "toy p1[2], p1[3]: `E4!ad:9*2` ≡ `E4*2`" not in key
    assert "`E4!ad:9*2` ≡ `E4*2`" in key and "`F4!ad:9*2` ≡ `F4*2`" in key
    assert "`D4.i1*2` ≡ `D4*2`" in key


def test_report_lists_vocabulary_and_song_redundancy():
    result = run(doc(I1, RUN_TWICE, C1, steps=16), ["hoist", "instruments"])
    report = orthogonality.report([("toy", result)])
    vocab = report.split("## Vocabulary redundancy")[1].split("##")[0]
    assert "goattracker2 · toy i1: `X.i1!run:c1` ≡ `X.i1 with i1 + {ctrl: c1}`" in vocab
    assert "## Song redundancy\n\n- none" in report


def test_reference_tunes_report_is_independent_of_order(optimised_tunes):
    songs = [(name, optimised_tunes[name][1]) for name in sorted(OPTIMISED)]
    first = orthogonality.report(songs)
    assert orthogonality.report(songs[::-1]) == first
    assert "X.i3!run:c1" in first and "## VM dead state" in first
    assert "defmon · field `restart.ad`" in first and "sidwizard · csillag codec shape" in first


I2 = ins_line("i2", "ad: 4, ctrl: [{ctrl: 33, note: +0}, end]")
X = (
    "      x:\n        x1: (v + 32*[(φ - 3) % 12 >= 6] - 16) % 65536\n"
    "        x2: (v + 64*[(φ - 3) % 12 >= 6] - 32) % 65536\n"
)
PHRASE = (
    "[C4.i1*2, D#4, G4!freq:x1, C5, C4.i2*2, D#4, G4!freq:x2, C5, F4.i1, G#4, C5, F5,"
    " C4*2, D#4, G4, C5, C4*2, D#4, G4, C5]"
)
SECOND = (
    "  - def:\n      phrase:\n        p3: [C4.i2*2, D#4, G4, C5, -*19]\n    play: [p3, p2, p2]\n"
)


def song():
    pulse = ins_line("i1", f"ad: 9, ctrl: [{{ctrl: 65, note: +0}}, end], pw: {PULSE}")
    text = doc(f"{pulse}\n{I2}", PHRASE, X, steps=24, extra=SECOND)
    return songio.loads(text.replace("\nsong:", "\ntempo: 6\nsong:"))


@pytest.fixture(scope="module", name="written")
def fixture_written():
    """The token-study song as the document ``songio.dumps`` hands its respeller."""
    holder = {}
    songio.dumps(song(), respell=lambda d: holder.setdefault("doc", d))
    return holder["doc"]


def test_sections_rests_and_primitives_split_a_document(written):
    text = songio.dumps(song())
    split = tokenstudy.tokenizers()["cl100k_base"]
    counts = tokenstudy.sections(text, split)
    assert all(counts[s] > 0 for s in tokenstudy.SECTIONS)
    total = sum(counts[s] for s in tokenstudy.SECTIONS)
    assert abs(total - counts["total"]) <= len(text.splitlines())
    moved = tokenstudy.sections(songio.dumps(song(), respell=tokenstudy.plays_first), split)
    assert moved["phrases"] > 0 and moved["definitions"] > 0
    assert moved["structure"] < counts["structure"]
    rest, total = tokenstudy.rests(written, split)
    assert 0 < rest < total
    assert tokenstudy.primitive_uses(written) == {"tri": 1, "clamp": 0}


def test_mockups_rewrite_what_they_name():
    texts = tokenstudy.mockups(song())
    assert "forms: {f1: '(a, b) (v + a*[(φ - 3) % 12 >= 6] - b) % 65536'}" in texts["forms"]
    assert "f1(32, 16)" in texts["forms"] and "f1(64, 32)" in texts["forms"]
    assert "tri(φ - 32, 128)" in texts["primitives"]
    assert "&s1" in texts["sub-phrases"] and "sub:" in texts["sub-phrases"]
    assert texts["grouped definitions"].count("def:") == 1
    assert "about: {i1: 'pul wave; ad, ctrl, pw, restart'" in texts["summaries"]
    assert "bpm:" in texts["tempo"]
    assert "voices: [[p1, p3], [p2, p2], [p2, p2]]" in texts["contents"]
    assert "\ndefs:\n" in texts["plays first"] and "i1_pul" in texts["mnemonics"]
    assert texts["hoisted instruments"] == texts["baseline"]


def test_hoisted_instruments_merge_phrases_alike_but_for_instruments():
    phrases = {"p1": ["C4.i1", "D4"], "p2": ["C4.i2", "D4"]}
    doc_ = {"song": [{"def": {"phrase": phrases}, "play": ["p1"]}, {"play": ["p2+3"]}, "end"]}
    out = tokenstudy.hoisted_instruments(doc_)
    assert "p2" not in out["song"][0]["def"]["phrase"] and out["song"][1]["play"] == ["p1(i1=i2)+3"]


def test_tokenizers_split_note_names_differently():
    toks = tokenstudy.tokenizers()
    if "llama2" not in toks:
        pytest.skip("Llama 2 tokenizer not in the local Hugging Face cache; tests do not fetch it")
    assert set(toks) == {"cl100k_base", "o200k_base", "llama2"}
    assert toks["cl100k_base"]("C#4") == ["C", "#", "4"]
    assert toks["llama2"]("C10") == ["▁C", "1", "0"]


def test_every_spelling_has_an_exact_inverse(written):
    key = spelling.best_key(written)
    variants = spelling.respelt(song())
    assert set(variants) == {"relative", "degrees", "delta", "expanded", "slashed", "absolute"}
    for name, (text, respelt) in variants.items():
        pairs = zip(tokenstudy.phrase_defs(respelt), tokenstudy.phrase_defs(written))
        for (table, pname), (base, _) in pairs:
            assert spelling.absolute(list(table[pname]), name, key) == list(base[pname]), name
        assert (text != variants["absolute"][0]) == (name != "absolute")


def test_spellings_read_as_intended(written):
    atoms = list(written["song"][0]["def"]["phrase"]["p1"])
    assert spelling.relative(atoms)[:4] == ["=C4", "+0.i1*2", "+3", "+7!freq:x1"]
    assert spelling.delta(atoms)[:4] == ["C4.i1*2", "+3", "+4!freq:x1", "+5"]
    assert spelling.best_key(written) == (0, "minor")
    assert spelling.degree_text(27, (0, "minor")) == "3o2"
    assert spelling.degree_text(28, (0, "minor")) == "3#o2"
    assert spelling.expanded(["C4*3"]) == ["C4", "-", "-"]
    assert spelling.slashed(["C4.i1*3"]) == ["C4.i1/3"]
    assert spelling.relative(["-*4"]) == ["-*4"]
    windows, other_keys = spelling.shapes(written)
    assert 0 < other_keys <= windows
    assert spelling.identical(written) == 0


def test_the_reports_tabulate_the_studies(tmp_path):
    path = tmp_path / "song.yaml"
    path.write_text(songio.dumps(song()), encoding="utf-8")
    study = tokenstudy.report([str(path)])
    assert "| variant | document | total cl100k_base |" in study
    assert "| sub-phrases | " in study and "| total o200k_base |" in study
    assert "expression primitives" in study
    spelt = spelling.report([str(path)])
    assert "| `C#4` | `C` `#` `4` |" in spelt and "| delta | " in spelt
    assert "phrase definitions identical to an earlier one: absolute 0" in spelt
