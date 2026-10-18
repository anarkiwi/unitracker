"""One spelling per value in a document's YAML: a sign only on a relative note (``+0``, never
``-0``), and quotes only where the writer quotes (docs/format/document.md)."""

import yaml

from unitracker.format.yamlstyle import Dumper, Flow, Quoted, Signed

RELATIVE = "note"


class SpellingError(ValueError):
    """A value written otherwise than its one spelling."""


def _style(text: str, flow: bool) -> str:
    """The quote the writer writes ``text`` with, in a flow collection or a block mapping; ""
    for none."""
    if flow:
        out = yaml.dump(Flow([text]), Dumper=Dumper, width=1 << 30, allow_unicode=True)[1:]
    else:
        out = yaml.dump({"k": text}, Dumper=Dumper, width=1 << 30, allow_unicode=True)[3:]
    return out[0] if out[:1] in ("'", '"') and not text.startswith(out[0]) else ""


def _quoted(value: Quoted) -> str | None:
    """Why quoted scalar ``value`` is not spelled as the writer spells it, or None."""
    text = str(value)
    style = _style(text, value.flow)
    if not style:
        return f"{text!r} is written without quotes"
    if style != value.style:
        return f"{text!r} is quoted {style}...{style}"
    return None


def plain(value, what: str) -> None:
    """Raise for ``value`` written in quotes where ``what`` is written plain."""
    if isinstance(value, Quoted):
        raise SpellingError(f"{what} {str(value)!r} is written without quotes")


def _signed(key, value: Signed) -> str | None:
    """Why signed integer ``value`` under ``key`` is not spelled as the writer spells it."""
    if value.text == "-0":
        return f"{key}: -0 is written {'+0' if key == RELATIVE else '0'}"
    if value.text.startswith("+") and key != RELATIVE:
        return f"{key}: {value.text} is written {int(value)}"
    return None


def check(node, key=None) -> None:
    """Raise for a value of parsed document ``node`` spelled otherwise than the writer spells
    it; ``key`` is the mapping key it is under."""
    if isinstance(node, dict):
        for k, v in node.items():
            check(k, key)
            check(v, k)
        return
    if isinstance(node, list):
        for v in node:
            check(v, key)
        return
    problem = None
    if isinstance(node, Quoted):
        problem = _quoted(node)
    elif isinstance(node, Signed):
        problem = _signed(key, node)
    if problem is not None:
        raise SpellingError(problem)
