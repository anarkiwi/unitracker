"""The optimiser's lossy mode: code modulator calls fitted by expression templates."""

import numpy as np
import pytest
from tqdm import tqdm
from unitracker_grammar import frequency
from unitracker_grammar.document import NONE, Call, Code, printed, read
from unitracker_grammar.expr import canonical
from unitracker_player.player import play

import optimiser_docs as D

from unitracker import cli
from unitracker.optimiser import GuaranteeError, coupled, fit, lossy, optimise
from unitracker.optimiser.__main__ import main
from unitracker.optimiser.trace import FIRING, record, trace

EXACT = {cell: fit.Tolerance(0, 0) for cell in fit.BITS}
OTHER = [r for r in range(25) if r not in (0, 1, 2, 3)]


def firings(phi, out, v=None, n=60) -> fit.Firings:
    """Synthetic firings of one voice, each its own chain unless ``v`` chains them."""
    rec = np.zeros(len(phi), dtype=FIRING)
    rec["t"], rec["phi"], rec["out"], rec["N"], rec["n"] = np.arange(len(phi)), phi, out, n, n
    rec["v"] = np.full(len(phi), 30000) if v is None else v
    return fit.Firings(rec, "freq", 985248)


def test_trace_records_each_code_call_s_firings_and_plays_the_grid():
    frames, fired = trace(D.EXACT, None)
    assert np.array_equal(frames, play(D.EXACT))
    assert {(str(c), cell) for c, cell in fired} == {
        ("ramp(40)", "pw"),
        ("ramp(24)", "pw"),
        ("start()", "freq"),
        ("wob(8)", "freq"),
        ("wob(16)", "freq"),
    }
    rec = next(r for (c, _), r in fired.items() if str(c) == "ramp(40)")
    assert np.array_equal((rec["out"] - rec["v"]) % 4096, np.full(len(rec), 40))


def test_code_that_is_exactly_an_expression_is_fitted_exactly():
    result = lossy.simplify(D.EXACT, None, EXACT)
    doc = read(result.text)
    assert not any(isinstance(body, Code) for _, body in doc.mods)
    assert doc.header.state is None and not result.refused
    assert {r.name for r in result.kept} == {"ramp", "start", "wob"}
    assert dict((k, str(v)) for k, v in doc.mods)["stepsq(step,half)"] == canonical(
        "v + step - 2*step*(phi // half % 2)"
    )
    assert "b.freq:stepsq(8,2)" in result.text and "b.freq:stepsq(16,2)" in result.text
    assert "pw:ramp(40)" in result.text and "a.freq:note()" in result.text
    assert np.array_equal(play(result.text), play(D.EXACT))
    assert result.after < result.before and all(s == (0, 0, 0) for s in result.stats.values())


def test_a_looser_tolerance_trades_fidelity_for_a_simpler_form():
    result = lossy.simplify(D.EXACT, None)
    assert "a.freq:pitch()" in result.text and "b.freq" not in result.text
    assert "stepsq(" not in result.text
    p50, p95, top = result.stats["freq"]
    assert 0 < p95 <= fit.TOLERANCES["freq"].p95 and top <= fit.TOLERANCES["freq"].max
    assert p50 <= p95
    base, out = play(D.EXACT), play(result.text)
    assert np.array_equal(base[:, OTHER], out[:, OTHER])
    assert any("wob" in line and "error" in line for line in result.report())


def test_noise_is_refused_at_a_tight_tolerance_and_kept_at_a_loose_one():
    tight = lossy.simplify(D.NOISY, None, {"pw": fit.Tolerance(2, 3)})
    assert not tight.kept and not tight.refused and tight.after == tight.before
    loose = lossy.simplify(D.NOISY, None, {"pw": fit.Tolerance(4, 4)})
    assert [r.name for r in loose.kept] == ["jit"]
    assert 0 < loose.stats["pw"][2] <= 4
    keep = [r for r in range(25) if r not in (2, 3)]
    assert np.array_equal(play(D.NOISY)[:, keep], play(loose.text)[:, keep])


def test_a_group_no_plan_fits_is_unfit_and_its_own_replacements_are_refused():
    result = lossy.simplify(D.COUPLED, None)
    assert [r.name for r in result.refused] == ["put"]
    assert [r.name for r in result.kept] == ["ramp"] and set(result.stats) == {"pw", "cut"}
    assert "lossy refused: put" in result.report() and "put()" in result.text
    assert len(result.unfit) == 1 and result.unfit[0].startswith("get+put (freq): host a: ")
    assert "get() fits no form (nearest ramp p95" in result.unfit[0]
    assert any(line.startswith("lossy unfit: get+put") for line in result.report())
    keep = [r for r in range(25) if r not in (2, 3, 21, 22)]
    assert np.array_equal(play(D.COUPLED)[:, keep], play(result.text)[:, keep])


def test_the_lossy_mode_needs_ticks_for_a_looping_song():
    with pytest.raises(GuaranteeError):
        lossy.simplify(D.LOOPING_CODE, None)
    assert lossy.simplify(D.LOOPING_CODE, 40).after <= lossy.simplify(D.LOOPING_CODE, 40).before


def test_optimise_runs_the_lossy_mode_before_the_exact_passes():
    result = optimise(D.EXACT, lossy=EXACT)
    assert result.lossy is not None and result.before == result.lossy.before
    assert result.after <= result.lossy.after
    assert np.array_equal(play(result.text), play(D.EXACT))
    assert optimise(D.EXACT).lossy is None


def test_check_refuses_a_text_that_does_not_play_or_differs_elsewhere():
    reference = play(D.EXACT)
    assert not lossy.check(reference, "nonsense", None, {}, fit.TOLERANCES).exact
    assert not lossy.check(reference[:-1], D.EXACT, None, {}, fit.TOLERANCES).exact
    other = D.EXACT.replace("ad:9", "ad:8", 1)
    verdict = lossy.check(reference, other, None, {("freq", 0): np.array([0])}, fit.TOLERANCES)
    assert not verdict.exact and not verdict.holds(fit.TOLERANCES)
    assert lossy.check(reference, D.EXACT, None, {("pw", 0): np.array([0])}, EXACT).holds(EXACT)


def test_tolerances_parse_and_measure_cents_units_and_percent():
    assert fit.Tolerance.parse("10,25") == fit.Tolerance(10, 25)
    rel = fit.Tolerance.parse("2%,5%")
    assert rel == fit.Tolerance(2, 5, True) and str(rel) == "2%,5%"
    cents = fit.error("freq", fit.TOLERANCES["freq"], np.array([200, 0, 5]), np.array([100, 0, 0]))
    assert cents[0] == pytest.approx(1200) and cents[1] == 0 and np.isinf(cents[2])
    assert fit.error("pw", rel, np.array([110]), np.array([100]))[0] == pytest.approx(10)
    assert fit.error("cut", fit.TOLERANCES["cut"], np.array([7]), np.array([3]))[0] == 4
    assert fit.stats(np.array([])) == (0.0, 0.0, 0.0)


@pytest.mark.parametrize(
    "shape,make",
    [
        ("tri", lambda phi: 100 + 7 * np.abs((phi + 2) % 10 - 5)),
        ("lin", lambda phi: 300 - 9 * phi),
        ("up", lambda phi: np.minimum(200 + 5 * phi, 260)),
        ("down", lambda phi: np.maximum(200 - 5 * phi, 150)),
        (
            "loop",
            lambda phi: np.array([3, 1, 4, 1, 5, 9, 2])[
                np.minimum(phi, 3) + np.maximum(phi - 3, 0) % 4
            ],
        ),
        ("tab", lambda phi: np.array([0, 50, 20, 70, 90])[np.minimum(phi, 4)]),
    ],
)
def test_each_shape_fits_its_own_values_exactly(shape, make):
    phi = np.tile(np.arange(40), 3)
    fir = firings(phi, 8000 + make(phi))
    forms = [f for f in fit.fits(fir, EXACT["freq"]) if f.shape == shape and f.base == "0"]
    assert forms and np.array_equal(fir.predict(forms[0]), fir.out)


def test_a_delayed_square_on_the_input_carries_along_a_chain():
    phi = np.arange(30)
    delta = np.where(phi < 3, 0, np.where((phi + 1) // 4 % 2, -6, 6))
    out = 9000 + np.cumsum(delta)
    v = np.concatenate([[9000], out[:-1]])
    fir = firings(phi, out, v)
    sq = [f for f in fit.fits(fir, EXACT["freq"]) if f.key == ("sq", "v", True)]
    assert sq and sq[0].params["d"] == 3 and f"({fit.PHI} >= d)" in sq[0].text()
    assert not [f for f in fit.fits(fir, EXACT["freq"]) if f.key == ("keep", "v", False)]


def test_the_cli_reports_the_lossy_mode(tmp_path, capsys):
    source, out = tmp_path / "in.yaml", tmp_path / "out.yaml"
    source.write_text(D.EXACT, encoding="utf-8")
    assert (
        main([str(source), "-o", str(out), "--lossy", "--freq-tol", "0,0", "--pw-tol", "0,0"]) == 0
    )
    err = capsys.readouterr().err
    assert "lossy: wob (2 calls, freq) -> stepsq" in err and "lossy: " in err and "before:" in err
    assert np.array_equal(play(out.read_text(encoding="utf-8")), play(D.EXACT))


def test_unitracker_lift_runs_the_lossy_mode(metal_warrior, tmp_path, capsys):
    out = tmp_path / "out.yaml"
    argv = [str(metal_warrior), "-o", str(out), "--optimise", "--lossy", "--ticks", "300"]
    assert cli.lift_main(argv) == 0
    err = capsys.readouterr().err
    assert "lossy: " in err and " tokens" in err


def test_a_group_sharing_state_is_fitted_jointly_and_its_state_dropped():
    result = lossy.simplify(D.JOINT, None, EXACT)
    assert [(r.name, r.templates) for r in result.kept] == [("add+set", ("add", "set"))]
    doc = read(result.text)
    assert dict((k, str(v)) for k, v in doc.mods) == {"ramp(step)": "v + step"}
    assert doc.header.state is None and "b.freq" not in result.text
    assert "a.freq:ramp(8)" in result.text and "a.freq:ramp(16)" in result.text
    assert np.array_equal(play(result.text), play(D.JOINT)) and not result.unfit


def test_groups_join_code_templates_by_the_state_they_share():
    assert coupled.groups(read(D.JOINT)) == [coupled.Group("freq", ("add", "set"), ("s",))]
    assert not coupled.groups(read(D.EXACT))
    assert coupled.Group("freq", ("add", "set"), ("s",)).name == "add+set"


def test_a_group_another_slot_fires_inside_is_unfit_and_falls_back():
    result = lossy.simplify(D.INTERLEAVED, None, EXACT)
    assert result.unfit == ["add+set (freq): slot m fires between the group's slots"]
    assert sorted(r.name for r in result.kept) == ["add", "set"]
    assert "a.freq" not in result.text and "b.freq:ramp(8)" in result.text
    assert np.array_equal(play(result.text), play(D.INTERLEAVED))


def test_a_group_refused_tries_its_other_plans_then_its_templates_own():
    doc = read(D.JOINT)

    def ended(name, *calls, templates=("add", "set")):
        out = lossy.Replacement(name, "freq", {}, [], [], np.zeros(1), templates=templates)
        out.calls = dict.fromkeys(calls, NONE)
        return out

    first = ended("first", Call("set", (8,)), Call("set", (16,)), Call("add"))
    other = ended("other", Call("add"), Call("set", (8,)), Call("set", (16,)))
    own = ended("own", Call("add"), templates=("add",))

    def run(good):
        first.alternatives, first.fallback = [other], [own]
        proof = lossy.Proof(doc, lambda _, rs: lossy.Check(rs[-1] is good, {}), fit.TOLERANCES)
        with tqdm(disable=True) as meter:
            proof.prove([first], meter)
            proof.alternatives(meter)
        return proof

    proof = run(other)
    assert proof.kept == [other] and not proof.refused
    proof = run(own)
    assert proof.kept == [own] and proof.refused == [first]
    proof = run(None)
    assert not proof.kept and proof.refused == [first]


def test_added_templates_are_merged_and_renumbered_from_their_stem():
    before = lossy.Names.of(read(D.JOINT))
    text = (
        D.JOINT.replace("mod:\n", "mod:\n ramp3(step):v + step\n ramp4(step):v + step\n")
        .replace("a.freq:set(8)", "a.freq:ramp3(8)")
        .replace("a.freq:set(16)", "a.freq:ramp4(16)")
    )
    doc, names = lossy.renumbered(read(text), before)
    assert names == {"ramp3": "ramp", "ramp4": "ramp"}
    assert [k for k, _ in doc.mods] == ["ramp(step)", "set(d)", "add()"]
    assert "a.freq:ramp(8)" in printed(doc) and "a.freq:ramp(16)" in printed(doc)
    assert lossy.renumbered(read(D.JOINT), before) == (read(D.JOINT), {})


def test_a_replaced_template_s_name_is_free_again():
    before = lossy.Names.of(read(D.JOINT))
    text = D.JOINT.replace(D.JOINT_CODE, "mod:\n set2(step):v + step\n").replace(
        "b.freq:add()", "b.freq:none"
    )
    doc, names = lossy.renumbered(read(text.replace("set(", "set2(")), before)
    assert names == {"set2": "set"} and "a.freq:set(16)" in printed(doc)


@pytest.mark.parametrize(
    "key,name",
    [
        (("off", "v", 0), "ramp"),
        (("off", "0", 0), "level"),
        (("off", "F(N)", 0), "detune"),
        (("tri", "0", 0), "tri"),
        (("tri", "F(N)", 0), "vib"),
        (("sq", "F(N)", 0), "vibsq"),
        (("first", "F(N)", 0), "note"),
        (("keep", "F(N)", fit.HELD), "heldpitch"),
        (("tri", "F(n)", fit.LATE), "lateevvib"),
        (("tab", "F(N)", fit.HELD | fit.LATE), "heldlatepitchseq"),
        (("toward", "F(N)", 0), "glideto"),
    ],
)
def test_shapes_are_named_by_what_they_do(key, name):
    assert fit.stem(*key) == name


def test_parameters_are_named_by_role_clear_of_reserved_names():
    assert [fit.role("tri", "0", k) for k in "qsoh"] == ["lo", "step", "phase", "half"]
    assert fit.role("off", "0", "q") == "value" and fit.role("off", "v", "q") == "step"
    assert fit.role("first", "F(N)", "l") == "len" and fit.role("sq", "F(n)", "w") == "depth"
    assert fit.vacant("step", set()) == "step" and fit.vacant("step", {"step"}) == "inc"
    assert fit.vacant("step", {"step", "inc", "delta"}) == "step2"
    form = fit.Form("off", "v", 0, {"q": 32}, np.zeros(1))
    sig, body, _, calls = lossy.template([(Call("x"), form)], {"ramp"}, frozenset({"step"}))
    assert (sig, str(body), calls) == ("ramp2(inc)", "v + inc", {Call("x"): Call("ramp2", (32,))})


def test_a_shape_keeps_its_natural_parameters_but_neutral_ones():
    form = fit.Form("sq", "F(N)", 0, {"q": 0, "w": 6, "o": 0, "h": 4}, np.zeros(1))
    sig, body, _, calls = lossy.template([(Call("x"), form)], set())
    assert sig == "vibsq(depth,half)" and calls == {Call("x"): Call("vibsq", (6, 4))}
    assert str(body) == canonical("F(N) + depth*(phi // half % 2)")
    note = fit.Form("first", "F(N)", 0, {"q": 0, "l": 1}, np.zeros(1))
    assert lossy.template([(Call("x"), note)], set())[0] == "note()"
    assert fit.natural("loop", "l", [0]) and not fit.natural("tab", "z", [5])


def test_a_held_form_passes_its_input_until_it_acts():
    phi = np.tile(np.arange(10), 3)
    n = np.repeat([60, 64, 67], 10)
    notes = np.array([frequency(k, 985248) for k in n])
    out = np.where(phi < 1, np.concatenate([[1000], notes[:-1]]), notes)
    v = np.concatenate([[1000], out[:-1]])
    rec = np.zeros(len(phi), dtype=FIRING)
    rec["t"], rec["phi"], rec["out"], rec["N"], rec["n"], rec["v"] = (
        np.arange(30),
        phi,
        out,
        n,
        n,
        v,
    )
    fir = fit.Firings(rec, "freq", 985248)
    held = [f for f in fit.fits(fir, EXACT["freq"]) if f.key == ("keep", "F(N)", fit.HELD)]
    assert held and held[0].params == {"d": 1}
    assert held[0].text() == f"v + ({fit.PHI} >= d)*(F(N) + 0 - v)"
    assert np.array_equal(fir.predict(held[0]), fir.out)


def test_a_glide_moves_toward_the_note_by_its_step():
    out = np.minimum(8000 + 300 * np.arange(1, 13), frequency(64, 985248))
    v = np.concatenate([[8000], out[:-1]])
    fir = firings(np.arange(12), out, v, n=64)
    glide = [f for f in fit.fits(fir, EXACT["freq"]) if f.key == ("toward", "F(N)", 0)]
    assert glide and glide[0].params == {"s": 300}
    assert np.array_equal(fir.predict(glide[0]), fir.out)
    assert "nearest" not in fit.nearest(fir, EXACT["freq"])


def test_record_logs_every_slot_s_firing_in_order():
    tr = record(D.INTERLEAVED, 6)
    assert len(tr.frames) == 6 and set(tr.chain["slot"]) == {0, 1, 2}
    expr = tr.chain[tr.chain["slot"] == 1]
    assert (expr["key"] == -1).all() and np.array_equal(expr["out"] - expr["v"], np.ones(6))
    assert [str(c) for c, _ in tr.keys] == ["set(8)", "add()"]
