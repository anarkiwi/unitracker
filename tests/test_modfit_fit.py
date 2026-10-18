"""Modulation fitting: normaliser identities, proved fits and witnesses, orbits, timers, bounce
orbits, table programs and the cli subcommands."""

import itertools

import pytest
import sympy

from unitracker.modfit import orbit as orbit_mod
from unitracker.modfit import playroutine, resolve, specs
from unitracker.modfit.arith import Arith
from unitracker.modfit.bounce import Bounce, iv_values, phase_text, phase_value
from unitracker.modfit.cli import check_lines, main
from unitracker.modfit.fit import Fitter, model_text, src
from unitracker.modfit.form import Form, atoms
from unitracker.modfit.orbit import deltas, orbits, pingpong, reflector, report
from unitracker.modfit.sweeps import Run, Stepper, entries, simulate, song_lines, verdict
from unitracker.modfit.timer import Timers, affine, closed, grouped, signature, vibrato_signatures
from unitracker.player.vm import CLAMPS, SongError, _acc

TOY = {"voice": {"x": {"width": 8}, "a": {"width": 8}, "b": {"width": 8}, "e": None}}


@pytest.fixture(name="fitters", scope="module")
def fitters_fixture() -> dict:
    """One fitter per library playroutine, its form caches shared across tests."""
    return {name: Fitter(playroutine(name)) for name in ("goattracker2", "defmon", "sidwizard")}


@pytest.fixture(name="toy", scope="module")
def toy_fixture() -> Form:
    """A form over three byte cells and one scratch cell."""
    return Form(TOY)


def fit(fitters, name: str, path: str, cell: str) -> list:
    return fitters[name].fit(resolve(playroutine(name), path), cell)


def test_normaliser_identities(toy):
    x1, e3 = toy.add("x", 1), toy.add("e", 3)
    assert toy.band("x", 255) == "x" and toy.band(x1, 255) != x1
    assert toy.band("x", 127) == ("and", "x", 127)
    mod = 1 << 16
    assert toy.modular(toy.scale(256, toy.band(e3, 255)), mod) == toy.modular(
        toy.scale(256, e3), mod
    )
    assert toy.modular(toy.band(e3, 255), mod) != toy.modular(e3, mod)
    wrapped = toy.band(x1, 255)
    assert toy.unwrap((toy.lt("x", 255),), wrapped, mod) == (x1, True)
    assert toy.unwrap((), wrapped, mod) == (wrapped, False)
    split = toy.add(toy.scale(256, toy.band(toy.shr("e", 8), 255)), toy.band("e", 255))
    assert toy.modular(split, mod) == "e"
    assert toy.lt("x", 0) is False and toy.ge("x", 0) is True and toy.eq("x", 300) is False
    assert toy.eq(toy.sub(3, "e"), 0) == toy.eq("e", 3)
    assert toy.negate(toy.lt("e", 4)) == toy.ge("e", 4)
    assert toy.implies((toy.lt("x", 4),), toy.lt("x", 9))
    assert not toy.implies((toy.lt("x", 9),), toy.lt("x", 4))


def test_parity_tests_are_complements(fitters):
    f = fitters["goattracker2"].form
    odd = f.eq(f.band(f.sub(1, "vibtime"), 1), 0)
    even = f.eq(f.band("vibtime", 1), 0)
    assert odd != f.negate(even)
    assert f.relation(odd, even) == "complement"
    assert f.relation(even, even) == "same"
    assert f.relation(even, f.lt("vibtime", 9)) is None


PAIRS = {
    "sidwizard": (
        {("fml", "fmh", 8), ("pwlo", "pwhi", 8), ("pwlo", "sph", 8), ("sfl", "sfh", 8)}
        | {("spl", "pwhi", 8), ("spl", "sph", 8), ("zpl", "zph", 8)}
        | {("vflo", "vfhi", 3), ("fcutlo", "fcuthi", 3)},
        {3, 8},
    ),
    "defmon": ({("flo", "fhi", 8)}, {8}),
    "goattracker2": (set(), set()),
}


def test_byte_and_shifted_pairs():
    for name, (expected, shifts) in PAIRS.items():
        form = Form(playroutine(name))
        assert expected <= form.pairs and {k for _, _, k in form.pairs} == shifts, name
        assert len({(lo, hi) for lo, hi, _ in form.pairs}) == len(form.pairs), name
    assert Form(playroutine("sidwizard")).width["vfhi:vflo:3"] == 11


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
        arms = [
            {"if": inner, "then": [{"set": {"x": t}}], "else": [{"set": {"x": e}}]}
            for t, e in (values[:2], values[2:])
        ]
        return [{"if": outer, "then": [arms[0]], "else": [arms[1]]}]

    first = Arith(TOY).operator(nest("a < 5", "b < 3", (1, 2, 3, 7)))["x"]
    second = Arith(TOY).operator(nest("b < 3", "a < 5", (1, 3, 2, 7)))["x"]
    assert sympy.expand(first - second) == 0
    assert any(len(m.free_symbols) == 2 for m in sympy.Add.make_args(first))


@pytest.mark.parametrize(
    "name, path, cell",
    [
        ("goattracker2", "record_types.pulsetab.each", "pulse"),
        ("goattracker2", "records.tickn.1.do", "freq"),
        ("defmon", "procs.pitch", "sacc"),
    ],
)
def test_slides_and_table_steps_are_accumulators(fitters, name, path, cell):
    results = fit(fitters, name, path, cell)
    assert results and all(set(found) == {"accumulate", "step"} for _, found in results)
    if cell == "pulse":
        f = fitters[name].form
        r = f.field("row", "r")
        [(guard, found)] = results
        assert guard == () and f.expr(found["step"], {}) == f.add(
            r, f.scale(-256, f.iv(f.ge(r, 128)))
        )


def test_sidwizard_porta_signed_toward_overshoots_by_one(fitters):
    fitter = fitters["sidwizard"]
    f = fitter.form
    actions = resolve(f.pr, "procs.tick_porta")
    unsigned = {"accumulate": "voff", "step": "((fmh << 8) | fml) + 1", "toward": "0"}
    assert not fitter.agrees(actions, (), "voff", unsigned)
    text = model_text(fitter.witness(actions, (), "voff", unsigned))
    assert "fmh:fml=" in text and "voff=" in text
    check = specs()["checks"][0]
    cand = check["candidate"]
    depth = f.expr("(fmh << 8) | fml", {})
    small = f.lt(depth, 1 << 15)
    meets = f.eq(f.add(f.signed("voff")("voff"), depth), 0)
    assert not fitter.agrees(actions, (), "voff", cand)
    assert not fitter.agrees(actions, (f.negate(small),), "voff", cand)
    assert not fitter.agrees(actions, (small, meets), "voff", cand)
    assert fitter.agrees(actions, (small, f.negate(meets)), "voff", cand)
    state = {"voff": 0x10000 - 5, "fmh": 0, "fml": 5}
    assert [st["voff"] for _, st in f.paths(actions, (), state)] == [1]
    assert [st["voff"] for _, st in f.paths([cand], (), state)] == [0]
    lines = check_lines(*check.values())
    assert lines[1].endswith(": refuted") and lines[2].startswith("      witness fmh:fml=")


PINGPONG = [
    ([1, 1, -1, -1, -1, -1, 1, 1, 1, 1], (-2, 2, 1)),
    ([1, -1, -1, -1, 1, 1, 1, -1], (-2, 1, 1)),
    ([-1, -1, 1, 1, -1, -1], (-2, 0, -1)),
    ([-1, 1, 1, -1, 1, 1], None),
    ([1] * 10, None),
    ([2, -1], None),
    ([0, 0], None),
    ([], None),
]


def test_pingpong():
    assert [pingpong(steps) for steps, _ in PINGPONG] == [expected for _, expected in PINGPONG]


def periods(name: str, first: int, last: int) -> dict:
    return {**specs()["orbits"][name], "periods": [first, last]}


def test_vibrato_orbits_are_one_reflect_accumulator_or_none():
    gt = {**orbits("goattracker2", periods("goattracker2", 0, 1), 60)}
    gt |= orbits("goattracker2", periods("goattracker2", 124, 127), 300)
    for p in (0, 1, 124, 125):
        lo, hi, first, acc = gt[p]
        assert (lo, hi, first) == (-((p + 1) // 2 + 1), p // 2 + 1, 1)
        assert (acc["min"], acc["max"], acc["clamp"]) == (20000 + lo, 20000 + hi, "reflect")
        assert "signed" not in acc
    sw = orbits("sidwizard", periods("sidwizard", 1, 4), 60)
    for p in (2, 4):
        lo, hi, first, acc = sw[p]
        assert (lo, hi, first) == (-p // 2, 0, -1)
        assert (acc["min"], acc["max"], acc["signed"]) == (-p // 2, 0, True)
    assert gt[126] is gt[127] is sw[1] is sw[3] is None


def test_reflector_takes_a_fresh_direction_cell():
    pr = {"voice": {"vd": {"width": 1}, "x": {"width": 8}}}
    rpr, acc, state = reflector(pr, "x", 10, (-1, 2, 1))
    assert state == {"x": 10, "vd_": 0} and rpr["voice"]["vd_"] == {"width": 1}
    steps = deltas(Form(rpr), [acc], state, "x", 12)
    assert pingpong(steps) == (-1, 2, 1)
    assert steps[:6].tolist() == [1, 1, -1, -1, -1, 1]


def test_wrong_bounds_are_rejected_by_the_reflect_run(monkeypatch):
    spec = periods("goattracker2", 4, 4)
    widen = pingpong
    monkeypatch.setattr(orbit_mod, "pingpong", lambda steps: (widen(steps)[0], 9, 1))
    assert orbits("goattracker2", spec, 100) == {4: (-3, 9, 1, None)}
    assert report("goattracker2", spec, 100)[1].endswith("first step +1, reflect differs")


def test_unsupported_input(toy):
    pr = playroutine("goattracker2")
    with pytest.raises(ValueError):
        deltas(Form(pr), resolve(pr, "procs.vibrato"), {"freq": 0}, "freq", 1)
    with pytest.raises(ValueError):
        toy.paths([{"play": "x"}])
    with pytest.raises(ValueError):
        toy.expr("[x]", {})
    with pytest.raises(ValueError):
        src(("shl", "x", "a"))


BOUNDS = (-129, -1, 0, 5, 127, 128, 255)
STEPS = (-128, -1, 0, 1, 3, 127, 128)
KINDS = [
    *({"min": b} for b in BOUNDS),
    *({"max": b} for b in BOUNDS),
    *({"min": a, "max": b} for a in BOUNDS for b in BOUNDS),
]


def edges(step: int, *bounds) -> set:
    """Start bytes within one of each value where ``_acc``'s case, wrap or sign can change."""
    marks = {0, 128, *(b for b in bounds if b is not None)}
    return {(m + d - s) & 255 for m in marks for d in (-1, 0, 1) for s in (0, step, -step)}


@pytest.mark.parametrize("clamp", [False, True, "reflect"])
def test_form_accumulate_is_vm_acc_on_bytes(clamp):
    form = Form({"voice": {"x": {"width": 8}, "h": {"width": 1}}})
    on_bound = [{"set": {"h": 1}}]
    towards = [{"toward": b} for b in BOUNDS] if clamp != "reflect" else []
    for bounds, step, signed in itertools.product(KINDS + towards, STEPS, (False, True)):
        acc = {"accumulate": "x", "step": step, **bounds, "clamp": clamp, "signed": signed}
        lo, hi, toward = (bounds.get(k) for k in ("min", "max", "toward"))
        for v0 in edges(step, lo, hi, toward):
            [(_, st)] = form.paths([{**acc, "on_bound": on_bound}], (), {"x": v0, "h": 0})
            expected = _acc(v0, step, 255, lo, hi, toward, CLAMPS[clamp], signed)
            assert (st["x"], st["h"] == 1) == expected, (acc, v0)
    if clamp == "reflect":
        with pytest.raises(SongError):
            form.paths([{"accumulate": "x", "step": 1, "toward": 0, "clamp": clamp}], (), {"x": 0})


@pytest.fixture(name="timers", scope="module")
def timers_fixture() -> dict:
    return {name: Timers(name) for name in ("goattracker2", "sidwizard")}


def test_affine_fits_floor_division_exactly():
    xs = list(range(126))
    assert affine(xs, [x // 2 + 1 for x in xs]) == (1, 2, 2)
    assert affine(xs, [-((x + 1) // 2 + 1) for x in xs]) == (-1, -2, 2)
    assert affine([0, 1, 2, 3], [0, 1, 4, 9]) is None
    assert closed({x: 3 * x - 1 for x in range(10)}) == "3*p - 1"
    assert closed({x: x + 1 if x < 8 else 1 for x in range(16)}) == "[0..7: p + 1] [8..15: 1]"
    assert closed({0: 5, 1: 7}) == "0:5, 1:7"


SIGNATURES = [
    ([True], 0, ("constant", (1,))),
    ([False, False, True], 2, ("delay", (2,))),
    ([True, True, True, False], 3, ("duration", (3,))),
    ([True, True, False, False], 0, ("period", (4,))),
    ([True, False, False], 0, ("drift", (1, 2))),
    ([False, True, False], None, ("drift", (1, 1))),
]


def test_signature_kinds():
    assert [signature(b, loop) for b, loop, _ in SIGNATURES] == [e for _, _, e in SIGNATURES]


def test_goattracker_timers(timers):
    gt = timers["goattracker2"]
    ptime = gt.timer("ptime")
    assert ptime.step == -1 and ptime.resets == [0] and ptime.lists == ["procs.pulse_exec"]
    tick = gt.timer("tick")
    assert tick.selectors == ["tempo"] and "alternating on tempo" in tick.text()
    vibtime = gt.timer("vibtime")
    assert vibtime.step == 2 and vibtime.flips
    assert gt.timer("state") is None and gt.timer("tempo") is None
    orbit = gt.orbit(ptime, "procs.pulse_exec")
    assert closed({p: orbit.duration(p) for p in orbit.domain()}) == "p + 1"


def test_sidwizard_row_duration_and_delay_closed_forms(timers):
    sw = timers["sidwizard"]
    orbit = sw.orbit(sw.timer("pwcnt"), "procs.tick_pw")
    assert orbit.bound == ("fld", "a", ("rec", "pwtab", "ppos"))
    assert orbit.domain() == range(256)
    ends = (0, 1, 126, 127, 128, 129, 255)
    assert {p: orbit.duration(p) for p in ends} == {p: p + 1 if p < 128 else 1 for p in ends}
    vdc = sw.timer("vdc")
    orbit = sw.orbit(vdc, "procs.tick_vibrato")
    [test] = vdc.tests
    sigs = {p: signature(*orbit.truths(test, p)) for p in orbit.domain()}
    assert list(grouped(sigs)) == [("duration", "0..127", ["p + 1"])]
    assert orbit.duration(5) == 7


def test_vibrato_orbits_reproduce_the_reflect_bounds():
    gt = periods("goattracker2", 0, 9)
    assert grouped(vibrato_signatures("goattracker2", gt, 200)) == [
        ("period", "0..9", ["(-p - 2)//2", "(p + 2)//2"])
    ]
    sw = periods("sidwizard", 2, 13)
    kinds = {k: (d, f) for k, d, f in grouped(vibrato_signatures("sidwizard", sw, 200))}
    assert kinds["period"] == ("2,4,6,8,10,12", ["(-p)//2", "0"])
    assert kinds["drift"] == ("3,5,7,9,11,13", ["(p - 1)//2", "(p + 1)//2"])


def test_phase_forms_agree_with_their_values():
    assert phase_text(1, 4095, 1, "m") == ("4094//m + 1", "4095 - 4094 % m")
    assert phase_text(-1, 0, "x", "d") == ("x//d + 1", "x % d")
    assert phase_text(-1, 5, "x", "d") == ("(x - 5)//d + 1", "5 + (x - 5) % d")
    assert phase_value(1, 4095, 1, 5) == (4094 // 5 + 1, 4095 - 4094 % 5)
    assert phase_value(-1, 0, 4088, 7) == (4088 // 7 + 1, 4088 % 7)
    assert iv_values(("lin", 65535, ((("iv", ("eq", "x", 0)), -61440),))) == {65535, 4095}
    assert not iv_values(("lin", 1, (("x", 1),)))


BOUNCE = {
    "name": "bounce",
    "voice": {"pw": {"width": 8}, "ps": {"width": 3}, "fc": {"width": 1}},
    "procs": {
        "pulse": [
            {"if": "ps == 0", "then": [{"return": True}]},
            {
                "if": "ps >= 4",
                "then": [
                    {"accumulate": "pw", "step": "ps & 3", "clamp": False,
                     "max": "0x3F if (pw >> 6) == 0 else 0xFF",
                     "on_bound": [{"set": {"pw": 0x38, "ps": "ps ^ 4"}}]}
                ],
                "else": [
                    {"accumulate": "pw", "step": "0 - ps - (1 - fc)", "clamp": False,
                     "max": "0x0F if (pw >> 4) == 0 else 0xFF",
                     "on_bound": [{"set": {"pw": 1, "ps": "ps ^ 4"}}]}
                ],
            },
        ]
    },
}  # fmt: skip


def test_pulse_bounce_phases(monkeypatch):
    monkeypatch.setattr("unitracker.modfit.bounce.playroutine", lambda name: BOUNCE)
    bounce = Bounce("bounce", "procs.pulse", "pw")
    assert (bounce.mode, bounce.flip, bounce.kept) == ("ps", 4, 3)
    assert {0, 63, 255} <= set(bounce.thresholds()) and bounce.reloads() == [1, 0x38]
    phases, const, bad = bounce.phases()
    assert not bad and sorted((b, ms) for b, ms in const) == [(0, [0]), (1, [0])]
    up, down = sorted(phases, key=lambda p: -p.sign)
    assert (up.bit, up.bound, up.reload, up.wraps, up.ms) == (1, 63, 0x38, True, [1, 2, 3])
    assert (down.bit, down.bound, down.reload, down.ms) == (0, 0, 1, [1, 2, 3])
    lines = bounce.lines()
    assert "   cycle m in 1..3: period 62//m + 1 + 56//(1 + -1 * fc + m) + 1" in lines
    assert any("max(56, 63 - 62 % m)" in x for x in lines) and lines[-1].endswith("bounds equal")


def test_bounce_closed_forms_match_the_vm():
    form, actions = Form(BOUNCE), resolve(BOUNCE, "procs.pulse")
    for m, fc in itertools.product((1, 2, 3), (0, 1)):
        d, up = m + 1 - fc, 62 // m + 1
        period = up + 56 // d + 1
        steps = deltas(form, actions, {"pw": 1, "ps": m | 4, "fc": fc}, "pw", 2 * period)
        values = (1 + steps.cumsum()).tolist()
        assert steps[0] == m and values[up - 1] == 0x38 and values[up - 2] == 63 - 62 % m
        assert values[period - 1] == 1 and values[2 * period - 1] == 1
        assert min(values) == min(1, 56 % d) and max(values) == max(56, 63 - 62 % m)


GT_PULSE = specs()["sweeps"]["goattracker2"][0]
SW_PULSE = specs()["sweeps"]["sidwizard"][0]


def gt_song(rows: dict) -> dict:
    pr = playroutine("goattracker2")
    records = {"pulsetab": rows, "ins": {1: {"pw": 1}}}
    return {"playroutine": pr, "records": records, "phrases": {}, "tables": {}}


def setrow(pw: int, nxt: int) -> dict:
    return {"l": 0x80 | (pw >> 8), "r": pw & 0xFF, "next": nxt}


def sweep(l: int, r: int, nxt: int) -> dict:
    return {"l": l, "r": r, "next": nxt}


PROGRAMS = [
    (
        {1: setrow(1024, 2), 2: sweep(8, 16, 3), 3: sweep(16, 240, 4), 4: sweep(8, 16, 2)},
        ["accumulate step 16 [896, 1152] reflect, first +16"],
    ),
    ({1: setrow(512, 2), 2: sweep(4, 8, 1)}, ["accumulate step 8 [-, 544] reload 512"]),
    ({1: setrow(100, 2), 2: sweep(4, 3, 2)}, ["accumulate step 3 wrap"]),
    ({1: setrow(100, 2), 2: sweep(4, 253, 0)}, ["accumulate step -3 [88, -] clamp"]),
    (
        {1: setrow(100, 2), 2: sweep(4, 3, 3), 3: sweep(2, 5, 0)},
        ["not one accumulator: step sizes +3 x4, +5 x2"],
    ),
    (
        {1: setrow(100, 2), 2: sweep(4, 3, 3), 3: sweep(2, 253, 4), 4: sweep(1, 3, 2)},
        ["step size 3 turning at 112, 106, 121, 115, not two bounds"],
    ),
    ({1: setrow(100, 0)}, ["set 100, then holds"]),
    ({1: setrow(100, 2), 2: sweep(0, 3, 0)}, ["set 100, then holds", "cycle of 1 ticks: holds 1"]),
    ({1: sweep(4, 3, 0)}, ["starts from the incoming value"]),
    (
        {1: setrow(100, 2), 2: setrow(200, 3), 3: sweep(2, 1, 1)},
        ["not one accumulator: 2 set rows in the cycle"],
    ),
    (
        {1: setrow(100, 2), 2: sweep(2, 1, 3), 3: sweep(1, 2, 1)},
        ["steps +1, +2 between reloads"],
    ),
]


def test_goattracker_pulse_programs():
    for rows, expected in PROGRAMS:
        stepper = Stepper(gt_song(rows), GT_PULSE)
        text = verdict(stepper.run(1, {}), stepper.top)
        assert all(e in text for e in expected), (rows, text)


def test_entries_from_instruments_commands_and_wave_rows():
    song = gt_song({1: setrow(100, 0), 5: setrow(5, 0), 7: setrow(7, 0)})
    song["records"]["ctrltab"] = {1: {"w": 0xF9, "n": 7, "next": 0}, 2: {"w": 0x10, "n": 5}}
    song["phrases"] = {"a": [{"note": 30, "command": [9, 5]}, {"commands": [[9, 0], [5, 3]]}]}
    found = entries(song, "pulsetab")
    assert found == {1: [("ins", 1)], 5: [("command", 9)], 7: [("ctrltab", 1)]}


def test_sidwizard_fused_pulse_crosses_the_low_byte():
    pr = playroutine("sidwizard")
    rows = {1: {"a": 0x88, "b": 4, "next": 2}, 2: {"a": 3, "b": 0xF8, "next": 3},
            3: {"a": 3, "b": 8, "next": 2}}  # fmt: skip
    song = {"playroutine": pr, "records": {"pwtab": rows, "ins": {1: {"pw": 1}}}, "tables": {}}
    stepper = Stepper(song, SW_PULSE)
    run = stepper.run(1, {"ci": 1})
    assert run.ops[:5] == [("set", 0x804), ("add", -8), ("add", -8), ("add", -8), ("add", 0)]
    text = verdict(run, stepper.top)
    assert "accumulate step 8 [2028, 2052] reflect, first -8" in text and "holds 1" in text


def test_unsteppable_runs_and_simulation():
    assert verdict(Run([], -1, "control x reads the swept value"), 255).startswith("not stepped")
    acc = {"step": 2, "min": 0, "max": 4, "clamp": "reflect"}
    assert [v for v, _ in simulate(acc, 0, 4, 255)] == [0, 2, 4, 2, 0]
    acc = {"step": 3, "max": 6, "clamp": False, "reload": 0}
    assert [v for v, _ in simulate(acc, 0, 3, 255)] == [0, 3, 6, 0]


@pytest.mark.parametrize(
    "name, expected",
    [
        (
            "metal_warrior",
            [
                "row 1 (ins 1): set 1024, then accumulate step 16 [512, 1536] reflect, first +16",
                "row 6 (ins 2): set 512, then accumulate step 40 [272, 2032] reflect, first +40",
            ],
        ),
        (
            "csillag",
            [
                "not one accumulator: step sizes +2 x64, -8 x64",
                "set 128, then accumulate step 1 [-, 240] clamp",
            ],
        ),
    ],
)
def test_reference_song_programs(request, name, expected):
    text = "\n".join(song_lines(request.getfixturevalue(name)))
    assert all(e in text for e in expected), text


ORBITS = {"goattracker2": periods("goattracker2", 125, 127)}


@pytest.mark.parametrize(
    "argv, patches, expected",
    [
        (
            ["fit", "defmon:procs.pulse:pw", "sidwizard:procs.tick_filt:vfhi:vflo:3"]
            + ["defmon:procs.acid_step:acc"],
            {},
            [
                "== defmon:procs.pulse -> pw\n   when",
                "no single accumulator\n      against {'set': {'pw': '4088'}}: ",
                "no inductive invariant",
                "      {'accumulate': 'vfhi:vflo:3', 'step':",
                "== defmon:procs.acid_step -> acc\n   when (not (cp == 0))\n"
                "      no single accumulator\n   when (cp == 0)\n      no single accumulator",
            ],
        ),
        (
            ["poly", "goattracker2:record_types.pulsetab.each"],
            {},
            ["== goattracker2:record_types.pulsetab.each\n  pulse' ="],
        ),
        (
            ["orbit", "--ticks", "300"],
            {"unitracker.modfit.cli.specs": lambda: {"orbits": ORBITS}},
            [
                "   125: ping-pong [-64, 63], first step +1, reflect {'accumulate': 'freq'",
                "   126: no ping-pong",
                "reflect for 1 of 3; fails: [126, 127]",
            ],
        ),
        (
            ["sweeps"],
            {"unitracker.modfit.sweeps.bounce_lines": lambda units: ["== bounce"]},
            [
                "# Bounce orbits\n\n== bounce",
                "# Table programs",
                "s8(pwtab[ppos].b)'} when ci != 0",
            ],
        ),
        (
            ["command", "goattracker2:ad"],
            {},
            ["== goattracker2 !ad (5), plain row", "setup ad := arg"],
        ),
        (["keywords", "defmon"], {}, ["keyword/field | playroutine", "\n!ins.filter"]),
        (
            ["generic", "defmon"],
            {
                "unitracker.modfit.generic.keywords": lambda pr: [],
                "unitracker.modfit.generic.bounce_lines": lambda units: [f"== bounce {len(units)}"],
            },
            [
                "# Grouping by role, respond and temporal kind",
                "== defmon timers",
                "defmon | procs.acid_step [(cp == 0)] | acc | CUT[3:10]",
                "# Ping-pong orbits",
                "== bounce 1",
            ],
        ),
    ],
    ids=lambda v: v[0] if isinstance(v, list) and v and v[0].isalpha() else None,
)
def test_cli(tmp_path, monkeypatch, argv, patches, expected):
    for target, value in patches.items():
        monkeypatch.setattr(target, value)
    out = tmp_path / "report.txt"
    main(["--out", str(out), *argv])
    text = out.read_text()
    assert all(e in text for e in expected), text
    if argv[0] == "sweeps":
        assert text.count(": proved") == 1
