"""The optimiser's lossy mode: code modulator calls fitted by expression templates."""

import numpy as np
import pytest
from unitracker_grammar.document import Code, read
from unitracker_grammar.expr import canonical
from unitracker_player.player import play

import optimiser_docs as D

from unitracker import cli
from unitracker.optimiser import GuaranteeError, fit, lossy, optimise
from unitracker.optimiser.__main__ import main
from unitracker.optimiser.trace import FIRING, trace

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
    assert {r.name for r in result.kept} == {"slide", "start", "wob"}
    assert dict((k, str(v)) for k, v in doc.mods)["sq(q)"] == canonical(
        "v + q - 2*q*(phi // 2 % 2)"
    )
    assert "b.freq:sq(8)" in result.text and "b.freq:sq(16)" in result.text
    assert np.array_equal(play(result.text), play(D.EXACT))
    assert result.after < result.before and all(s == (0, 0, 0) for s in result.stats.values())


def test_a_looser_tolerance_trades_fidelity_for_a_simpler_form():
    result = lossy.simplify(D.EXACT, None)
    assert "b.freq:keep()" in result.text and "sq(" not in result.text
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


def test_a_replacement_that_breaks_code_reading_its_state_is_refused():
    result = lossy.simplify(D.COUPLED, None)
    assert [r.name for r in result.refused] == ["put"]
    assert [r.name for r in result.kept] == ["slide"] and set(result.stats) == {"pw", "cut"}
    assert "lossy refused: put" in result.report() and "put()" in result.text
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
    assert "lossy: wob (2 calls, freq) -> sq" in err and "lossy: " in err and "before:" in err
    assert np.array_equal(play(out.read_text(encoding="utf-8")), play(D.EXACT))


def test_unitracker_lift_runs_the_lossy_mode(metal_warrior, tmp_path, capsys):
    out = tmp_path / "out.yaml"
    argv = [str(metal_warrior), "-o", str(out), "--optimise", "--lossy", "--ticks", "300"]
    assert cli.lift_main(argv) == 0
    err = capsys.readouterr().err
    assert "lossy: " in err and " tokens" in err
