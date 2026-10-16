"""Keyword composition, register roles, invariants and keyword signatures."""

import pytest

from unitracker.modfit import playroutine, resolve
from unitracker.modfit.cli import main
from unitracker.modfit.compose import bind, can_differ, composer, keywords
from unitracker.modfit.fit import Fitter
from unitracker.modfit.flow import Liveness, flow_of, persistent
from unitracker.modfit.invariant import Modular, fit_under, inductive, invariants
from unitracker.modfit.keywords import Item, signer, verdict


@pytest.fixture(name="sw", scope="module")
def sw_fixture():
    return composer("sidwizard")


def step_of(eff: dict, cell: str):
    """The single proved per-tick accumulator of ``cell`` and its guard."""
    [(guard, found)] = eff["fits"][cell]
    assert set(found) == {"accumulate", "step"}
    return guard, found["step"]


@pytest.mark.parametrize("code, sign", [(1, 1), (2, -1)])
def test_pitch_slides_add_fm_of_arg_and_note(sw, code, sign):
    eff = sw.compose(code, None, "plain")
    f = sw.form
    assert {c: v for _, u in eff["setup_upd"] for c, v in u.items()}["svib"] == 0x80 | code
    guard, step = step_of(eff, "voff")
    assert guard == (f.ne("ci", 0),)
    fm = f.expr("0 if arg == 0 else (fmhi(arg, note + oct) << 8) | fmlo(arg, note + oct)", {})
    assert not can_differ(f, (), f.expr(step, {}), f.scale(sign, fm))


@pytest.mark.parametrize("plain, inst", [(0x20, 0x120), (0x30, 0x130), (0x50, 0x150)])
def test_instrument_column_small_fx_equal_plain_on_rows_without_note(sw, plain, inst):
    a, b = (sw.compose(c, None, "plain", fits=False) for c in (plain, inst))
    assert a["setup_upd"] == b["setup_upd"] and a["tick_upd"] == b["tick_upd"]


def test_attack_loads_the_high_nibble_with_a_sticky_override(sw):
    eff = sw.compose(0x20, None, "plain", fits=False)
    f = sw.form
    upd = {c: (cs, v) for cs, u in eff["setup_upd"] for c, v in u.items()}
    assert upd["aov"] == ((), "arg") and upd["dov"] == ((), -1)
    assert upd["sad"] == ((f.ne("ci", 0),), f.expr("((arg << 4) | (ins[ci].ad & 15)) & 255", {}))


def test_goattracker_ad_loads_the_byte():
    eff = composer("goattracker2").compose(5, None, "plain")
    assert eff["setup_upd"] == [((), {"ad": "arg"})] and not eff["ticks"]


def test_goattracker_toneporta_is_toward_the_note():
    c = composer("goattracker2")
    f = c.form
    fits = dict(c.compose(3, None, "plain")["fits"]["freq"])
    assert fits[(f.eq("arg", 0),)] == {"set": {"freq": "ftab[(note & 127)]"}}
    toward = fits[(f.ne("arg", 0),)]
    assert f.expr(toward["toward"], {}) == f.expr("ftab[note & 127]", {})


def test_defmon_run2_starts_layer_b():
    eff = composer("defmon").compose(1, None, "plain", fits=False)
    upd = {c: v for _, u in eff["setup_upd"] for c, v in u.items()}
    assert upd == {"rowB": "arg", "cntB": 0} and eff["ticks"] == ["procs.layer_b"]


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


@pytest.mark.parametrize(
    "name, cell, role",
    [
        ("goattracker2", "freq", {"FREQ": set(range(16))}),
        ("goattracker2", "filtercutoff", {"CUT": set(range(3, 11))}),
        ("sidwizard", "pwlo", {"PW": set(range(8))}),
        ("sidwizard", "pwhi", {"PW": set(range(8, 12))}),
        ("sidwizard", "fcutlo", {"CUT": {0, 1, 2}}),
        ("defmon", "fv", {"MODEVOL": {4, 5, 6, 7}}),
        ("goattracker2", "tempo", {"TIMING": {0}}),
    ],
)
def test_register_roles(name, cell, role):
    assert flow_of(playroutine(name)).role(cell) == role


def test_pair_role_is_the_12_bit_pulse():
    assert signer("sidwizard").cell_role("pwhi:pwlo") == {"PW": set(range(12))}


def test_filter_sweep_is_one_11_bit_accumulator():
    fitter = Fitter(playroutine("sidwizard"))
    f = fitter.form
    found = dict(fitter.fit(resolve(f.pr, "procs.tick_filt"), "vfhi:vflo:3"))
    sweep = [a for a in found.values() if "accumulate" in a]
    assert f.width["vfhi:vflo:3"] == 11 and len(sweep) == 1
    assert sweep[0]["accumulate"] == "vfhi:vflo:3"
    assert f.expr(sweep[0]["step"], {}) == f.expr("s8(flttab[fpos].b)", {})


def test_vibrato_counter_proved_under_its_invariant():
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


def test_acid_invariant_holds_but_the_reload_reads_the_cell():
    invs = invariants("defmon", "procs.acid", "acc")
    assert invs == ("acc < 32768",)
    fitter = Fitter(playroutine("defmon"))
    actions = resolve(fitter.form.pr, "procs.acid")
    groups = fitter.fit(actions, "acc")
    assert len(groups) == 1 and groups[0][1] is None
    assert fit_under(fitter, actions, "acc", groups[0][0], invs) == (invs[0], None)


def pool(names) -> list:
    out = []
    for name, labels in names.items():
        s = signer(name)
        for code, kw, when in keywords(s.pr):
            if labels is None or kw in labels:
                r = s.keyword(code, when)
                out.append(Item(name, f"!{kw}", r["sig"], r["entries"], "", (code, when)))
    return out


@pytest.fixture(name="items", scope="module")
def items_fixture():
    return pool(
        {
            "goattracker2": {"slideup", "slidedown", "ad", "sr", "mark"},
            "sidwizard": {"pitchup", "pitchdown", "attack", "iattack"},
        }
    )


def find(items, pr, label):
    return next(x for x in items if x.pr == pr and x.label == label)


def test_pitchup_is_slideup_decoded_differently(items):
    matches, v, reason = verdict(find(items, "sidwizard", "!pitchup"), items)
    assert v == "same effect, different argument decoding"
    assert [m.label for m in matches] == ["!slideup"]
    assert reason == "ACC+ note-relative vs ref speedtab"


def test_attack_is_part_of_ad(items):
    matches, v, reason = verdict(find(items, "sidwizard", "!attack"), items)
    assert v == "partial" and [m.label for m in matches] == ["!iattack", "!ad"]
    assert "AD:A vs AD (nibble vs byte)" in reason and "sticky override aov" in reason


def test_iattack_differs_from_attack_on_note_rows(items):
    matches, v, reason = verdict(find(items, "sidwizard", "!iattack"), items)
    assert [m.label for m in matches] == ["!attack", "!ad"] and v == "partial"
    assert reason.startswith("note row: procs.note_setup test")
    assert "pifx != 0" in reason


def test_pitchdown_matches_slidedown(items):
    matches, v, _ = verdict(find(items, "sidwizard", "!pitchdown"), items)
    assert v == "same effect, different argument decoding"
    assert [m.label for m in matches] == ["!slidedown"]


def test_no_effect(items):
    assert verdict(find(items, "goattracker2", "!mark"), items)[1] == "no effect"


def test_field_signatures():
    s = signer("goattracker2")
    assert ("PW", "ACC", "[0:7]→PW") in s.field("pulsetab", "r")["sig"]
    assert ("FREQ+TIMING", "REF", "ref speedtab") in s.field("ins", "speed")["sig"]
    assert ("PW", "PTR", "ref pulsetab") in s.field("ins", "pulse")["sig"]


def test_cli_command_and_keywords(tmp_path):
    out = tmp_path / "cmd.txt"
    main(["--out", str(out), "command", "goattracker2:ad"])
    text = out.read_text()
    assert "== goattracker2 !ad (5), plain row" in text and "setup ad := arg" in text
    main(["--out", str(out), "keywords", "defmon"])
    rows = out.read_text().splitlines()
    assert rows[0].startswith("keyword/field | playroutine") and any(
        r.startswith("!run2") for r in rows
    )


def test_cli_fit_pair_reports_an_invariant(capsys):
    main(["fit", "sidwizard:procs.tick_filt:vfhi:vflo:3", "defmon:procs.acid:acc"])
    out = capsys.readouterr().out
    assert "'accumulate': 'vfhi:vflo:3'" in out and "refuted under invariant acc < 32768" in out
