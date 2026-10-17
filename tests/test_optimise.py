"""The optimiser's passes: each fires where the grid proves it and is refused where it would not."""

import copy

import numpy as np
import pytest

from unitracker import backends, cli, optimise, songio
from unitracker.vm import SongPlayer, first_mismatch

RESTART = "r1: [{hold: 4, gate: 0, ad: 15, sr: 0}, {ctrl: 9, gate: 1}, end]"
PULSE = "16*abs((φ - 32) % 128 - 64) + 512"


def doc(ins: str, phrase: str, defs: str = "", steps: int = 8, extra: str = "") -> str:
    """A three-voice GoatTracker 2 document: voice 1 plays ``phrase``, the others rest."""
    return f"""unitracker: 1.15
playroutine: {{ref: goattracker2}}
meta: {{title: t, author: a, released: r}}
song:
  - def:
      restart:
        {RESTART}
      ins:
{ins}
{defs}      phrase:
        p1: {phrase}
        p2: [-*{steps}]
{extra}    play: [p1, p2, p2]
loop: 0
"""


def ins_line(name: str, fields: str) -> str:
    return f"        {name}: {{{fields}, restart: r1}}"


I1 = ins_line("i1", "ad: 9, ctrl: [{ctrl: 65, note: +0}, end]")


def run(text: str, passes, workers: int = 1):
    return optimise.optimise(songio.loads(text), passes, workers=workers, progress=False)


def kept(result) -> list:
    return [f.edit[0] for f in result.findings if f.kept]


def proved(result) -> list:
    return [f.edit[0] for f in result.findings]


def plays_alike(result) -> bool:
    back = songio.loads(result.text)
    return optimise.mismatch(optimise.Trace(result.source), back) is None


def test_mismatch_finds_the_first_difference_and_refuses_other_starts():
    text = doc(I1, "[C4.i1*2, D4*2, E4*2, F4*2]")
    ref = optimise.Trace(songio.loads(text))
    assert optimise.mismatch(ref, songio.loads(text)) is None
    tick, reg = optimise.mismatch(ref, songio.loads(text.replace("E4*2", "G4*2")))
    assert tick > ref.starts[0] and reg in (0, 1)
    late = songio.loads(text.replace("[C4.i1*2, D4*2, E4*2, F4*2]", "[-, C4.i1, D4*2, E4*2, F4*2]"))
    assert optimise.mismatch(ref, late)[0] == ref.starts[0]


def test_mismatch_beyond_the_bound_is_unproved(monkeypatch):
    text = doc(I1, "[C4.i1*2, D4*2, E4*2, F4*2]")
    ref = optimise.Trace(songio.loads(text))
    monkeypatch.setattr(optimise.reach, "MAX_TICKS", 10)
    assert optimise.mismatch(ref, songio.loads(text))[0] == "unproved"


def test_trace_rows_repeat_from_the_recurrence():
    ref = optimise.Trace(songio.loads(doc(I1, "[C4.i1*2, D4*2, E4*2, F4*2]")))
    grid = SongPlayer(songio.loads(doc(I1, "[C4.i1*2, D4*2, E4*2, F4*2]"))).render_grid(3000)
    np.testing.assert_array_equal(ref.grid(3000), grid)
    assert ref.row(2999) == tuple(grid[2999])


C1 = "      ctrltab:\n        c1: [{ctrl: 33, note: +0}, end]\n"
RUN = "[C4.i1!run:@c1*2, D4!run:@c1*2, E4!run:@c1*2, F4!run:@c1*2]"


def test_hoist_moves_a_command_into_the_instrument():
    result = run(doc(I1, RUN, C1), ["hoist"])
    assert kept(result) == ["hoist"] and "!run" not in result.text
    assert plays_alike(result)


def test_hoist_refuses_a_command_the_field_does_not_play():
    result = run(doc(I1, "[C4.i1!ctrl:33*2, D4!ctrl:33*2, E4!ctrl:33*2, F4!ctrl:33*2]"), ["hoist"])
    assert proved(result) == [] and result.after == result.before


def test_hoist_value_names_the_field():
    ir = songio.loads(doc(I1, RUN, C1))
    assert optimise.hoist_value(ir, 1, 8, 2)[0] == "ctrl"
    assert optimise.hoist_value(ir, 1, 5, 9) == ("ad", 9)
    assert optimise.hoist_value(ir, 1, 17, 0) is None
    assert optimise.hoist_value(ir, 9, 5, 9) is None


I2_UNHEARD = ins_line("i2", "ad: 9, vd: 5, ctrl: [{ctrl: 65, note: +0}, end]")
I2_HEARD = ins_line("i2", "ad: 10, ctrl: [{ctrl: 65, note: +0}, end]")
TWO = "[C4.i1*2, D4.i2*2, E4.i1*2, F4.i2*2]"


def test_instruments_merge_when_their_difference_is_unheard():
    result = run(doc(f"{I1}\n{I2_UNHEARD}", TWO), ["instruments"])
    assert kept(result) == ["merge"] and "i2" not in result.text
    assert plays_alike(result)


def test_instruments_stay_apart_when_heard():
    result = run(doc(f"{I1}\n{I2_HEARD}", TWO), ["instruments"])
    assert "merge" not in proved(result)


TWO_RUNS = "[C4.i1!run:@c1*2, D4!run:@c2*2, E4!run:@c1*2, F4!run:@c2*2]"


def programs(second: str) -> str:
    return f"      ctrltab:\n        c1: [{{ctrl: 33, note: +0}}, end]\n        c2: {second}\n"


def test_programs_merge_when_they_play_alike():
    defs = programs("[{ctrl: 33, note: +0}, {ctrl: 33, note: +0}, end]")
    result = run(doc(I1, TWO_RUNS, defs), ["programs"])
    assert "merge" in kept(result) and "c2" not in result.text
    assert plays_alike(result)


def test_programs_stay_apart_when_heard():
    defs = programs("[{ctrl: 33, note: +0}, {ctrl: 17, note: +0}, end]")
    assert "merge" not in proved(run(doc(I1, TWO_RUNS, defs), ["programs"]))


def test_truncate_cuts_rows_that_change_nothing():
    i1 = ins_line("i1", "ad: 9, ctrl: [{ctrl: 65, note: +0}, {ctrl: 65, note: +0}, end]")
    result = run(doc(i1, "[C4.i1*2, D4*2, E4*2, F4*2]"), ["programs"])
    assert kept(result) == ["truncate"] and plays_alike(result)


def test_truncate_keeps_rows_that_are_heard():
    i1 = ins_line("i1", "ad: 9, ctrl: [{ctrl: 65, note: +0}, {ctrl: 33, note: +0}, end]")
    assert "truncate" not in proved(run(doc(i1, "[C4.i1*2, D4*2, E4*2, F4*2]"), ["programs"]))


A, B = "{ctrl: 65, note: +0}", "{ctrl: 33, note: +0}"


def test_reloop_writes_the_shortest_cycle():
    loop = f"[{A}, {B}, {A}, {B}, {{loop: 0}}]"
    result = run(doc(ins_line("i1", f"ad: 9, ctrl: {loop}"), "[C4.i1*8]"), ["programs"])
    assert kept(result) == ["reloop"] and plays_alike(result)


def test_reloop_has_nothing_to_do_on_a_shortest_cycle():
    loop = f"[{A}, {B}, {A}, {{ctrl: 17, note: +0}}, {{loop: 0}}]"
    ir = songio.loads(doc(ins_line("i1", f"ad: 9, ctrl: {loop}"), "[C4.i1*8]"))
    entry = ir["records"]["ins"][1]["ctrl"]
    assert optimise.reloop(ir, "ctrltab", entry) is None
    assert optimise.truncate(ir, "ctrltab", entry, 5) is None


def exprs(second: str) -> str:
    return f"      x:\n        x1: (v + 32) % 65536\n        x2: {second}\n"


FREQS = "[C4.i1!freq:@x1*2, D4!freq:@x2*2, E4!freq:@x1*2, F4!freq:@x2*2]"


def test_expressions_merge_when_they_play_alike():
    result = run(doc(I1, FREQS, exprs("(v + 16 + 16) % 65536")), ["expressions"])
    assert kept(result) == ["merge"] and "x2" not in result.text
    assert plays_alike(result)


def test_expressions_stay_apart_when_heard():
    assert proved(run(doc(I1, FREQS, exprs("(v + 33) % 65536")), ["expressions"])) == []


def aliased(second: str) -> str:
    extra = f"  - def:\n      phrase:\n        p3: {second}\n    play: [p3, p2, p2]\n"
    return doc(I1, "[C4.i1*2, D4*2, E4*2, F4*2]", extra="").replace(
        "loop: 0\n", extra + "loop: 0\n"
    )


def test_alias_plays_one_phrase_for_another():
    result = run(aliased("[C4.i1*2, D4.i1*2, E4*2, F4*2]"), ["phrases"])
    assert kept(result) == ["alias"] and "p3" not in result.text
    assert plays_alike(result)


def test_alias_keeps_phrases_that_differ():
    assert "alias" not in proved(run(aliased("[C4.i1*2, D4.i1*2, G4*2, F4*2]"), ["phrases"]))


PART = ", ".join(["C4.i1", "D4", "E4", "F4", "G4", "A4", "B4", "C5", "D5", "E5", "F5", "G5"])


def test_factor_writes_a_repeating_phrase_as_plays_of_its_part():
    result = run(doc(I1, f"[{', '.join([PART] * 4)}]", steps=48), ["phrases"])
    assert kept(result) == ["factor"] and result.text.count("play: [p1, ~, ~]") == 3
    assert plays_alike(result)


def test_factor_refused_where_a_phrase_start_is_heard():
    i1 = ins_line("i1", f"ad: 9, ctrl: [{{ctrl: 65, note: +0}}, end], pw: {PULSE}")
    part = "-*4, C4.i1*4, D4*4"
    text = doc(i1, f"[{', '.join([part] * 4)}]", steps=48)
    ir = songio.loads(text)
    assert optimise.mismatch(optimise.Trace(ir), optimise.factor(ir, "p1", 3)) is not None
    assert "factor" not in proved(run(text, ["phrases"]))


def test_commands_drop_what_the_context_already_plays():
    result = run(doc(I1, "[C4.i1*2, D4.i1*2, E4!ad:9*2, F4*2]"), ["commands"])
    assert sorted(kept(result)) == ["drop_command", "drop_instrument", "drop_instrument"]
    assert plays_alike(result) and "!ad" not in result.text


def test_commands_keep_what_is_heard():
    text = doc(f"{I1}\n{I2_HEARD}", "[C4.i1*2, D4.i2*2, E4!ad:11*2, F4*2]")
    result = run(text, ["commands"])
    assert proved(result) == [] and "C4.i1" in result.text and "!ad:11" in result.text


def test_fields_clear_what_is_never_heard():
    result = run(
        doc(
            ins_line("i1", "ad: 9, vd: 5, ctrl: [{ctrl: 65, note: +0}, end]"),
            TWO.replace(".i2", ""),
        ),
        ["fields"],
    )
    assert ("clear", ("ins", 1, "vd")) in [f.edit for f in result.findings if f.kept]
    assert "ad: 9" in result.text and plays_alike(result)


def test_edits_refuse_what_they_cannot_apply():
    ir = songio.loads(doc(I1, "[C4.i1*2, D4*2, E4*2, F4*2]"))
    assert optimise.merge(ir, "ins", 1, 7) is None
    assert optimise.alias(ir, "p1", "p1") is None
    assert optimise.factor(ir, "p1", 3) is None
    assert optimise.drop_command(ir, "p1", 0, 0) is None
    assert optimise.drop_keep(ir, "p1", 0) is None
    assert optimise.drop_instrument(ir, "p1", 1) is None
    assert optimise.clear(ir, "ins", 1, "pw") is None
    assert optimise.hoist(ir, 1, 17, 0) is None


def test_drop_keep_restarts_the_kept_programs():
    ir = songio.loads(doc(I1, "[C4.i1*2, D4*2, E4*2, F4*2]"))
    ir["phrases"]["p1"][1]["keep"] = 1
    assert ("drop_keep", ("p1", 1)) in optimise.commands(ir)
    assert "keep" not in optimise.drop_keep(ir, "p1", 1)["phrases"]["p1"][1]
    assert ir["phrases"]["p1"][1]["keep"] == 1


def test_the_driver_is_canonical_and_deterministic():
    text = doc(
        f"{I1}\n{I2_UNHEARD}", "[C4.i1!run:@c1*2, D4.i2*2, E4.i1!ad:9*2, F4.i2!run:@c1*2]", C1
    )
    one, two = run(text, None, workers=1), run(text, None, workers=2)
    assert one.text == two.text and one.after < one.before
    assert [(f.edit, f.kept, f.forms) for f in one.findings] == [
        (f.edit, f.kept, f.forms) for f in two.findings
    ]
    assert songio.dumps(songio.loads(one.text)) == one.text
    assert plays_alike(one)


def test_an_embedded_playroutine_is_optimised_embedded():
    ir = songio.loads(doc(f"{I1}\n{I2_UNHEARD}", TWO))
    ir["playroutine"] = copy.deepcopy(ir["playroutine"])
    ir["playroutine"]["voice"]["spare"] = {"width": 8}
    with pytest.raises(songio.SongFormatError):
        songio.dumps(ir)
    result = optimise.optimise(ir, ["instruments"], workers=1, progress=False)
    assert kept(result) == ["merge"] and "record_types:" in result.text


def test_cli_optimises_and_reports(tmp_path, capsys):
    song = tmp_path / "song.yaml"
    song.write_text(doc(I1, "[C4.i1*2, D4.i1*2, E4!ad:9*2, F4*2]"), encoding="utf-8")
    out, report = tmp_path / "small.yaml", tmp_path / "orth.md"
    args = [
        str(song),
        "-o",
        str(out),
        "--report",
        str(report),
        "--passes",
        "commands",
        "--workers",
        "1",
    ]
    assert cli.optimise_main(args) == 0
    assert "tokens" in capsys.readouterr().err
    assert "!ad" not in out.read_text(encoding="utf-8")
    assert "E4!ad:9*2" in report.read_text(encoding="utf-8")
    assert cli.optimise_main([str(song), "--passes", "fields", "--workers", "1"]) == 0
    assert capsys.readouterr().out.startswith("unitracker: 1.15")
    with pytest.raises(SystemExit):
        cli.optimise_main([str(song), "--passes", "nosuch"])


def test_lift_optimise_flag(tmp_path, monkeypatch):
    text = doc(f"{I1}\n{I2_UNHEARD}", TWO)
    monkeypatch.setattr(backends, "lift", lambda data, subtune: songio.loads(text))
    sid, out = tmp_path / "x.sid", tmp_path / "x.yaml"
    sid.write_bytes(b"")
    assert cli.lift_main([str(sid), "-o", str(out), "--optimise"]) == 0
    assert "i2" not in out.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["metal_warrior", "crosseyed", "csillag"])
def test_reference_tunes_optimised_play_their_native_players(optimised_tunes, name):
    sid, result = optimised_tunes[name]
    assert result.after < result.before and kept(result)
    player = SongPlayer(songio.loads(result.text))
    ours = player.render_grid(3007)
    theirs = np.asarray(backends.native_player(sid.read_bytes()).render_grid(3007), dtype=np.uint8)
    assert first_mismatch(ours, theirs, player.starts) is None


def test_unhoist_plays_an_instrument_as_another_with_a_command():
    i2 = ins_line("i2", "ad: 10, ctrl: [{ctrl: 65, note: +0}, end]")
    result = run(doc(f"{I1}\n{i2}", "[C4.i1*2, D4*2, E4.i2*2, F4.i1*2]"), ["instruments"])
    assert kept(result) == ["unhoist"] and "E4.i1!ad:10*2" in result.text
    assert "i2" not in result.text
    assert plays_alike(result)


def test_unhoist_refused_without_one_field_and_a_free_slot():
    two = ins_line("i2", "ad: 10, sr: 5, ctrl: [{ctrl: 65, note: +0}, end]")
    ir = songio.loads(doc(f"{I1}\n{two}", "[C4.i1*2, D4*2, E4.i2*2, F4.i1*2]"))
    assert optimise.unhoist(ir, 1, 2) is None
    i2 = ins_line("i2", "ad: 10, ctrl: [{ctrl: 65, note: +0}, end]")
    full = "[C4.i1*2, D4*2, E4.i2!legato:0!freq:@x1*2, F4.i1*2]"
    ir = songio.loads(doc(f"{I1}\n{i2}", full, "      x:\n        x1: (v + 32) % 65536\n"))
    assert optimise.unhoist(ir, 1, 2) is None
    assert optimise.unhoist(ir, 1, 1) is None


LONG = "C5, D5, E5, F5, G5, A5, B5, C6, B5, A5, G5, F5"


def shared(ins: str) -> str:
    extra = f"  - def:\n      phrase:\n        p3: [C4*2, D4*2, {LONG}]\n    play: [p3, p2, p2]\n"
    text = doc(ins, f"[{LONG.replace('C5', 'C5.i1', 1)}, C4*2, D4*2]", steps=16)
    return text.replace("loop: 0\n", extra + "loop: 0\n")


def test_share_plays_a_recurring_run_as_one_phrase():
    result = run(
        shared(I1).replace(
            f"[C4*2, D4*2, {LONG}]", f"[C4*2, D4*2, {LONG.replace('C5', 'C5.i1', 1)}]"
        ),
        ["phrases"],
    )
    assert "share" in kept(result) and result.text.count("C6") == 1
    assert plays_alike(result)


def test_share_refused_where_a_phrase_start_is_heard():
    i1 = ins_line("i1", f"ad: 9, ctrl: [{{ctrl: 65, note: +0}}, end], pw: {PULSE}")
    text = shared(i1).replace(
        f"[C4*2, D4*2, {LONG}]", f"[C4*2, D4*2, {LONG.replace('C5', 'C5.i1', 1)}]"
    )
    ir = songio.loads(text)
    keys = optimise.shared_runs(ir)
    assert keys and all(
        optimise.mismatch(optimise.Trace(ir), songio.loads(songio.dumps(optimise.share(ir, k))))
        for k in keys
        if optimise.share(ir, k) is not None and len(k) > 4
    )
    assert "share" not in kept(run(text, ["phrases"]))
