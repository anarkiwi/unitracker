"""Tally the vocabulary of generic documents: events, commands, row keys, expression operators.

    python tools/vocab_census.py DOC.yaml ... [--json OUT.json]

Over documents parsed with ``unitracker_grammar.parse``: each form's documents and occurrences.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from lark import Token, Tree
from tqdm import tqdm
from unitracker_grammar import parse

CHAINS = ("cmd_", "row_", "ins_")
KINDS = {
    "RESTART": "restart",
    "INS": "ins",
    "CTRL": "ctrl",
    "PW": "pw",
    "FILTER": "filter",
    "X": "x",
    "PHRASE": "phrase",
}
OPS = {
    "MUL": "*",
    "FLOORDIV": "//",
    "MOD": "%",
    "ABS": "abs(",
    "MIN": "min(",
    "MAX": "max(",
    "F": "F(",
    "NEG": "neg",
    "V": "v",
    "PHI": "φ",
    "N": "N",
}
EXPRESSIONS = ("fexpr", "expr")


def _link(node) -> bool:
    return isinstance(node, Tree) and node.data.startswith(CHAINS)


def _split(node: Tree) -> tuple:
    """A chain link's own children and the next link, or None."""
    own = [c for c in node.children if not _link(c)]
    return own, next((c for c in node.children if _link(c)), None)


def _tokens(node) -> list:
    if isinstance(node, Token):
        return [node]
    return list(node.scan_values(lambda v: isinstance(v, Token)))


class Census:
    """Counts keyed by ``(section, item)``, and the documents that use each."""

    def __init__(self):
        self.count = Counter()
        self.docs = defaultdict(set)

    def add(self, doc: str, section: str, item: str, n: int = 1) -> None:
        """Record ``n`` occurrences of ``item`` in ``section`` for ``doc``."""
        self.count[(section, item)] += n
        self.docs[(section, item)].add(doc)

    def document(self, doc: str, text: str) -> None:
        """Tally document ``text``, named ``doc``."""
        for node in parse(text).children:
            if isinstance(node, Tree):
                getattr(self, f"_{node.data}")(doc, node)

    def _meta(self, doc, node):
        for item in node.children:
            self.add(doc, "meta key", item.children[0].value)

    def _clock(self, doc, _node):
        self.add(doc, "header", "clock")

    def _voices(self, doc, _node):
        self.add(doc, "header", "voices")

    def _tempo(self, doc, node):
        self.add(doc, "header", "tempo swing" if len(node.children) == 2 else "tempo")

    def _tables(self, doc, node):
        for tok in node.children:
            if isinstance(tok, Token):
                self.add(doc, "table", tok.value)

    def _init(self, doc, node):
        self.add(doc, "header", "init")
        for event in node.children:
            self.event(doc, "init", event)

    def _row(self, doc, node):
        sections = [c for c in node.children if c.data == "section"]
        self.add(doc, "timeline", "def row" if sections else "play row")
        for section in sections:
            self.section(doc, section)
        for play in node.children[-1].children:
            types = [t.type for t in play.children]
            form = {"CONT": "~", "END": "end"}.get(types[0], "phrase")
            self.add(doc, "play entry", "transposed" if "SIGNED" in types else form)

    def _ending(self, doc, node):
        self.add(doc, "timeline", "loop" if node.children else "end")

    def section(self, doc: str, node: Tree) -> None:
        """Tally one record type's definitions in a row."""
        kind = KINDS[node.children[0].type]
        values = node.children[1::2]
        self.add(doc, "record type", kind, len(values))
        for value in values:
            if kind == "phrase":
                for event in value.children:
                    self.event(doc, "phrase", event)
            elif kind == "ins":
                self.chain(doc, "ins", _split(value)[1])
            else:
                self.value(doc, kind, [value])

    def chain(self, doc: str, where: str, node) -> list:
        """Tally ``(where key, key)`` and ``(where key, shape)`` of each key the chain of links
        from ``node`` writes; the keys in order."""
        keys = []
        while node is not None:
            own, nxt = _split(node)
            if own:
                key = node.data.split("_", 1)[1]
                key = f"!{key}" if node.data.startswith("cmd_") else key
                keys.append(key)
                self.add(doc, f"{where} key", key)
                self.add(doc, f"{where} {key}", self.value(doc, f"{where} {key}", own))
            node = nxt
        return keys

    def value(self, doc: str, where: str, nodes: list) -> str:
        """How ``nodes`` are written (token types, ``program``, ``int`` or ``expr``), tallying
        the programs and expressions among them."""
        shapes = []
        for node in nodes:
            if isinstance(node, Token):
                shapes.append(node.type.lower())
            elif node.data == "program":
                self.program(doc, where.split()[-1], node)
                shapes.append("program")
            elif node.data in EXPRESSIONS:
                shapes.append(self.expression(doc, where, node))
            else:
                shapes.append(self.value(doc, where, node.children))
        return "/".join(shapes)

    def program(self, doc: str, kind: str, node: Tree) -> None:
        """Tally a program's rows, their keys and its ending."""
        rows = [c for c in node.children if isinstance(c, Tree)]
        self.add(doc, f"{kind} program", "loop" if node.children[-1].type == "INT" else "end")
        self.add(doc, f"{kind} rows", "row", len(rows))
        for row in rows:
            if not row.children:
                self.add(doc, f"{kind} rows", "empty row")
            self.chain(doc, f"{kind} row", next(iter(row.children), None))

    def expression(self, doc: str, where: str, node: Tree) -> str:
        """Tally an expression's operators under ``where ops``; ``int`` for a constant."""
        ops = Counter()
        for tok in _tokens(node):
            if tok.type in ("ADD", "CMP"):
                ops[tok.value.strip()] += 1
            elif tok.type == "XTABLE":
                ops[f"{tok.value}[]"] += 1
            elif tok.type in OPS:
                ops[OPS[tok.type]] += 1
        for op, n in ops.items():
            self.add(doc, f"{where} ops", op, n)
        return "expr" if ops else "int"

    def event(self, doc: str, where: str, node: Tree) -> None:
        """Tally an event's note form, instrument, commands, keep flags and repeat."""
        tokens = [c for c in node.children if isinstance(c, Token)]
        note = tokens[0].value
        named = any(t.type == "INS" for t in tokens)
        self.add(doc, f"{where} note", note if note in ("-", "off", "on") else "pitch")
        self.add(doc, f"{where} event", "names instrument" if named else "no instrument")
        if tokens[-1].type == "INT":
            self.add(doc, f"{where} event", "*steps")
        trees = [c for c in node.children if isinstance(c, Tree)]
        keys = self.chain(doc, f"{where} command", trees[0])
        for keep in trees[1:]:
            if keep.children:
                keys.append(keep.children[0].value)
                self.add(doc, f"{where} command key", keys[-1])
        if len(keys) > 1:
            self.add(doc, f"{where} event", f"{len(keys)} commands")
            self.add(doc, f"{where} command set", "+".join(keys))


def report(census: Census, total: int) -> str:
    """One table per section: item, documents using it, occurrences."""
    lines = [f"## {total} documents"]
    by_section = defaultdict(list)
    for (section, item), n in census.count.items():
        by_section[section].append((item, len(census.docs[(section, item)]), n))
    for section in sorted(by_section):
        lines.append(f"\n{section}:")
        for item, docs, n in sorted(by_section[section], key=lambda t: (-t[1], -t[2], t[0])):
            lines.append(f"  {item:28s} {docs:4d} docs {n:8d}")
    return "\n".join(lines)


def main(argv=None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("docs", type=Path, nargs="+")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    census = Census()
    for path in tqdm(args.docs, unit="doc"):
        census.document(str(path), path.read_text(encoding="utf-8"))
    print(report(census, len(args.docs)))
    if args.json:
        out = {f"{s}|{i}": [n, len(census.docs[(s, i)])] for (s, i), n in census.count.items()}
        args.json.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
