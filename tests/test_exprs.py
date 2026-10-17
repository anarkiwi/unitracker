"""v1.5 expressions: each key and command drives its cell as docs/format.md says."""

import numpy as np
import pytest

from unitracker import backends, songio, xexpr
from unitracker.vm import SongError, SongPlayer

GT = songio.library("goattracker2")["tables"]
SW = songio.library("sidwizard")["tables"]
DM = songio.library("defmon")["tables"]


def voice0(text, ticks):
    """Voice 0's frequency and pulse-width words and control register per tick."""
    grid = SongPlayer(songio.loads(text)).render_grid(ticks).astype(int)
    return grid[:, 0] | grid[:, 1] << 8, grid[:, 2] | grid[:, 3] << 8, grid[:, 4]


def moves(words):
    """The nonzero tick-to-tick changes of a register word."""
    return [int(d) for d in np.diff(words) if d]


def doc(playroutine, defs, phrase, extra=""):
    return f"""
unitracker: 1.5
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
        i1: {sr: 240, ctrl: [{ctrl: 65, note: +0}, end], gt: 2, fw: 9, pw: '1024 + 16*min(3, φ)'}
      x:
        x1: (v + 300) % 65536
        x2: F(N)""",
    "[C4.i1, -!freq:@x1, -!freq:@x1, D4!legato:0!freq:@x2, -]",
)


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
    assert sorted(song["exprs"].values()) == ["(v + 300) % 65536", "1024 + 16*min(3, φ)", "F(N)"]
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


DM_SONG = doc(
    "defmon",
    """      sidtab:
        t1: [{ctrl: 65, pw: 256 + 10*φ, freq: (v - 139) % 65536}, end]""",
    "[C4.t1*8]",
    "      vinit: {0: {}, 1: {}, 2: {}}\n      ginit: {0: {}}\n",
)


def test_defmon_row_expressions_drive_pulse_and_pitch():
    freq, pulse, _ = voice0(DM_SONG, 10)
    assert moves(pulse)[1:5] == [10] * 4
    assert moves(freq)[1:5] == [-139] * 4


@pytest.mark.parametrize(
    "old,new,match",
    [
        ("unitracker: 1.5", "unitracker: 1.4", "not a unitracker v1.5 document"),
        ("      x:\n", "      mod:\n        m1: {reg: freq, step: 300}\n      x:\n", "type 'mod'"),
        ("-!freq:@x1, -!freq:@x1,", "-!mod:@m1, -!freq:@x1,", "unknown name 'm1'"),
        ("-!freq:@x1, -!freq:@x1,", "-!noteglide:0, -!freq:@x1,", "can't express !noteglide"),
        ("pw: '1024 + 16*min(3, φ)'", "pw: [{pw: 1024}, {step: 16, for: 3}, end]", "keys"),
        ("x1: (v + 300) % 65536", "x1: v // 2", "outside the grammar"),
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


def grid_of(text, ticks):
    return SongPlayer(songio.loads(text)).render_grid(ticks)


def native(raw, ticks):
    return np.asarray(backends.native_player(raw).render_grid(ticks), dtype=np.uint8)


def test_metal_warrior_one_off_expression_and_dropped_legato_break_the_grid(metal_warrior):
    raw, text = lifted(metal_warrior)
    want = native(raw, 3840)
    assert np.array_equal(grid_of(text, 3840), want)
    assert "x1: (v + 32) % 65536" in text and "C4!legato:0!freq:@x2" in text
    off = text.replace("x1: (v + 32) % 65536", "x1: (v + 33) % 65536")
    assert not np.array_equal(grid_of(off, 3840), want)
    dropped = text.replace("C4!legato:0!freq:@x2", "C4!freq:@x2", 1)
    assert not np.array_equal(grid_of(dropped, 3840), want)


def test_csillag_wrong_delay_constant_breaks_the_grid(csillag):
    raw, text = lifted(csillag)
    gate = "'[φ >= 15]*((-W[N + 33]*abs((φ + 3) % 8 - 4) + 2*W[N + 33]) % 65536)'"
    assert gate in text
    later = text.replace(gate, gate.replace("[φ >= 15]", "[φ >= 14]"))
    ticks = 2304 * 6
    assert not np.array_equal(grid_of(later, ticks), native(raw, ticks))


def test_crosseyed_wrong_carried_direction_breaks_the_grid(crosseyed):
    raw, text = lifted(crosseyed)
    ticks = 95 + 2 * 1152 + 100
    want = native(raw, ticks)
    assert np.array_equal(grid_of(text, ticks), want)
    start = "*[φ >= 1] + 81919"
    assert start in text
    assert not np.array_equal(grid_of(text.replace(start, "*[φ >= 1] + 16384"), ticks), want)
