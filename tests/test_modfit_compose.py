"""Keyword composition, generic factorisation, register roles, invariants and keyword
signatures."""

import pytest

from unitracker.modfit import playroutine, resolve
from unitracker.modfit.compose import Merge, bind, can_differ, composer, keywords
from unitracker.modfit.factor import Factorer, basis, keyword_factors
from unitracker.modfit.fit import Fitter
from unitracker.modfit.flow import Composed, Flow, Liveness, Writes, flow_of, persistent
from unitracker.modfit.form import Form
from unitracker.modfit.generic import Report, Row, grouping, table
from unitracker.modfit.invariant import Modular, Slicer, fit_under, inductive, invariants
from unitracker.modfit.keywords import Item, signer, verdict

PLAYERS = ("goattracker2", "defmon", "sidwizard")
FREQ, PW, BYTE = set(range(16)), set(range(12)), set(range(8))
TOY = {"voice": {"x": {"width": 8}, "t": {"width": 8}}}


@pytest.fixture(name="composers", scope="module")
def composers_fixture() -> dict:
    return {name: composer(name) for name in PLAYERS}


@pytest.fixture(name="units", scope="module")
def units_fixture() -> dict:
    return {name: Factorer(Merge(playroutine(name))) for name in PLAYERS}


def setup_of(eff: dict) -> dict:
    return {c: (cs, v) for cs, u in eff["setup_upd"] for c, v in u.items()}


@pytest.mark.parametrize("code, sign", [(1, 1), (2, -1)])
def test_pitch_slides_add_fm_of_arg_and_note(composers, code, sign):
    sw = composers["sidwizard"]
    eff, f = sw.compose(code, None, "plain"), sw.form
    assert setup_of(eff)["svib"][1] == 0x80 | code
    [(guard, found)] = eff["fits"]["voff"]
    assert set(found) == {"accumulate", "step"} and guard == (f.ne("ci", 0),)
    fm = f.expr("0 if arg == 0 else (fmhi(arg, note + oct) << 8) | fmlo(arg, note + oct)", {})
    assert not can_differ(f, (), f.expr(found["step"], {}), f.scale(sign, fm))


SETUPS = [
    ("goattracker2", 5, {"ad": ((), "arg")}, []),
    ("defmon", 1, {"rowB": ((), "arg"), "cntB": ((), 0)}, ["procs.layer_b"]),
]


def test_setup_updates_and_tick_lists(composers):
    for name, code, expected, ticks in SETUPS:
        eff = composers[name].compose(code, None, "plain", fits=False)
        assert (setup_of(eff), eff["ticks"]) == (expected, ticks), name
    assert not composers["goattracker2"].compose(5, None, "plain")["ticks"]
    sw = composers["sidwizard"]
    f = sw.form
    attack = sw.compose(0x20, None, "plain", fits=False)
    upd = setup_of(attack)
    assert upd["aov"] == ((), "arg") and upd["dov"] == ((), -1)
    assert upd["sad"] == ((f.ne("ci", 0),), f.expr("((arg << 4) | (ins[ci].ad & 15)) & 255", {}))
    for plain, inst in ((0x20, 0x120), (0x30, 0x130), (0x50, 0x150)):
        a = attack if plain == 0x20 else sw.compose(plain, None, "plain", fits=False)
        b = sw.compose(inst, None, "plain", fits=False)
        assert (a["setup_upd"], a["tick_upd"]) == (b["setup_upd"], b["tick_upd"]), hex(inst)


def test_goattracker_toneporta_is_toward_the_note(composers):
    c = composers["goattracker2"]
    f = c.form
    fits = dict(c.compose(3, None, "plain")["fits"]["freq"])
    assert fits[(f.eq("arg", 0),)] == {"set": {"freq": "ftab[(note & 127)]"}}
    toward = fits[(f.ne("arg", 0),)]
    assert toward["accumulate"] == "freq"
    assert f.expr(toward["toward"], {}) == f.expr("ftab[note & 127]", {})


def test_keywords_and_bind():
    assert ("vol", 13, "arg < 0x10") in [
        (n, c, w) for c, n, w in keywords(playroutine("goattracker2"))
    ]
    assert bind([{"set": {"x": "CODE"}}, "CODE + 1"], 7) == [{"set": {"x": 7}}, "7 + 1"]


def test_temporaries_and_persistent_cells():
    pr = playroutine("sidwizard")
    keep = persistent(pr)
    assert {"aov", "oct", "vdc", "voff"} <= keep and not {"t1", "t3", "sel"} & keep
    live, must = Liveness(pr).proc("write_adsr")
    assert {"aov", "dov", "ci"} <= live and must == {"sad", "ssr", "adsrw"}


def same(fz, text: str, source: str, gate=()) -> bool:
    f = fz.form
    return not can_differ(f, gate, f.expr(text, {}), f.expr(source, {}))


def unit(units, name: str, path: str, cell: str) -> list:
    fz = units[name]
    return fz.unit(resolve(fz.form.pr, path), cell)


def rebuilt(fz, factor, cell: str, state: dict) -> int:
    """The rebuilt accumulator's value of ``cell`` from a concrete state."""
    f, w = fz.form, fz.form.width[cell]
    step = f.expr(factor.texts["step"], state)
    v = (state[cell] + step) & ((1 << w) - 1)
    env = {"v0": state[cell], "v": v, "s": step}
    hit = factor.hit != "-" and f.truth(f.expr(factor.hit, state, env))
    return f.expr(factor.respond, state, env) & ((1 << w) - 1) if hit else v


TOWARD = {"hold", "clamp", "reflect"}


def test_goattracker_factors(units):
    fz = units["goattracker2"]
    [slide] = unit(units, "goattracker2", "records.tickn.1.do", "freq")
    assert slide.proved and slide.hit == "-" and same(fz, slide.texts["step"], "spd(cmddata)")
    [pulse] = unit(units, "goattracker2", "record_types.pulsetab.each", "pulse")
    assert pulse.proved
    assert same(fz, pulse.texts["step"], "row.r - 0x100 if row.r >= 0x80 else row.r")
    [cut] = unit(units, "goattracker2", "record_types.filttab.each", "filtercutoff")
    assert cut.proved and cut.texts["step"] == "row.r"
    load, toward = sorted(
        unit(units, "goattracker2", "procs.toneporta", "freq"), key=lambda g: str(g.gate)
    )
    assert load.proved and load.hit == "1" and load.kinds == ("reload",)
    assert toward.proved and set(toward.kinds) == TOWARD
    assert same(fz, toward.texts["step"], "spd(arg)", toward.gate)


def test_sidwizard_factors(units, composers):
    for code in (1, 2):
        [(cell, slide)] = keyword_factors(composers["sidwizard"], code, None)
        assert cell == "voff" and slide.proved and slide.hit == "-"
    [(_, glide)] = keyword_factors(composers["sidwizard"], 3, None)
    assert glide.proved and set(glide.kinds) == TOWARD
    fz = units["sidwizard"]
    [porta] = unit(units, "sidwizard", "procs.tick_porta", "voff")
    assert porta.proved and set(porta.kinds) == TOWARD
    up = rebuilt(fz, porta, "voff", {"voff": 0xFFFF, "fml": 1, "fmh": 0})
    down = rebuilt(fz, porta, "voff", {"voff": 1, "fml": 1, "fmh": 0})
    assert (up, down) == (1, 0)
    for path, cell, field in (
        ("procs.tick_pw", "pwhi:pwlo", "pwtab[ppos].b"),
        ("procs.tick_filt", "vfhi:vflo:3", "flttab[fpos].b"),
    ):
        groups = unit(units, "sidwizard", path, cell)
        assert all(g.proved for g in groups)
        [sweep] = [g for g in groups if g.hit == "-"]
        assert same(fz, sweep.texts["step"], f"s8({field})", sweep.gate)
        assert [g.kinds for g in groups if g.hit == "1"] == [("reload",)]


def test_defmon_factors_and_report_rows(units):
    [slide] = unit(units, "defmon", "procs.pitch_acc", "sacc")
    assert slide.proved and slide.hit == "-"
    pulse = unit(units, "defmon", "procs.pulse", "pw")
    assert len(pulse) == 2 and all(g.proved and g.kinds == ("bounce",) for g in pulse)
    assert all("(v0 >> 8)" in g.hit for g in pulse)
    acid = unit(units, "defmon", "procs.acid_step", "acc")
    assert len(acid) == 2 and not any(g.proved or g.kinds for g in acid)
    rep = Report("defmon")
    rows = rep.record_rows("procs.pulse", "pw")
    assert [(r.kind, r.status) for r in rows] == [("bounce", "proved")] * 2
    assert (
        rep.summary("cnt")
        == "timer cnt (-1): counts [0..127: p + 1] [128..255: 1] ticks for start cnt = p in 0..255"
    )
    acid = rep.record_rows("procs.acid_step", "acc")
    assert [(r.role, r.status) for r in acid] == [("CUT[3:10]", "step reads x")] * 2


FAILURES = [
    ([{"set": {"x": "x * 2"}}], "step reads x"),
    (
        [{"set": {"t": "t + 1"}}, {"accumulate": "x", "step": 1, "max": "t"}],
        "hit reads non-bound state",
    ),
    (
        [
            {"set": {"t": "t + 1"}},
            {"accumulate": "x", "step": 1, "max": 100, "clamp": False,
             "on_bound": [{"set": {"x": "t"}}]},
        ],
        "respond outside (v0, v, bounds)",
    ),
]  # fmt: skip


def test_factor_failures_and_witnesses():
    fz = Factorer(Merge(TOY))
    for actions, reason in FAILURES:
        [factor] = fz.unit(actions, "x")
        assert factor.status == reason and not factor.proved
    paths = fz.paths([{"accumulate": "x", "step": 1}], "x")
    status = fz.prove(paths, "x", (), (), {"step": "2", "hit": "-", "respond": "-"})
    assert status.startswith("witness x=")
    assert fz.prove(paths, "x", (), (), {"step": "1", "hit": "-", "respond": "-"}) == "proved"
    with pytest.raises(ValueError):
        Factorer(Merge({"voice": {"v": {"width": 8}}}))


def test_step_basis_and_report_tables():
    f = Form({"voice": {"x": {"width": 8}, "note": {"width": 8}}, "tables": {"t": [1, 2, 3]}})
    assert basis(3, {"note"}) == "constant"
    assert basis(f.expr("t[note + 1] - t[note]", {}), {"note"}) == "note-relative t"
    assert basis(f.expr("t[arg] + 1", {}), {"note"}) == "decoded arg, t"
    assert basis(f.expr("x * 2", {}), {"note"}) == "cells x"
    rows = [
        Row(p, label, "c", role, "1", "constant", "-", kind, kind, gate, gate, "ok")
        for p, label, role, kind, gate in (
            ("a", "!x", "FREQ", "accumulate", "timer"),
            ("b", "!y", "FREQ", "accumulate", "timer"),
            ("a", "!z", "PW", "bounce", "row duration"),
        )
    ]
    assert grouping(rows)[2] == "FREQ | accumulate | timer | 2 | a:!x, b:!y"
    assert table(rows)[2].startswith("a | !x | c | FREQ")


def test_invariants_prove_the_vibrato_counter_and_not_the_acid_carry():
    invs = invariants("sidwizard", "procs.tick_vibrato", "vfc")
    assert invs == ("vfc <= ((255 + vper) & 255)",)
    pr = playroutine("sidwizard")
    assert not inductive(pr, "vfc <= vper")
    assert Modular(pr, "vfc <= vper").broken() == "procs.tick_vibrato"
    fitter = Fitter(pr)
    actions = resolve(pr, "procs.tick_vibrato")
    for guard, found in fitter.fit(actions, "vfc"):
        assert found is None
        inv, under = fit_under(fitter, actions, "vfc", guard, invs)
        assert inv == invs[0]
        assert under == {"accumulate": "vfc", "step": "255", "max": "((255 + vper) & 255)"}
    assert not invariants("defmon", "procs.acid_step", "acc")
    fitter = Fitter(playroutine("defmon"))
    actions = resolve(fitter.form.pr, "procs.acid_step")
    groups = fitter.fit(actions, "acc")
    assert sorted(g[0] for g in groups) == [(("eq", "cp", 0),), (("not", ("eq", "cp", 0)),)]
    assert all(g[1] is None for g in groups)
    assert all(fit_under(fitter, actions, "acc", g[0], ()) == ("", None) for g in groups)


ROLES = [
    ("flow", "goattracker2", "freq", {"FREQ": FREQ}),
    ("flow", "goattracker2", "filtercutoff", {"CUT": set(range(3, 11))}),
    ("flow", "sidwizard", "pwlo", {"PW": BYTE}),
    ("flow", "sidwizard", "pwhi", {"PW": set(range(8, 12))}),
    ("flow", "sidwizard", "fcutlo", {"CUT": {0, 1, 2}}),
    ("flow", "defmon", "fv", {"MODEVOL": {4, 5, 6, 7}}),
    ("flow", "goattracker2", "tempo", {"TIMING": {0}}),
    ("composed", "goattracker2", "arg", {}),
    ("composed", "goattracker2", "cmddata", {"FREQ": FREQ}),
    ("composed", "goattracker2", "vibtime", {}),
    ("composed", "defmon", "fc", {}),
    ("composed", "defmon", "vinit.sacc", {"FREQ": FREQ}),
    ("composed", "defmon", "vinit.tbase", {"FREQ": FREQ}),
    ("composed", "defmon", "ps", {"PW": PW}),
    ("composed", "sidwizard", "t1", {}),
    ("composed", "sidwizard", "rx", {}),
    ("composed", "sidwizard", "rw", {}),
    ("composed", "sidwizard", "sad", {"AD": BYTE}),
    ("plain", "goattracker2", "masterfader", {"MODEVOL": BYTE}),
    ("plain", "goattracker2", "wave", {}),
]


def test_register_roles():
    analyses = {
        "flow": lambda name: flow_of(playroutine(name)),
        "composed": lambda name: Composed(playroutine(name)),
        "plain": lambda name: Composed(playroutine(name), plain=True),
    }
    built = {}
    for kind, name, cell, role in ROLES:
        if (kind, name) not in built:
            built[kind, name] = analyses[kind](name)
        assert built[kind, name].role(cell) == role, (kind, name, cell)
    assert set(built["composed", "goattracker2"].role("newcmddata")) > {"AD", "SR", "CTRL", "FREQ"}
    assert built["plain", "goattracker2"].bit_range("masterfader") == 0x0F
    assert signer("sidwizard").cell_role("pwhi:pwlo") == {"PW": PW}
    pr = {
        "global": {"a": {"width": 8}, "b": {"width": 8}},
        "tick": [{"set": {"b": "a & 0x70"}}],
        "ghost": {"global": {0x18: "a | (b & 0x70)", 0x17: "b | 0x0F"}},
    }
    flow = Flow(pr)
    assert flow.role("a") == {"MODEVOL": BYTE, "RESFILT": {4, 5, 6}}
    assert flow.role("b") == {"MODEVOL": {4, 5, 6}, "RESFILT": {4, 5, 6, 7}}


TEST = ("-", "TEST", "-")
FIELDS = [
    ("goattracker2", "pulsetab", "r", {TEST, ("PW", "ACC", "[0:7]→PW")}, True),
    ("goattracker2", "pulsetab", "next", {TEST, ("PW", "PTR", "ref pulsetab")}, True),
    ("goattracker2", "ins", "speed", {("FREQ+TIMING", "REF", "ref speedtab")}, False),
    ("goattracker2", "ins", "pw", {("PW", "PTR", "ref pulsetab")}, False),
    ("defmon", "sidtab", "wgl", {("CTRL", "MASK", "[0:7]→CTRL")}, False),
    ("defmon", "sidtab", "wgh", {("CTRL", "MASK", "[0:7]→CTRL")}, False),
    ("defmon", "ginit", "sub", {TEST}, True),
]
KEYWORDS = [
    ("goattracker2", 4, None, {("FREQ", "ARM", "-"), ("FREQ", "REFLECT", "ref speedtab")}),
    ("goattracker2", 13, "arg < 0x10", {("MODEVOL", "LOAD", "[0:7]→MODEVOL")}),
]


def test_field_and_keyword_signatures():
    for name, record, field, sig, exact in FIELDS:
        got = signer(name).field(record, field)["sig"]
        assert got == sig if exact else sig <= got, (record, field, got)
    for name, code, when, sig in KEYWORDS:
        assert signer(name).keyword(code, when)["sig"] == sig, code


def test_slicer_keeps_a_branch_return_before_kept_actions():
    pr = playroutine("sidwizard")
    sliced, _ = Slicer(pr, Writes(pr), dict(pr["procs"])).seq(
        resolve(pr, "procs.tick_vibrato"), {"vfc"}, {"vfc"}
    )
    branch = next(a for a in sliced if a.get("if") == "svib == 0")
    assert [b["if"] for b in branch["else"]] == ["svib != 0x10", "vdc >= 0"]


def test_keyword_verdicts():
    labels = {"goattracker2": {"freq", "ad", "sr", "mark"}, "sidwizard": {"freq", "ad"}}
    items = []
    for name, wanted in labels.items():
        s = signer(name)
        for code, kw, when in keywords(s.pr):
            if kw in wanted:
                r = s.keyword(code, when)
                items.append(Item(name, f"!{kw}", r["sig"], r["entries"], "", (code, when)))

    def find(pr, label, code=None):
        return next(x for x in items if (x.pr, x.label) == (pr, label) and code in (None, x.key[0]))

    for pr, label in (("sidwizard", "!freq"), ("goattracker2", "!mark")):
        matches, v, reason = verdict(find(pr, label), items)
        assert (matches, v, reason) == ([], "no effect", "no register, timing or table effect")
    matches, v, reason = verdict(find("sidwizard", "!ad", 0x120), items)
    assert [(m.pr, m.key[0]) for m in matches] == [("sidwizard", 0x20), ("goattracker2", 5)]
    assert v == "partial" and reason.startswith("note row: procs.note_setup test")
    assert "pifx != 0" in reason and "AD:A vs AD (nibble vs byte)" in reason
    assert "sticky override aov" in reason
