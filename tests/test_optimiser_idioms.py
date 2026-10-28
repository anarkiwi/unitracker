"""The optimiser's transpose and snap passes, the names rename and the defaults pass's
instrument nones and empty rows, on hand-written documents and lifted ones."""

import pytest
from unitracker_grammar.document import printed, read

import optimiser_docs as D

from unitracker import cli
from unitracker.optimiser import Guarantee, driver, names, optimise, passes, snap, templates
from unitracker.optimiser.transpose import number, transpose


def only(name: str, propose, text: str, **kwargs):
    return optimise(text, order=((name, propose),), rename=False, **kwargs)


def test_notes_number_as_the_format_does():
    assert [number(n) for n in ("C4", "C#4", "B-1", "-", "off", "on")] == [48, 49, -1] + [None] * 3


def test_transpose_folds_a_phrase_shifted_by_a_constant_into_the_first():
    edits = transpose(read(D.TRANSPOSE))
    assert [e.label for e in edits] == ["phrase p2 -> p1+2"]
    result = only("transpose", transpose, D.TRANSPOSE)
    assert result.kept == [("transpose", "phrase p2 -> p1+2")] and result.after < result.before
    assert "p2:" not in result.text and " - play:[p1+1]\n" in result.text
    assert "p3:" in result.text and Guarantee(D.TRANSPOSE).holds(result.text)


def test_transpose_writes_no_zero_transpose_and_skips_phrases_without_notes():
    text = D.TRANSPOSE.replace("[p2-1]", "[p2-2]").replace("p3:[C4.i1,", "p3:[off,")
    text = text.replace("F,G,-,C5,", "-,-,-,-,")
    out = transpose(read(text))[0].apply(read(text))
    assert " - play:[p1]\n - play:[p1]\n" in printed(out)
    assert len(transpose(read(text))) == 1


def test_transpose_keeps_a_phrase_whose_fold_costs_more_than_it_saves():
    text = D.TRANSPOSE.replace("p1:[C4.i1,-,E4,G4,-,C5,-*3]", "p1:[C4.i1,-]")
    text = text.replace("p2:[D4.i1,-,f4,A4,-,D5,-*3]", "p2:[D4.i1,-]")
    text = text.replace("[p2-1]", "[p2]" + "\n - play:[p2]" * 8)
    result = only("transpose", transpose, text)
    assert not result.kept and result.text == text


def test_snap_drops_a_jump_to_the_note_or_ends_the_bindings_it_followed():
    edits = snap.snap(read(D.SNAP), 100)
    assert [e.label for e in edits] == ["p1/2: tick.freq:snap()", "p1/6: tick.freq:snap()"]
    result = only("snap", snap.snap, D.SNAP)
    assert len(result.kept) == 2 and Guarantee(D.SNAP).holds(result.text)
    assert "E legato,-,D.i2,-,G freq:none legato" in result.text
    assert "E tick.freq:up() legato" in result.text
    unbound = D.SNAP.replace("D.i2,-,E tick.freq:up()", "C4.i1,-,E tick.freq:up()")
    assert [e.label for e in snap.snap(read(unbound), 100)] == ["p1/2: tick.freq:snap()"]


def test_snap_finds_a_binding_after_and_inside_a_repeat_group():
    doc = read(D.SNAP_GROUP)
    edits = snap.snap(doc, 100)
    assert [e.label for e in edits] == ["p1/1/body/2: tick.freq:snap()", "p1/4: tick.freq:snap()"]
    for edit in edits:
        doc = edit.apply(doc)
    assert "[D.i2,-,E freq:none tick.freq:none legato,-]x2,D.i2,-,G freq:none" in printed(doc)
    assert Guarantee(D.SNAP_GROUP).holds(printed(doc))


def test_snap_is_refused_where_the_modulator_writes_state_read_later():
    sites = snap.sites(D.SNAP_STATE, 100)
    assert [s.exact for s in sites.values()] == [True, False]
    result = only("snap", snap.snap, D.SNAP_STATE)
    assert result.refused == ["p1/2: freq:snapc()"] and result.text == D.SNAP_STATE


def test_defaults_drop_an_instrument_none_that_ends_no_binding():
    result = only("defaults", passes.defaults, D.INS_NONE)
    assert "i1:{ad:9,ctrl:c1}" in result.text
    assert "i2:{ad:8,ctrl:c1,freq:none}" in result.text
    assert "defs/0/records/1/1: freq:none" in result.refused
    assert "c1:[{hold:3,ctrl:65},{ctrl:64},end]" in result.text
    assert Guarantee(D.INS_NONE).holds(result.text)


def test_defaults_fold_empty_rows_together_and_refuse_a_restart_s_last():
    text = D.INS_NONE.replace("{},{ctrl:64}", "{},{wait:1},{ctrl:64}")
    result = only("defaults", passes.defaults, text)
    assert "c1:[{hold:5,ctrl:65},{ctrl:64},end]" in result.text
    looped = read(D.INS_NONE.replace("{ctrl:64},end]", "{ctrl:64},{},{loop:1}]"))
    found = [e.label for e in passes.defaults(looped) if "{}" in e.label]
    assert found == ["defs/1/records/0/1/rows/3: {}"]
    result = only("defaults", passes.defaults, D.RESTART_EMPTY)
    assert any(label.endswith("rows/2: {}") for label in result.refused)
    assert "{hold:2,ad:9,sr:240},{},end]" in result.text


def test_names_name_generated_templates_by_their_shape():
    result = optimise(D.TEMPLATE, order=(("template", templates.template),))
    assert ("names", "ctrl_1 -> arp") in result.kept and ("names", "restart_1 -> hr") in result.kept
    assert "arp(t,t2):[{ctrl:65" in result.text and " run:arp(7,3)" in result.text
    assert "ctrl_1" not in result.text and "hr(h,ad,sr):[" in result.text
    assert Guarantee(D.TEMPLATE).holds(result.text)


def test_names_number_a_taken_stem_and_keep_other_names():
    text = D.TEMPLATE.replace(" - end\n", " - end\n").replace(
        "ins:\n", "mod:\n arp(d):v + d\nins:\n", 1
    )
    doc = read(text)
    for edit in templates.template(doc):
        doc = edit.apply(doc)
    _, renamed = names.rename(doc)
    assert renamed == {"ctrl_1": "arp2", "restart_1": "hr"}
    assert names.rename(read(D.TEMPLATE)) == (read(D.TEMPLATE), {})
    assert [names.stem(k, read(D.TEMPLATE).records("ctrl")["c1"]) for k in ("pw", "filter")] == [
        "pwm",
        "flt",
    ]


def test_a_rename_the_guarantee_refuses_is_not_kept(monkeypatch):
    monkeypatch.setattr(names, "rename", lambda doc: (read(D.MERGE), {"a": "b"}))
    result = optimise(D.TEMPLATE, order=())
    assert result.refused == ["names"] and result.text == D.TEMPLATE


def test_the_passes_run_in_order_with_the_rename_last():
    order = [n for n, _ in driver.PASSES]
    assert order == [
        "defaults",
        "merge",
        "transpose",
        "snap",
        "template",
        "hoist",
        "once",
        "prelude",
        "instruments",
        "chords",
        "phrases",
        "repeats",
    ]


@pytest.mark.parametrize("tune", ["metal_warrior", "lykia_boss", "persei", "space_invadarz"])
def test_a_lifted_document_optimises_through_every_pass(tune, request):
    text = cli.lift(request.getfixturevalue(tune).read_bytes(), check=False)
    result = optimise(text, 3000)
    assert result.after < result.before and Guarantee(text, 3000).holds(result.text)
    assert read(result.text) == result.doc
    assert not any(names.GENERATED.fullmatch(n) for n, _ in result.doc.mods)
