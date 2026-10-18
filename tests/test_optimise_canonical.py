"""The canonical passes: each shape rewritten where the grid proves it and left where it does not,
the composite proof's bisection, and the lift's ``canonical`` pass."""

import copy

import pytest
from conftest import gt_tune
from test_optimise import doc as optimise_doc
from test_optimise import ins_line

from unitracker import optimise
from unitracker.format import songio
from unitracker.lift.goattracker import GoatTrackerLift
from unitracker.modfit import grammar as G
from unitracker.optimise import driver, shattered
from unitracker.player import reach


def doc(phrase: str, *xs: str) -> str:
    """A three-voice GoatTracker 2 document of eight steps: voice 1 plays ``phrase``, with
    expressions ``x1``, ``x2``, ... ``xs``."""
    x = "".join(f"        x{k}: {t}\n" for k, t in enumerate(xs, 1))
    return optimise_doc(
        ins_line("i1", "ad: 9, ctrl: [{ctrl: 65, note: +0}, end]"), phrase, x and f"      x:\n{x}"
    )


def canonical(text: str):
    ir = songio.loads(text)
    return optimise.optimise(ir, list(optimise.CANONICAL), workers=1, progress=False)


def plays_alike(result) -> bool:
    return optimise.mismatch(optimise.Trace(result.source), songio.loads(result.text)) is None


SHATTERED = "[C4.i1*2, -!freq:x1, -!freq:x2, -!freq:x3, D4*3]"
FIRES = {
    "restitch": (doc(SHATTERED, "16*φ", "16*φ + 80", "16*φ + 160"), "-!freq:x1*3", "x2"),
    "slides": (
        doc("[C4.i1*2, -!freq:x1, -!freq:x2, -!freq:x1, -!freq:x2, D4*2]", "v + 16", "v - 16"),
        "-!freq:x1*4",
        "x2",
    ),
    "holds": (
        doc("[C4.i1*2, -!freq:x1, C4!freq:x2!legato, E4*4]", "16*min(φ, 1) - 16", "0"),
        "C4!freq:none!legato",
        "x2",
    ),
    "anchors": (
        doc(
            "[C4.i1!freq:x1*2, D4*2, -!freq:x2, -!freq:x3, -*2]",
            "16*φ",
            "16*min(φ, 4)",
            "16*φ + 80",
        ),
        "-!freq:x1*4",
        "x2",
    ),
}


@pytest.mark.parametrize("case", FIRES)
def test_a_canonical_pass_rewrites_the_shape_and_plays_the_grid(case):
    text, written, gone = FIRES[case]
    result = canonical(text)
    assert [f.pass_name for f in result.findings if f.kept] and result.tally[case]["proved"]
    assert written in result.text and f"{gone}:" not in result.text
    assert result.after < result.before and plays_alike(result)


def test_slides_fit_one_expression_in_the_firings():
    text = doc(
        "[C4.i1*2, -!freq:x1, -!freq:x2, -!freq:x3, -!freq:x4, D4*2]",
        *(f"v + {c}" for c in (16, 32, 48, 64)),
    )
    edit = shattered.slides(songio.loads(text))[0]
    expr = G.parse(edit[1][3]) - G.V
    fn = G.compiled(expr)
    assert [fn(0, p, 0) for p in range(20)] == [c for c in (16, 32, 48, 64) for _ in range(5)]


REFUSES = {
    "a group that does not continue the first": (
        "restitch",
        doc(SHATTERED, "16*φ", "16*φ + 81", "16*φ + 160"),
    ),
    "a 0 on a note the slide left": (
        "holds",
        doc("[C4.i1*2, -!freq:x1, D4!freq:x2!legato, E4*4]", "16*min(φ, 1) - 16", "0"),
    ),
}


@pytest.mark.parametrize("case", REFUSES)
def test_a_canonical_pass_keeps_nothing_the_grid_or_the_reader_refuses(case):
    name, text = REFUSES[case]
    result = canonical(text)
    assert not any(f.kept for f in result.findings) and result.text == songio.dumps(result.source)
    assert result.tally[name]["proved"] == 0


def test_a_hold_the_voice_already_plays_is_left_out():
    result = canonical(doc("[C4.i1*2, -, C4!freq:x1!legato, E4*4]", "0"))
    assert result.tally["holds"] == {"found": 1, "refused": 0, "proved": 1}
    assert "!freq" not in result.text and plays_alike(result)


def test_a_refused_composite_is_bisected_to_the_candidates_that_prove(monkeypatch):
    text = doc(
        "[C4.i1*2, -!freq:x1, C4!freq:x2!legato, -!freq:x1, D4!freq:x2!legato, E4*2]",
        "16*min(φ, 1) - 16",
        "0",
    )
    proofs = []
    real = driver.mismatch
    monkeypatch.setattr(driver, "mismatch", lambda ref, ir: proofs.append(1) or real(ref, ir))
    opt = optimise.Optimiser(songio.loads(text), ["holds"], workers=1)
    proofs.clear()
    assert opt.keep("holds") and len(proofs) == 3 and not opt.keep("holds")
    assert [f.edit for f in opt.findings.values()] == [("hold", ("p1", 2))]
    assert opt.tally["holds"]["proved"] == 1
    assert "C4!freq:none!legato" in opt.text and "D4!freq:x" in opt.text


STAIRS = "[C4.i1!cut:256*2, -!cut:512, -!cut:768, -!cut:1024, D4*3]"


def with_cut_expressions(ir: dict) -> dict:
    """``ir`` whose playroutine also takes ``!cut`` of an expression, as command 19."""
    out = copy.deepcopy(ir)
    out["playroutine"]["commands"][19] = {"name": "cut", "ref": "x"}
    return out


def test_a_staircase_is_one_expression_of_the_step_where_the_player_takes_one():
    ir = songio.loads(doc(STAIRS))
    edit = shattered.staircases(ir)[0]
    name, start, end, key, text = edit[1]
    assert (name, start, end, key) == ("p1", 0, 4, "cut")
    fn = G.compiled(G.parse(text))
    assert [fn(0, p, 0) for p in range(5)] == [256, 256, 512, 768, 1024]
    assert optimise.staircase(ir, *edit[1]) is None
    out = optimise.staircase(with_cut_expressions(ir), *edit[1])
    assert [e.get("command") for e in out["phrases"]["p1"][:4]] == [[19, 1], None, None, None]
    assert canonical(doc(STAIRS)).tally["staircases"] == {"found": 1, "refused": 1, "proved": 0}


def test_a_note_breaks_a_staircase():
    ir = songio.loads(doc("[C4.i1!cut:256*2, -!cut:512, D4!cut:768, -!cut:1024, E4*3]"))
    assert [e[1][1:3] for e in shattered.staircases(ir)] == [(0, 2), (2, 4)]


def test_agreement_is_over_the_domain_and_under_translation():
    assert shattered.agrees("(φ - 3) % 12", "(φ - 5) % 12", 10, None)
    assert not shattered.agrees("(φ - 3) % 12", "(φ - 5) % 12", 2, None)
    assert shattered.agrees("16*φ", "16*min(φ, 4)", 0, 5)
    assert not shattered.agrees("16*φ", "16*min(φ, 4)", 0, 6)
    assert shattered.agrees("v + F(N) - F(N)", "v", 0, None)
    assert not shattered.agrees("v + F(N + 1) - F(N)", "v", 0, None)
    assert not shattered.agrees("v // 0", "v", 0, 3)


def test_stitch_and_hold_refuse_what_they_cannot_write():
    ir = songio.loads(doc(SHATTERED, "16*φ", "16*φ + 80", "16*φ + 160"))
    assert optimise.stitch(ir, "p1", 2, 4, "16*φ") is None
    assert optimise.stitch(ir, "p1", 1, 2, "16*φ") is None
    pr = copy.deepcopy(ir["playroutine"])
    pr["commands"].pop(16)
    bare = {**ir, "playroutine": pr}
    assert optimise.hold(bare, "p1", 1) is None
    assert not shattered.holds(bare) and not shattered.chains(bare)


def test_canonical_is_cached_and_leaves_a_song_it_cannot_prove(tmp_path, monkeypatch):
    ir = songio.loads(FIRES["restitch"][0])
    out = optimise.canonical(ir, workers=1, cache=tmp_path)
    assert out is not ir and len(list(tmp_path.glob("canonical-*.pkl"))) == 1
    monkeypatch.setattr(driver, "optimise", lambda *_a, **_k: pytest.fail("not cached"))
    assert optimise.canonical(ir, workers=1, cache=tmp_path) == out

    def unrecurring(*_args, **_kw):
        raise reach.ReachabilityError("no recurrence")

    monkeypatch.setattr(driver, "optimise", unrecurring)
    assert optimise.canonical(ir, workers=1, cache=None) is ir
    plain = songio.loads(doc("[C4.i1*8]"))
    monkeypatch.undo()
    assert optimise.canonical(plain, workers=1, cache=None) is plain


def test_the_lift_ends_on_canonical_and_skips_it_without_expressions(monkeypatch):
    seen = []
    monkeypatch.setattr(GoatTrackerLift, "canonical", lambda _lift, song: seen.append(song) or song)
    dec, _ = gt_tune([(0x41, 0), (0xFF, 0)])
    assert GoatTrackerLift.passes()[-2:] == ("convert", "canonical")
    stages = list(GoatTrackerLift(dec).stages())
    assert [n for n, _ in stages][-2:] == ["convert", "canonical"] and seen == [stages[-2][1]]
    assert "canonical" not in dict(GoatTrackerLift(dec, expressions=False).stages())
    assert len(seen) == 1
