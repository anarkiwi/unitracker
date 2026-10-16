"""Mod records and mod rows: each key means what docs/format.md says in every player."""

import numpy as np
import pytest
import yaml

from unitracker import songio
from unitracker.vm import SongPlayer

GT = songio.library("goattracker2")["tables"]
SW = songio.library("sidwizard")["tables"]
DM = songio.library("defmon")["tables"]


def voice0(text, ticks):
    """Voice 0's frequency and pulse-width words per tick."""
    grid = SongPlayer(songio.loads(text)).render_grid(ticks).astype(int)
    return grid[:, 0] | grid[:, 1] << 8, grid[:, 2] | grid[:, 3] << 8


def moves(words):
    """The nonzero tick-to-tick changes of a register word."""
    return [int(d) for d in np.diff(words) if d]


def doc(playroutine, defs, phrase, extra=""):
    return f"""
unitracker: 1.4
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
        i1: {sr: 240, ctrl: [{ctrl: 65, note: +0}, end], gt: 2,
          pulse: [{pw: 1024}, {step: 16, for: 3}, {step: -8, for: 2}, end]}
      mod:
        m1: {reg: freq, step: 300}
        m2: {reg: freq, step: 4, rel: note}
        m3: {reg: freq, to: note}
        m4: {reg: freq, step: 1000, to: note}""",
    "[C4.i1, -!mod:@m1, -!mod:@m2, -, D4!mod:@m3, -, C4.i1, G4!mod:@m4, -!mod:@m4, -]",
)


def test_goattracker_mods_step_scale_and_reach_the_note():
    freq, pulse = voice0(GT_SONG, 80)
    c4, d4, g4 = GT["ftab"][48], GT["ftab"][50], GT["ftab"][55]
    quarter = (GT["ftab"][49] - c4) >> 2
    # Each continuous command fires on every tick of its step but the first.
    approach = [1000] * ((g4 - c4) // 1000) + [(g4 - c4) % 1000]
    assert (
        moves(freq)
        == [c4]
        + [300] * 6
        + [quarter] * 6
        + [d4 - (c4 + 1800 + 6 * quarter)]
        + [c4 - d4]
        + approach
    )
    assert freq[-1] == g4
    assert moves(pulse)[:6] == [1024, 16, 16, 16, -8, -8]


SW_SONG = doc(
    "sidwizard",
    """      ins:
        i1: {sr: 240, ctrl: [{ctrl: 65, note: +0}, end],
          pulse: [{pw: 2048}, {hold: 2, step: 2, for: 4}, {hold: 2, step: -8, for: 3}, end]}
      mod:
        m1: {reg: freq, step: 24, rel: note, for: note}
        m2: {reg: freq, step: 32, rel: note, to: note, for: note}""",
    "[C4.i1, -!mod:@m1*2, E4!mod:@m2*4]",
)


def sw_basis(k, note):
    """SID-Wizard's note-relative step: its speed table at the voice's note index plus k."""
    i = min(k + note, 203)
    return SW["exptab"][i] if i < 107 else word(i - 107)


def word(n):
    """SID-Wizard's frequency word of note index ``n``."""
    return SW["freqlo"][n] | SW["freqhi"][n] << 8


def test_sidwizard_mods_step_scale_and_glide_one_past_the_basis():
    freq, pulse = voice0(SW_SONG, 60)
    c4, e4 = 48 + 1, 52 + 1
    # The voice adds the WF row's carry to the note's frequency word.
    carry = int(freq[6]) - word(c4)
    slide = moves(freq)[4:]
    assert slide[:12] == [sw_basis(24, c4)] * 12
    glide = sw_basis(32, e4) + 1
    target = word(e4) + carry
    distance = target - word(c4) - carry - 12 * sw_basis(24, c4)
    assert slide[12:] == [glide] * (distance // glide) + [distance % glide]
    assert freq[-1] == target
    steps = np.diff(pulse)[3:12]
    assert list(steps) == [2, 2, 2, 2, 0, -8, -8, -8, 0]


DM_SONG = doc(
    "defmon",
    """      sidtab:
        t1: [{ctrl: 65, pw: 3840, reg: pw, step: 60, at: reflect}, end]
        t2: [{ctrl: 65, reg: freq, step: -139, for: note}, end]
        t3: [{ctrl: 65, pw: 0, reg: pw, step: -10, at: reflect}, end]""",
    "[C4.t1*8, C4.t2*4, D4*4, C4.t3*4]",
    "      vinit: {0: {}, 1: {}, 2: {}}\n      ginit: {0: {}}\n",
)


def test_defmon_mods_reflect_to_its_bounds_and_slide_until_the_note():
    freq, pulse = voice0(DM_SONG, 22)
    assert list(pulse[1:6]) == [3900, 3960, 4020, 4080, 4088]
    assert set(np.diff(pulse)[5:16]) == {-61, -60}
    assert list(pulse[17:]) == [1, 10, 19, 28, 37]
    rate = DM["rate_lo"][48] | DM["rate_hi"][48] << 8
    assert rate == 139 and moves(freq)[1:5] == [-rate] * 4
    assert freq[13] == DM["note_lo"][74] | DM["note_hi"][74] << 8


def gt_ir():
    song = songio.loads(GT_SONG)
    song["records"]["speedtab"] = {1: {"l": 1, "r": 44}, 2: {"l": 1, "r": 44}, 3: {"l": 2}}
    song["phrases"]["p1"] = [
        {"note": 48, "instrument": 1, "command": [1, 1]},
        {"command": [1, 2]},
        {"command": [2, 3]},
        {"command": [4, 3]},
    ]
    return song


def test_equal_mods_are_one_record_and_read_back_as_commands():
    text = songio.dumps(gt_ir())
    mods = [item["def"].get("mod") for item in yaml.safe_load(text)["song"] if "def" in item]
    assert mods[0] == {"m1": {"reg": "freq", "step": 300}, "m2": {"reg": "freq", "step": -512}}
    assert "!vib:@s1" in text
    song = songio.loads(text)
    events = song["phrases"]["p1"]
    commands = [e.get("command") for e in events]
    speeds = song["records"]["speedtab"]
    assert [c[0] for c in commands] == [1, 1, 2, 4]
    assert [speeds[c[1]] for c in commands[:3]] == [{"l": 1, "r": 44}] * 2 + [{"l": 2, "r": 0}]
    assert songio.dumps(song) == text


@pytest.mark.parametrize(
    "old,new,match",
    [
        ("{step: 16, for: 3}", "[3, 16]", "with shared keys"),
        ("-!mod:@m1,", "-!slideup:5,", "can't express !slideup"),
        ("-!mod:@m1,", "-!run:@m1,", "takes a mod record"),
        ("-!mod:@m1,", "-!mod:5,", "takes a mod record"),
        ("m1: {reg: freq, step: 300}", "m1: {reg: freq, step: 300, every: 2}", "keys"),
        ("m1: {reg: freq, step: 300}", "m1: {reg: freq, step: 300, at: bounce}", "bad at"),
    ],
)
def test_reader_refuses_old_forms_and_mods_the_player_cannot_play(old, new, match):
    assert old in GT_SONG
    with pytest.raises(songio.SongFormatError, match=match):
        songio.loads(GT_SONG.replace(old, new, 1))


def test_writer_refuses_a_command_whose_mod_has_no_form():
    song = gt_ir()
    song["playroutine"]["record_types"]["mod"]["codec"] = [{"when": "0", "keys": {"reg": 0}}]
    with pytest.raises(songio.SongFormatError, match="mod: row .* no shared-vocabulary form"):
        songio.dumps(song, embed=True)
