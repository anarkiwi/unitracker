"""SID-Wizard's FX column holds one command: an event with two for it is refused."""

import pytest

from test_envelope import INS, commands
from unitracker.format import songio


def song(atoms: str) -> str:
    return (
        f"unitracker: {songio.VERSION}\nplayroutine: {{ref: sidwizard}}\nsong:\n  - def:\n{INS}"
        "      ctrl:\n        c1: [{ctrl: 33, note: +0}, end]\n"
        f"      phrase:\n        p1: [{atoms}]\n    play: [p1, p1, p1]\nloop: ~\n"
    )


@pytest.mark.parametrize(
    "atoms,shown",
    [
        ("C4.i1!tempo:4!vtempo:5*4", "!tempo and !vtempo"),
        ("C4.i1!sr:192!tempo:4*4", "!sr and !tempo"),
        ("C4.i1*4, -!tempo:4!sr:192*4", "!tempo and !sr"),
        ("C4.i1!run:@c1!funk:4/5*4", "!run and !funk"),
    ],
)
def test_two_fx_column_commands_are_refused_naming_both(atoms, shown):
    with pytest.raises(songio.SongFormatError, match=f"^sidwizard: the player can't play {shown}"):
        songio.loads(song(atoms))


@pytest.mark.parametrize(
    "atoms,second",
    [
        ("C4.i1*4, D4!sr:192!tempo:4*4", [[336, 12], [18, 4]]),
        ("C4.i1*4, D4!run:@c1!tempo:4*4", [[112, 2], [18, 4]]),
        ("C4.i1*4, D4!legato:0!tempo:4*4", [[63, 0], [18, 4]]),
    ],
)
def test_a_pair_with_one_command_in_another_column_reads_and_writes(atoms, second):
    text = songio.dumps(songio.loads(song(atoms)))
    ir = songio.loads(text)
    assert commands(ir)[1] == second and songio.dumps(ir) == text


def test_the_writer_refuses_two_fx_column_commands():
    ir = songio.loads(song("C4.i1!tempo:4*4"))
    ir["phrases"]["p1"][0].pop("command")
    ir["phrases"]["p1"][0]["commands"] = [[18, 4], [16, 0]]
    with pytest.raises(songio.SongFormatError, match="can't play !tempo and !freq on one event"):
        songio.dumps(ir)
