"""The format 2.3 template passes: ``prelude``, ``instruments``, ``phrases`` and ``chords``,
the lossy mode's prelude forms and the inline modes of their templates and call records."""

import os
import re
from pathlib import Path

import numpy as np
import pytest
from unitracker_grammar import expr
from unitracker_grammar.document import Call, printed, read
from unitracker_grammar.prelude import NAMES

import optimiser_docs as D

from unitracker.optimiser import Guarantee, fit, optimise
from unitracker.optimiser.chords import chord, chords, note_positions, permutation
from unitracker.optimiser.inline import inline
from unitracker.optimiser.prelude import core, prelude
from unitracker.optimiser.records import instruments, phrases
from unitracker.optimiser.templates import _HOLE, _parameters
from unitracker.optimiser.trace import FIRING

REFERENCE = Path(os.environ.get("UNITRACKER_REF_DOCS", "out/ref22/lifted"))


def only(name: str, propose, text: str, **kwargs):
    """``text`` optimised by the one pass ``propose``."""
    return optimise(text, order=((name, propose),), rename=False, **kwargs)


def firings(phi, v, n=48, cell="freq") -> fit.Firings:
    """Synthetic firings of one voice, ``v`` each one's input."""
    rec = np.zeros(len(phi), dtype=FIRING)
    rec["t"], rec["phi"], rec["v"], rec["N"], rec["n"] = np.arange(len(phi)), phi, v, n, n
    return fit.Firings(rec, cell, 985248)


def played(fir: fit.Firings, name: str, args: tuple, chain: bool) -> np.ndarray:
    """The values prelude call ``name(args)`` gives at ``fir``'s firings, each on the value the
    one before gave where ``chain``."""
    params, node = fit._prelude(name)  # pylint: disable=protected-access
    bound = expr.bind(node, dict(zip(params, args)))
    fn = expr.compile_expr(bound, {"_F": fir.word, "_T": {}}, zero=True)
    out, v = [], int(fir.base["v"][0])
    for k, phi in enumerate(fir.phi.tolist()):
        v = fn(v if chain else int(fir.base["v"][k]), phi, int(fir.N[k])) & fir.mask
        out.append(v)
    return np.array(out, dtype=np.int64)


def test_core_spells_chord_names_as_intervals():
    assert core(Call("arp", ("min7", 2))) == Call("arp", (3, 7, 10, 2))


def test_prelude_replaces_calls_that_play_as_prelude_calls_and_drops_their_templates():
    edits = prelude(read(D.PRELUDE), 2000)
    assert [e.label for e in edits] == [
        "gl -> glide",
        "sw -> sweep",
        "tr -> tremolo",
        "up -> glide",
        "vb -> vibrato",
    ]
    result = only("prelude", prelude, D.PRELUDE, ticks=2000)
    for call in ("tremolo(800,1600,4)", "vibrato(5,3,2)", "sweep(300,2000,20)", "glide(40)"):
        assert call in result.text
    assert "pw:glide(9),freq:glide(-7)" in result.text and "freq:wob(3)" in result.text
    assert [k for k, _ in read(result.text).mods] == ["wob(e)"]
    assert Guarantee(D.PRELUDE).holds(result.text) and result.after < result.before


def test_prelude_proposes_nothing_without_templates_of_its_own():
    assert not prelude(read(D.INSTRUMENTS), 100)


@pytest.mark.parametrize(
    "name,args,chain",
    [
        ("glide", (-37,), True),
        ("slide", (7, 40), True),
        ("vibrato", (3, 4, 5), False),
        ("pwm", (300, 900, 70), False),
        ("sweep", (2000, 400, 90), False),
        ("tremolo", (100, 700, 3), False),
    ],
)
def test_each_prelude_template_is_fitted_exactly_from_its_values(name, args, chain):
    fir = firings(np.arange(40), np.full(40, 4000), cell="freq" if name != "pwm" else "pw")
    out = played(fir, name, args, chain)
    v = np.concatenate([[4000], out[:-1]]) if chain else fir.base["v"]
    fitted = dict(fit.prelude_calls(fir, v, out))
    assert name in fitted and fit.plays(fir, name, fitted[name], v, out)
    other = out.copy()
    other[-1] ^= 1
    assert name not in dict(fit.prelude_calls(fir, v, other))


def test_a_call_on_two_cells_takes_the_prelude_call_that_plays_both():
    a = firings(np.arange(8), np.arange(8) * 5)
    b = firings(np.arange(8), np.arange(8) * 9, cell="pw")
    a.out, b.out = a.base["v"] + 3, b.base["v"] + 3
    assert fit.prelude_call([a, b]) == ("glide", (3,))
    b.out = b.base["v"] + 4
    assert fit.prelude_call([a, b]) is None


def test_the_lossy_mode_takes_a_prelude_call_for_a_form_within_tolerance():
    fir = firings(np.arange(30), 1000 + 5 * np.arange(30) + np.arange(30) % 2)
    fir.out = fir.base["v"] + 5
    fir.out[1::2] -= 1
    forms = fit.fits(fir, fit.Tolerance(10, 25))
    prel = [f for f in forms if f.shape == "prelude"]
    assert "glide" in {f.base for f in prel} and all(
        fit.within(f.err, fit.Tolerance(10, 25)) for f in prel
    )
    assert not [
        f for f in fit.fits(fir, fit.Tolerance(10, 25), prelude=False) if f.shape == "prelude"
    ]


def test_parameters_never_take_a_record_s_name():
    shape = (0, None, tuple((0, 0, "c", _HOLE) for _ in range(3)))
    names = _parameters(shape, [0, 1, 2], [(1, 2), (3, 4), (5, 6)], lambda s: s)
    assert list(names.values()) == ["c", "c_2", "c_3"]


def test_instruments_alike_but_for_integers_become_calls_of_one_template():
    edits = instruments(read(D.INSTRUMENTS))
    edit = edits[0]
    assert len(edits) == 1
    assert edit.label == "ins ins_1(ad,p): 3"
    result = only("instruments", instruments, D.INSTRUMENTS)
    assert " i1:ins_1(9,800)\n i2:ins_1(8,900)\n i3:ins_1(7,1000)\n" in result.text
    assert " ins_1(ad,p):{ad:ad,sr:240,ctrl:c1,pw:p,restart:hr(ad,240)}\n" in result.text
    assert " i4:{ad:7,sr:240,ctrl:c2," in result.text and "C4.i1" in result.text
    assert Guarantee(D.INSTRUMENTS).holds(result.text) and read(result.text) == result.doc
    assert not instruments(result.doc)


def test_instruments_of_one_shape_and_values_are_left_to_merge():
    text = D.INSTRUMENTS.replace(
        "ad:8,sr:240,ctrl:c1,pw:900,restart:hr(8,240)",
        "ad:9,sr:240,ctrl:c1,pw:800,restart:hr(9,240)",
    )
    text = text.replace("ad:7,sr:240,ctrl:c1,pw:1000,", "ad:7,sr:240,ctrl:c2,pw:1000,")
    assert not instruments(read(text))


def test_phrases_alike_but_for_notes_become_calls_of_one_template():
    edits = phrases(read(D.PHRASES))
    edit = edits[0]
    assert len(edits) == 1
    assert edit.label == "phrase ph_1(x,y,z,u): 2"
    result = only("phrases", phrases, D.PHRASES)
    assert " p1:ph_1(C4,G4,C5,D5)\n p2:ph_1(D4,A4,D5,E5)\n" in result.text
    assert (
        " ph_1(x,y,z,u):[x.i1,-,E4,y pw:800,[z,u]x2,-*3,E4 pw:900,-,G pw:1000,-*2,off]\n"
        in result.text
    )
    assert " p3:[F4.i2," in result.text
    assert Guarantee(D.PHRASES).holds(result.text) and read(result.text) == result.doc


def test_phrases_share_a_parameter_where_their_notes_are_equal():
    text = D.PHRASES.replace("[D5,E5]x2", "[D5,A4]x2")
    text = text.replace("[C5,D5]x2", "[C5,G4]x2")
    edits = phrases(read(text))
    edit = edits[0]
    assert len(edits) == 1
    assert edit.label == "phrase ph_1(x,y,z): 2"
    assert " ph_1(x,y,z):[x.i1,-,E4,y pw:800,[z,y]x2,-*3," in printed(edit.apply(read(text)))


def test_phrases_of_other_shapes_are_kept():
    text = D.PHRASES.replace("p2:[D4.i1,-,E4,A4 pw:800,", "p2:[D4.i1,-,E4,A4 pw:900,")
    assert not phrases(read(text))


def test_chords_are_named_by_music21_in_root_position_only():
    assert [chord(r) for r in ((4, 7), (3, 7, 10), (4, 7, 11), (7,), (2, 7))] == [
        "maj",
        "min7",
        "maj7",
        "power",
        "sus2",
    ]
    assert [chord(r) for r in ((3, 6, 9), (3, 7, 9), (12,), (7, 4), (0, 4))] == [None] * 5


def test_chords_permute_note_parameters_to_ascend_and_name_their_runs():
    doc = read(D.CHORDS)
    assert note_positions(doc) == {"arp": {0, 1, 2}, "sw": {1}, "ins_1": {1, 2, 3}}
    assert permutation(doc, "arp", {0, 1, 2}) == [1, 2, 0]
    result = only("chords", chords, D.CHORDS)
    text = result.text
    assert " arp(t2,t3,t):[{ctrl:65},{note:+t2},{note:+t3},{note:+t},{loop:0}]\n" in text
    assert " ins_1(ad,t2,t3,t):{ad:ad,ctrl:arp(t2,t3,t)}\n i1:ins_1(9,min7)\n" in text
    assert "ctrl:arp(maj7)" in text and "G.ins_1(9,maj7)" in text and "run:arp(min,9)" in text
    assert "sw(4,7)" in text and Guarantee(D.CHORDS).holds(text)


def test_chords_refuse_orders_that_disagree():
    doc = read(D.CHORDS.replace("run:arp(9,3,7)", "run:arp(3,9,7)"))
    assert permutation(doc, "arp", {0, 1, 2}) is None


def test_single_writes_a_call_record_used_once_as_its_call():
    holds = Guarantee(D.CALLS).holds
    out, refused = inline(read(D.CALLS), "single", holds)
    text = printed(out)
    assert not refused and "play:[ph_1(D4,F4)]" in text and "y.ins_1(8,1600)" in text
    assert " p1:ph_1(C4,E4)\n" in text and "ph_1(x,y):" in text and holds(text)


def test_all_writes_templates_and_call_records_at_their_first_use():
    holds = Guarantee(D.CALLS).holds
    out, refused = inline(read(D.CALLS), "all", holds)
    text = printed(out)
    assert not refused and not out.defs and not out.mods and holds(text)
    assert re.search(r"p1=ph_1\(x,y\)=\[x\.i1=ins_1\(ad,p\)=\{", text)


def test_the_driver_runs_the_template_passes_then_inlines_them():
    result = optimise(D.PHRASES, inline="all")
    assert ("phrases", "phrase ph_1(x,y,z,u): 2") in result.kept
    assert not result.doc.defs and Guarantee(D.PHRASES).holds(result.text)


@pytest.mark.skipif(not REFERENCE.is_dir(), reason="no reference documents")
@pytest.mark.parametrize("path", sorted(REFERENCE.glob("*.yaml")) if REFERENCE.is_dir() else [])
def test_a_reference_document_optimises_to_its_grid(path):
    lines = path.read_text(encoding="utf-8").split("\n", 1)
    text = re.sub(r"\bslide\(", "pslide(", "unitracker:2.3\n" + lines[1])
    result = optimise(text, 3000)
    assert Guarantee(text, 3000).holds(result.text) and read(result.text) == result.doc
    assert not NAMES & {k.partition("(")[0] for k, _ in result.doc.mods}
