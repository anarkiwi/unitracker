"""Restricted expression compilation."""

import pytest

from unitracker.expr import ExprError, Resolver, parse


class Names(Resolver):
    """Resolves every name to itself, tables and records to helper calls."""

    def name(self, name):
        return name

    def table(self, name, index):
        return f"{name}[{index}]"

    def field(self, record, index, field):
        return f"{record}[{index}][{field!r}]"


def ev(text, functions=None, **env):
    return eval(Names(functions).compile(text), {}, env)  # pylint: disable=eval-used


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1 + 2 * 3", 7),
        ("0x10 >> 2 | 1", 5),
        ("7 // 2 - 7 % 2", 2),
        ("~0 & 0xFF ^ 0x0F", 0xF0),
        ("-3 + +3", 0),
        ("not 0", 1),
        ("1 < 2 <= 2", 1),
        ("3 == 4 or 2 != 2", 0),
        ("5 if 1 > 0 else 6", 5),
        (12, 12),
        (True, 1),
    ],
)
def test_arithmetic(text, expected):
    assert ev(text) == expected


def test_names_tables_records_functions():
    funcs = {
        "twice": {"args": ["a"], "expr": "a + a"},
        "quad": {"args": ["b"], "expr": "twice(twice(b))"},
    }
    assert ev("quad(x) + t[1] + r[0].f", funcs, x=2, t=[0, 5], r=[{"f": 3}]) == 16


@pytest.mark.parametrize(
    "text",
    [
        "a.b",
        "f(x=1)",
        "(1, 2)",
        "lambda: 1",
        "x[1][2]",
        "1.5",
        "'s'",
        "1 is 1",
        "1 in 2",
        "2 ** 3",
        "a @ b",
        "[1]",
        "x(",
        None,
    ],
)
def test_rejected(text):
    with pytest.raises(ExprError):
        Names().compile(text)


def test_function_errors():
    with pytest.raises(ExprError, match="unknown function"):
        Names().compile("g(1)")
    with pytest.raises(ExprError, match="takes 1"):
        Names({"f": {"args": ["a"], "expr": "a"}}).compile("f(1, 2)")
    with pytest.raises(ExprError, match="recurses"):
        Names({"f": {"args": ["a"], "expr": "f(a)"}}).compile("f(1)")


def test_base_resolver_rejects_everything():
    base = Resolver()
    for text in ("x", "t[0]", "r[0].f"):
        with pytest.raises(ExprError):
            base.compile(text)
    assert parse("1").value == 1
