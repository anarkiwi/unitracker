"""!ad:n and !sr:n: SID-Wizard's nibble small FX written as the envelope byte they write."""

import numpy as np
import pytest

from unitracker import envelope, songio
from unitracker.vm import SongPlayer

INS = """      ins:
        i1: {ad: 18, sr: 240, ctrl: [{ctrl: 65, note: +0}, end]}
        i2: {ad: 52, sr: 248, ctrl: [{ctrl: 65, note: +0}, end]}
"""
AD, SR = 5, 6


def song(phrases, rows="    play: [p1, p1, p1]\n", loop="~"):
    phrase = "".join(f"        {name}: [{atoms}]\n" for name, atoms in phrases.items())
    return f"""unitracker: 1.16
playroutine: {{ref: sidwizard}}
song:
  - def:
{INS}      phrase:
{phrase}{rows}loop: {loop}
"""


def commands(ir, phrase="p1"):
    return [e.get("commands") or e.get("command") for e in ir["phrases"][phrase]]


def grid(ir, ticks=120):
    return SongPlayer(ir).render_grid(ticks)


def test_the_slot_and_the_instrument_give_the_column():
    ir = songio.loads(song({"p1": "C4.i1!sr:192*4, D4!sr:208*4, E4!legato:0!sr:241*4, -!ad:20*4"}))
    assert commands(ir) == [[80, 12], [336, 13], [[63, 0], [96, 1]], [304, 4]]


@pytest.mark.parametrize(
    "atom,command",
    [("-!ad:66", [288, 4]), ("-!ad:21", [304, 5]), ("-!ad:18", [288, 1]), ("-!sr:247", [352, 7])],
)
def test_a_byte_reads_as_the_nibble_that_differs_from_the_instrument(atom, command):
    ir = songio.loads(song({"p1": f"C4.i1*4, {atom}*4"}))
    assert commands(ir)[1] == command


def test_the_player_writes_the_byte():
    ir = songio.loads(song({"p1": "C4.i1*4, -!sr:192*4, C4*4, D4!ad:66*4, -!ad:21*4"}))
    voice = grid(ir, 100)[:, [AD, SR]]
    first = [tuple(int(x) for x in r) for r in voice[np.r_[True, (voice[1:] != voice[:-1]).any(1)]]]
    assert first[-5:] == [(18, 240), (18, 192), (18, 240), (66, 240), (21, 240)]


@pytest.mark.parametrize(
    "atom,match",
    [
        ("-!ad:255", "sidwizard: the player can't express !ad:255 over the instrument's ad 18"),
        ("-!sr:256", "sidwizard: the player can't express !sr:256 over the instrument's sr 240"),
        ("-!sr:none", "sidwizard: the player can't express !sr:none"),
        ("-!attack:1", "!attack is retired in v1.14: write the envelope byte as `!ad:n`"),
        ("-!irelease:1", "!irelease is retired in v1.14"),
    ],
)
def test_the_reader_refuses_what_no_small_fx_writes(atom, match):
    with pytest.raises(songio.SongFormatError, match=match):
        songio.loads(song({"p1": f"C4.i1*4, {atom}*4"}))


@pytest.mark.parametrize(
    "atoms,match",
    [
        ("-!ad:18*4, C4.i1*4", "!ad on a voice that has no instrument"),
        ("C4.i1*4, -.i2*4, -!ad:50*4", "!ad also writes sr 248 over 240"),
    ],
)
def test_a_command_the_voice_cannot_write_as_its_byte_is_refused(atoms, match):
    with pytest.raises(songio.SongFormatError, match=f"sidwizard: {match}"):
        songio.loads(song({"p1": atoms}))


def test_a_phrase_that_reads_as_different_nibbles_is_split_and_merges_back():
    text = song({"p1": "C4.i1*4", "p2": "C4!ad:50*4", "p3": "C4.i2*4"}, ROWS)
    ir = songio.loads(text)
    assert commands(ir, "p2") == [[288, 3]] and commands(ir, "p2/1") == [[304, 2]]
    assert [p["phrase"] for p in ir["tracks"][0]["plays"]] == ["p1", "p2", "p3", "p2/1"]
    out = songio.dumps(ir)
    assert out.count("C4!ad:50*4") == 1 and "p2/1" not in out
    assert songio.dumps(songio.loads(out)) == out


ROWS = "    play: [p1, p1, p1]\n  - play: [p2, p2, p2]\n  - play: [p3, p3, p3]\n" + (
    "  - play: [p2, p2, p2]\n"
)


def test_a_phrase_whose_nibble_writes_different_bytes_is_split():
    ir = songio.loads(song({"p1": "C4.i1*4", "p2": "C4!ad:50*4", "p3": "C4.i2*4"}, ROWS))
    for trk in ir["tracks"]:
        for play in trk["plays"]:
            play["phrase"] = play["phrase"].split("/")[0]
    text = songio.dumps(ir)
    assert "C4!ad:50*4" in text and "C4!ad:52*4" in text
    back = songio.loads(text)
    assert songio.dumps(back) == text
    assert np.array_equal(grid(back, 200), grid(ir, 200))


def test_a_play_that_differs_on_its_loop_passes_is_refused():
    text = song(
        {"p1": "C4.i1*4", "p2": "C4!ad:50*4", "p3": "C4.i2*4"},
        "    play: [p1, p1, p1]\n  - play: [p2, p2, p2]\n  - play: [p3, p3, p3]\n",
        loop=1,
    )
    with pytest.raises(songio.SongFormatError, match="phrase p2 plays its envelope commands"):
        songio.loads(text)


def test_a_lone_fx_column_command_off_a_note_start_is_written_in_the_first_slot():
    ir = songio.loads(song({"p1": "C4.i1*4, off!sr:192*4"}))
    native = songio.loads(song({"p1": "C4.i1*4, off!sr:192*4"}))
    native["phrases"]["p1"][1]["command"] = [80, 12]
    text = songio.dumps(native)
    assert "off!sr:192*4" in text and commands(songio.loads(text)) == commands(ir)
    assert np.array_equal(grid(native), grid(ir))


@pytest.mark.parametrize(
    "event,column",
    [
        ({"note": 40, "command": [80, 12]}, "FX"),
        ({"note": 40, "instrument": 1, "command": [336, 12]}, "instrument"),
        ({"note": 40, "commands": [[112, 1], [336, 12]]}, "instrument"),
    ],
)
def test_a_column_the_slots_cannot_say_has_no_written_form(event, column):
    ir = songio.loads(song({"p1": "C4.i1*4, D4*4"}))
    ir["phrases"]["p1"][1] = event
    with pytest.raises(songio.SongFormatError, match=f"!sr in the {column} column has no written"):
        songio.dumps(ir)


def test_a_player_without_envelope_commands_is_left_as_it_is():
    pr = songio.library("goattracker2")
    env = envelope.Envelope(pr, {})
    ir = {"tracks": [{"plays": [{"phrase": "a"}]}], "phrases": {"a": [{"command": [5, 3]}]}}
    assert env.read(ir, {}) is ir and env.write(ir, {}) is ir and not env.names
