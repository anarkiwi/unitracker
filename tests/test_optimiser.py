"""The generic optimiser: each pass on small documents, the guarantee, the driver, the CLI, and
lifted GoatTracker documents where the cache holds them."""

import os
from pathlib import Path

import numpy as np
import pytest

from unitracker_grammar import document

import optimiser_docs as D

from unitracker.optimiser import Guarantee, GuaranteeError, optimise, passes, size, templates, tree
from unitracker.optimiser.__main__ import main
from unitracker.optimiser.guarantee import grid, same

read, printed = document.read, document.printed
LIFTED = Path(os.environ.get("UNITRACKER_DIRECT_DOCS", ".tunecache/direct"))


def labels(edits) -> list:
    return [e.label for e in edits]


def test_defaults_drop_what_a_document_spells_at_its_default():
    result = optimise(D.DEFAULTS, order=(("defaults", passes.defaults),))
    assert result.after < result.before and not result.refused
    assert "tempo" not in result.text and "tick.freq: none" not in result.text
    assert "c1: [{ctrl: 65}, {ctrl: 64}, end]" in result.text
    assert "p1: [C4.i1, -, D4, -*3]" in result.text
    assert Guarantee(D.DEFAULTS).holds(result.text)


def test_defaults_keep_a_none_of_a_slot_something_binds():
    found = labels(passes.defaults(read(D.DEFAULTS_BOUND)))
    assert not any("none" in label for label in found)


def test_defaults_drop_header_lines_and_spellings_at_their_defaults():
    text = D.doc("      phrase:\n        p1: [C4!tempo:7/7!vtempo:5/5, -*3]\n", mods="")
    text = text.replace(
        "voices: 1\n",
        "clock: {cycles_per_tick: 19656, clock_hz: 985248}\nvoices: 3\ntempo: [5, 5]\n",
    )
    found = labels(passes.defaults(read(text)))
    assert {"clock: PAL", "voices: 3", "tempo: [a, a]"} <= set(found)
    assert sum("a/a" in label for label in found) == 2
    edited = read(text)
    for edit in passes.defaults(edited):
        edited = edit.apply(edited)
    out = printed(edited)
    assert "!tempo:7!vtempo:5" in out and "clock" not in out and "tempo: 5\n" in out


def test_defaults_drop_an_instrument_binding_equal_to_its_slot_s_default():
    text = D.HOIST.replace("voices: 1\n", "voices: 1\nslots: {pw: {pw: {mod: '2048'}}}\n")
    found = labels(passes.defaults(read(text)))
    assert sum(label.endswith("pw: 2048") for label in found) == 5


def test_merge_makes_identical_definitions_one_and_renames_their_references():
    result = optimise(D.MERGE, order=(("merge", passes.merge),))
    assert sorted(n for _, n in result.kept) == [
        "ctrl c2 -> c1",
        "ins i2 -> i1",
        "mod wob -> vib",
        "phrase p2 -> p1",
    ]
    assert "i2" not in result.text and "c2" not in result.text and "wob" not in result.text
    assert "  - play: [p1]\n" in result.text


def test_merge_refuses_templates_whose_bindings_keep_their_phase_once_one():
    result = optimise(D.MERGE_PHI, order=(("merge", passes.merge),))
    assert result.refused == ["mod wob -> vib"] and result.text == D.MERGE_PHI


def test_merge_names_program_templates_and_their_calls_by_position():
    text = D.doc(
        "      ins:\n"
        "        i1: {ad: 9, ctrl: arp(7,4)}\n"
        "        i2: {ad: 9, ctrl: hop(7,4)}\n"
        "      ctrl:\n"
        "        arp(a, b): [{ctrl: 65, note: +a}, {note: -b}, {pw: walk(b)}, end]\n"
        "        hop(c, d): [{ctrl: 65, note: +c}, {note: -d}, {pw: walk(d)}, end]\n"
        "      phrase:\n"
        "        p1: [C4.i1, -, D4.i2, -*3]\n",
        mods="mod:\n  walk(s): v + s\n",
    )
    doc = read(text)
    found = passes.merge(doc)
    assert labels(found) == ["ctrl hop -> arp"]
    out = printed(found[0].apply(doc))
    assert "hop" not in out and "i2: {ad: 9, ctrl: arp(7,4)}" in out


def test_hoist_moves_a_binding_every_instrument_carries_into_its_slot_s_default():
    result = optimise(D.HOIST, order=(("hoist", passes.hoist),))
    assert result.kept == [("hoist", "pw.pw: 2048")]
    assert "slots: {pw: {pw: {mod: '2048'}}}\n" in result.text and "pw: 2048" not in result.text


def test_hoist_is_refused_where_the_default_plays_before_the_first_note():
    result = optimise(D.HOIST_LATE, order=(("hoist", passes.hoist),))
    assert result.refused == ["pw.pw: 2048"] and not result.kept


def test_hoist_resets_on_note_a_binding_that_reads_phi():
    text = D.HOIST.replace("pw: 2048", "pw: vib(2)")
    text = text.replace("voices: 1\n", "voices: 1\n" + D.MODS.split("  wob", maxsplit=1)[0])
    (edit,) = passes.hoist(read(text))
    assert "slots: {pw: {pw: {mod: vib(2), reset: [note]}}}" in printed(edit.apply(read(text)))


def test_hoist_proposes_nothing_without_a_common_binding_or_before_1_22():
    assert not passes.hoist(read(D.MERGE))
    assert not passes.hoist(read(D.HOIST.replace("unitracker: 1.23", "unitracker: 1.21")))
    assert not passes.hoist(read(D.doc("      phrase:\n        p1: [C4, -*3]\n", mods="")))


def test_the_guarantee_compares_eventually_periodic_grids_forever():
    a = np.arange(10, dtype=np.uint8).reshape(-1, 1)
    looped = np.array([[0], [1], [2], [1], [2]], dtype=np.uint8)
    twice = np.array([[0], [1], [2], [1], [2], [1], [2]], dtype=np.uint8)
    assert same((looped, 1), (twice, 3)) and same((looped, 1), (looped[:3], 1))
    assert not same((looped, 1), (looped[:3], 2)) and not same((a, None), (a[:3], 1))
    assert same((a, None), (a.copy(), None)) and not same((a, None), (a[:9], None))


def test_the_guarantee_plays_to_an_end_a_proved_loop_or_a_tick_count():
    frames, loop = grid(D.LOOPING)
    assert loop is not None and len(frames) > loop
    assert grid(D.MERGE)[1] is None
    assert len(grid(D.LOOPING_CODE, ticks=50)[0]) == 50
    with pytest.raises(GuaranteeError):
        Guarantee(D.LOOPING_CODE)
    guarantee = Guarantee(D.LOOPING)
    assert guarantee.holds(D.LOOPING) and not guarantee.holds("unitracker: 1.23\n")
    assert not guarantee.holds(D.LOOPING.replace("D4.i2", "E4.i2"))


def test_a_looping_document_with_code_is_optimised_over_its_tick_count():
    text = D.LOOPING_CODE.replace(
        "\n  i1:", "\n  i2: {ad: 9, ctrl: [{ctrl: 65}, end], pw: up(3)}\n  i1:"
    )
    text = text.replace("D4, -]", "D4.i2, -]")
    result = optimise(text, ticks=100)
    assert ("merge", "ins i1 -> i2") in result.kept
    assert Guarantee(text, 100).holds(result.text)


def test_the_tree_walks_and_rebuilds_a_document():
    doc = read(D.MERGE)
    paths = dict(tree.walk(doc))
    assert isinstance(paths[("defs", 0, "records", 0, 1)], document.Instrument)
    path = ("defs", 0, "records", 0, 1)
    assert tree.at(doc, path).get("ad") == 9
    edited = tree.update(doc, path, lambda ins: document.replace(ins, fields={"ad": 1}))
    assert tree.at(edited, path).fields == (("ad", 1),)
    assert tree.transform(doc, lambda n: n) is doc


def test_size_is_the_token_count_of_the_canonical_text():
    doc = read(D.MERGE)
    assert size(doc) == size(D.MERGE) > 0


def test_the_cli_writes_the_optimised_document_and_its_sizes(tmp_path, capsys):
    source, out = tmp_path / "in.yaml", tmp_path / "out.yaml"
    source.write_text(D.MERGE, encoding="utf-8")
    assert main([str(source), "-o", str(out)]) == 0
    err = capsys.readouterr().err
    assert "before:" in err and "after:" in err and "merge: ins i2 -> i1" in err
    assert Guarantee(D.MERGE).holds(out.read_text(encoding="utf-8"))
    assert main([str(source), "--ticks", "40", "--rounds", "1"]) == 0
    assert capsys.readouterr().out.startswith("unitracker: 1.23\n")


@pytest.mark.parametrize("path", sorted(LIFTED.glob("*.yaml")), ids=lambda p: p.stem)
def test_a_lifted_goattracker_document_shrinks_and_plays_its_grid(path):
    text = path.read_text(encoding="utf-8")
    result = optimise(text, ticks=3000)
    assert result.after < result.before
    assert Guarantee(text, 3000).holds(result.text)
    assert read(result.text) == result.doc


def test_merge_drops_a_top_level_definition_and_renames_its_references():
    doc = read(D.MERGE)
    edit = next(e for e in passes.merge(doc) if e.label == "ctrl c2 -> c1")
    out = edit.apply(doc)
    assert [n for n, _ in out.defs[1].records] == ["c1"]
    assert out.defs[0].records[1][1].get("ctrl") == "c1"


def test_template_makes_programs_alike_but_for_integers_one_template_of_calls():
    found = templates.template(read(D.TEMPLATE))
    assert labels(found) == ["ctrl ctrl_1(t, t2): 3", "restart restart_1(h, ad, sr): 3"]
    result = optimise(D.TEMPLATE, order=(("template", templates.template),))
    assert len(result.kept) == 2 and result.after < result.before
    text = result.text
    assert (
        "ctrl_1(t, t2): [{ctrl: 65, note: +t}, {wait: 1, note: +t2}, {wait: 1, note: +0}," in text
    )
    assert "restart_1(h, ad, sr): [{hold: h, gate: 0, ad: 15}, {ad: ad, sr: sr}, end]" in text
    assert "i1: {ad: 9, sr: 240, ctrl: ctrl_1(7,4), restart: restart_1(2,9,240)}" in text
    assert "!run:ctrl_1(7,3)" in text and "c2" not in text
    assert Guarantee(D.TEMPLATE).holds(text)


def test_template_proposes_nothing_without_two_programs_differing_in_integers():
    assert not templates.template(read(D.MERGE))
    assert not templates.template(read(D.TEMPLATE.replace("unitracker: 1.23", "unitracker: 1.22")))
