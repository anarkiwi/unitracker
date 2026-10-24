"""The ``repeats`` pass: phrases' events and the song's rows written with groups and sections
(docs/optimiser.md), each the same document once its sugar is expanded."""

from bisect import bisect_right
from collections import Counter
from dataclasses import replace
from functools import lru_cache, partial

import numpy as np
from unitracker_grammar.document import (  # pylint: disable=protected-access
    Document,
    Group,
    Part,
    Play,
    Section,
    top_level,
    _events,
    _item,
)
from unitracker_grammar.expand import flat
from unitracker_player.streams import repair

from unitracker.optimiser.measure import size
from unitracker.optimiser.passes import Edit
from unitracker.optimiser.tree import transform, walk

COMMA, OPEN, BAR, CLOSE = size(","), size("["), size("|"), size("]")
LINE = size(" - \n")


def lcp(ids: np.ndarray) -> np.ndarray:
    """``L[a, b]`` (``a < b``): the length of the longest common prefix of ``ids[a:]`` and
    ``ids[b:]``, one vectorised run length per diagonal."""
    n = len(ids)
    out = np.zeros((n + 1, n + 1), np.int32)
    for d in range(1, n):
        idx = np.arange(n - d)
        stop = np.where(ids[:-d] == ids[d:], n - d, idx)
        out[idx, idx + d] = np.minimum.accumulate(stop[::-1])[::-1] - idx
    return out


class Encoder:  # pylint: disable=too-few-public-methods
    """The cheapest encoding of a sequence as items and groups by an exact interval DP: each
    item of ``costs[k]`` tokens, a tandem repeat ``[B]xk`` or a volta ``[B|E1|E2...]`` (endings
    of one length, the body maximal) of encoded parts, ``single(k, n)`` the cost of
    symbol ``k`` repeated ``n`` times as one item (None: no such form)."""

    def __init__(self, symbols: list, costs: list, single=None):
        dense = {}
        ids = np.array([dense.setdefault(s, len(dense)) for s in symbols], np.int64)
        self.costs, self.single = costs, single
        self.lcp = lcp(ids)
        self.cands = [sorted(self._candidates(a)) for a in range(len(ids))]
        self.ends = [[c[0] for c in cands] for cands in self.cands]
        self.gcost = [[None] * len(cands) for cands in self.cands]
        self.memo = {}

    def _primitive(self, a: int, p: int) -> bool:
        return all(p % q or self.lcp[a, a + q] < p - q for q in range(1, p))

    def _candidates(self, a: int):
        """``(end, kind, params)`` of each group starting at ``a``."""
        rest, row = len(self.costs) - a, self.lcp[a]
        for p in range(1, rest // 2 + 1):
            if row[a + p] >= p and self._primitive(a, p):
                for k in range(2, 2 + row[a + p] // p):
                    yield a + k * p, "rep", (p, k)
                    if p == 1 and self.single and self.single(a, k) is not None:
                        yield a + k, "single", (k,)
        for d in np.nonzero(row[a + 2 : a + rest // 2 + 1] >= 1)[0] + 2:
            body, d = d - 1, int(d)
            for m in range(1, rest // d):
                body = min(body, int(row[a + m * d]))
                if body < 1:
                    break
                tail = d - body
                if any(self.lcp[a + body, a + j * d + body] < tail for j in range(1, m + 1)):
                    yield a + (m + 1) * d, "volta", (d, body, m + 1)

    def _parts(self, a: int, idx: int) -> list:
        """The spans ``(i, j)`` candidate ``idx`` at ``a`` encodes: its body, then its endings."""
        _, kind, params = self.cands[a][idx]
        if kind == "single":
            return []
        if kind == "rep":
            return [(a, a + params[0])]
        d, b, n = params
        return [(a, a + b)] + [(a + m * d + b, a + (m + 1) * d) for m in range(n)]

    def _cost(self, a: int, idx: int) -> int:
        """The tokens of candidate ``idx`` at ``a``, its separator left out."""
        cost = self.gcost[a][idx]
        if cost is None:
            _, kind, params = self.cands[a][idx]
            parts = sum(self.best(i, j)[0] for i, j in self._parts(a, idx))
            if kind == "single":
                cost = self.single(a, params[0])
            elif kind == "rep":
                cost = OPEN + parts - COMMA + size(f"]x{params[1]}")
            else:
                cost = OPEN + parts - (params[2] + 1) * COMMA + params[2] * BAR + CLOSE
            self.gcost[a][idx] = cost
        return cost

    def _item(self, a: int, idx: int) -> tuple:
        """Candidate ``idx`` at ``a`` as an encoding item, its parts encoded."""
        _, kind, params = self.cands[a][idx]
        parts = [self.best(i, j)[1] for i, j in self._parts(a, idx)]
        if kind == "single":
            return ("single", a, params[0])
        if kind == "rep":
            return ("rep", parts[0], params[1])
        return ("volta", parts[0], tuple(parts[1:]))

    def best(self, i: int, j: int, sep: int = COMMA) -> tuple:
        """``(cost, items)`` of ``[i, j)``, each item followed by ``sep`` tokens: an item a
        position, ``("rep", items, k)``, ``("volta", items, endings)`` or ``("single", k, n)``."""
        if sep == COMMA and (i, j) in self.memo:
            return self.memo[(i, j)]
        cost, back = [0] + [np.inf] * (j - i), [None] * (j - i + 1)
        for a in range(i, j):
            here = cost[a - i]
            if here + self.costs[a] + sep < cost[a - i + 1]:
                cost[a - i + 1], back[a - i + 1] = here + self.costs[a] + sep, (a, None)
            for idx in range(bisect_right(self.ends[a], j)):
                end = self.ends[a][idx]
                total = here + self._cost(a, idx) + sep
                if total < cost[end - i]:
                    cost[end - i], back[end - i] = total, (a, idx)
        items, t = [], j
        while t > i:
            a, idx = back[t - i]
            items.append(a if idx is None else self._item(a, idx))
            t = a
        out = (cost[j - i], tuple(reversed(items)))
        if sep == COMMA:
            self.memo[(i, j)] = out
        return out


def encoded(symbols: list, costs: list, sep: int = COMMA, single=None) -> tuple:
    """``(cost, items)``: the cheapest encoding of ``symbols`` (:class:`Encoder`)."""
    return Encoder(symbols, costs, single).best(0, len(symbols), sep)


def built(items: tuple, leaf, single=None) -> tuple:
    """The model items of an encoding: ``leaf(k)`` of a position, a Group of a group,
    ``single(k, n)`` of a single symbol's repeat."""
    out = []
    for item in items:
        if not isinstance(item, tuple):
            out.append(leaf(item))
        elif item[0] == "single":
            out.append(single(item[1], item[2]))
        elif item[0] == "rep":
            out.append(Group(built(item[1], leaf, single), item[2]))
        else:
            ends = tuple(built(e, leaf, single) for e in item[2])
            out.append(Group(built(item[1], leaf, single), None, ends))
    return tuple(out)


# ---- phrases --------------------------------------------------------------------------------


@lru_cache(maxsize=None)
def _event_cost(event) -> int:
    """An event's tokens alone, its note with its octave (an upper bound of its cost)."""
    return size(_events((event,), phrase=False)[1:-1])


def grouped(events: tuple) -> tuple:
    """A phrase's events (groups played out) in their cheapest encoding with groups."""
    events = flat(events)
    _, items = encoded(list(events), [_event_cost(e) for e in events])
    return built(items, events.__getitem__)


def _phrase_record(name: str, played: tuple, new: tuple, doc: Document) -> Document:
    def section(s):
        if s.kind != "phrase":
            return s
        records = tuple(
            (k, new if k == name and isinstance(v, tuple) and flat(v) == played else v)
            for k, v in s.records
        )
        return replace(s, records=records)

    return replace(doc, defs=tuple(section(s) for s in doc.defs))


def _inline_phrase(played: tuple, new: tuple, doc: Document) -> Document:
    def node(n):
        if isinstance(n, Play) and isinstance(n.phrase, tuple) and flat(n.phrase) == played:
            return replace(n, phrase=new)
        return n

    return transform(doc, node)


def _phrase_edits(doc: Document) -> list:
    """One edit per phrase record and per distinct inline phrase whose encoding changes."""
    edits = []
    for name, value in doc.records("phrase").items():
        if isinstance(value, tuple) and (new := grouped(value)) != value:
            edits.append(Edit(f"phrase {name}", partial(_phrase_record, name, flat(value), new)))
    found, changed = {}, {}
    for _, node in walk(doc):
        if isinstance(node, Play) and isinstance(node.phrase, tuple):
            played = flat(node.phrase)
            new = found.setdefault(played, grouped(played))
            if new != node.phrase:
                changed.setdefault(played, new)
    for k, (played, new) in enumerate(changed.items()):
        edits.append(Edit(f"inline phrase {k + 1}", partial(_inline_phrase, played, new)))
    return edits


# ---- the song -------------------------------------------------------------------------------


def song_rows(doc: Document) -> tuple:
    """``(rows, loop)``: the song's sections and groups played out into rows, ``loop`` the row
    its loop item starts (None for ``end``)."""
    sections = top_level(doc).records("section")

    def rows(items: tuple) -> list:
        out = []
        for item in flat(items):
            out += (
                rows(sections[item.section]) * (item.times or 1)
                if isinstance(item, Part)
                else [item]
            )
        return out

    starts, out = [], []
    for item in top_level(doc).rows:
        starts.append(len(out))
        out += rows((item,))
    if doc.loop is None:
        return tuple(out), None
    return tuple(out), starts[doc.loop] if doc.loop < len(starts) else len(out)


class _Rules:
    """Re-Pair's rules over the song's rows (a row the symbol ``(k,)`` of ``rows[k]``), those
    whose definition does not pay inlined to a fixpoint."""

    def __init__(self, rows: tuple, split: int):
        index = {}
        ids = [(index.setdefault(r, len(index)),) for r in rows]
        self.rows = list(index)
        self.row_cost = [size(_item(r)) for r in self.rows]
        grammar = repair([ids[:split], ids[split:]])
        self.starts, self.rules = grammar.starts, grammar.rules
        self.kept, self.memo = set(range(len(self.rules))), {}

    def expand(self, seq) -> tuple:
        """``seq`` with every rule not kept written out."""
        out = []
        for x in seq:
            out += self.expand(self.rules[x]) if isinstance(x, int) and x not in self.kept else [x]
        return tuple(out)

    def encode(self, seq: tuple, name, sep: int = COMMA) -> tuple:
        """``(cost, items)`` of a sequence of rows and kept rules, rule ``x`` named ``name(x)``."""

        def single(k, n):
            return size(f"{name(seq[k])}x{n}") if isinstance(seq[k], int) else None

        costs = [size(name(x)) if isinstance(x, int) else self.row_cost[x[0]] for x in seq]
        return encoded(list(seq), costs, sep, single)

    def _unpaid(self) -> int | None:
        """The first kept rule whose definition costs at least what its uses save."""
        bodies = {r: self.expand(self.rules[r]) for r in sorted(self.kept)}
        seqs = [self.expand(s) for s in self.starts] + list(bodies.values())
        uses = Counter(x for s in seqs for x in s if isinstance(x, int))
        label = f"s{len(self.kept)}"
        ref, head = size(label), size(f" {label}:[") + CLOSE + size("\n") - COMMA
        for r, body in bodies.items():
            if (body, label) not in self.memo:
                self.memo[(body, label)] = self.encode(body, lambda _: label)[0]
            cost = self.memo[(body, label)]
            if head + cost + uses[r] * (ref + COMMA) >= uses[r] * cost:
                return r
        return None

    def fixpoint(self):
        """Inline the unpaid rules, one at a time, until each kept one pays."""
        while (r := self._unpaid()) is not None:
            self.kept.discard(r)


def _restructured(rows: tuple, loop: int | None) -> tuple:
    """``(items, loop, records)``: the song items of ``rows`` in sections and groups, the
    prefix before the loop row and the looped rows encoded apart over shared sections."""
    rules = _Rules(rows, len(rows) if loop is None else loop)
    rules.fixpoint()
    names, order = {}, []

    def name(seq):
        for x in seq:
            if isinstance(x, int) and x not in names:
                names[x] = f"s{len(names) + 1}"
                order.append(x)
                name(rules.expand(rules.rules[x]))

    tops = [rules.expand(s) for s in rules.starts]
    name(tops[0] + tops[1])

    def items(seq: tuple, sep: int) -> tuple:
        def leaf(k):
            return Part(names[seq[k]]) if isinstance(seq[k], int) else rules.rows[seq[k][0]]

        _, encoding = rules.encode(seq, names.__getitem__, sep)
        return built(encoding, leaf, lambda k, n: Part(names[seq[k]], n))

    prefix, suffix = items(tops[0], LINE), items(tops[1], LINE)
    records = tuple((names[r], items(rules.expand(rules.rules[r]), COMMA)) for r in order)
    return prefix + suffix, None if loop is None else len(prefix), records


@lru_cache(maxsize=8)
def _song_of(rows: tuple, loop: int | None) -> tuple:
    return _restructured(rows, loop)


def _song(doc: Document) -> Document:
    """``doc`` with its song restructured (:func:`_restructured`)."""
    items, loop, records = _song_of(*song_rows(doc))
    doc = top_level(doc)
    defs = tuple(s for s in doc.defs if s.kind != "section")
    defs += (Section("section", records),) if records else ()
    return replace(doc, defs=defs, rows=items, loop=loop)


def repeats(doc: Document) -> list:
    """Each phrase (a record, an inline phrase) whose cheapest encoding with groups differs,
    then the song in sections and groups where its structure changes."""
    edits = _phrase_edits(doc)
    if _song(doc) != doc:
        edits.append(Edit("song", _song))
    return edits
