"""The format's two small grammars: event atoms and restricted expressions."""

import numpy as np
import pytest

from unitracker.format.atoms import AtomError, format_atom, note_index, note_name, parse_atom, runs
from unitracker.format.expr import ExprError, Resolver, parse

ATOMS = [
    (1, 24, 3, "i1", [], "C2.i1*3"),
    (0, 0, 1, None, [], "-"),
    (2, 0, 4, None, [], "off*4"),
    (3, 0, 1, None, [], "on"),
    (1, 46, 1, "i4", ["slideup:@s1"], "A#3.i4!slideup:@s1"),
    (0, 0, 48, None, ["tempo:16"], "-!tempo:16*48"),
    (0, 0, 1, None, ["run:none"], "-!run:none"),
    (1, 60, 2, "i2", ["run:@w2", "sr:136"], "C5.i2!run:@w2!sr:136*2"),
]
BAD_ATOMS = [
    "", "X4", "C4.1x", "C4!1:1", "C4!A:1", "C4*", 7, "C4 .i1", "C4!run:@1x", "C4!run:-1",
    "C4!run:1!",
]  # fmt: skip


def test_note_names():
    for note, name in [(0, "C0"), (24, "C2"), (46, "A#3"), (121, "C#10")]:
        assert (note_name(note), note_index(name)) == (name, note)
    assert all(note_index(note_name(n)) == n for n in range(128))
    for bad in ["H2", "C", "C#", "-", "C2x"]:
        with pytest.raises(AtomError):
            note_index(bad)
    with pytest.raises(AtomError):
        note_name(-1)


def test_atoms_format_and_parse():
    for kind, note, steps, ins, commands, atom in ATOMS:
        assert format_atom(kind, note, steps, ins, commands) == atom
        parsed = parse_atom(atom)
        assert (parsed["kind"], parsed["steps"], parsed["ins"]) == (kind, steps, ins)
        assert kind != 1 or parsed["note"] == note
        args = [c.split(":") for c in commands]
        assert parsed["commands"] == [(n, a if a[0] in "@n" else int(a)) for n, a in args]
    assert (parse_atom(False)["kind"], parse_atom(True)["kind"]) == (2, 3)
    for bad in BAD_ATOMS:
        with pytest.raises(AtomError):
            parse_atom(bad)


def test_runs_fold_empty_rows():
    rows = np.array([[0] * 7, [1, 5, 0, 0, 0, 0, 0], [0] * 7, [0, 0, 0, 0, 0, 2, 3]])
    assert runs(rows) == [(0, 1), (1, 2), (3, 1)]


class Names(Resolver):
    """Resolves every name to itself, tables and records to subscripts."""

    def name(self, name):
        return name

    def table(self, name, index):
        return f"{name}[{index}]"

    def field(self, record, index, field):
        return f"{record}[{index}][{field!r}]"


def ev(text, functions=None, **env):
    return eval(Names(functions).compile(text), {}, env)  # pylint: disable=eval-used


ARITHMETIC = [
    ("1 + 2 * 3", 7),
    ("0x10 >> 2 | 1", 5),
    ("7 // 2 - 7 % 2", 2),
    ("~0 & 0xFF ^ 0x0F", 0xF0),
    ("-3 + +3", 0),
    ("not 0", 1),
    ("1 < 2 <= 2", 1),
    ("3 == 4 or 2 != 2", 0),
    ("5 if 1 > 0 else 6", 5),
    ("s8(0x7F) + s8(0x80) + s8(0xFF)", -2),
    ("s16(0x7FFF) + s16(0x8000) + s16(0xFFFF)", -2),
    (12, 12),
    (True, 1),
]
REJECTED = [
    "a.b", "f(x=1)", "(1, 2)", "lambda: 1", "x[1][2]", "1.5", "'s'", "1 is 1", "1 in 2", "2 ** 3",
    "a @ b", "[1]", "x(", None,
]  # fmt: skip


def test_expressions():
    assert [ev(text) for text, _ in ARITHMETIC] == [value for _, value in ARITHMETIC]
    funcs = {
        "twice": {"args": ["a"], "expr": "a + a"},
        "quad": {"args": ["b"], "expr": "twice(twice(b))"},
    }
    assert ev("quad(x) + t[1] + r[0].f", funcs, x=2, t=[0, 5], r=[{"f": 3}]) == 16
    assert parse("1").value == 1


@pytest.mark.parametrize(
    "resolver,texts,match",
    [
        (Names(), REJECTED, None),
        (Resolver(), ["x", "t[0]", "r[0].f"], None),
        (Names(), ["g(1)"], "unknown function"),
        (Names({"f": {"args": ["a"], "expr": "a"}}), ["f(1, 2)"], "takes 1"),
        (Names({"f": {"args": ["a"], "expr": "f(a)"}}), ["f(1)"], "recurses"),
        (Names, [{"s8": {"args": ["a"], "expr": "a"}}], "builtins"),
    ],
)
def test_expression_errors(resolver, texts, match):
    for text in texts:
        with pytest.raises(ExprError, match=match):
            resolver(text) if isinstance(resolver, type) else resolver.compile(text)
