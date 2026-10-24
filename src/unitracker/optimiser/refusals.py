"""The edits the guarantee refused, keyed by the parts of the document each touches and reads
(transitively, with the header, tables, init row and rows every part plays under) and their
values, so a later round proposes one again only once one of those parts changed."""

from unitracker_grammar import code
from unitracker_grammar import document as model

ALWAYS = (("header",), ("tables",), ("init",), ("rows",))


def parts(doc: model.Document) -> dict:
    """``{part: value}`` of ``doc``: ``("header",)``, ``("tables",)``, ``("init",)``,
    ``("rows",)`` (with the loop), ``("mod", name)`` per template and ``(kind, name)`` per
    record, a template's by its signature's name."""
    out = {
        ("header",): doc.header,
        ("tables",): doc.tables,
        ("init",): doc.init,
        ("rows",): (doc.rows, doc.loop),
    }
    out.update({("mod", _name(k)): (k, v) for k, v in doc.mods})
    out.update({(s.kind, _name(k)): (k, v) for s in doc.defs for k, v in s.records})
    return out


def _name(key: str) -> str:
    return code.signature(key)[0] if "(" in key else key


def references(value) -> set:
    """The names of the definitions ``value`` references (records' names, templates' calls)."""
    found = set()

    def visit(ref, _site):
        found.add(ref.name if isinstance(ref, (model.Call, model.Def)) else ref)
        return ref

    model._walk(value, visit, "run")  # pylint: disable=protected-access
    return {_name(n) for n in found}


class Refusals:
    """The keys of refused edits; :meth:`key` of an edit on a document, given its result."""

    def __init__(self):
        self.refused, self._doc, self._parts, self._index = set(), None, {}, {}

    def _of(self, doc: model.Document) -> dict:
        if doc is not self._doc:
            self._doc, self._parts, self._index = doc, parts(doc), {}
            for part in self._parts:
                if len(part) == 2:
                    self._index.setdefault(part[1], []).append(part)
        return self._parts

    def key(self, name: str, edit, doc: model.Document, after: model.Document) -> tuple:
        """The key of ``edit`` of pass ``name`` from ``doc`` to ``after``: its touched parts,
        what they read and :data:`ALWAYS`, each with its value in ``doc``."""
        before, changed = self._of(doc), parts(after)
        touched = {p for p in before.keys() | changed.keys() if before.get(p) != changed.get(p)}
        need, seen = [p for p in touched if p in before], set(ALWAYS) | touched
        while need:
            part = need.pop()
            for ref in references(before[part][1] if len(part) == 2 else before[part]):
                for part in self._index.get(ref, ()):
                    if part not in seen:
                        seen.add(part)
                        need.append(part)
        return (name, edit.label, tuple(sorted((p, before.get(p)) for p in seen)))

    def known(self, key: tuple) -> bool:
        """Whether ``key``'s edit was refused with every part it touches and reads as now."""
        return key in self.refused

    def add(self, key: tuple):
        """Remembers ``key``'s edit as refused."""
        self.refused.add(key)
