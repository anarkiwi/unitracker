"""The optimiser: the proof, each pass's edits where the grid proves them and refused where it
does not, the driver, and its command lines."""

import copy
import functools

import numpy as np
import pytest

from unitracker import cli, optimise
from unitracker.format import songio
from unitracker.lift import backends
from unitracker.player.vm import SongPlayer

RESTART = "r1: [{hold: 4, gate: 0, ad: 15, sr: 0}, {ctrl: 9}, end]"
PULSE = "16*abs((φ - 32) % 128 - 64) + 512"


def doc(ins: str, phrase: str, defs: str = "", steps: int = 8, extra: str = "") -> str:
    """A three-voice GoatTracker 2 document: voice 1 plays ``phrase``, the others rest; ``extra``
    rows follow the first."""
    return f"""unitracker: 1.20
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
    play: [p1, p2, p2]
{extra}  - {{loop: 0}}
"""


def ins_line(name: str, fields: str) -> str:
    return f"        {name}: {{{fields}, restart: r1}}"


def row(name: str, phrase: str) -> str:
    return f"  - def:\n      phrase:\n        {name}: {phrase}\n    play: [{name}, p2, p2]\n"


def ctrl(fields: str = "ad: 9", wave: str = "[{ctrl: 65, note: +0}, end]") -> str:
    """Instrument ``i1`` with ``fields`` and wave program ``wave``."""
    return ins_line("i1", f"{fields}, ctrl: {wave}")


A, B, Z = "{ctrl: 65, note: +0}", "{ctrl: 33, note: +0}", "{ctrl: 17, note: +0}"
I1 = ctrl()
SCALE = "[C4.i1*2, D4*2, E4*2, F4*2]"
C1 = "      ctrl:\n        c1: [{ctrl: 33, note: +0}, end]\n"
RUN = "[C4.i1!run:c1*2, D4!run:c1*2, E4!run:c1*2, F4!run:c1*2]"
RUN_TWICE = RUN[:-1] + ", " + RUN[1:].replace("4", "5").replace(".i1", "")
LATE = "freq: (φ >= 38)*(F(N + 1) - F(N))"
I2_UNHEARD = ins_line("i2", f"ad: 9, {LATE}, ctrl: [{A}, end]")
I2_HEARD = ins_line("i2", f"ad: 10, ctrl: [{A}, end]")
TWO = "[C4.i1*2, D4.i2*2, E4.i1*2, F4.i2*2]"
AS_I2 = "[C4.i1*2, D4*2, E4.i2*2, F4.i1*2]"
TWO_RUNS = "[C4.i1!run:c1*2, D4!run:c2*2, E4!run:c1*2, F4!run:c2*2]"
FREQS = "[C4.i1!freq:x1*2, D4!freq:x2*2, E4!freq:x1*2, F4!freq:x2*2]"
PART = ", ".join(["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5", "D5", "E5", "F5", "G5"])
HEARD_PART = "-*4, C4*4, D4*4"
LONG = "C5, D5, E5, F5, G5, A5, B5, C6, B5, A5, G5, F5"
LONG_I1 = LONG.replace("C5", "C5.i1", 1)
PULSED = ctrl(f"ad: 9, pw: {PULSE}")
SHORTEST = doc(ctrl(wave=f"[{A}, {B}, {A}, {Z}, {{loop: 0}}]"), "[C4.i1*8]")


def programs(second: str) -> str:
    return f"      ctrl:\n        c1: [{B}, end]\n        c2: {second}\n"


def exprs(second: str) -> str:
    return f"      x:\n        x1: (v + 32) % 65536\n        x2: {second}\n"


def shared(ins: str) -> str:
    return doc(
        ins, f"[{LONG_I1}, C4*2, D4*2]", steps=16, extra=row("p3", f"[C4*2, D4*2, {LONG_I1}]")
    )


FIRES = {
    "hoist": ("hoist", doc(I1, RUN_TWICE, C1, steps=16), ["hoist"], lambda t: "!run" not in t),
    "merge instruments": (
        "instruments",
        doc(f"{I1}\n{I2_UNHEARD}", TWO),
        ["merge"],
        lambda t: "i2" not in t,
    ),
    "unhoist": (
        "instruments",
        doc(f"{I1}\n{I2_HEARD}", AS_I2),
        ["unhoist"],
        lambda t: "i2" not in t and "E4!ad:10*2" in t,
    ),
    "merge programs": (
        "programs",
        doc(I1, TWO_RUNS, programs(f"[{B}, {B}, end]")),
        ["merge"],
        lambda t: "c2" not in t,
    ),
    "truncate": (
        "programs",
        doc(ctrl(wave=f"[{A}, {A}, end]"), SCALE),
        ["truncate"],
        lambda t: f"{A}, {A}" not in t,
    ),
    "reloop": (
        "programs",
        doc(ctrl(wave=f"[{A}, {B}, {A}, {B}, {{loop: 0}}]"), "[C4.i1*8]"),
        ["reloop"],
        lambda t: f"{A}, {B}, {A}" not in t,
    ),
    "merge expressions": (
        "expressions",
        doc(I1, FREQS, exprs("v + 32")),
        ["merge"],
        lambda t: "x2" not in t,
    ),
    "alias": (
        "phrases",
        doc(I1, SCALE, extra=row("p3", "[C4*2, D4*2, E4*2, F4*2]")),
        ["alias"],
        lambda t: "p3" not in t,
    ),
    "factor": (
        "phrases",
        doc(I1, f"[{', '.join([PART] * 4)}]", steps=48),
        ["factor"],
        lambda t: t.count("play: [p1, ~, ~]") == 3,
    ),
    "share": ("phrases", shared(I1), ["share"], lambda t: t.count("C6") == 1),
    "commands": (
        "commands",
        doc(I1, "[C4*2, D4*2, E4!ad:9*2, F4*2]"),
        ["drop_command"],
        lambda t: "!ad" not in t,
    ),
    "fields": (
        "fields",
        doc(ctrl(f"ad: 9, {LATE}"), "[C4.i1*2, D4*2, E4*2, F4*2]"),
        ["clear"],
        lambda t: "ad: 9" in t and "φ" not in t,
    ),
}

REFUSES = {
    "hoist": ("hoist", doc(I1, RUN.replace("run:c1", "ctrl:33")), ["hoist"]),
    "merge instruments": ("instruments", doc(f"{I1}\n{I2_HEARD}", TWO), ["merge"]),
    "merge programs": (
        "programs",
        doc(I1, TWO_RUNS, programs(f"[{B}, {Z}, end]")),
        ["merge"],
    ),
    "truncate": ("programs", doc(ctrl(wave=f"[{A}, {B}, end]"), SCALE), ["truncate"]),
    "reloop": ("programs", SHORTEST, ["reloop"]),
    "merge expressions": ("expressions", doc(I1, FREQS, exprs("(v + 33) % 65536")), ["merge"]),
    "alias": (
        "phrases",
        doc(I1, SCALE, extra=row("p3", "[C4*2, D4*2, G4*2, F4*2]")),
        ["alias"],
    ),
    "factor": (
        "phrases",
        doc(PULSED, f"[{', '.join([HEARD_PART] * 4)}]", steps=48),
        ["factor"],
    ),
    "share": ("phrases", shared(PULSED), ["share"]),
    "commands": (
        "commands",
        doc(f"{I1}\n{I2_HEARD}", "[C4.i1*2, D4.i2*2, E4!ad:11*2, F4*2]"),
        ["drop_command", "drop_instrument"],
    ),
    "fields": ("fields", doc(ctrl(f"ad: 9, {LATE}"), "[C4.i1*8]"), ["clear"]),
}


def run(text: str, passes, workers: int = 1):
    return optimise.optimise(songio.loads(text), passes, workers=workers, progress=False)


def kept(result) -> list:
    return [f.edit[0] for f in result.findings if f.kept]


def plays_alike(result) -> bool:
    return optimise.mismatch(optimise.Trace(result.source), songio.loads(result.text)) is None


def test_the_proof_repeats_the_grid_and_finds_the_first_difference(monkeypatch):
    ref = optimise.Trace(songio.loads(doc(I1, SCALE)))
    grid = SongPlayer(songio.loads(doc(I1, SCALE))).render_grid(3000)
    assert 0 < ref.period and ref.mu + ref.period < 3000
    np.testing.assert_array_equal(ref.grid(3000), grid)
    assert ref.row(2999) == tuple(grid[2999])
    assert optimise.mismatch(ref, songio.loads(doc(I1, SCALE))) is None
    tick, reg = optimise.mismatch(ref, songio.loads(doc(I1, SCALE.replace("E4", "G4"))))
    assert tick > ref.starts[0] and reg in (0, 1)
    late = songio.loads(doc(I1, "[-, C4.i1, D4*2, E4*2, F4*2]"))
    assert optimise.mismatch(ref, late)[0] == ref.starts[0]
    monkeypatch.setattr(optimise.reach, "MAX_TICKS", 10)
    assert optimise.mismatch(ref, songio.loads(doc(I1, SCALE)))[0] == "unproved"


@pytest.mark.parametrize("case", FIRES)
def test_a_pass_keeps_the_edits_the_grid_proves(case):
    name, text, ops, written = FIRES[case]
    opt = optimise.Optimiser(songio.loads(text), [name], workers=1)
    before = opt.tokens
    assert opt.keep(name)
    assert sorted(f.edit[0] for f in opt.findings.values() if f.kept) == ops
    assert opt.tokens < before and written(opt.text)
    assert optimise.mismatch(opt.ref, songio.loads(opt.text)) is None


@pytest.mark.parametrize("case", REFUSES)
def test_a_pass_refuses_the_edits_the_grid_hears(case):
    name, text, ops = REFUSES[case]
    ir = songio.loads(text)
    assert {e[0] for e in optimise.PASSES[name](ir)} >= set(ops)
    opt = optimise.Optimiser(ir, [name], workers=1)
    assert not {edit[0] for edit, _ in opt.screen(name)} & set(ops)


UNHOIST_FULL = doc(
    f"{I1}\n{I2_HEARD}",
    AS_I2.replace("E4.i2", "E4.i2!freq:x1!legato"),
    "      x:\n        x1: (v + 32) % 65536\n",
)


UNAPPLIED = [
    (doc(I1, SCALE), ("merge", ("ins", 1, 7))),
    (doc(I1, SCALE), ("alias", ("p1", "p1"))),
    (doc(I1, SCALE), ("factor", ("p1", 3))),
    (doc(I1, SCALE), ("drop_command", ("p1", 0, 0))),
    (doc(I1, SCALE), ("drop_keep", ("p1", 0))),
    (doc(I1, SCALE), ("drop_instrument", ("p1", 1))),
    (doc(I1, SCALE), ("clear", ("ins", 1, "pw"))),
    (doc(I1, SCALE), ("hoist", (1, 17, 0))),
    (doc(I1, RUN.replace("D4!run", "-!run"), C1), ("hoist", (1, 8, 2))),
    (doc(f"{I1}\n{I2_HEARD.replace('ad: 10', 'ad: 10, sr: 5')}", AS_I2), ("unhoist", (1, 2))),
    (UNHOIST_FULL, ("unhoist", (1, 2))),
    (UNHOIST_FULL, ("unhoist", (1, 1))),
    (SHORTEST, ("reloop", ("ctrltab", 1))),
    (SHORTEST, ("truncate", ("ctrltab", 1, 5))),
]


def test_an_edit_refuses_what_it_cannot_apply():
    for text, (op, args) in UNAPPLIED:
        assert optimise.EDITS[op](songio.loads(text), *args) is None, (op, args)


def test_hoist_value_names_the_field_a_command_plays():
    ir = songio.loads(doc(I1, RUN, C1))
    assert optimise.hoist_value(ir, 1, 8, 2)[0] == "ctrl"
    assert optimise.hoist_value(ir, 1, 5, 9) == ("ad", 9)
    assert optimise.hoist_value(ir, 1, 17, 0) is None
    assert optimise.hoist_value(ir, 9, 5, 9) is None


def test_drop_keep_restarts_the_kept_programs():
    ir = songio.loads(doc(I1, SCALE))
    ir["phrases"]["p1"][1]["keep"] = 1
    assert ("drop_keep", ("p1", 1)) in optimise.commands(ir)
    assert "keep" not in optimise.drop_keep(ir, "p1", 1)["phrases"]["p1"][1]
    assert ir["phrases"]["p1"][1]["keep"] == 1


def test_the_driver_is_canonical_and_deterministic():
    text = doc(f"{I1}\n{I2_UNHEARD}", TWO)
    one, two = (run(text, ["instruments", "commands"], workers=w) for w in (1, 2))
    assert one.text == two.text and one.after < one.before
    assert kept(one)[0] == "merge" and "drop_instrument" in kept(one)
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
    song.write_text(doc(I1, "[C4*2, D4*2, E4!ad:9*2, F4*2]"), encoding="utf-8")
    out, report = tmp_path / "small.yaml", tmp_path / "orth.md"
    args = ["-o", str(out), "--report", str(report), "--passes", "commands", "--workers", "1"]
    assert cli.optimise_main([str(song), *args]) == 0
    assert "tokens" in capsys.readouterr().err
    assert "!ad" not in out.read_text(encoding="utf-8")
    assert "E4!ad:9*2" in report.read_text(encoding="utf-8")
    assert cli.optimise_main([str(song), "--passes", "expressions", "--workers", "1"]) == 0
    assert capsys.readouterr().out == songio.dumps(songio.load(song))
    with pytest.raises(SystemExit):
        cli.optimise_main([str(song), "--passes", "nosuch"])


def test_lift_optimise_flag(tmp_path, monkeypatch):
    text = doc(f"{I1}\n{I2_UNHEARD}", TWO)
    monkeypatch.setattr(backends, "lift", lambda data, subtune: songio.loads(text))
    fast = functools.partial(optimise.optimise, passes=["instruments"], workers=1, progress=False)
    monkeypatch.setattr(optimise, "optimise", fast)
    sid, out = tmp_path / "x.sid", tmp_path / "x.yaml"
    sid.write_bytes(b"")
    assert cli.lift_main([str(sid), "-o", str(out), "--optimise"]) == 0
    assert "i2" not in out.read_text(encoding="utf-8")
