"""Bounce orbits, fused pair fits and table programs stepped as generic accumulators."""

import pytest

from unitracker.modfit import playroutine, resolve
from unitracker.modfit.bounce import Bounce, iv_values, phase_text, phase_value
from unitracker.modfit.cli import check_lines, main
from unitracker.modfit.fit import Fitter
from unitracker.modfit.form import Form
from unitracker.modfit.orbit import deltas
from unitracker.modfit.sweeps import Run, Stepper, entries, simulate, song_lines, verdict

GT_PULSE = {"record": "pulsetab", "path": "procs.pulse_exec", "cell": "pulse", "pointer": "pptr",
            "state": {"ptime": 0}}  # fmt: skip
SW_PULSE = {"record": "pwtab", "path": "procs.tick_pw", "cell": "pwhi:pwlo", "pointer": "ppos",
            "state": {"pwcnt": 0}, "instrument": "ci"}  # fmt: skip


@pytest.fixture(name="bounce", scope="module")
def bounce_fixture() -> Bounce:
    return Bounce("defmon", "procs.pulse", "pw")


def test_phase_forms_agree_with_their_values():
    assert phase_text(1, 4095, 1, "m") == ("4094//m + 1", "4095 - 4094 % m")
    assert phase_text(-1, 0, "x", "d") == ("x//d + 1", "x % d")
    assert phase_text(-1, 5, "x", "d") == ("(x - 5)//d + 1", "5 + (x - 5) % d")
    assert phase_value(1, 4095, 1, 5) == (4094 // 5 + 1, 4095 - 4094 % 5)
    assert phase_value(-1, 0, 4088, 7) == (4088 // 7 + 1, 4088 % 7)
    assert iv_values(("lin", 65535, ((("iv", ("eq", "x", 0)), -61440),))) == {65535, 4095}
    assert not iv_values(("lin", 1, (("x", 1),)))


def test_defmon_pulse_bounce_phases(bounce):
    assert (bounce.mode, bounce.flip, bounce.kept) == ("ps", 128, 127)
    assert {4095, 0} <= set(bounce.thresholds()) and bounce.reloads() == [1, 4088]
    phases, const, bad = bounce.phases()
    assert not bad and sorted((b, ms) for b, ms in const) == [(0, [0]), (1, [0])]
    up, down = sorted(phases, key=lambda p: -p.sign)
    assert (up.bit, up.bound, up.reload, up.wraps, up.ms) == (
        1,
        4095,
        4088,
        True,
        list(range(1, 128)),
    )
    assert (down.bit, down.bound, down.reload) == (0, 0, 1)
    lines = bounce.cycle_lines(phases)
    assert lines[0] == "   cycle m in 1..127: period 4094//m + 1 + 4088//(1 + -1 * fc + m) + 1"
    assert "max(4088, 4095 - 4094 % m)" in lines[1] and lines[-1].endswith("bounds equal")


@pytest.mark.parametrize("m, fc", [(3, 0), (64, 1), (127, 0)])
def test_bounce_closed_forms_match_the_vm_semantics(m, fc):
    pr = playroutine("defmon")
    d = m + 1 - fc
    period = 4094 // m + 1 + 4088 // d + 1
    steps = deltas(Form(pr), resolve(pr, "procs.pulse"), {"pw": 1, "ps": m | 128, "fc": fc}, "pw",
                   2 * period)  # fmt: skip
    values = (1 + steps.cumsum()).tolist()
    assert values[period - 1] == 1 and values[2 * period - 1] == 1
    assert min(values) == min(1, 4088 % d) and max(values) == max(4088, 4095 - 4094 % m)
    assert steps[0] == m and 1 not in values[: period - 1]


def test_fused_pair_fit_is_one_accumulate():
    pr = playroutine("sidwizard")
    fitter = Fitter(pr)
    fits = fitter.fit(resolve(pr, "procs.tick_pw"), "pwhi:pwlo")
    assert [f["accumulate"] for _, f in fits if f and "accumulate" in f] == ["pwhi:pwlo"]
    lines = check_lines(
        "sidwizard",
        "procs.tick_pw",
        "pwhi:pwlo",
        {"accumulate": "pwhi:pwlo", "step": "pwtab[ppos].b"},
        "ci != 0 and ppos != 0 and pwtab[ppos].a < 0x80 and pwcnt < pwtab[ppos].a",
    )
    assert lines[1].endswith("refuted") and lines[2].startswith("      witness")


def gt_song(rows: dict, **extra) -> dict:
    pr = playroutine("goattracker2")
    records = {"pulsetab": rows, "ins": {1: {"pulse": 1}}}
    return {"playroutine": pr, "records": records, "phrases": extra.get("phrases", {}),
            "tables": {}}  # fmt: skip


def program(song: dict, spec: dict, start: int = 1, context=None) -> str:
    stepper = Stepper(song, spec)
    return verdict(stepper.run(start, context or {}), stepper.top)


def setrow(pw: int, nxt: int) -> dict:
    return {"l": 0x80 | (pw >> 8), "r": pw & 0xFF, "next": nxt}


@pytest.mark.parametrize(
    "rows, expected",
    [
        (
            {
                1: setrow(1024, 2),
                2: {"l": 8, "r": 16, "next": 3},
                3: {"l": 16, "r": 240, "next": 4},
                4: {"l": 8, "r": 16, "next": 2},
            },
            "accumulate step 16 [896, 1152] reflect, first +16",
        ),
        (
            {1: setrow(512, 2), 2: {"l": 4, "r": 8, "next": 1}},
            "accumulate step 8 [-, 544] reload 512",
        ),
        ({1: setrow(100, 2), 2: {"l": 4, "r": 3, "next": 2}}, "accumulate step 3 wrap"),
        ({1: setrow(100, 2), 2: {"l": 4, "r": 253, "next": 0}}, "accumulate step -3 [88, -] clamp"),
        (
            {1: setrow(100, 2), 2: {"l": 4, "r": 3, "next": 3}, 3: {"l": 2, "r": 5, "next": 0}},
            "not one accumulator: step sizes +3 x4, +5 x2",
        ),
        (
            {
                1: setrow(100, 2),
                2: {"l": 4, "r": 3, "next": 3},
                3: {"l": 2, "r": 253, "next": 4},
                4: {"l": 1, "r": 3, "next": 2},
            },
            "step size 3 turning at 112, 106, 121, 115, not two bounds",
        ),
        ({1: setrow(100, 0)}, "set 100, then holds"),
        ({1: {"l": 4, "r": 3, "next": 0}}, "starts from the incoming value"),
        (
            {1: setrow(100, 2), 2: setrow(200, 3), 3: {"l": 2, "r": 1, "next": 1}},
            "not one accumulator: 2 set rows in the cycle",
        ),
        (
            {1: setrow(100, 2), 2: {"l": 2, "r": 1, "next": 3}, 3: {"l": 1, "r": 2, "next": 1}},
            "steps +1, +2 between reloads",
        ),
    ],
)
def test_goattracker_pulse_programs(rows, expected):
    assert expected in program(gt_song(rows), GT_PULSE)


def test_a_zero_length_sweep_row_holds_forever():
    text = program(gt_song({1: setrow(100, 2), 2: {"l": 0, "r": 3, "next": 0}}), GT_PULSE)
    assert text.startswith("set 100, then holds") and "cycle of 1 ticks: holds 1" in text


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
    song = {"playroutine": pr, "records": {"pwtab": rows, "ins": {1: {"pulse": 1}}}, "tables": {}}
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


def test_reference_songs(metal_warrior, csillag):
    gt = "\n".join(song_lines(metal_warrior))
    assert "row 1 (ins 1): set 1024, then accumulate step 16 [512, 1536] reflect, first +16" in gt
    assert "row 6 (ins 3): set 512, then accumulate step 40 [272, 2032] reflect, first +40" in gt
    sw = "\n".join(song_lines(csillag))
    assert "not one accumulator: step sizes +2 x64, -8 x64" in sw
    assert "set 128, then accumulate step 1 [-, 240] clamp" in sw


def test_cli_sweeps(tmp_path, monkeypatch):
    monkeypatch.setattr("unitracker.modfit.sweeps.bounce_lines", lambda units: ["== bounce"])
    out = tmp_path / "sweeps.txt"
    main(["--out", str(out), "sweeps"])
    text = out.read_text()
    assert text.startswith("# Bounce orbits\n\n== bounce") and "# Table programs" in text
    assert "s8(pwtab[ppos].b)'} when ci != 0" in text and text.count(": proved") == 1
