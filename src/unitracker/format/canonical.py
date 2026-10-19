"""The canonical document grammar, ``canonical.lark``, and a parser for it."""

import functools
from importlib import resources

from lark import Lark, exceptions

GRAMMAR = resources.files("unitracker.format") / "canonical.lark"


class CanonicalError(ValueError):
    """A text that is not a canonical document."""


@functools.lru_cache(maxsize=1)
def parser() -> Lark:
    """The LALR parser of the canonical grammar."""
    return Lark(GRAMMAR.read_text(encoding="utf-8"), parser="lalr", lexer="contextual")


def parse(text: str):
    """The parse tree of canonical document ``text``."""
    try:
        return parser().parse(text)
    except exceptions.UnexpectedInput as exc:
        raise CanonicalError(
            f"line {exc.line} column {exc.column}: {str(exc).splitlines()[0]}"
        ) from exc
