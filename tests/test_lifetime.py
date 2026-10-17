"""!pulse:0 and !filter:0: a note that does not restart its instrument's program, per player."""

import numpy as np
import pytest

from unitracker import lifetime, songio
from unitracker.vm import SongPlayer

SW_INS = """      ins:
        i1: {sr: 240, ctrl: [{ctrl: 65, note: +0}, end], pw: '2048 + 16*min(64, φ)'}
        i2: {sr: 240, ctrl: [{ctrl: 65, note: +0}, end], pw: '1024 + 16*min(64, φ)'}
"""
GT_INS = """      x:
        x1: '1024 + 16*min(3, φ)'
      pw:
        p1: '2048 + 16*min(3, φ)'
      ins:
        i1: {sr: 240, ctrl: [{ctrl: 65, note: +0}, end], pw: '1024 + 16*min(3, φ)',
          restart: [{hold: 2, gate: 0, ad: 15, sr: 0}, {ctrl: 9, gate: 1}, end]}
        i2: {sr: 240, ctrl: [{ctrl: 65, note: +0}, end]}
        i3: {sr: 240, ctrl: [{ctrl: 65, note: +0}, {run: p1}, end]}
"""


def song(pr, defs, phrases, rows="    play: [p1, p1, p1]\n", loop="~"):
    phrase = "".join(f"        {name}: [{atoms}]\n" for name, atoms in phrases.items())
    return f"""unitracker: 1.17
playroutine: {{ref: {pr}}}
song:
  - def:
{defs}      phrase:
{phrase}{rows}loop: {loop}
"""


SW_BASE = song("sidwizard", SW_INS, {"p1": "C4.i1*4, D4*4, E4*4, C4.i2*4, D4*4"})


def grid(ir, ticks=150):
    return SongPlayer(ir).render_grid(ticks)


@pytest.mark.parametrize(
    "bits,written",
    [(0x40, "D4!pulse:0*4, E4!pulse:0*4"), (0xC0, "D4!pulse:0!filter:0*4, E4!pulse:0!filter:0*4")],
)
def test_sidwizard_writes_the_flag_bits_on_the_notes_they_keep(bits, written):
    plain = songio.loads(SW_BASE)
    flagged = songio.loads(SW_BASE)
    flagged["records"]["ins"][1]["flags"] = 0x10 | bits
    text = songio.dumps(flagged)
    assert f"p1: [C4.i1*4, {written}, C4.i2*4, D4*4]" in text
    assert "flags" not in text
    back = songio.loads(text)
    assert back["records"]["ins"][1]["flags"] == 0x10 | bits
    assert "flags" not in back["records"]["ins"][2]
    assert songio.dumps(back) == text
    assert np.array_equal(grid(back), grid(flagged))
    assert not np.array_equal(grid(back), grid(plain))


@pytest.mark.parametrize(
    "atoms,match",
    [
        ("C4.i1*4, D4!pulse:0*4, E4*4", "is on some notes that start instrument i1 without"),
        ("C4.i1!pulse:0*4, D4*4", "on a note that selects an instrument, which restarts the pulse"),
        ("C4.i1*4, -!filter:0*4", "!filter:0 needs a note"),
        ("C4.i1*4, D4!pulse:1*4", "sidwizard: the player can't express !pulse:1"),
    ],
)
def test_sidwizard_refuses_an_inconsistent_document(atoms, match):
    with pytest.raises(songio.SongFormatError, match=match):
        songio.loads(song("sidwizard", SW_INS, {"p1": atoms}))


def test_lifetime_commands_take_no_command_slot():
    text = song(
        "sidwizard",
        SW_INS + "      x:\n        x1: (v - 7) % 65536\n",
        {"p1": "C4.i1*4, D4!ad:16!freq:@x1!pulse:0*4"},
    )
    ir = songio.loads(text)
    assert ir["records"]["ins"][1]["flags"] == 0x50
    assert [code for code, _ in ir["phrases"]["p1"][1]["commands"]] == [288, 16]
    out = songio.dumps(ir)
    assert "D4!ad:16!freq:@x1!pulse:0*4" in out
    assert songio.dumps(songio.loads(out)) == out


LOOPED = song(
    "sidwizard",
    SW_INS,
    {"p1": "C4.i1*4", "p2": "C4.i2*4", "p3": "D4*4"},
    "    play: [p1, p1, p1]\n  - play: [p3, p3, p3]\n  - play: [p2, p2, p2]\n"
    "  - play: [p3, p3, p3]\n",
    loop=1,
)


def test_a_note_under_instruments_that_keep_and_restart_has_no_written_form():
    ir = songio.loads(LOOPED)
    assert "!pulse:0" not in songio.dumps(ir)
    ir["records"]["ins"][1]["flags"] = 0x50
    with pytest.raises(songio.SongFormatError, match="keep and restart the pulse program"):
        songio.dumps(ir)
    ir["records"]["ins"][2]["flags"] = 0x50
    assert ": [D4!pulse:0*4]" in songio.dumps(ir)


def test_the_loop_pass_plays_a_leading_note_under_the_voice_last_instrument():
    ir = songio.loads(LOOPED)
    plays = list(lifetime.notes(ir["tracks"][:1], ir["phrases"], 0, 63))
    assert [(name, ins, sel) for name, _, ins, sel in plays] == [
        ("p1", 1, True),
        ("p3", 1, False),
        ("p2", 2, True),
        ("p3", 2, False),
        ("p3", 2, False),
        ("p2", 2, True),
        ("p3", 2, False),
    ]


def test_legato_notes_start_nothing():
    text = song("sidwizard", SW_INS, {"p1": "C4.i1*4, D4!legato:0!pulse:0*4, E4*4"})
    ir = songio.loads(text)
    assert "flags" not in ir["records"]["ins"][1]
    assert "!pulse:0" not in songio.dumps(ir)


GT_PHRASE = "C4.i1*2, D4.i2*2, E4*2, F4.i3*2, C4.i2*2"


def test_goattracker_accepts_the_command_where_no_program_starts_and_writes_it_implicitly():
    plain = songio.loads(song("goattracker2", GT_INS, {"p1": GT_PHRASE}))
    kept = GT_PHRASE.replace("D4.i2", "D4.i2!pulse:0!filter:0").replace("E4", "E4!pulse:0")
    ir = songio.loads(song("goattracker2", GT_INS, {"p1": kept}))
    assert ir == plain
    assert "!pulse:0" not in songio.dumps(ir)


@pytest.mark.parametrize(
    "old,new,match",
    [
        ("C4.i1", "C4.i1!pulse:0", "whose instrument i1 starts the pulse program"),
        ("F4.i3", "F4.i3!pulse:0", "whose instrument i3 starts the pulse program"),
        ("D4.i2", "D4.i2!run:@p1!pulse:0", "!pulse:0 on a note that starts the program"),
    ],
)
def test_goattracker_refuses_the_command_on_a_note_that_starts_the_program(old, new, match):
    text = song("goattracker2", GT_INS, {"p1": GT_PHRASE.replace(old, new)})
    with pytest.raises(songio.SongFormatError, match=match):
        songio.loads(text)


def test_defmon_refuses_the_commands():
    defs = "      ctrl:\n        c1: [{ctrl: 65}, end]\n"
    for command in ("pulse", "filter"):
        text = song("defmon", defs, {"p1": f"C4.c1!{command}:0"})
        with pytest.raises(
            songio.SongFormatError, match=f"defmon: the player can't express !{command}:0"
        ):
            songio.loads(text)
