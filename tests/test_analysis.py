"""The analyses: dead state and unreached vocabulary."""

import pytest
from test_optimise import A, B, RUNS, song

from unitracker.analysis import orthogonality
from unitracker.playroutine.library import library

TOY = {
    "name": "toy",
    "constants": {"k": 3, "unused": 4},
    "voice": {"a": {"width": 8}, "b": {"width": 8}, "c": {"width": 8}, "m": {"width": 8}},
    "procs": {"p": [{"set": {"c": 1}}], "q": []},
    "record_types": {
        "prog": {"chain": "next", "fields": ["v", "w", "next"], "do": [{"set": {"a": "row.v"}}]},
        "ins": {"fields": ["vib"]},
    },
    "modulators": {"units": [{"name": "u", "retire": {"record": "ins", "clear": {"vib": 0}}}]},
    "init": [{"set": {"m": 0}}],
    "tick": [
        {"for_voices": [{"set": {"a": "a + k", "b": "a + 1"}}, {"call": "p"}]},
        {"if": "m == 1", "then": [{"set": {"a": 0}}]},
        {"if": "c > 5", "then": [{"set": {"b": 2}}]},
        {"accumulate": "a", "step": 1, "on_bound": [{"set": {"m": 0}}]},
    ],
    "ghost": {"voice": {0: "a"}},
}


def test_dead_state_of_a_playroutine():
    found = {(ln.where, ln.forms): ln.resolution for ln in orthogonality.dead_state(TOY)}
    for where, forms in [
        ("cell `b`", "written, never read"),
        ("cell `c`", "read only by dead cells"),
        ("proc `q`", "never called"),
        ("constant `unused`", "never named"),
        ("test `c > 5`", "always false for every value its cells take"),
        ("field `prog.w`", "never read by the VM"),
    ]:
        assert (where, forms) in found
    assert all(w not in ("cell `a`", "cell `m`") for w, _ in found)
    assert found[("field `ins.vib`", "never read by the VM")].startswith("retired")
    resolution = found[("field `prog.next`", "never read by the VM")]
    assert resolution.startswith("read by the format's program walk")


@pytest.mark.parametrize("name", ["goattracker2", "sidwizard", "defmon"])
def test_library_playroutines_have_no_dead_cells_or_procs(name):
    lines = orthogonality.dead_state(library(name))
    assert {ln.where.split()[0] for ln in lines} <= {"field"}
    if name == "defmon":
        restart = {ln.where: ln.resolution for ln in lines}["field `restart.ad`"]
        assert restart.startswith("format-side")


def test_vocabulary_lists_shapes_and_commands_no_row_reaches():
    ir = song(RUNS, programs=([A], [B]))
    found = [(ln.where, ln.forms) for ln in orthogonality.vocabulary([("toy", ir)])]
    assert any(w.startswith("toy codec shape ctrl[") and w.endswith("`{vol}`") for w, _ in found)
    assert ("toy command `!tempo`", "no lifted event plays it") in found
    assert all("command `!run`" not in w for w, _ in found)
    uses = orthogonality.shape_uses(ir)
    assert uses[("ctrltab", 0)] >= 2 and orthogonality.command_uses(ir) == {"run": 4}
