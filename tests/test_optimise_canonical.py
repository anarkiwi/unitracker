"""The canonical passes: each shape rewritten where the grid proves it and left where it does not,
the composite proof's bisection, and the lift's ``canonical`` pass."""

import copy

import pytest
from conftest import grid_diff_of, gt_tune
from test_optimise import CUT, FREQ, LEGATO, codes, ev, plays_alike, song

from unitracker import document, optimise
from unitracker.lift.goattracker import GoatTrackerLift
from unitracker.modfit import grammar as G
from unitracker.optimise import driver, shattered
from unitracker.player import reach


def fq(*refs) -> list:
    """A rest step playing ``!freq`` of each of ``refs`` in turn."""
    return [{"command": [FREQ, x]} for x in refs]


def held(note: str, ref: int) -> dict:
    """``note`` played legato with ``!freq`` of ``ref``."""
    return ev(note, commands=[[FREQ, ref], [LEGATO, 0]])


def cuts(*args) -> list:
    """A rest step playing ``!cut`` of each of ``args`` in turn."""
    return [{"command": [CUT, a]} for a in args]


C4 = ev("C4", 2, instrument=1)


def canonical(ir: dict):
    return optimise.optimise(ir, list(optimise.CANONICAL), workers=1, progress=False)


def alike(result, request) -> bool:
    """Whether the pass's result plays the source's grid; the test's failure carries the diff."""
    mismatch, verdict = plays_alike(result.source, result.ir, result.text)
    if not verdict.ok:
        grid_diff_of(request, verdict)
    return mismatch is None and verdict.ok


SHATTERED = [C4, *fq(1, 2, 3), ev("D4", 3)]
HOLD = ("16*min(φ, 1) - 16", "0")
WRITER = "the generic document plays a step-lasting !freq on the tick that fetches the next row"


def freqs(ir: dict) -> set:
    """The expressions the played ``!freq`` commands name, 0 for none."""
    return {arg for code, arg in codes(ir) if code == FREQ}


FIRES = {
    "restitch": (
        song(SHATTERED, exprs=("16*φ", "16*φ + 80", "16*φ + 160")),
        lambda ir: len(freqs(ir)) == 1,
    ),
    "slides": (
        song([C4, *fq(1, 2, 1, 2), ev("D4", 2)], exprs=("v + 16", "v - 16")),
        lambda ir: len(freqs(ir)) == 1,
    ),
    "holds": (
        song([C4, *fq(1), held("C4", 2), ev("E4", 4)], exprs=HOLD),
        lambda ir: freqs(ir) == {0, 1},
    ),
    "anchors": (
        song(
            [ev("C4", command=[FREQ, 1], instrument=1), *fq(1), ev("D4", 2), *fq(2, 3, 3, 3)],
            exprs=("16*φ", "16*min(φ, 4)", "16*φ + 80"),
        ),
        lambda ir: len(freqs(ir)) == 1,
    ),
}


@pytest.mark.parametrize(
    "case",
    [
        k if k == "holds" else pytest.param(k, marks=pytest.mark.xfail(strict=True, reason=WRITER))
        for k in FIRES
    ],
)
def test_a_canonical_pass_rewrites_the_shape_and_plays_the_grid(case, request):
    ir, rewritten = FIRES[case]
    result = canonical(ir)
    assert not rewritten(ir) and rewritten(result.ir)
    assert [f.pass_name for f in result.findings if f.kept] and result.tally[case]["proved"]
    assert result.after < result.before and alike(result, request)


def test_slides_fit_one_expression_in_the_firings():
    ir = song([C4, *fq(1, 2, 3, 4), ev("D4", 2)], exprs=tuple(f"v + {c}" for c in (16, 32, 48, 64)))
    edit = shattered.slides(ir)[0]
    expr = G.parse(edit[1][3]) - G.V
    fn = G.compiled(expr)
    assert [fn(0, p, 0) for p in range(20)] == [c for c in (16, 32, 48, 64) for _ in range(5)]


REFUSES = {
    "a group that does not continue the first": (
        "restitch",
        song(SHATTERED, exprs=("16*φ", "16*φ + 81", "16*φ + 160")),
    ),
    "a 0 on a note the slide left": (
        "holds",
        song([C4, *fq(1), held("D4", 2), ev("E4", 4)], exprs=HOLD),
    ),
}


@pytest.mark.parametrize("case", REFUSES)
def test_a_canonical_pass_keeps_nothing_the_grid_refuses(case):
    name, ir = REFUSES[case]
    result = canonical(ir)
    assert not any(f.kept for f in result.findings) and result.ir is ir
    assert result.text == document.write(ir) and result.tally[name]["proved"] == 0


def test_a_hold_the_voice_already_plays_is_left_out(request):
    result = canonical(song([C4, {}, held("C4", 1), ev("E4", 4)], exprs=("0",)))
    assert result.tally["holds"] == {"found": 1, "refused": 0, "proved": 1}
    assert freqs(result.source) == {1} and freqs(result.ir) == {0} and alike(result, request)


def test_a_refused_composite_is_bisected_to_the_candidates_that_prove(monkeypatch):
    ir = song([C4, *fq(1), held("C4", 2), *fq(1), held("D4", 2), ev("E4", 2)], exprs=HOLD)
    proofs = []
    real = driver.mismatch
    monkeypatch.setattr(driver, "mismatch", lambda ref, ir: proofs.append(1) or real(ref, ir))
    opt = optimise.Optimiser(ir, ["holds"], workers=1)
    proofs.clear()
    assert opt.keep("holds") and len(proofs) == 3 and not opt.keep("holds")
    assert [f.edit for f in opt.findings.values()] == [("hold", ("p1", 2))]
    assert opt.tally["holds"]["proved"] == 1
    held_ = [e["commands"][0][1] for e in opt.base["phrases"]["p1"] if "commands" in e]
    assert held_ == [0, 2]


STAIRS = [ev("C4", 2, command=[CUT, 32], instrument=1), *cuts(64, 96, 128), ev("D4", 3)]


def with_cut_expressions(ir: dict) -> dict:
    """``ir`` whose playroutine also takes ``!cut`` of an expression, as command 19."""
    out = copy.deepcopy(ir)
    out["playroutine"]["commands"][19] = {"name": "cut", "ref": "x"}
    return out


def test_a_staircase_is_one_expression_of_the_step_where_the_player_takes_one():
    ir = song(STAIRS)
    edit = shattered.staircases(ir)[0]
    name, start, end, key, text = edit[1]
    assert (name, start, end, key) == ("p1", 0, 4, "cut")
    fn = G.compiled(G.parse(text))
    assert [fn(0, p, 0) for p in range(5)] == [256, 256, 512, 768, 1024]
    assert optimise.staircase(ir, *edit[1]) is None
    out = optimise.staircase(with_cut_expressions(ir), *edit[1])
    assert [e.get("command") for e in out["phrases"]["p1"][:4]] == [[19, 1], None, None, None]
    assert canonical(ir).tally["staircases"] == {"found": 1, "refused": 1, "proved": 0}


def test_a_note_breaks_a_staircase():
    phrase = [ev("C4", 2, command=[CUT, 32], instrument=1), *cuts(64)]
    phrase += [ev("D4", command=[CUT, 96]), *cuts(128), ev("E4", 3)]
    assert [e[1][1:3] for e in shattered.staircases(song(phrase))] == [(0, 2), (2, 4)]


def test_agreement_is_over_the_domain_and_under_translation():
    assert shattered.agrees("(φ - 3) % 12", "(φ - 5) % 12", 10, None)
    assert not shattered.agrees("(φ - 3) % 12", "(φ - 5) % 12", 2, None)
    assert shattered.agrees("16*φ", "16*min(φ, 4)", 0, 5)
    assert not shattered.agrees("16*φ", "16*min(φ, 4)", 0, 6)
    assert shattered.agrees("v + F(N) - F(N)", "v", 0, None)
    assert not shattered.agrees("v + F(N + 1) - F(N)", "v", 0, None)
    assert not shattered.agrees("v // 0", "v", 0, 3)


def test_stitch_and_hold_refuse_what_they_cannot_write():
    ir = song(SHATTERED, exprs=("16*φ", "16*φ + 80", "16*φ + 160"))
    assert optimise.stitch(ir, "p1", 2, 4, "16*φ") is None
    assert optimise.stitch(ir, "p1", 1, 2, "16*φ") is None
    pr = copy.deepcopy(ir["playroutine"])
    pr["commands"].pop(16)
    bare = {**ir, "playroutine": pr}
    assert optimise.hold(bare, "p1", 1) is None
    assert not shattered.holds(bare) and not shattered.chains(bare)


def test_canonical_is_cached_and_leaves_a_song_it_cannot_prove_or_write(tmp_path, monkeypatch):
    ir = FIRES["holds"][0]
    out = optimise.canonical(ir, workers=1, cache=tmp_path)
    assert out is not ir and freqs(out) == {0, 1}
    assert len(list(tmp_path.glob("canonical-*.pkl"))) == 1
    monkeypatch.setattr(driver, "optimise", lambda *_a, **_k: pytest.fail("not cached"))
    assert optimise.canonical(ir, workers=1, cache=tmp_path) == out

    def refused(error):
        def raise_(*_args, **_kw):
            raise error

        return raise_

    monkeypatch.setattr(driver, "optimise", refused(reach.ReachabilityError("no recurrence")))
    assert optimise.canonical(ir, workers=1, cache=None) is ir
    monkeypatch.undo()
    monkeypatch.setattr(driver.document, "write", refused(document.GenericError("no form")))
    assert optimise.canonical(ir, workers=1, cache=None) is ir
    monkeypatch.undo()
    plain = song([ev("C4", 8, instrument=1)])
    assert optimise.canonical(plain, workers=1, cache=None) is plain


def test_the_lift_ends_on_canonical_and_skips_it_without_expressions(monkeypatch):
    seen = []
    monkeypatch.setattr(GoatTrackerLift, "canonical", lambda _lift, song: seen.append(song) or song)
    dec, _ = gt_tune([(0x41, 0), (0xFF, 0)])
    assert GoatTrackerLift.passes()[-3:] == ("convert", "canonical", "verify")
    stages = list(GoatTrackerLift(dec).stages())
    assert [n for n, _ in stages][-3:] == ["convert", "canonical", "verify"]
    assert seen == [stages[-3][1]]
    assert "canonical" not in dict(GoatTrackerLift(dec, expressions=False).stages())
    assert len(seen) == 1
