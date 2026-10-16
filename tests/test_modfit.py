"""Modulation fitting: normaliser identities, byte pairs, proved fits, rejections and orbits."""

import pytest
import sympy

from unitracker.modfit import playroutine, resolve, specs
from unitracker.modfit.arith import Arith
from unitracker.modfit.cli import main
from unitracker.modfit.fit import Fitter, model_text, src
from unitracker.modfit.form import Form, atoms
from unitracker.modfit.orbit import deltas, orbits, pingpong, report, start

TOY = {"voice": {"x": {"width": 8}, "a": {"width": 8}, "b": {"width": 8}, "e": None}}


@pytest.fixture(name="fitters", scope="session")
def fitters_fixture() -> dict:
    """One fitter per library playroutine, its form caches shared across tests."""
    return {name: Fitter(playroutine(name)) for name in ("goattracker2", "defmon", "sidwizard")}


@pytest.fixture(name="toy", scope="session")
def toy_fixture() -> Form:
    """A form over three byte cells and one scratch cell."""
    return Form(TOY)


def fit(fitters, name: str, path: str, cell: str) -> list:
    return fitters[name].fit(resolve(playroutine(name), path), cell)


def test_covered_mask_dropped(toy):
    assert toy.band("x", 255) == "x"
    assert toy.band(toy.add("x", 1), 255) != toy.add("x", 1)
    assert toy.band("x", 127) == ("and", "x", 127)


def test_wrap_vanishes_under_modulus(toy):
    e = toy.add("e", 3)
    assert toy.modular(toy.scale(256, toy.band(e, 255)), 1 << 16) == toy.modular(
        toy.scale(256, e), 1 << 16
    )
    assert toy.modular(toy.band(e, 255), 1 << 16) != toy.modular(e, 1 << 16)


def test_carry_removed_under_guard(toy):
    wrapped = toy.band(toy.add("x", 1), 255)
    assert toy.unwrap((toy.lt("x", 255),), wrapped, 1 << 16) == (toy.add("x", 1), True)
    assert toy.unwrap((), wrapped, 1 << 16) == (wrapped, False)


def test_split_identity(toy):
    split = toy.add(toy.scale(256, toy.band(toy.shr("e", 8), 255)), toy.band("e", 255))
    assert toy.modular(split, 1 << 16) == "e"


def test_parity_tests_are_complements(fitters):
    f = fitters["goattracker2"].form
    odd = f.eq(f.band(f.sub(1, "vibtime"), 1), 0)
    even = f.eq(f.band("vibtime", 1), 0)
    assert odd != f.negate(even)
    assert f.relation(odd, even) == "complement"
    assert f.relation(even, even) == "same"
    assert f.relation(even, f.lt("vibtime", 9)) is None


def test_comparisons_canonical(toy):
    assert toy.lt("x", 0) is False and toy.ge("x", 0) is True
    assert toy.eq("x", 300) is False
    assert toy.eq(toy.sub(3, "e"), 0) == toy.eq("e", 3)
    assert toy.negate(toy.lt("e", 4)) == toy.ge("e", 4)
    assert toy.implies((toy.lt("x", 4),), toy.lt("x", 9))
    assert not toy.implies((toy.lt("x", 9),), toy.lt("x", 4))


def test_byte_pairs():
    assert {("pwlo", "pwhi"), ("fml", "fmh"), ("zpl", "zph")} <= Form(
        playroutine("sidwizard")
    ).pairs
    assert ("flo", "fhi") in Form(playroutine("defmon")).pairs
    assert not Form(playroutine("goattracker2")).pairs


def test_sweep_fuses_to_one_16bit_update(fitters):
    f = fitters["sidwizard"].form
    b = f.field(f.subscript("pwtab", "ppos"), "b")
    negative = f.ge(b, 128)
    sweeps = {}
    for conds, upd in f.updates(resolve(f.pr, "procs.tick_pw")):
        if "pwhi:pwlo" in atoms(upd.get("pwhi:pwlo")):
            assert "pwlo" not in upd and "pwhi" not in upd
            sweeps.setdefault(f.implies(conds, negative), set()).add(upd["pwhi:pwlo"])
    assert len(sweeps[True]) == len(sweeps[False]) == 1
    joint = f.ite(negative, *sweeps[True], *sweeps[False])
    assert joint == f.band(f.add("pwhi:pwlo", b, f.scale(-256, f.iv(negative))), 0xFFFF)


def test_arith_independent_of_test_order():
    def nest(outer: str, inner: str, values) -> list:
        then = {
            "if": inner,
            "then": [{"set": {"x": values[0]}}],
            "else": [{"set": {"x": values[1]}}],
        }
        other = {
            "if": inner,
            "then": [{"set": {"x": values[2]}}],
            "else": [{"set": {"x": values[3]}}],
        }
        return [{"if": outer, "then": [then], "else": [other]}]

    first = Arith(TOY).operator(nest("a < 5", "b < 3", (1, 2, 3, 7)))["x"]
    second = Arith(TOY).operator(nest("b < 3", "a < 5", (1, 3, 2, 7)))["x"]
    assert sympy.expand(first - second) == 0
    assert any(len(m.free_symbols) == 2 for m in sympy.Add.make_args(first))


def test_pulse_table_is_signed_byte_step(fitters):
    [(guard, found)] = fit(fitters, "goattracker2", "record_types.pulsetab.each", "pulse")
    f = fitters["goattracker2"].form
    r = f.field("row", "r")
    assert guard == () and set(found) == {"accumulate", "step"}
    assert f.expr(found["step"], {}) == f.add(r, f.scale(-256, f.iv(f.ge(r, 128))))


def test_tick_command_is_accumulate(fitters):
    [(guard, found)] = fit(fitters, "goattracker2", "records.tickn.1.do", "freq")
    assert guard == () and set(found) == {"accumulate", "step"}


def test_toneporta_is_toward_or_set(fitters):
    f = fitters["goattracker2"].form
    found = dict(fit(fitters, "goattracker2", "procs.toneporta", "freq"))
    target = f.subscript("ftab", f.band("note", 127))
    assert found[(f.eq("arg", 0),)] == {"set": {"freq": src(target)}}
    porta = found[(f.ne("arg", 0),)]
    assert porta["accumulate"] == "freq" and f.expr(porta["toward"], {}) == target


def test_defmon_pitch_slide_is_accumulate(fitters):
    results = fit(fitters, "defmon", "procs.pitch", "sacc")
    assert results and all(set(found) == {"accumulate", "step"} for _, found in results)


def test_defmon_pulse_stays_rejected(fitters):
    results = fit(fitters, "defmon", "procs.pulse", "pw")
    assert len(results) == 2 and all(found is None for _, found in results)


def test_sidwizard_porta_has_witness(fitters):
    fitter = fitters["sidwizard"]
    actions = resolve(fitter.form.pr, "procs.tick_porta")
    cand = {"accumulate": "voff", "step": "((fmh << 8) | fml) + 1", "toward": "0"}
    assert not fitter.agrees(actions, (), "voff", cand)
    model = fitter.witness(actions, (), "voff", cand)
    assert "fmh:fml=" in model_text(model) and "voff=" in model_text(model)


@pytest.mark.parametrize(
    "steps, expected",
    [
        ([1, 1, -1, -1, -1, -1, 1, 1, 1, 1], (-2, 2, 1)),
        ([1, -1, -1, -1, 1, 1, 1, -1], (-2, 1, 1)),
        ([-1, -1, 1, 1, -1, -1], (-2, 0, -1)),
        ([-1, 1, 1, -1, 1, 1], None),
        ([1] * 10, None),
        ([2, -1], None),
        ([0, 0], None),
        ([], None),
    ],
)
def test_pingpong(steps, expected):
    assert pingpong(steps) == expected


def orbit(name: str, period: int, ticks: int = 600):
    spec = specs()["orbits"][name]
    state, data = start(spec, period)
    pr = playroutine(name)
    return deltas(Form(pr, data), resolve(pr, spec["path"]), state, spec["cell"], ticks)


@pytest.mark.parametrize("p", [0, 1, 7, 64, 125])
def test_goattracker_vibrato_closed_form(p):
    assert pingpong(orbit("goattracker2", p)) == (-((p + 1) // 2 + 1), p // 2 + 1, 1)


@pytest.mark.parametrize("p", [126, 127])
def test_goattracker_vibrato_slides(p):
    steps = orbit("goattracker2", p)
    assert set(steps.tolist()) == {1} and pingpong(steps) is None


@pytest.mark.parametrize("p", [2, 4, 10, 64])
def test_sidwizard_even_period_pingpong(p):
    assert pingpong(orbit("sidwizard", p)) == (-p // 2, 0, -1)


def test_sidwizard_odd_period_drifts():
    steps = orbit("sidwizard", 3, 60)
    assert steps[:3].tolist() == [-1, 1, 1] and pingpong(steps) is None


def test_orbit_report_lists_failures():
    spec = {**specs()["orbits"]["goattracker2"], "periods": [125, 126]}
    assert orbits("goattracker2", spec, 300) == {125: (-64, 63, 1), 126: None}
    lines = report("goattracker2", spec, 300)
    assert lines[1].endswith("first step +1") and lines[-1].endswith("fails: [126]")


def test_symbolic_state_is_not_concrete():
    pr = playroutine("goattracker2")
    with pytest.raises(ValueError):
        deltas(Form(pr), resolve(pr, "procs.vibrato"), {"freq": 0}, "freq", 1)


def test_unsupported_input(toy):
    with pytest.raises(ValueError):
        toy.paths([{"play": "x"}])
    with pytest.raises(ValueError):
        toy.expr("[x]", {})
    with pytest.raises(ValueError):
        src(("shl", "x", "a"))


def test_cli_fit(capsys):
    main(["fit", "defmon:procs.pulse:pw"])
    out = capsys.readouterr().out
    assert out.count("no single accumulator") == 2 and "against {" in out


def test_cli_poly(tmp_path):
    out = tmp_path / "poly.txt"
    main(["--out", str(out), "poly", "goattracker2:record_types.pulsetab.each"])
    assert out.read_text().startswith("== goattracker2:record_types.pulsetab.each\n  pulse' =")


def test_cli_orbit(capsys):
    main(["orbit", "goattracker2", "--ticks", "300"])
    assert "ping-pong for 126 of 128; fails: [126, 127]" in capsys.readouterr().out
