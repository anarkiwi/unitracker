"""What the v2 reader and writer share: the format error, the library and the rule language."""

import ast
import functools
import re
from importlib import resources

import yaml


class SongFormatError(ValueError):
    """A document does not follow the unitracker v2 format."""


@functools.cache
def _library_text(name: str) -> str:
    path = resources.files("unitracker").joinpath(f"playroutines/{name}.yaml")
    if not path.is_file():
        raise SongFormatError(f"no playroutine {name!r} in the library")
    return path.read_text()


def library(name: str) -> dict:
    """The library definition of playroutine ``name``."""
    return yaml.safe_load(_library_text(name))


class View:
    """The parts of a playroutine both the reader and the writer consult."""

    # pylint: disable=too-few-public-methods

    def __init__(self, playroutine: dict):
        from unitracker.records import record_types  # pylint: disable=import-outside-toplevel

        self.rtypes = record_types(playroutine)
        inst = playroutine.get("instrument") or {}
        self.ins_type = inst.get("type")
        self.ins_base = int(inst.get("base", 0))
        self.commands = playroutine.get("commands") or {}
        self.offset = int(playroutine.get("note_offset", 0))


def is_spec(value) -> bool:
    """Whether a record value is a spec mapping."""
    return isinstance(value, dict) and set(value) != {"live"}


def is_live(value) -> bool:
    """Whether a record value is a live expression."""
    return isinstance(value, dict) and set(value) == {"live"}


class _Params:
    """A spec's parameters as attributes for rules; a missing number reads as -1."""

    # pylint: disable=too-few-public-methods

    def __init__(self, spec):
        self._spec = spec

    def __getattr__(self, name):
        value = self._spec.get("for" if name == "for_" else name)
        if isinstance(value, dict):
            return _Params(value)
        return -1 if value is None else value


_RULE_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.IfExp,
    ast.Name,
    ast.Constant,
    ast.Attribute,
    ast.Subscript,
    ast.Call,
    ast.Load,
    ast.operator,
    ast.unaryop,
    ast.cmpop,
    ast.boolop,
)


@functools.cache
def _rule_code(text: str):
    tree = ast.parse(text, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _RULE_NODES):
            raise SongFormatError(f"rule {text!r}: {type(node).__name__} not allowed")
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in ("has", "count", "isspec")
        ):
            raise SongFormatError(f"rule {text!r}: only has(), count() and isspec() are calls")
    return compile(tree, "<rule>", "eval")


def check_rules(rules, values: dict, defaults, where: str):
    """Raise SongFormatError naming the first rule ``values`` break."""
    if not rules:
        return
    values = {("for_" if k == "for" else k): v for k, v in values.items()}
    names = {}
    for key, value in values.items():
        if is_spec(value):
            names[key] = _Params(value)
        elif isinstance(value, list):
            names[key] = value
        elif isinstance(value, (int, bool)):
            names[key] = int(value)
    env = {
        "has": lambda k: k in values,
        "count": lambda *k: len(values[k[0]]) if k else len(values),
        "isspec": lambda k: is_spec(values.get(k)),
    }

    class _Names(dict):
        def __missing__(self, name):
            if name in ("has", "count", "isspec"):
                return env[name]
            return defaults(name)

    scope = _Names(names)
    for rule in rules:
        text = rule["check"]
        code = _rule_code(_quote_names(text))
        try:
            ok = eval(code, {"__builtins__": {}}, scope)  # pylint: disable=eval-used
        except (TypeError, IndexError, KeyError) as exc:
            raise SongFormatError(f"{where}: rule {text!r} cannot apply: {exc}") from exc
        if not ok:
            raise SongFormatError(f"{where}: {rule['error']}")


def _quote_names(text: str) -> str:
    """``has(x)``, ``isspec(x)`` and ``count(x)`` take key names; quote them."""
    return re.sub(r"\b(has|isspec|count)\((\w+)\)", r"\1('\2')", text)
