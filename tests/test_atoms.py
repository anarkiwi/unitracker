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
    "row,steps,ins,arg,atom",
    [
        ((1, 24, 1, 0, 0), 3, "i1", None, "C2.i1*3"),
        ((0, 0, 0, 0, 0), 1, None, None, "-"),
        ((2, 0, 0, 0, 0), 4, None, None, "off*4"),
        ((3, 0, 0, 0, 0), 1, None, None, "on"),
        ((1, 46, 4, 1, 1), 1, "i4", "@s1", "A#3.i4!1:@s1"),
        ((0, 0, 0, 15, 16), 48, None, "16", "-!15:16*48"),
        ((0, 0, 0, 0, 5), 1, None, "5", "-!0:5"),
    ],
)
def test_format_and_parse(row, steps, ins, arg, atom):
    assert format_atom(row, steps, ins, arg) == atom
    parsed = parse_atom(atom)
    assert (parsed["kind"], parsed["steps"], parsed["ins"]) == (row[0], steps, ins)
    assert parsed["cmd"] == row[3]
    if row[0] == 1:
        assert parsed["note"] == row[1]
    if arg is not None:
        assert parsed["arg"] == (arg if arg.startswith("@") else int(arg))


def test_yaml_booleans_are_gate_atoms():
    assert parse_atom(False)["kind"] == 2
    assert parse_atom(True)["kind"] == 3


@pytest.mark.parametrize("bad", ["", "X4", "C4.1x", "C4!a:1", "C4*", 7, "C4 .i1", "C4!1:@1x"])
def test_parse_rejects(bad):
    with pytest.raises(AtomError):
        parse_atom(bad)


def test_runs_fold_empty_rows():
    rows = np.array([[0, 0, 0, 0, 0], [1, 5, 0, 0, 0], [0, 0, 0, 0, 0], [0, 0, 2, 0, 0]])
    assert runs(rows) == [(0, 1), (1, 2), (3, 1)]
