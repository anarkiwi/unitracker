"""Register roles through the composed init and tick: by value, per call site, masks apart."""

import pytest

from unitracker.modfit import playroutine, resolve
from unitracker.modfit.compose import Merge
from unitracker.modfit.flow import Composed, Writes
from unitracker.modfit.invariant import Slicer
from unitracker.modfit.keywords import signer

FREQ, PW, CTRL = set(range(16)), set(range(12)), set(range(8))


@pytest.fixture(name="roles", scope="module")
def roles_fixture() -> dict:
    return {n: Composed(playroutine(n)) for n in ("goattracker2", "defmon", "sidwizard")}


def test_parameter_cell_carries_only_its_call_site(roles):
    gt = roles["goattracker2"]
    assert gt.role("arg") == {}
    assert gt.role("cmddata") == {"FREQ": FREQ}
    assert set(gt.role("newcmddata")) > {"AD", "SR", "CTRL", "FREQ"}


def test_carry_test_steers_no_value(roles):
    dm = roles["defmon"]
    assert dm.role("fc") == {}
    assert dm.role("vinit.sacc") == dm.role("vinit.note") == {"FREQ": FREQ}
    assert dm.role("ps") == {"PW": PW}


def test_vibrato_phase_only_gates(roles):
    assert roles["goattracker2"].role("vibtime") == {}
    sig = signer("goattracker2").keyword(4, None)["sig"]
    assert sig == {("FREQ", "ARM", "-"), ("FREQ", "REFLECT", "ref speedtab")}


def test_scratch_resolved_per_list(roles):
    sw = roles["sidwizard"]
    assert sw.role("t1") == sw.role("rx") == sw.role("rw") == {}
    assert sw.role("sad") == {"AD": CTRL}


def test_bitwise_op_of_two_cells_is_a_mask():
    dm = signer("defmon")
    for field in ("wgl", "wgh"):
        assert ("CTRL", "MASK", "[0:7]→CTRL") in dm.field("sidtab", field)["sig"]
    plain = Composed(playroutine("goattracker2"), plain=True)
    assert plain.role("masterfader") == {"MODEVOL": CTRL}
    assert plain.role("wave") == {} and plain.bit_range("masterfader") == 0x0F
    assert signer("goattracker2").keyword(13, "arg < 0x10")["sig"] == {
        ("MODEVOL", "LOAD", "[0:7]→MODEVOL")
    }


def test_chained_pointer_selects_no_value():
    gt = signer("goattracker2")
    assert gt.field("pulsetab", "next")["sig"] == {
        ("-", "TEST", "-"),
        ("PW", "PTR", "ref pulsetab"),
    }
    assert gt.field("pulsetab", "r")["sig"] == {("-", "TEST", "-"), ("PW", "ACC", "[0:7]→PW")}


def test_mode_field_read_only_by_tests():
    assert signer("defmon").field("ginit", "sub")["sig"] == {("-", "TEST", "-")}


def test_sidwizard_header_walk_reaches_ctrl_and_freq():
    sw = signer("sidwizard")
    assert sw.field("ins", "ad")["sig"] == {
        ("-", "TEST", "-"),
        ("AD", "LOAD", "[0:7]→AD"),
        ("CTRL", "MASK", "[0:7]→CTRL"),
    }
    for name in ("hrad", "sr"):
        assert any(r.startswith("FREQ") for r, _, _ in sw.field("ins", name)["sig"])
    pr = playroutine("sidwizard")
    m = Merge(pr)
    walk, _ = m.merge(pr["procs"]["wf_row"], {"started": 0, "hpos": 1})
    head, _ = m.merge(pr["procs"]["wf_row"], {"started": 0, "hpos": 0})
    assert walk["rw"] == ("fld", "ad", ("rec", "ins", "ci"))
    assert head["rx"] == ("fld", "hrad", ("rec", "ins", "ci"))


def test_slicer_keeps_a_branch_return_before_kept_actions():
    pr = playroutine("sidwizard")
    sliced, _ = Slicer(pr, Writes(pr), dict(pr["procs"])).seq(
        resolve(pr, "procs.tick_vibrato"), {"vfc"}, {"vfc"}
    )
    branch = next(a for a in sliced if a.get("if") == "svib == 0")
    assert [b["if"] for b in branch["else"]] == ["svib != 0x10", "vdc >= 0"]
