"""Generic accumulator factorisation: the targets proved, and each failure reason."""

import pytest

from unitracker.modfit import playroutine, resolve
from unitracker.modfit.compose import Merge, can_differ, composer
from unitracker.modfit.factor import Factorer, keyword_factors

TOY = {"voice": {"x": {"width": 8}, "t": {"width": 8}}}


@pytest.fixture(name="units", scope="module")
def units_fixture() -> dict:
    return {n: Factorer(Merge(playroutine(n))) for n in ("goattracker2", "defmon", "sidwizard")}


def unit(units, name: str, path: str, cell: str) -> list:
    fz = units[name]
    return fz.unit(resolve(fz.form.pr, path), cell)


def same(fz, text: str, src: str, gate=()) -> bool:
    f = fz.form
    return not can_differ(f, gate, f.expr(text, {}), f.expr(src, {}))


def rebuilt(fz, factor, cell: str, state: dict) -> int:
    """The rebuilt accumulator's value of ``cell`` from a concrete state."""
    f, w = fz.form, fz.form.width[cell]
    step = f.expr(factor.texts["step"], state)
    v = (state[cell] + step) & ((1 << w) - 1)
    env = {"v0": state[cell], "v": v, "s": step}
    hit = factor.hit != "-" and f.truth(f.expr(factor.hit, state, env))
    return f.expr(factor.respond, state, env) & ((1 << w) - 1) if hit else v


def test_goattracker_slide_and_table_steps(units):
    fz = units["goattracker2"]
    [slide] = unit(units, "goattracker2", "records.tickn.1.do", "freq")
    assert slide.proved and slide.hit == "-" and same(fz, slide.texts["step"], "spd(cmddata)")
    [pulse] = unit(units, "goattracker2", "record_types.pulsetab.each", "pulse")
    assert pulse.proved and same(
        fz, pulse.texts["step"], "row.r - 0x100 if row.r >= 0x80 else row.r"
    )
    [cut] = unit(units, "goattracker2", "record_types.filttab.each", "filtercutoff")
    assert cut.proved and cut.texts["step"] == "row.r"


def test_goattracker_porta_is_toward_as_clamp_and_reflect(units):
    load, toward = sorted(unit(units, "goattracker2", "procs.toneporta", "freq"), key=len_gate)
    assert load.proved and load.hit == "1" and load.kinds == ("reload",)
    assert toward.proved and set(toward.kinds) == {"hold", "clamp", "reflect"}
    assert same(units["goattracker2"], toward.texts["step"], "spd(arg)", toward.gate)


def len_gate(factor) -> str:
    return str(factor.gate)


def test_sidwizard_slides_and_glide_overshoot(units):
    comp = composer("sidwizard")
    for code in (1, 2):
        [(cell, slide)] = keyword_factors(comp, code, None)
        assert cell == "voff" and slide.proved and slide.hit == "-"
    [(_, glide)] = keyword_factors(comp, 3, None)
    assert glide.proved and set(glide.kinds) == {"hold", "clamp", "reflect"}
    fz = units["sidwizard"]
    [porta] = unit(units, "sidwizard", "procs.tick_porta", "voff")
    assert porta.proved and set(porta.kinds) == {"hold", "clamp", "reflect"}
    up = rebuilt(fz, porta, "voff", {"voff": 0xFFFF, "fml": 1, "fmh": 0})
    down = rebuilt(fz, porta, "voff", {"voff": 1, "fml": 1, "fmh": 0})
    assert (up, down) == (1, 0)


@pytest.mark.parametrize(
    "path, cell, field",
    [
        ("procs.tick_pw", "pwhi:pwlo", "pwtab[ppos].b"),
        ("procs.tick_filt", "vfhi:vflo:3", "flttab[fpos].b"),
    ],
)
def test_sidwizard_sweeps_on_fused_pairs(units, path, cell, field):
    fz = units["sidwizard"]
    groups = unit(units, "sidwizard", path, cell)
    assert all(g.proved for g in groups)
    [sweep] = [g for g in groups if g.hit == "-"]
    assert same(fz, sweep.texts["step"], f"s8({field})", sweep.gate)
    assert [g.kinds for g in groups if g.hit == "1"] == [("reload",)]


def test_defmon_slide_pulse_bounce_and_acid(units):
    [slide] = unit(units, "defmon", "procs.pitch_acc", "sacc")
    assert slide.proved and slide.hit == "-"
    pulse = unit(units, "defmon", "procs.pulse", "pw")
    assert len(pulse) == 2 and all(g.proved and g.kinds == ("bounce",) for g in pulse)
    assert all("(v0 >> 8)" in g.hit for g in pulse)
    acid = unit(units, "defmon", "procs.acid_step", "acc")
    assert len(acid) == 2 and not any(g.proved or g.kinds for g in acid)


@pytest.mark.parametrize(
    "actions, reason",
    [
        ([{"set": {"x": "x * 2"}}], "step reads x"),
        (
            [{"set": {"t": "t + 1"}}, {"accumulate": "x", "step": 1, "max": "t"}],
            "hit reads non-bound state",
        ),
        (
            [
                {"set": {"t": "t + 1"}},
                {
                    "accumulate": "x",
                    "step": 1,
                    "max": 100,
                    "clamp": False,
                    "on_bound": [{"set": {"x": "t"}}],
                },
            ],
            "respond outside (v0, v, bounds)",
        ),
    ],
)
def test_failure_reasons(actions, reason):
    [factor] = Factorer(Merge(TOY)).unit(actions, "x")
    assert factor.status == reason and not factor.proved


def test_wrong_lambdas_give_a_witness():
    fz = Factorer(Merge(TOY))
    paths = fz.paths([{"accumulate": "x", "step": 1}], "x")
    status = fz.prove(paths, "x", (), (), {"step": "2", "hit": "-", "respond": "-"})
    assert status.startswith("witness x=")
    assert fz.prove(paths, "x", (), (), {"step": "1", "hit": "-", "respond": "-"}) == "proved"


def test_lambda_names_are_free():
    with pytest.raises(ValueError):
        Factorer(Merge({"voice": {"v": {"width": 8}}}))
