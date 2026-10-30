"""The inline pass: definitions written at their uses play as their named forms."""

import pytest
from unitracker_grammar.document import printed, read

import optimiser_docs as D

from unitracker import cli
from unitracker.optimiser import Guarantee, fit, optimise
from unitracker.optimiser.__main__ import main
from unitracker.optimiser.inline import candidates, cyclic, definitions, inline, inlined

SHARED = """unitracker:2.3
meta:{title:T,author:A}
voices:2
mod:
 vib(d):v + d*(phi % 4)
 add(d):|
	clc
	lda v
	adc #<d
	sta v
	lda v+1
	adc #>d
	sta v+1
	rts
restart:
 r1:[{hold:1,gate:0},{},end]
ins:
 i1:{ad:9,sr:240,ctrl:c1,pw:pw2,restart:r1}
 i2:{ad:5,ctrl:c2,pw:pw1}
 i3:{ad:3,ctrl:arp(7,4),pw:add(100)}
ctrl:
 c1:[{ctrl:65,note:+0},{wait:1,note:+7,run:pw3},end]
 c2:[{ctrl:17},{ctrl:16,run:c4},end]
 c4:[{ctrl:33},end]
 arp(a,b):[{ctrl:65,note:+0},{note:+a},{note:+b},{loop:0}]
pw:
 pw1:v + 3*phi
 pw2:[{pw:800},{wait:2,pw:v + 9},{loop:1}]
 pw3:v + 5
 pw4:vib(3)
 pw5:pt(900)
 pt(b):[{pw:b},{wait:2,pw:v + 1},end]
filter:
 f1:[{cut:300,res:9,route:1,mode:16,vol:15},end]
phrase:
 p1:[C4.i1,D.i2 run:f1,-*2,E.i3 freq:vib(2),-*3]
 p2:[G3.i2,- run:c2,A,-*5]
song:
 - play:[p1,p2]
 - play:[p2+2,p2-3]
 - end
"""
SINGLE = ["c1", "c4", "f1", "i1", "i3", "p1", "pw1", "pw2", "pw3", "r1"]
# i2's named binding restarts its phi on each of p2's notes, as an instrument's own binding does
CONTINUOUS = SHARED.replace("p2:[G3.i2,- run:c2,A,-*5]", "p2:[G3.i2,-,A.i2,-*5]")
RUNS = SHARED.replace("i3:{ad:3,ctrl:arp(7,4),pw:add(100)}", "i3:{ad:3,pw:pw4}").replace(
    "- run:c2", "- run:pw5"
)


def test_named_writes_nothing_inline():
    doc = read(SHARED)
    assert candidates(doc, "named") == set() and inline(doc, "named") == (doc, [])
    with pytest.raises(ValueError):
        candidates(doc, "every")


def test_single_inlines_each_definition_used_once_and_keeps_shared_ones_and_templates():
    doc, guarantee = read(SHARED), Guarantee(SHARED)
    assert sorted(candidates(doc, "single")) == SINGLE
    out, refused = inline(doc, "single", guarantee.holds)
    text = printed(out)
    assert not refused and read(text) == out and guarantee.holds(text)
    text = " ".join(text.split())
    assert set(definitions(out)) == {"i2", "c2", "p2", "pw4", "pw5"}
    assert {k for k, _ in out.mods} == {"vib(d)", "add(d)"}
    assert "arp(a,b):[" in text and "ctrl:arp(7,4)" in text and "pw:add(100)" in text
    assert "run:ctrl[{ctrl:33},end]" in text and "run:pw[{pw:v + 5},end]" in text
    assert "D.i2 run:filter[{cut:300" in text and "pw:v + 3*phi" in text
    assert "- play:[p2+2,p2-3]" in text and "restart:[{hold:1,gate:0}" in text


def test_all_defines_each_definition_the_song_reaches_at_its_first_use():
    doc, guarantee = read(SHARED), Guarantee(SHARED)
    assert candidates(doc, "all") == set()
    out, refused = inline(doc, "all", guarantee.holds)
    text = printed(out)
    assert not refused and read(text) == out and guarantee.holds(text)
    assert not out.defs and not out.mods and "pw4" not in text and "pt(" not in text
    for name in ("i1=", "i2=", "c2=", "p2=", "arp(a,b)=", "add(d)=", "vib(d)="):
        assert text.count(name) == 1, name
    assert "- run:c2," in text and "[p2+2,p2-3]" in text
    assert inline(doc, "all", lambda text: False) == (doc, ["first use"])


def test_a_named_binding_inline_is_the_field_s_binding():
    out, refused = inline(read(CONTINUOUS), "single", Guarantee(CONTINUOUS).holds)
    assert not refused and "pw:v + 3*phi}" in printed(out) and "{pw:v + 3*phi}" not in printed(out)


def test_a_call_inline_stays_a_call_or_binds_as_a_program():
    doc = read(RUNS)
    out = inlined(doc, {"pw4", "pw5"})
    text = printed(out)
    assert "pw:vib(3)}" in text and "{pw:vib(3)}" not in text and "- run:pt(900)" in text
    assert Guarantee(RUNS).holds(text)


def test_a_definition_reaching_itself_stays_named():
    text = SHARED.replace("c4:[{ctrl:33},end]", "c4:[{ctrl:33,run:c4},end]")
    doc = read(text)
    assert cyclic(definitions(doc)) == {"c4"} and "c4" not in candidates(doc, "single")


def test_a_refused_definition_is_kept_named_and_the_rest_inline():
    out, refused = inline(read(SHARED), "single", lambda text: "run:pw[" not in text)
    assert refused == ["pw3"] and set(definitions(out)) == {"i2", "c2", "p2", "pw3", "pw4", "pw5"}


def test_the_driver_inlines_after_its_passes(tmp_path, capsys):
    result = optimise(SHARED, inline="single")
    assert result.after != result.before and Guarantee(SHARED).holds(result.text)
    assert "i1" not in result.text and "i2:" in result.text
    source, out = tmp_path / "in.yaml", tmp_path / "out.yaml"
    source.write_text(SHARED, encoding="utf-8")
    assert main([str(source), "-o", str(out), "--inline", "single"]) == 0
    assert out.read_text(encoding="utf-8") == result.text
    assert "after:" in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["single", "all"])
@pytest.mark.parametrize("tune", ["metal_warrior", "crosseyed", "space_invadarz"])
def test_a_lifted_document_inline_plays_its_grid(tune, mode, request, tmp_path):
    out = tmp_path / "out.yaml"
    path = request.getfixturevalue(tune)
    argv = [str(path), "-o", str(out), "--optimise", "--ticks", "3000", "--inline", mode]
    argv.append("--unchecked")
    assert cli.lift_main(argv) == 0
    text = out.read_text(encoding="utf-8")
    doc = read(text)
    assert printed(doc) == text and (mode == "single" or not doc.defs and not doc.mods)
    assert Guarantee(cli.lift(path.read_bytes(), check=False), 3000).holds(text)


def test_the_inline_pass_follows_the_lossy_mode():
    exact = {cell: fit.Tolerance(0, 0) for cell in fit.BITS}
    lossy = optimise(D.EXACT, lossy=exact)
    result = optimise(D.EXACT, lossy=exact, inline="all")
    assert result.lossy.text == lossy.lossy.text and not result.doc.defs and not result.doc.mods
    assert Guarantee(lossy.lossy.text).holds(result.text)
