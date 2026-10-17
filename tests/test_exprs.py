"""v1.19 expressions: each key and command drives its cell as docs/format.md says."""

import numpy as np
import pytest

from unitracker import backends, songio, xexpr
from unitracker.vm import SongError, SongPlayer

GT = songio.library("goattracker2")["tables"]
SW = songio.library("sidwizard")["tables"]
DM = songio.library("defmon")["tables"]
# A plain gated instrument whose hard restart turns the gate off two ticks ahead.
INS = (
    "{sr: 240, ctrl: [{ctrl: 65, note: +0}, end], "
    "restart: [{hold: 2, gate: 0, ad: 15, sr: 0}, {ctrl: 9, gate: 1}, end]"
)


def voice0(text, ticks):
    """Voice 0's frequency and pulse-width words and control register per tick."""
    grid = SongPlayer(songio.loads(text)).render_grid(ticks).astype(int)
    return grid[:, 0] | grid[:, 1] << 8, grid[:, 2] | grid[:, 3] << 8, grid[:, 4]


def moves(words):
    """The nonzero tick-to-tick changes of a register word."""
    return [int(d) for d in np.diff(words) if d]


def doc(playroutine, defs, phrase, extra=""):
    return f"""
unitracker: 1.19
playroutine: {{ref: {playroutine}}}
song:
  - def:
{extra}{defs}
      phrase:
        p1: {phrase}
    play: [p1, p1, p1]
loop: ~
"""


GT_SONG = doc(
    "goattracker2, constants: {deftempo: 6}",
    """      ins:
        i1: """
    + INS
    + """, pw: '1024 + 16*min(3, φ)'}
      x:
        x1: (v + 300) % 65536
        x2: 0""",
    "[C4.i1, -!freq:@x1, -!freq:@x1, D4!legato:0!freq:@x2, -]",
)


@pytest.mark.parametrize(
    "playroutine,ins,match",
    [
        ("goattracker2", "{sr: 240, gt: 2}", "ins.gt is retired in v1.8: write the hard restart"),
        ("goattracker2", "{fw: 9}", "ins.fw is retired in v1.8"),
        ("sidwizard", "{hrad: 15}", "ins.hrad is retired in v1.8"),
        ("sidwizard", "{hrsr: 0}", "ins.hrsr is retired in v1.8"),
        ("sidwizard", "{firstctrl: 9}", "ins.firstctrl is retired in v1.8"),
        ("sidwizard", "{flags: 26}", "ins.flags is retired in v1.13: its bits are"),
        ("sidwizard", "{flags: 16}", "ins.flags is retired in v1.13"),
        ("sidwizard", "{vibrato: 51}", "ins.vibrato is retired in v1.11: write the vibrato"),
        ("sidwizard", "{vibdelay: 4}", "ins.vibdelay is retired in v1.11"),
        ("sidwizard", "{octave: -12}", "ins.octave is retired in v1.11: a note is written as"),
        ("sidwizard", "{flags: 192}", "ins.flags is retired in v1.13"),
        ("goattracker2", "{speed: 1}", "ins.speed is retired in v1.16: write the vibrato as"),
        ("goattracker2", "{vd: 3}", "ins.vd is retired in v1.16"),
    ],
)
def test_the_reader_names_retired_instrument_keys(playroutine, ins, match):
    with pytest.raises(songio.SongFormatError, match=match):
        songio.loads(doc(playroutine, f"      ins:\n        i1: {ins}", "[C4.i1]"))


@pytest.mark.parametrize(
    "playroutine,command", [("sidwizard", "!notevib:5"), ("goattracker2", "!vib:@s1")]
)
def test_the_reader_names_the_retired_vibrato_commands(playroutine, command):
    text = doc(playroutine, "      ins:\n        i1: {sr: 240}", f"[C4.i1, -{command}]")
    name = command.split(":")[0]
    with pytest.raises(songio.SongFormatError, match=f"{name} is retired in v1.11: write"):
        songio.loads(text)


@pytest.mark.parametrize(
    "fields,match",
    [
        ({"vibrato": 0x33}, "ins.vibrato has no written form: it is retired in v1.11"),
        ({"vibdelay": 4}, "ins.vibdelay has no written form"),
        ({"octave": -12}, "ins.octave has no written form"),
        ({"flags": 0x20}, "ins.flags has no written form: it is retired in v1.13"),
        ({"flags": 0x11}, "ins.flags has no written form"),
    ],
)
def test_the_writer_refuses_the_retired_header_bytes(fields, match):
    song = songio.loads(SW_SONG)
    song["records"]["ins"][1].update(fields)
    with pytest.raises(songio.SongFormatError, match=match):
        songio.dumps(song)


def test_the_speed_table_is_retired():
    text = doc("goattracker2", "      speedtab:\n        s1: [4, 32]", "[C4]")
    with pytest.raises(songio.SongFormatError, match="record type 'speedtab' is retired in v1.16"):
        songio.loads(text)


@pytest.mark.parametrize(
    "fields,match",
    [
        ({"vd": 3}, "ins.vd has no written form: it is retired in v1.16"),
        ({"speed": 1}, "ins.speed"),
    ],
)
def test_the_writer_refuses_the_goattracker_instrument_vibrato(fields, match):
    song = songio.loads(GT_SONG)
    song["records"]["ins"][1].update(fields)
    song["records"]["speedtab"] = {1: {"l": 0, "r": 32}}
    with pytest.raises(songio.SongFormatError, match=match):
        songio.dumps(song)


@pytest.mark.parametrize(
    "row,match",
    [
        ("{ctrl: 64, wgl: 1}", "ctrl: wgl is retired in v1.8: write"),
        ("{ctrl: 64, af: 12}", "ctrl: af is retired in v1.11: write the pitch offset"),
    ],
)
def test_the_reader_names_the_retired_sidtab_columns(row, match):
    text = doc("defmon", f"      ins:\n        i1: {{ctrl: [{row}, end]}}", "[C4.i1]")
    with pytest.raises(songio.SongFormatError, match=match):
        songio.loads(text)


@pytest.mark.parametrize(
    "key,row",
    [("pw", "{pw: 2048, k: 5}"), ("filter", "{mode: 16, res: 3, cut: 64, k: 5}")],
)
def test_the_reader_names_the_retired_keyboard_track_field(key, row):
    text = doc("sidwizard", f"      ins:\n        i1: {{sr: 240, {key}: [{row}, end]}}", "[C4.i1]")
    with pytest.raises(songio.SongFormatError, match="k is retired in v1.19: a keyboard-tracked"):
        songio.loads(text)


def test_funk_arguments_share_their_speed_table_rows():
    text = doc("goattracker2", "      ins:\n        i1: {sr: 240}", "[C4.i1!funk:6/9, -!funk:6/9]")
    song = songio.loads(text.replace("-!funk:6/9]", "-!funk:6/9, -!funk:3/4, -!funk:none]"))
    assert song["records"]["speedtab"] == {1: {"l": 6, "r": 9}, 2: {"l": 3, "r": 4}}
    cmds = [e.get("command") or e["commands"][-1] for e in song["phrases"]["p1"]]
    assert cmds == [[14, 1], [14, 1], [14, 2], [14, 0]]
    assert "-!funk:3/4, -!funk:none]" in songio.dumps(song)


@pytest.mark.parametrize(
    "command,match",
    [
        ("!funk:6", r"!funk: the argument is 2 values, l/r"),
        ("!funk:6/256", r"!funk: each value is a byte"),
        ("!run:6/9", r"!run: the command takes no row argument"),
    ],
)
def test_the_reader_refuses_a_bad_row_argument(command, match):
    with pytest.raises(songio.SongFormatError, match=match):
        songio.loads(doc("goattracker2", "      ins:\n        i1: {sr: 240}", f"[C4.i1{command}]"))


def test_goattracker_freq_command_pulse_expression_and_legato():
    freq, pulse, ctrl = voice0(GT_SONG, 34)
    c4, d4 = GT["ftab"][48], GT["ftab"][50]
    assert moves(freq) == [c4] + [300] * 12 + [d4 - c4 - 3600]
    assert moves(pulse)[:4] == [1024, 16, 16, 16]
    assert set(ctrl[9:34]) == {65}
    dropped = GT_SONG.replace("D4!legato:0!freq:@x2", "D4!freq:@x2")
    assert {64, 9} <= set(voice0(dropped, 34)[2][9:34])


def test_constant_and_text_expressions_read_back_as_written():
    song = songio.loads(GT_SONG)
    assert sorted(song["exprs"].values()) == ["(v + 300) % 65536", "0", "1024 + 16*min(3, φ)"]
    text = songio.dumps(song)
    assert "pw: '1024 + 16*min(3, φ)'" in text and "!legato:0!freq:@x2" in text
    assert songio.dumps(songio.loads(text)) == text
    const = songio.loads(text.replace("pw: '1024 + 16*min(3, φ)'", "pw: 2048"))
    assert "2048" in const["exprs"].values() and "pw: 2048" in songio.dumps(const)


SW_SONG = doc(
    "sidwizard",
    """      ins:
        i1: {sr: 240, ctrl: [{ctrl: 65, note: +0}, end], pw: '2048 + 2*min(4, φ)',
          freq: '[φ >= 2]*W[N + 25]'}""",
    "[C4.i1*4, -!freq:@x1*4]",
    "      x:\n        x1: (v - 7) % 65536\n",
)


def test_sidwizard_instrument_freq_and_pulse_and_a_freq_command():
    freq, pulse, _ = voice0(SW_SONG, 40)
    c4 = 48 + 1
    base = SW["freqlo"][c4] | SW["freqhi"][c4] << 8
    vib = SW["exptab"][c4 + 24] if c4 + 24 < 107 else 0
    assert vib and vib in moves(freq) and moves(freq)[-6:] == [-7] * 6
    assert int(freq[23]) - base in (vib, vib + 1)
    assert [int(d) for d in np.diff(pulse)[3:9] if d] == [2, 2, 2, 2]


SW_TRACKED = doc(
    "sidwizard",
    """      ins:
        i1: {sr: 240, ctrl: [{ctrl: 65, note: +0}, end], pw: '2048 + 256*(E[N + 6] - E[N + 5])'}""",
    "[C2.i1*8, C6.i1*8]",
)


def test_a_sidwizard_pulse_expression_reads_the_keyboard_track_table():
    _, pulse, _ = voice0(SW_TRACKED, 120)
    step = [SW["freqhi"][n + 6 - 11] - SW["freqhi"][n + 5 - 11] for n in (24, 72)]
    assert step[0] != step[1]
    assert {2048 + 256 * d for d in step} <= {int(p) for p in pulse}


DM_SONG = doc(
    "defmon",
    """      ins:
        i1: {ctrl: [{ctrl: 65, pw: 256 + 10*φ, freq: (v - 139) % 65536}, end]}""",
    "[C4.i1*8]",
    "      vinit: {0: {}, 1: {}, 2: {}}\n      ginit: {0: {}}\n",
)


def test_defmon_row_expressions_drive_pulse_and_pitch():
    freq, pulse, _ = voice0(DM_SONG, 10)
    assert moves(pulse)[1:5] == [10] * 4
    assert moves(freq)[1:5] == [-139] * 4


@pytest.mark.parametrize(
    "old,new,match",
    [
        ("unitracker: 1.19", "unitracker: 1.8", "not a unitracker v1.19 document"),
        (
            "      x:\n",
            "      mod:\n        m1: {reg: freq, step: 300}\n      x:\n",
            "'mod' is retired",
        ),
        ("-!freq:@x1, -!freq:@x1,", "-!mod:@m1, -!freq:@x1,", "!mod is retired in v1.5"),
        ("-!freq:@x1, -!freq:@x1,", "-!noteglide:0, -!freq:@x1,", "can't express !noteglide"),
        ("pw: '1024 + 16*min(3, φ)'", "pw: [{pw: 1024}, {step: 16, for: 3}, end]", "keys"),
        ("x1: (v + 300) % 65536", "x1: v / 2", "outside the grammar"),
        ("x1: (v + 300) % 65536", "x1: W[N]", "unknown table 'W'"),
        ("x1: (v + 300) % 65536", "x1: '[0 < v < 9]'", "not one comparison"),
        ("x1: (v + 300) % 65536", "x1: sqrt(v)", "calls no grammar function"),
        ("x1: (v + 300) % 65536", "x1: (v + ", "bad expression"),
        ("-!freq:@x1, -!freq:@x1,", "-!freq:@x9, -!freq:@x1,", "unknown name 'x9'"),
    ],
)
def test_reader_refuses_old_forms_and_text_outside_the_grammar(old, new, match):
    assert old in GT_SONG
    with pytest.raises(songio.SongFormatError, match=match):
        songio.loads(GT_SONG.replace(old, new, 1))


def test_expressions_compile_to_the_grammar_semantics():
    ns = {"_F": lambda k: 1000 + k, "_T": {"W": lambda i: 2 * i}}
    f = xexpr.compile_expr("[v >= 3]*(F(N) % 7) - abs(-φ) + max(W[2], 1) + min(v, -3)", ns)
    assert f(3, 2, 4) == (1004 % 7) - 2 + 4 - 3 and f(2, 0, 4) == 4 - 3 + 1 - 1
    assert xexpr.written("-5") == -5 and xexpr.written("v") == "v"
    with pytest.raises(xexpr.ExpressionError, match="string or an integer"):
        xexpr.text_of(True)
    with pytest.raises(xexpr.ExpressionError, match="takes 2"):
        xexpr.source("min(v)")


def test_floor_division_floors_toward_minus_infinity():
    f = xexpr.compile_expr("φ // 30 - v // 4", {"_T": {}})
    assert [f(-5, phi, 0) for phi in (0, 29, 30, 61)] == [2, 2, 3, 4]


@pytest.mark.parametrize("key,value", [("cut", 1024), ("cut", 0)])
def test_a_constant_expression_in_a_filter_row_reads_back_as_written(key, value):
    ins = INS + ", filter: "
    rows = f"[{{{key}: '{value}', res: 12, route: 1, mode: 16}}, {{{key}: 512}}, end]"
    text = songio.dumps(songio.loads(GT_SONG.replace(INS + ",", ins + rows + ",", 1)))
    assert f"{key}: '{value}'" in text and f"{key}: 512" in text
    assert songio.dumps(songio.loads(text)) == text


def test_sidwizard_constant_cut_expression_reads_back_as_written():
    ins = "freq: '[φ >= 2]*W[N + 25]'"
    rows = ins + ", filter: [{cut: '1024', res: 0, mode: 16}, {cut: 512, res: 3, mode: 32}, end]"
    text = songio.dumps(songio.loads(SW_SONG.replace(ins, rows, 1)))
    assert "cut: '1024'" in text and "cut: 512" in text
    assert songio.dumps(songio.loads(text)) == text


def test_a_command_naming_an_expression_past_a_byte_is_refused():
    many = "".join(f"        y{k}: {k}\n" for k in range(300))
    text = GT_SONG.replace("      x:\n", "      x:\n" + many, 1)
    text = text.replace("-!freq:@x1, -!freq:@x1", "-!freq:@y1, -!freq:@y299", 1)
    with pytest.raises(songio.SongFormatError, match="argument is a byte"):
        songio.loads(text)


def test_a_selected_expression_must_exist():
    song = songio.loads(GT_SONG)
    song["exprs"] = {1: "v"}
    with pytest.raises(SongError, match="undefined"):
        SongPlayer(song).render_grid(20)
    song["exprs"] = {1: "v //"}
    with pytest.raises(SongError, match="x1"):
        SongPlayer(song)


# ---- the reference songs: a wrong constant or a dropped flag breaks the grid -------------


def lifted(path):
    raw = path.read_bytes()
    return raw, songio.dumps(backends.lift(raw))


def native(raw, ticks):
    return np.asarray(backends.native_player(raw).render_grid(ticks), dtype=np.uint8)


def mismatch(text, theirs):
    """The first ``(tick, register)`` where the song text plays unlike the native grid."""
    return SongPlayer(songio.loads(text)).mismatch(theirs)


def test_metal_warrior_one_off_expression_and_dropped_legato_break_the_grid(metal_warrior):
    raw, text = lifted(metal_warrior)
    want = native(raw, 3840)
    assert mismatch(text, want) is None
    assert "x1: (v + 32) % 65536" in text and "C4!legato:0!freq:@x2" in text
    off = text.replace("x1: (v + 32) % 65536", "x1: (v + 33) % 65536")
    assert mismatch(off, want) is not None
    dropped = text.replace("C4!legato:0!freq:@x2", "C4!freq:@x2", 1)
    assert mismatch(dropped, want) is not None


def test_csillag_wrong_delay_constant_breaks_the_grid(csillag):
    raw, text = lifted(csillag)
    gate = "'[φ >= 15]*((-W[N + 33]*abs((φ + 3) % 8 - 4) + 2*W[N + 33]) % 65536)'"
    assert gate in text
    later = text.replace(gate, gate.replace("[φ >= 15]", "[φ >= 14]"))
    assert mismatch(later, native(raw, 2304 * 6)) is not None


def test_crosseyed_wrong_acid_step_breaks_the_grid(crosseyed):
    raw, text = lifted(crosseyed)
    want = native(raw, 95 + 2 * 1152 + 100)
    assert mismatch(text, want) is None
    step = "v % 65536 + 2560)*[v % 65536 < 62976]"
    assert step in text
    assert mismatch(text.replace(step, "v % 65536 + 2304)*[v % 65536 < 62976]"), want) is not None
