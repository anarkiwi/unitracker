"""The orthogonality report: proved redundancies, dead playroutine state, unreached vocabulary."""

from conftest import OPTIMISED
from test_optimise import C1, I1, RUN, doc, run

from unitracker import optimise, orthogonality, songio

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


def texts(lines) -> list:
    return [(ln.where, ln.forms) for ln in lines]


def test_dead_state_of_a_playroutine():
    found = texts(orthogonality.dead_state(TOY))
    assert ("cell `b`", "written, never read") in found
    assert ("cell `c`", "read only by dead cells") in found
    assert ("proc `q`", "never called") in found
    assert ("constant `unused`", "never named") in found
    assert ("test `c > 5`", "always false for every value its cells take") in found
    assert ("field `prog.w`", "never read by the VM") in found
    assert all(w not in ("cell `a`", "cell `m`") for w, _ in found)


def test_field_resolutions_tell_retired_and_format_side_fields():
    lines = {ln.where: ln.resolution for ln in orthogonality.dead_state(TOY)}
    assert lines["field `ins.vib`"].startswith("retired")
    assert lines["field `prog.next`"].startswith("read by the format's program walk")
    defmon = {ln.where: ln.resolution for ln in orthogonality.dead_state(songio.library("defmon"))}
    assert defmon["field `restart.ad`"].startswith("format-side")


def test_library_playroutines_have_no_dead_cells_or_procs():
    for name in ("goattracker2", "sidwizard", "defmon"):
        kinds = {ln.where.split()[0] for ln in orthogonality.dead_state(songio.library(name))}
        assert kinds <= {"field"}, name


def test_vocabulary_lists_shapes_and_commands_no_row_reaches():
    ir = songio.loads(doc(I1, RUN, C1))
    found = texts(orthogonality.vocabulary([("toy", ir)]))
    assert any(w.startswith("toy codec shape ctrltab[") and w.endswith("`{vol}`") for w, _ in found)
    assert ("toy command `!tempo`", "no lifted event plays it") in found
    assert all("command `!run`" not in w for w, _ in found)
    uses = orthogonality.shape_uses(ir)
    assert uses[("ctrltab", 0)] >= 2 and orthogonality.command_uses(ir) == {"run": 4}


def test_report_lists_a_known_redundancy_once_per_form():
    result = run(doc(I1, "[C4.i1*2, D4.i1*2, E4!ad:9*2, F4!ad:9*2]"), ["commands"])
    report = orthogonality.report([("toy", result)])
    assert report.startswith("# Orthogonality and redundancy")
    key = report.split("## Non-orthogonal key")[1].split("##")[0]
    assert "toy p1[2], p1[3]: `E4!ad:9*2` ≡ `E4*2`" not in key
    assert "`E4!ad:9*2` ≡ `E4*2`" in key and "`F4!ad:9*2` ≡ `F4*2`" in key
    assert "`D4.i1*2` ≡ `D4*2`" in key
    assert report == orthogonality.report([("toy", result)])


def test_report_lists_vocabulary_and_song_redundancy():
    result = run(doc(I1, RUN, C1), ["hoist", "instruments"])
    report = orthogonality.report([("toy", result)])
    vocab = report.split("## Vocabulary redundancy")[1].split("##")[0]
    assert "goattracker2 · toy i1: `X.i1!run:@c1` ≡ `X.i1 with i1 + {ctrl: c1}`" in vocab
    assert "## Song redundancy\n\n- none" in report


def test_reference_tunes_report_is_deterministic(optimised_tunes):
    songs = [(name, optimised_tunes[name][1]) for name in sorted(OPTIMISED)]
    first = orthogonality.report(songs)
    source = optimised_tunes["metal_warrior"][1].source
    again = optimise.optimise(source, ["hoist"], workers=1, progress=False)
    swapped = [(n, again if n == "metal_warrior" else r) for n, r in songs]
    assert orthogonality.report(swapped) == first
    assert "X.i3!run:@c1" in first and "## VM dead state" in first
    assert "defmon · field `restart.ad`" in first and "sidwizard · csillag codec shape" in first
