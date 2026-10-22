"""Restricted expression compiler: unitracker expression syntax to Python source."""

import ast
import copy


class ExprError(ValueError):
    """An expression uses syntax or a name the format does not allow."""


_BINOPS = (
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.FloorDiv,
    ast.Mod,
    ast.BitAnd,
    ast.BitOr,
    ast.BitXor,
    ast.LShift,
    ast.RShift,
)
_UNARY = (ast.USub, ast.UAdd, ast.Invert, ast.Not)
_CMPOPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def parse(text) -> ast.expr:
    """Parse ``text`` (a str, int or bool) into a validated expression AST."""
    if isinstance(text, bool):
        text = str(int(text))
    elif isinstance(text, int):
        text = str(text)
    if not isinstance(text, str):
        raise ExprError(f"not an expression: {text!r}")
    try:
        tree = ast.parse(text.strip(), mode="eval").body
    except SyntaxError as exc:
        raise ExprError(f"bad expression {text!r}: {exc.msg}") from exc
    _validate(tree, text)
    return tree


_CHECKS = {
    ast.Constant: lambda n: isinstance(n.value, int) and not isinstance(n.value, bool),
    ast.Name: lambda n: True,
    ast.BinOp: lambda n: isinstance(n.op, _BINOPS),
    ast.UnaryOp: lambda n: isinstance(n.op, _UNARY),
    ast.BoolOp: lambda n: True,
    ast.Compare: lambda n: all(isinstance(op, _CMPOPS) for op in n.ops),
    ast.IfExp: lambda n: True,
    ast.Subscript: lambda n: isinstance(n.value, ast.Name),
    ast.Attribute: lambda n: isinstance(n.value, ast.Subscript)
    or (isinstance(n.value, ast.Name) and n.value.id == "row"),
    ast.Call: lambda n: isinstance(n.func, ast.Name) and not n.keywords,
}
_PASSIVE = (ast.expr_context, ast.operator, ast.unaryop, ast.cmpop, ast.boolop)


def _validate(node, text):
    check = _CHECKS.get(type(node))
    if not (isinstance(node, _PASSIVE) or (check is not None and check(node))):
        raise ExprError(f"{type(node).__name__} not allowed in {text!r}")
    for child in ast.iter_child_nodes(node):
        _validate(child, text)


class _Inline(ast.NodeTransformer):
    """Substitute function calls by their bodies, with arguments bound."""

    def __init__(self, functions, depth=0):
        self.functions = functions
        self.depth = depth

    def visit_Call(self, node):  # pylint: disable=invalid-name
        """Inline one call."""
        node = self.generic_visit(node)
        name = node.func.id
        if name not in self.functions:
            raise ExprError(f"unknown function {name!r}")
        args, body = self.functions[name]
        if len(args) != len(node.args):
            raise ExprError(f"{name} takes {len(args)} arguments")
        if self.depth > 16:
            raise ExprError(f"function {name!r} recurses")
        out = _Bind(dict(zip(args, node.args))).visit(copy.deepcopy(body))
        return _Inline(self.functions, self.depth + 1).visit(out)


class _Bind(ast.NodeTransformer):
    def __init__(self, bound):
        self.bound = bound

    def visit_Name(self, node):  # pylint: disable=invalid-name
        """Replace an argument name by a copy of its value expression."""
        return copy.deepcopy(self.bound[node.id]) if node.id in self.bound else node


BUILTINS = {
    "s8": {"args": ["x"], "expr": "(x - 0x100) if x & 0x80 else x"},
    "s16": {"args": ["x"], "expr": "(x - 0x10000) if x & 0x8000 else x"},
}


def with_builtins(functions) -> dict:
    """A playroutine's ``functions`` and the format's :data:`BUILTINS`: ``s8(x)`` reads byte ``x``
    and ``s16(x)`` word ``x`` as two's complement."""
    shadowed = sorted(BUILTINS.keys() & (functions or {}).keys())
    if shadowed:
        raise ExprError(f"functions {shadowed} are builtins")
    return {**BUILTINS, **(functions or {})}


def _body(spec) -> tuple:
    return list(spec.get("args", [])), parse(spec["expr"])


_BUILTIN_BODIES = {name: _body(spec) for name, spec in BUILTINS.items()}


def table_get(table, index):
    """``table[index]``, 0 outside the table."""
    return table[index] if 0 <= index < len(table) else 0


def _source(node) -> str:
    return f"({ast.unparse(node)})"


def _node(source: str) -> ast.expr:
    return ast.parse(source, mode="eval").body


def _truth(node) -> ast.Call:
    return ast.Call(ast.Name("int"), [node], [])


class Resolver(ast.NodeTransformer):
    """Maps the names an expression uses to Python source for the generated code; ``tables``
    are read through ``_tg`` (:func:`table_get`) from ``T``."""

    def __init__(self, functions=None, tables=()):
        self.functions = {
            name: _BUILTIN_BODIES[name] if name in BUILTINS else _body(spec)
            for name, spec in with_builtins(functions).items()
        }
        self.tables = set(tables)

    def name(self, name: str) -> str:
        """Source for a bare name."""
        raise ExprError(f"unknown name {name!r}")

    def table(self, name: str, index: str) -> str:
        """Source for ``name[index]``."""
        if name not in self.tables:
            raise ExprError(f"unknown table {name!r}")
        return f"_tg(T[{name!r}], {index})"

    def field(self, record: str, index: str, field: str) -> str:
        """Source for ``record[index].field``."""
        raise ExprError(f"unknown record {record!r}")

    def row_field(self, field: str) -> str:
        """Source for ``row.field``, the current row in a record template."""
        raise ExprError(f"row.{field} is only defined in a record template")

    def compile(self, text) -> str:
        """Python source for expression ``text``."""
        return _source(self.visit(_Inline(self.functions).visit(parse(text))))

    def visit_Name(self, node):  # pylint: disable=invalid-name
        """A bare name."""
        return _node(self.name(node.id))

    def visit_Subscript(self, node):  # pylint: disable=invalid-name
        """A table read."""
        return _node(self.table(node.value.id, _source(self.visit(node.slice))))

    def visit_Attribute(self, node):  # pylint: disable=invalid-name
        """A record or row field."""
        sub = node.value
        if isinstance(sub, ast.Name):
            return _node(self.row_field(node.attr))
        if not isinstance(sub.value, ast.Name):
            raise ExprError("record access must be name[index].field")
        return _node(self.field(sub.value.id, _source(self.visit(sub.slice)), node.attr))

    def visit_IfExp(self, node):  # pylint: disable=invalid-name
        """A conditional, its branches resolved before its test."""
        node.body, node.test, node.orelse = map(self.visit, (node.body, node.test, node.orelse))
        return node

    def visit_Compare(self, node):  # pylint: disable=invalid-name
        """A comparison as 1 or 0."""
        return _truth(self.generic_visit(node))

    def visit_UnaryOp(self, node):  # pylint: disable=invalid-name
        """``not`` as 1 or 0."""
        node = self.generic_visit(node)
        return _truth(node) if isinstance(node.op, ast.Not) else node
