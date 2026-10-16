"""Timers: detection, orbits, gate signatures and exact closed forms."""

import pytest

from unitracker.modfit import specs
from unitracker.modfit.timer import Timers, affine, closed, grouped, signature, vibrato_signatures


@pytest.fixture(name="gt", scope="module")
def gt_fixture() -> Timers:
    return Timers("goattracker2")


@pytest.fixture(name="sw", scope="module")
def sw_fixture() -> Timers:
    return Timers("sidwizard")


def test_affine_fits_floor_division_exactly():
    xs = list(range(126))
    assert affine(xs, [x // 2 + 1 for x in xs]) == (1, 2, 2)
    assert affine(xs, [-((x + 1) // 2 + 1) for x in xs]) == (-1, -2, 2)
    assert affine([0, 1, 2, 3], [0, 1, 4, 9]) is None
    assert closed({x: 3 * x - 1 for x in range(10)}) == "3*p - 1"
    assert closed({x: x + 1 if x < 8 else 1 for x in range(16)}) == "[0..7: p + 1] [8..15: 1]"
    assert closed({0: 5, 1: 7}) == "0:5, 1:7"


@pytest.mark.parametrize(
    "bits, loop, expected",
    [
        ([True], 0, ("constant", (1,))),
        ([False, False, True], 2, ("delay", (2,))),
        ([True, True, True, False], 3, ("duration", (3,))),
        ([True, True, False, False], 0, ("period", (4,))),
        ([True, False, False], 0, ("drift", (1, 2))),
        ([False, True, False], None, ("drift", (1, 1))),
    ],
)
def test_signature_kinds(bits, loop, expected):
    assert signature(bits, loop) == expected


def test_goattracker_timers(gt):
    ptime = gt.timer("ptime")
    assert ptime.step == -1 and ptime.resets == [0] and ptime.lists == ["procs.pulse_exec"]
    tick = gt.timer("tick")
    assert tick.selectors == ["tempo"] and "alternating on tempo" in tick.text()
    vibtime = gt.timer("vibtime")
    assert vibtime.step == 2 and vibtime.flips
    assert gt.timer("state") is None and gt.timer("tempo") is None


def test_row_duration_and_delay_closed_forms(gt, sw):
    orbit = gt.orbit(gt.timer("ptime"), "procs.pulse_exec")
    assert closed({p: orbit.duration(p) for p in orbit.domain()}) == "p + 1"
    pw = sw.timer("pwcnt")
    orbit = sw.orbit(pw, "procs.tick_pw")
    assert orbit.bound == ("fld", "a", ("rec", "pwtab", "ppos"))
    durations = {p: orbit.duration(p) for p in orbit.domain()}
    assert closed(durations) == "[0..127: p + 1] [128..255: 1]"
    vdc = sw.timer("vdc")
    orbit = sw.orbit(vdc, "procs.tick_vibrato")
    [test] = vdc.tests
    sigs = {p: signature(*orbit.truths(test, p)) for p in orbit.domain()}
    assert list(grouped(sigs)) == [("duration", "0..127", ["p + 1"])]
    assert orbit.duration(5) is None


def test_vibrato_orbits_reproduce_the_reflect_bounds():
    gt = dict(specs()["orbits"]["goattracker2"], periods=[0, 9])
    assert grouped(vibrato_signatures("goattracker2", gt, 200)) == [
        ("period", "0..9", ["(-p - 2)//2", "(p + 2)//2"])
    ]
    sw = dict(specs()["orbits"]["sidwizard"], periods=[2, 13])
    kinds = {k: (d, f) for k, d, f in grouped(vibrato_signatures("sidwizard", sw, 200))}
    assert kinds["period"] == ("2,4,6,8,10,12", ["(-p)//2", "0"])
    assert kinds["drift"] == ("3,5,7,9,11,13", ["(p - 1)//2", "(p + 1)//2"])
