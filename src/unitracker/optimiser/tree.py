"""Paths into, walks over and rebuilds of a document model (``unitracker_grammar.document``)."""

from dataclasses import fields, is_dataclass, replace

from unitracker_grammar.document import Code, Expr, Header


def _leaf(node) -> bool:
    return not isinstance(node, tuple) and not is_dataclass(node) or isinstance(node, (Code, Expr))


def _children(node):
    if is_dataclass(node):
        return [(f.name, getattr(node, f.name)) for f in fields(node) if f.compare]
    return list(enumerate(node)) if isinstance(node, tuple) else []


def walk(node, path: tuple = ()):
    """``(path, node)`` of ``node`` and of every dataclass and tuple below it, but the header's
    meta and the insides of code and expressions; a path names fields and tuple indices."""
    yield path, node
    for key, child in _children(node):
        if not _leaf(child) and not (isinstance(node, Header) and key == "meta"):
            yield from walk(child, path + (key,))


def _rebuilt(node, changed: dict):
    if not changed:
        return node
    if is_dataclass(node):
        return replace(node, **changed)
    items = [changed.get(i, c) for i, c in enumerate(node)]
    return type(node)._make(items) if hasattr(node, "_make") else tuple(items)


def at(node, path: tuple):
    """The node at ``path`` below ``node``."""
    for key in path:
        node = getattr(node, key) if isinstance(key, str) else node[key]
    return node


def update(node, path: tuple, fn):
    """``node`` with its node at ``path`` replaced by ``fn`` of it."""
    if not path:
        return fn(node)
    key, child = path[0], at(node, path[:1])
    return _rebuilt(node, {key: update(child, path[1:], fn)})


def transform(node, fn):
    """``node`` rebuilt bottom up, each dataclass or tuple below it (and it) replaced by ``fn``
    of it once its children are."""
    if _leaf(node):
        return node
    changed = {}
    for key, child in _children(node):
        new = transform(child, fn)
        if new is not child:
            changed[key] = new
    return fn(_rebuilt(node, changed))
