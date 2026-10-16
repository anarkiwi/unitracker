"""Event atoms: real-pitch note names and the atom grammar."""

import pytest

from unitracker.atoms import AtomError, format_atom, note_index, note_name, parse_atom


@pytest.mark.parametrize(
    "note,name", [(0, "C0"), (24, "C2"), (46, "A#3"), (121, "C#10"), (-1, "B-1"), (-24, "C-2")]
)
def test_note_names(note, name):
    assert note_name(note) == name
    assert note_index(name) == note


@pytest.mark.parametrize("bad", ["H2", "C", "C#", "-", "C2x"])
def test_note_index_rejects(bad):
    with pytest.raises(AtomError):
        note_index(bad)


@pytest.mark.parametrize(
    "parts,atom",
    [
        ((1, 24, "i1", None, None, 3), "C2.i1*3"),
        ((0, 0, None, None, None, 1), "-"),
        ((2, 0, None, None, None, 4), "off*4"),
        ((3, 0, None, None, None, 1), "on"),
        ((1, 46, "i4", "freq", "@v1", 1), "A#3.i4!freq:@v1"),
        ((0, 0, None, "tempo", "16", 48), "-!tempo:16*48"),
        ((0, 0, None, "funk", None, 1), "-!funk"),
        ((0, 0, None, "funk", "8,5", 1), "-!funk:8,5"),
        ((1, -3, None, "ctrlprog", "off", 1), "A-1!ctrlprog:off"),
    ],
)
def test_format_and_parse(parts, atom):
    assert format_atom(parts) == atom
    parsed = parse_atom(atom)
    kind, note, ins, cmd, arg, steps = parts
    assert (parsed["kind"], parsed["ins"], parsed["cmd"], parsed["steps"]) == (
        kind,
        ins,
        cmd,
        steps,
    )
    if kind == 1:
        assert parsed["note"] == note
    expected = {"16": 16, "8,5": [8, 5]}.get(arg, arg)
    assert parsed["arg"] == expected


def test_spec_argument():
    parsed = parse_atom("C4!freq:{step: 32, toward: note}*2")
    assert parsed["arg"] == {"step": 32, "toward": "note"} and parsed["steps"] == 2
    assert parse_atom("-!vol:-1")["arg"] == -1


def test_yaml_booleans_are_gate_atoms():
    assert parse_atom(False)["kind"] == 2
    assert parse_atom(True)["kind"] == 3


@pytest.mark.parametrize(
    "bad", ["", "X4", "C4.1x", "C4!1:1", "C4*", 7, "C4 .i1", "C4!freq:{", "C4!freq:[1]", "C4!a:x"]
)
def test_parse_rejects(bad):
    with pytest.raises(AtomError):
        parse_atom(bad)
