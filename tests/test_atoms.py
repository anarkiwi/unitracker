"""Event atoms: note names and the atom grammar."""

import numpy as np
import pytest

from unitracker.atoms import AtomError, format_atom, note_index, note_name, parse_atom, runs


@pytest.mark.parametrize("note,name", [(0, "C0"), (24, "C2"), (46, "A#3"), (121, "C#10")])
def test_note_names(note, name):
    assert note_name(note) == name
    assert note_index(name) == note


@pytest.mark.parametrize("bad", ["H2", "C", "C#", "-", "C2x"])
def test_note_index_rejects(bad):
    with pytest.raises(AtomError):
        note_index(bad)


def test_note_name_rejects_negative():
    with pytest.raises(AtomError):
        note_name(-1)


@pytest.mark.parametrize(
    "kind,note,steps,ins,commands,atom",
    [
        (1, 24, 3, "i1", [], "C2.i1*3"),
        (0, 0, 1, None, [], "-"),
        (2, 0, 4, None, [], "off*4"),
        (3, 0, 1, None, [], "on"),
        (1, 46, 1, "i4", ["slideup:@s1"], "A#3.i4!slideup:@s1"),
        (0, 0, 48, None, ["tempo:16"], "-!tempo:16*48"),
        (0, 0, 1, None, ["run:none"], "-!run:none"),
        (1, 60, 2, "i2", ["run:@w2", "sustain:8"], "C5.i2!run:@w2!sustain:8*2"),
    ],
)
def test_format_and_parse(
    kind, note, steps, ins, commands, atom
):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    assert format_atom(kind, note, steps, ins, commands) == atom
    parsed = parse_atom(atom)
    assert (parsed["kind"], parsed["steps"], parsed["ins"]) == (kind, steps, ins)
    if kind == 1:
        assert parsed["note"] == note
    expected = []
    for command in commands:
        name, arg = command.split(":")
        expected.append((name, arg if arg[0] in "@n" else int(arg)))
    assert parsed["commands"] == expected


def test_yaml_booleans_are_gate_atoms():
    assert parse_atom(False)["kind"] == 2
    assert parse_atom(True)["kind"] == 3


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "X4",
        "C4.1x",
        "C4!1:1",
        "C4!A:1",
        "C4*",
        7,
        "C4 .i1",
        "C4!run:@1x",
        "C4!run:-1",
        "C4!run:1!",
    ],
)
def test_parse_rejects(bad):
    with pytest.raises(AtomError):
        parse_atom(bad)


def test_runs_fold_empty_rows():
    rows = np.array([[0] * 7, [1, 5, 0, 0, 0, 0, 0], [0] * 7, [0, 0, 0, 0, 0, 2, 3]])
    assert runs(rows) == [(0, 1), (1, 2), (3, 1)]
