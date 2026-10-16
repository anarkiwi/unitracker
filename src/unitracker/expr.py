"""Restricted expression compiler: unitracker expression syntax to Python source."""

import ast


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
        bound = dict(zip(args, node.args))
        out = _Bind(bound).visit(ast.parse(ast.unparse(body), mode="eval").body)
        return _Inline(self.functions, self.depth + 1).visit(out)


class _Bind(ast.NodeTransformer):
    def __init__(self, bound):
        self.bound = bound

    def visit_Name(self, node):  # pylint: disable=invalid-name
        """Replace an argument name by its value expression."""
        return self.bound.get(node.id, node)


class Resolver:
    """Maps the names an expression uses to Python source for the generated code."""

    def __init__(self, functions=None):
        self.functions = {
            name: (list(spec.get("args", [])), parse(spec["expr"]))
            for name, spec in (functions or {}).items()
        }

    def name(self, name: str) -> str:
        """Source for a bare name."""
        raise ExprError(f"unknown name {name!r}")

    def table(self, name: str, index: str) -> str:
        """Source for ``name[index]``."""
        raise ExprError(f"unknown table {name!r}")

    def field(self, record: str, index: str, field: str) -> str:
        """Source for ``record[index].field``."""
        raise ExprError(f"unknown record {record!r}")

    def row_field(self, field: str) -> str:
        """Source for ``row.field``, the current row in a record template."""
        raise ExprError(f"row.{field} is only defined in a record template")

    def compile(self, text) -> str:
        """Python source for expression ``text``."""
        tree = _Inline(self.functions).visit(parse(text))
        return self._emit(tree)

    def _emit(self, node) -> str:
        return getattr(self, f"_emit_{type(node).__name__}")(node)

    def _emit_Constant(self, node):  # pylint: disable=invalid-name
        return str(int(node.value))

    def _emit_Name(self, node):  # pylint: disable=invalid-name
        return self.name(node.id)

    def _emit_Subscript(self, node):  # pylint: disable=invalid-name
        return self.table(node.value.id, self._emit(node.slice))

    def _emit_Attribute(self, node):  # pylint: disable=invalid-name
        sub = node.value
        if isinstance(sub, ast.Name):
            return self.row_field(node.attr)
        if not isinstance(sub.value, ast.Name):
            raise ExprError("record access must be name[index].field")
        return self.field(sub.value.id, self._emit(sub.slice), node.attr)

    def _emit_BinOp(self, node):  # pylint: disable=invalid-name
        op = ast.unparse(ast.BinOp(ast.Constant(0), node.op, ast.Constant(0)))[2:-2]
        return f"({self._emit(node.left)} {op} {self._emit(node.right)})"

    def _emit_UnaryOp(self, node):  # pylint: disable=invalid-name
        if isinstance(node.op, ast.Not):
            return f"int(not {self._emit(node.operand)})"
        op = {ast.USub: "-", ast.UAdd: "+", ast.Invert: "~"}[type(node.op)]
        return f"({op}{self._emit(node.operand)})"

    def _emit_BoolOp(self, node):  # pylint: disable=invalid-name
        op = " and " if isinstance(node.op, ast.And) else " or "
        return "(" + op.join(self._emit(v) for v in node.values) + ")"

    def _emit_Compare(self, node):  # pylint: disable=invalid-name
        parts = [self._emit(node.left)]
        for op, right in zip(node.ops, node.comparators):
            parts += [_CMP_SRC[type(op)], self._emit(right)]
        return f"int({' '.join(parts)})"

    def _emit_IfExp(self, node):  # pylint: disable=invalid-name
        return (
            f"({self._emit(node.body)} if {self._emit(node.test)} "
            f"else {self._emit(node.orelse)})"
        )


_CMP_SRC = {
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
}
