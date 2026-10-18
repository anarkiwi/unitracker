"""Modulator expressions: the format's grammar text, checked and compiled for the player.

Integer literals, ``+ - *``, ``//`` (floor division), ``%`` (floor modulo), ``abs``, ``min``,
``max``, a comparison ``(a op b)`` as 1 or 0 (op one of ``== != < <= > >=``), ``v``, ``φ``,
``N``, ``F(k)`` (the frequency of note k) and ``T[k]`` for a named playroutine table ``T``.
"""

import ast
import io
import re
import tokenize

PHI = "φ"
NAMES = {"v": "v", PHI: "phi", "N": "N"}
CALLS = {"abs": 1, "min": 2, "max": 2}
_BINARY = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.FloorDiv: "//", ast.Mod: "%"}
_DECIMAL = re.compile(r"0|[1-9][0-9]*")
_DIVIDE = {ast.FloorDiv: "//", ast.Mod: "%"}
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


def literals(text: str) -> None:
    """Raise unless each integer literal of expression ``text`` is decimal without leading
    zeros or ``_``."""
    try:
        numbers = [
            t.string
            for t in tokenize.generate_tokens(io.StringIO(text).readline)
            if t.type == tokenize.NUMBER
        ]
    except (tokenize.TokenError, SyntaxError) as exc:
        raise ExpressionError(f"bad expression {text!r}") from exc
    for number in numbers:
        if not _DECIMAL.fullmatch(number):
            raise ExpressionError(f"{number} in {text!r} is not a decimal integer")


def source(text: str, tables=()) -> tuple[str, set]:
    """``(Python source over v, phi, N, _F and _T, names read)`` of an expression text."""
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"bad expression {text!r}: {exc.msg}") from exc
    src = _Source(tables)
    return src(tree.body), src.reads


def canonical(text: str) -> str:
    """The one text of expression ``text`` (see :class:`_Printer`)."""
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"bad expression {text!r}: {exc.msg}") from exc
    return _Printer().text(tree.body)


class _Printer:
    """One text per expression: sums and products as linear forms (constant terms and factors
    folded, like terms merged), terms in one order, parentheses only where the grammar needs
    them, and decimal literals."""

    # pylint: disable=too-few-public-methods

    def text(self, node) -> str:
        """The text of expression node ``node``."""
        return self._sum(*self._linear(node))

    def _linear(self, node) -> tuple[int, dict]:
        """``(constant, {factors: coefficient})`` of ``node`` as a sum of products."""
        if isinstance(node, ast.Constant):
            return int(node.value), {}
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            const, terms = self._linear(node.operand)
            return -const, {f: -c for f, c in terms.items()}
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            sign = 1 if isinstance(node.op, ast.Add) else -1
            (a, at), (b, bt) = self._linear(node.left), self._linear(node.right)
            terms = dict(at)
            for f, c in bt.items():
                terms[f] = terms.get(f, 0) + sign * c
            return a + sign * b, {f: c for f, c in terms.items() if c}
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            return self._product(self._linear(node.left), self._linear(node.right))
        return 0, {(self._factor(node),): 1}

    def _product(self, left: tuple, right: tuple) -> tuple[int, dict]:
        (a, at), (b, bt) = left, right
        if not at or not bt:
            const, terms, k = (b, bt, a) if not at else (a, at, b)
            return const * k, {f: c * k for f, c in terms.items() if c * k}
        factors = []
        coef = 1
        for const, terms in (left, right):
            if not const and len(terms) == 1:
                ((f, c),) = terms.items()
                factors += f
                coef *= c
            else:
                factors.append(f"({self._sum(const, terms)})")
        return 0, {tuple(sorted(factors, key=_order)): coef}

    def _factor(self, node) -> str:
        """The text of a node that is no sum or product, as a factor."""
        # pylint: disable=too-many-return-statements
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Compare):
            op = _COMPARE[type(node.ops[0])]
            return f"({self.text(node.left)} {op} {self.text(node.comparators[0])})"
        if isinstance(node, ast.Call):
            args = [self.text(a) for a in node.args]
            if node.func.id in ("min", "max"):
                args.sort(key=_order)
            return f"{node.func.id}({', '.join(args)})"
        if isinstance(node, ast.Subscript):
            return f"{node.value.id}[{self.text(node.slice)}]"
        if isinstance(node, ast.BinOp):
            left, right = self._linear(node.left), self._linear(node.right)
            return f"{self._operand(*left, True)} {_DIVIDE[type(node.op)]} {self._operand(*right)}"
        raise ExpressionError(f"{ast.unparse(node)!r} is outside the grammar")

    def _operand(self, const: int, terms: dict, left: bool = False) -> str:
        """An operand of ``%`` or ``//``: a term on the left, a factor on the right."""
        text = self._sum(const, terms)
        if not terms or (len(terms) == 1 and not const and (left or _atomic(terms))):
            return text
        return f"({text})"

    def _sum(self, const: int, terms: dict) -> str:
        out = []
        for factors, coef in sorted(terms.items(), key=lambda t: _order("*".join(t[0]))):
            body = "*".join(
                f"({f})" if _divides(f) and (len(factors) > 1 or abs(coef) != 1) else f
                for f in factors
            )
            if abs(coef) != 1:
                body = f"{abs(coef)}*{body}"
            if not out:
                lead = "-" if coef < 0 else ""
                if coef == -1 and len(factors) == 1 and _divides(factors[0]):
                    body = f"({body})"
                out.append(lead + body)
            else:
                out.append((" - " if coef < 0 else " + ") + body)
        if const or not out:
            out.append(str(const) if not out else (" - " if const < 0 else " + ") + str(abs(const)))
        return "".join(out)


def _divides(factor: str) -> bool:
    """Whether a factor's text is a ``%`` or ``//`` at its top level."""
    depth = 0
    for k, ch in enumerate(factor):
        depth += {"(": 1, "[": 1, ")": -1, "]": -1}.get(ch, 0)
        if not depth and factor[k : k + 3] in (" % ", " //"):
            return True
    return False


def _atomic(terms: dict) -> bool:
    ((factors, coef),) = terms.items()
    return coef == 1 and len(factors) == 1 and not _divides(factors[0])


def _order(text: str) -> tuple:
    """The order of terms and factors: integers, names, then the rest, each by its text."""
    return (0 if text.lstrip("-").isdigit() else 1 if text in NAMES else 2, text)


def compile_expr(text: str, namespace: dict):
    """A function ``(v, phi, N) -> int`` of an expression text.

    ``namespace`` holds ``_F(k)``, the frequency of format note k, and ``_T``, each named
    table's function of its index.
    """
    body, _ = source(text, namespace["_T"])
    return eval(f"lambda v, phi, N: {body}", namespace)  # pylint: disable=eval-used
