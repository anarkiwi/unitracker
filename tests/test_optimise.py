"""The optimiser: the proof, each pass's edits where the grid proves them and refused where it
does not, the driver, and its command lines."""

import functools
import itertools

import irs
import numpy as np
import pytest
from conftest import grid_diff_of

from unitracker import cli, document, optimise
from unitracker.lift import backends
from unitracker.lift.verify import Verdict, compare
from unitracker.optimise.model import walk
from unitracker.player import oracle
from unitracker.player.vm import SongPlayer
from unitracker.playroutine.atoms import note_index

META = {"title": "t", "author": "a", "released": "r"}
RESTART = {
    1: {"hold": 4, "gate": 0, "ad": 15, "sr": 0, "ctrl": -1, "next": 2},
    2: {"ad": -1, "sr": -1, "ctrl": 9, "gate": 1, "next": 0},
}
A, B, Z = 65, 33, 17
AD, CTRL, RUN, CUT, FREQ, LEGATO = 5, 7, 8, 12, 16, 17
PULSE = "16*abs((φ - 32) % 128 - 64) + 512"
LATE = "(φ >= 38)*(F(N + 1) - F(N))"
DRIFT = "(v + 32) % 65536"


def ev(note: str | None = None, steps: int = 1, **fields) -> dict:
    """A phrase event: ``note`` (a name, None for none) held ``steps``, with ``fields``."""
    out = {} if note is None else {"note": note_index(note)}
    return out | ({"steps": steps} if steps != 1 else {}) | fields


def line(notes: str, steps: int = 1, instruments=(), each=None) -> list:
    """Events of the space-separated ``notes`` (``-`` a rest) held ``steps``, each with fields
    ``each`` and the matching one of ``instruments`` (None or past their end: none)."""
    out = []
    for n, i in itertools.zip_longest(notes.split(), instruments):
        out.append(ev(None if n == "-" else n, steps, **(each or {})))
        if i is not None:
            out[-1]["instrument"] = i
    return out


def stepped(notes: str, refs) -> list:
    """Two steps of each of ``notes`` playing ``!freq`` of the matching one of ``refs`` on each
    step, as a command that lasts a step is held."""
    out = []
    for n, ref in zip(notes.split(), refs):
        out += [ev(n, command=[FREQ, ref]), {"command": [FREQ, ref]}]
    return out


def ctrltab(*programs) -> dict:
    """``ctrltab`` records of wave ``programs`` laid end to end from row 1; a program is its
    waves, ending, or ``(waves, k)`` looping to its own ``k``-th row."""
    out = {}
    for prog in programs:
        waves, loop = prog if isinstance(prog, tuple) else (prog, None)
        first = len(out) + 1
        for k, w in enumerate(waves):
            last = k == len(waves) - 1
            to = (0 if loop is None else first + loop) if last else first + k + 1
            out[first + k] = {"n": 0, "w": w, "next": to}
    return out


def ins(ad: int = 9, ctrl: int = 1, **fields) -> dict:
    return {"ad": ad, "ctrl": ctrl, "restart": 1, **fields}


def song(phrase, instruments=None, programs=([A],), exprs=(), steps=8, more=None, **records):
    """A three-voice GoatTracker 2 song looping to its start: voice 1 plays ``phrase`` then each
    phrase of ``more``, the others rest ``steps``; ``instruments`` ``{index: fields}``
    (default one :func:`ins`), wave ``programs`` (see :func:`ctrltab`), ``exprs`` from 1."""
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    more = more or {}
    voices = (["p1", *more], ["p2"], ["p2"])
    return irs.song(
        "goattracker2",
        records={
            "ins": instruments or {1: ins()},
            "restart": RESTART,
            "ctrltab": ctrltab(*programs),
            **records,
        },
        phrases={"p1": phrase, "p2": [ev(steps=steps)], **more},
        tracks=[{"plays": irs.plays(*v), "loop": 0} for v in voices],
        exprs=dict(enumerate(exprs, 1)),
        meta=META,
    )


def with_command(events: list, *commands) -> list:
    """``events`` with the matching one of ``commands`` (None: none) as each one's command."""
    return [e if c is None else e | {"command": list(c)} for e, c in zip(events, commands)]


TWO_INS = {1: ins(), 2: ins(ad=10, ctrl=2)}
UNHEARD_INS = {1: ins(), 2: ins(ctrl=2, freq=1)}
SCALE = line("C4 D4 E4 F4", 2, [1])
RUNS = line("C4 D4 E4 F4", 2, [1], {"command": [RUN, 2]})
RUN_LONG = line(" ".join(["C4 D4 E4 F4"] * 8), 1, [1], {"command": [RUN, 2]})
TWO = line("C4 D4 E4 F4", 2, [1, 2, 1, 2])
AS_I2 = line("C4 D4 E4 F4", 2, [1, None, 2, 1])
TWO_RUNS = with_command(SCALE, *[(RUN, c) for c in (2, 3, 2, 3)])
FREQS = stepped("C4 D4 E4 F4", (1, 2, 1, 2))
FREQS[0]["instrument"] = 1
PART = "C4 D4 E4 F4 G4 A4 B4 C5 D5 E5 F5 G5"
LONG = line("C5 D5 E5 F5 G5 A5 B5 C6 B5 A5 G5 F5", 1, [1])
PULSED = {
    "instruments": {1: ins(pw=1)},
    "exprs": (PULSE,),
    "pulsetab": {1: {"next": 0, "x": 1}},
}
SHORTEST = song([ev("C4", 8, instrument=1)], programs=(([A, B, A, Z], 0),))
UNHOIST_FULL = song(
    SCALE[:2]
    + [ev("E4", commands=[[FREQ, 1], [LEGATO, 0]], instrument=2), {"command": [FREQ, 1]}]
    + line("F4", 2, [1]),
    TWO_INS,
    ([A], [A]),
    (DRIFT,),
)


def shared(**kw) -> dict:
    return song(LONG + line("C4 D4", 2), steps=16, more={"p3": line("C4 D4", 2) + LONG}, **kw)


def heard(ir: dict) -> list:
    """Every event the voices play, each voice twice through its loop."""
    return [e for name, _ in walk(ir) for e in ir["phrases"][name]]


def codes(ir: dict) -> set:
    """Every ``(command, argument)`` an event the voices play carries."""
    return {
        tuple(c)
        for e in heard(ir)
        for c in e.get("commands", [e["command"]] if "command" in e else [])
    }


def program(ir: dict, entry: int) -> tuple:
    """``(waves, loop)`` of the wave program starting at ctrltab row ``entry``."""
    rows, seen, k = ir["records"]["ctrltab"], [], entry
    while k and k not in seen:
        seen.append(k)
        k = rows[k]["next"]
    return [rows[i]["w"] for i in seen], (seen.index(k) if k else None)


def heard_programs(ir: dict) -> list:
    """The wave program of each instrument a played note names, in instrument order."""
    named = sorted({e["instrument"] for e in heard(ir) if "instrument" in e})
    return [program(ir, ir["records"]["ins"][i]["ctrl"]) for i in named]


FIRES = {
    "hoist": (
        "hoist",
        song(RUN_LONG, programs=([A], [B]), steps=32),
        ["hoist"],
        lambda ir, _: not codes(ir) and heard_programs(ir) == [([B], None)],
    ),
    "merge instruments": (
        "instruments",
        song(TWO, UNHEARD_INS, ([A], [A]), (LATE,)),
        ["merge"],
        lambda ir, _: {e.get("instrument") for e in heard(ir)} == {1, None},
    ),
    "unhoist": (
        "instruments",
        song(AS_I2, TWO_INS, ([A], [A])),
        ["unhoist"],
        lambda ir, _: {e.get("instrument") for e in heard(ir)} == {1, None}
        and codes(ir) == {(AD, 10)},
    ),
    "merge programs": (
        "programs",
        song(TWO_RUNS, programs=([A], [B], [B, B])),
        ["merge"],
        lambda ir, _: {c[1] for c in codes(ir)} == {2},
    ),
    "truncate": (
        "programs",
        song(SCALE, programs=([A, A],)),
        ["truncate"],
        lambda ir, _: heard_programs(ir) == [([A], None)],
    ),
    "reloop": (
        "programs",
        song([ev("C4", 8, instrument=1)], programs=(([A, B, A, B], 0),)),
        ["reloop"],
        lambda ir, _: heard_programs(ir) == [([A, B], 0)],
    ),
    "merge expressions": (
        "expressions",
        song(FREQS, exprs=(DRIFT, "v + 32")),
        ["merge"],
        lambda ir, _: {c[1] for c in codes(ir)} == {1},
    ),
    "alias": (
        "phrases",
        song(SCALE, more={"p3": line("C4 D4 E4 F4", 2)}),
        ["alias"],
        lambda ir, _: "p3" not in dict(walk(ir)),
    ),
    "factor": (
        "phrases",
        song(line(" ".join([PART] * 4), 1, ([1] + [None] * 11) * 4), steps=48),
        ["factor"],
        lambda ir, _: [len(ir["phrases"][n]) for n, _ in walk(ir)][:4] == [12] * 4,
    ),
    "share": (
        "phrases",
        shared(),
        ["share"],
        lambda _, text: text.count("C6") == 1,
    ),
    "commands": (
        "commands",
        song(with_command(SCALE, None, None, (RUN, 1))),
        ["drop_command"],
        lambda ir, _: not codes(ir),
    ),
    "fields": (
        "fields",
        song(SCALE, {1: ins(freq=1)}, exprs=(LATE,)),
        ["clear"],
        lambda ir, _: ir["records"]["ins"][1].get("freq", 0) == 0
        and ir["records"]["ins"][1]["ad"] == 9,
    ),
}

REFUSES = {
    "hoist": ("hoist", song(with_command(SCALE, *[(CTRL, B)] * 4)), ["hoist"]),
    "merge instruments": ("instruments", song(TWO, TWO_INS, ([A], [A])), ["merge"]),
    "merge programs": ("programs", song(TWO_RUNS, programs=([A], [B], [B, Z])), ["merge"]),
    "truncate": ("programs", song(SCALE, programs=([A, B],)), ["truncate"]),
    "reloop": ("programs", SHORTEST, ["reloop"]),
    "merge expressions": (
        "expressions",
        song(FREQS, exprs=(DRIFT, "(v + 33) % 65536")),
        ["merge"],
    ),
    "alias": ("phrases", song(SCALE, more={"p3": line("C4 D4 G4 F4", 2)}), ["alias"]),
    "factor": ("phrases", song(line(" ".join(["- C4 D4"] * 4), 4), steps=48, **PULSED), ["factor"]),
    "share": ("phrases", shared(**PULSED), ["share"]),
    "commands": (
        "commands",
        song(with_command(TWO[:2] + line("E4 F4", 2), None, None, (AD, 11)), TWO_INS, ([A], [A])),
        ["drop_command", "drop_instrument"],
    ),
    "fields": (
        "fields",
        song([ev("C4", 8, instrument=1)], {1: ins(freq=1)}, exprs=(LATE,)),
        ["clear"],
    ),
}


def run(ir: dict, passes, workers: int = 1):
    return optimise.optimise(ir, passes, workers=workers, progress=False)


def kept(result) -> list:
    return [f.edit[0] for f in result.findings if f.kept]


def plays_alike(source: dict, ir: dict, text: str) -> tuple[object, Verdict]:
    """``(mismatch, verdict)``: whether ``ir`` replays ``source`` on the VM and document
    ``text`` plays the source's grid on unitracker-player from each voice's first note;
    ``verdict`` is the grids' diff (tools/grid_diff.py's report on it)."""
    native = document.native_state(source)
    grid, _ = oracle.play(document.write(source, native), native.ticks)
    return optimise.mismatch(optimise.Trace(source), ir), compare(text, grid)


UNAPPLIED = [
    (song(SCALE), ("merge", ("ins", 1, 7))),
    (song(SCALE), ("alias", ("p1", "p1"))),
    (song(SCALE), ("factor", ("p1", 3))),
    (song(SCALE), ("drop_command", ("p1", 0, 0))),
    (song(SCALE), ("drop_keep", ("p1", 0))),
    (song(SCALE), ("drop_instrument", ("p1", 1))),
    (song(SCALE), ("clear", ("ins", 1, "pw"))),
    (song(SCALE), ("hoist", (1, 17, 0))),
    (
        song(line("C4 - E4 F4", 2, [1], {"command": [RUN, 2]}), programs=([A], [B])),
        ("hoist", (1, 8, 2)),
    ),
    (song(AS_I2, {1: ins(), 2: ins(ad=10, ctrl=2, sr=5)}, ([A], [A])), ("unhoist", (1, 2))),
    (UNHOIST_FULL, ("unhoist", (1, 2))),
    (UNHOIST_FULL, ("unhoist", (1, 1))),
    (SHORTEST, ("reloop", ("ctrltab", 1))),
    (SHORTEST, ("truncate", ("ctrltab", 1, 5))),
]


def test_the_proof_repeats_the_grid_and_finds_the_first_difference(monkeypatch):
    ref = optimise.Trace(song(SCALE))
    grid = SongPlayer(song(SCALE)).render_grid(3000)
    assert 0 < ref.period and ref.mu + ref.period < 3000
    np.testing.assert_array_equal(ref.grid(3000), grid)
    assert ref.row(2999) == tuple(grid[2999])
    assert optimise.mismatch(ref, song(SCALE)) is None
    tick, reg = optimise.mismatch(ref, song(line("C4 D4 G4 F4", 2, [1])))
    assert tick > ref.starts[0] and reg in (0, 1)
    late = song([ev(), ev("C4", instrument=1)] + line("D4 E4 F4", 2))
    assert optimise.mismatch(ref, late)[0] == ref.starts[0]
    monkeypatch.setattr(optimise.reach, "MAX_TICKS", 10)
    assert optimise.mismatch(ref, song(SCALE))[0] == "unproved"


UNPROVED = {
    "merge expressions": "the writer's re-timing already writes the two expressions as one",
    "unhoist": "the generic document plays a note's !ad at its hard restart a tick apart from"
    " the instrument's ad",
}


@pytest.mark.parametrize(
    "case",
    [
        (
            pytest.param(k, marks=pytest.mark.xfail(strict=True, reason=UNPROVED[k]))
            if k in UNPROVED
            else k
        )
        for k in FIRES
    ],
)
def test_a_pass_keeps_the_edits_the_grid_proves(case, request):
    name, ir, ops, edited = FIRES[case]
    opt = optimise.Optimiser(ir, [name], workers=1)
    before, text = opt.tokens, opt.text
    assert not edited(ir, text) and opt.keep(name)
    assert sorted(f.edit[0] for f in opt.findings.values() if f.kept) == ops
    assert opt.tokens < before and edited(opt.base, opt.text)
    mismatch, verdict = plays_alike(ir, opt.base, opt.text)
    if not verdict.ok:
        grid_diff_of(request, verdict)
    assert mismatch is None and verdict.ok


@pytest.mark.parametrize("case", REFUSES)
def test_a_pass_refuses_the_edits_the_grid_hears(case):
    name, ir, ops = REFUSES[case]
    assert {e[0] for e in optimise.PASSES[name](ir)} >= set(ops)
    opt = optimise.Optimiser(ir, [name], workers=1)
    assert not {edit[0] for edit, _ in opt.screen(name)} & set(ops)
    edited = [optimise.EDITS[op](ir, *args) for op, args in optimise.PASSES[name](ir) if op in ops]
    edited = [e for e in edited if e is not None]
    assert not edited or any(optimise.mismatch(opt.ref, e) is not None for e in edited)


def test_an_edit_refuses_what_it_cannot_apply():
    for ir, (op, args) in UNAPPLIED:
        assert optimise.EDITS[op](ir, *args) is None, (op, args)


def test_hoist_value_names_the_field_a_command_plays():
    ir = song(RUNS, programs=([A], [B]))
    assert optimise.hoist_value(ir, 1, 8, 2)[0] == "ctrl"
    assert optimise.hoist_value(ir, 1, 5, 9) == ("ad", 9)
    assert optimise.hoist_value(ir, 1, 17, 0) is None
    assert optimise.hoist_value(ir, 9, 5, 9) is None


def test_drop_keep_restarts_the_kept_programs():
    ir = song(SCALE)
    ir["phrases"]["p1"][1]["keep"] = 1
    assert ("drop_keep", ("p1", 1)) in optimise.commands(ir)
    assert "keep" not in optimise.drop_keep(ir, "p1", 1)["phrases"]["p1"][1]
    assert ir["phrases"]["p1"][1]["keep"] == 1


def test_the_driver_is_canonical_and_deterministic():
    ir = song(TWO, UNHEARD_INS, ([A], [A]), (LATE,))
    one, two = (run(ir, ["instruments", "commands"], workers=w) for w in (1, 2))
    assert one.text == two.text and one.ir == two.ir and one.after < one.before
    assert kept(one) == ["merge"]
    assert [(f.edit, f.kept) for f in one.findings] == [(f.edit, f.kept) for f in two.findings]
    assert one.source is ir and {e.get("instrument") for e in heard(one.ir)} == {1, None}
    assert plays_alike(ir, one.ir, one.text)


def test_lift_optimise_flag(tmp_path, monkeypatch):
    ir = song(TWO, UNHEARD_INS, ([A], [A]), (LATE,))
    verified = []

    class Lift:
        """A lift of ``ir`` whose verdict is the songs it is asked to verify."""

        document = gap = None
        verdict = Verdict(1, 1, (), None, None)

        @staticmethod
        def run():
            return ir

        @staticmethod
        def verify(song_):
            verified.append(song_)
            Lift.document = "document\n"

    monkeypatch.setattr(backends, "lifter", lambda data, subtune: Lift())
    fast = functools.partial(optimise.optimise, passes=["instruments"], workers=1, progress=False)
    monkeypatch.setattr(optimise, "optimise", fast)
    sid, out = tmp_path / "x.sid", tmp_path / "x.yaml"
    sid.write_bytes(b"")
    assert cli.lift_main([str(sid), "-o", str(out), "--optimise"]) == 0
    assert out.read_text(encoding="utf-8") == "document\n"
    assert 2 in {e.get("instrument") for e in heard(ir)}
    assert {e.get("instrument") for e in heard(verified[0])} == {1, None}
