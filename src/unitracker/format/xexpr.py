"""Modulator expressions: the format's grammar text, checked and compiled for the player.

Integer literals, ``+ - *``, ``//`` (floor division), ``%`` (floor modulo), ``abs``, ``min``,
``max``, a comparison ``(a op b)`` as 1 or 0 (op one of ``== != < <= > >=``), ``v``, ``φ``,
``N``, ``F(k)`` (the frequency of note k) and ``T[k]`` for a named playroutine table ``T``.
"""

import ast

PHI = "φ"
NAMES = {"v": "v", PHI: "phi", "N": "N"}
CALLS = {"abs": 1, "min": 2, "max": 2}
_BINARY = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.FloorDiv: "//", ast.Mod: "%"}
_COMPARE = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}


class ExpressionError(ValueError):
    """A text is not an expression of the modulator grammar."""


def text_of(value) -> str:
    """The expression text of a written value: an integer literal or a string."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ExpressionError(f"an expression is a string or an integer, not {value!r}")
    return str(value)


def written(text: str):
    """The written value of an expression text: an integer literal as an int."""
    try:
        return int(text)
    except ValueError:
        return text


class _Source:
    """Python source of a parsed expression; names the tables and calls it reads."""

    def __init__(self, tables):
        self.tables = set(tables)
        self.reads = set()

    def __call__(self, node) -> str:
        # pylint: disable=too-many-return-statements
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, int)
            and not isinstance(node.value, bool)
        ):
            return str(node.value)
        if isinstance(node, ast.Name) and node.id in NAMES:
            self.reads.add(node.id)
            return NAMES[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return f"(-{self(node.operand)})"
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            return f"({self(node.left)} {_BINARY[type(node.op)]} {self(node.right)})"
        if isinstance(node, ast.Compare):
            return self.compare(node)
        if isinstance(node, ast.List):
            raise ExpressionError(
                f"{ast.unparse(node)!r} is outside the grammar: a comparison is written (a op b)"
            )
        if isinstance(node, ast.Call):
            return self.call(node)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            name = node.value.id
            if name not in self.tables:
                raise ExpressionError(f"unknown table {name!r}")
            self.reads.add(name)
            return f"_T[{name!r}]({self(node.slice)})"
        raise ExpressionError(f"{ast.unparse(node)!r} is outside the grammar")

    def compare(self, node) -> str:
        """One comparison, 1 when it holds, else 0."""
        if len(node.ops) != 1:
            raise ExpressionError(f"({ast.unparse(node)}) is not one comparison")
        op = _COMPARE.get(type(node.ops[0]))
        if op is None:
            raise ExpressionError(f"({ast.unparse(node)}) compares with no grammar operator")
        return f"(1 if {self(node.left)} {op} {self(node.comparators[0])} else 0)"

    def call(self, node) -> str:
        """``abs``, ``min``, ``max`` or ``F``."""
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if node.keywords or name not in (*CALLS, "F"):
            raise ExpressionError(f"{ast.unparse(node)!r} calls no grammar function")
        if len(node.args) != CALLS.get(name, 1):
            raise ExpressionError(f"{name} takes {CALLS.get(name, 1)} argument(s)")
        args = ", ".join(self(a) for a in node.args)
        if name == "F":
            self.reads.add("F")
            return f"_F({args})"
        return f"{name}({args})"


def source(text: str, tables=()) -> tuple[str, set]:
    """``(Python source over v, phi, N, _F and _T, names read)`` of an expression text."""
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"bad expression {text!r}: {exc.msg}") from exc
    src = _Source(tables)
    return src(tree.body), src.reads


def compile_expr(text: str, namespace: dict):
    """A function ``(v, phi, N) -> int`` of an expression text.

    ``namespace`` holds ``_F(k)``, the frequency of format note k, and ``_T``, each named
    table's function of its index.
    """
    body, _ = source(text, namespace["_T"])
    return eval(f"lambda v, phi, N: {body}", namespace)  # pylint: disable=eval-used
