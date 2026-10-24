"""Coupled code modulators: the code templates of a cell that share declared state, grouped,
and the plans that replace a group's calls together by expressions fitted to the cell values
the group leaves (docs/optimiser.md, Lossy mode)."""

from dataclasses import dataclass, field

import numpy as np
from unitracker_grammar import code as _code
from unitracker_grammar import frequency
from unitracker_grammar.document import Code, Document

from unitracker.optimiser import fit
from unitracker.optimiser.trace import CELLS, FIRING, Trace

IDENTITY = ("keep", "v", False)


@dataclass(frozen=True)
class Group:
    """Code templates of one cell that share state, by name, and the state they share."""

    cell: str
    templates: tuple
    state: tuple

    @property
    def name(self) -> str:
        """The group's report name, its templates joined by ``+``."""
        return "+".join(self.templates)


@dataclass
class Plan:
    """A group's replacement: the slot ``host`` whose calls take expressions fitted to the
    group's output (``hosted``, each call's fitting forms), the calls before it that keep
    their own output (``own``, likewise) and every other call of the group, which ends its
    binding (``ended``)."""

    host: str
    hosted: dict
    own: dict = field(default_factory=dict)
    ended: set = field(default_factory=set)


def state_of(doc: Document) -> dict:
    """``{template: (cell, state variables it names)}`` of the code templates naming declared
    state."""
    cells = {n: cell for cell, names in doc.header.state or () for n, _ in names}
    out = {}
    for key, body in doc.mods:
        if isinstance(body, Code):
            names = {s.split(".")[0] for s in _code.symbols(body.statements)} & set(cells)
            if names:
                out[_code.signature(key)[0]] = (cells[min(names)], frozenset(names))
    return out


def groups(doc: Document) -> list:
    """The :class:`Group` of each set of two or more code templates joined by the state
    variables they share (a connected component), in name order."""
    used = state_of(doc)
    parent = {name: name for name in used}

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    owner = {}
    for name in sorted(used):
        for var in sorted(used[name][1]):
            if var in owner:
                parent[root(name)] = root(owner[var])
            owner.setdefault(var, name)
    members = {}
    for name in sorted(used):
        members.setdefault(root(name), []).append(name)
    return [
        Group(
            used[names[0]][0],
            tuple(names),
            tuple(sorted(set().union(*(used[n][1] for n in names)))),
        )
        for names in members.values()
        if len(names) > 1
    ]


def _segments(rows: np.ndarray) -> np.ndarray:
    """The index each firing's tick on its cell starts at, firings in order."""
    new = np.ones(len(rows), dtype=bool)
    new[1:] = (rows["t"][1:] != rows["t"][:-1]) | (rows["owner"][1:] != rows["owner"][:-1])
    return np.maximum.accumulate(np.where(new, np.arange(len(rows)), 0))


@dataclass
class _Ticks:
    """A group's firings on its cell: ``rows`` every firing there, ``seg`` the index its tick's
    firings start at, ``member`` which are the group's, ``first`` and ``last`` the index of its
    tick's first and last member firing (-1 without one)."""

    rows: np.ndarray
    seg: np.ndarray
    member: np.ndarray
    first: np.ndarray
    last: np.ndarray

    @classmethod
    def of(cls, tr: Trace, group: Group) -> "_Ticks":
        """The firings of ``group`` in ``tr``."""
        ks = [k for k, (call, _) in enumerate(tr.keys) if call.name in group.templates]
        rows = tr.chain[tr.chain["cell"] == CELLS.index(group.cell)]
        member = np.isin(rows["key"], ks)
        seg = _segments(rows)
        idx = np.arange(len(rows))
        first = np.full(len(rows) + 1, len(rows))
        last = np.full(len(rows) + 1, -1)
        np.minimum.at(first, seg[member], idx[member])
        np.maximum.at(last, seg[member], idx[member])
        first, last = first[seg], last[seg]
        return cls(rows, seg, member, np.where(last >= 0, first, -1), last)

    def interleaved(self) -> np.ndarray:
        """The other modulators' firings between a tick's member firings."""
        idx = np.arange(len(self.rows))
        return ~self.member & (idx > self.first) & (idx < self.last)

    def orphans(self, at: np.ndarray, own: bool, h: int) -> tuple:
        """``(predicted, reference, alone, N)`` at each tick the group fires but not in slot
        ``h`` (its firings ``at``): the cell the group's input leaves, or with ``own`` its
        last firing before ``h``, the group's output, whether nothing else fires, the note."""
        rows, idx, n = self.rows, np.arange(len(self.rows)), len(self.rows)
        lead = idx[(self.first == idx) & ~np.isin(self.seg, self.seg[at])]
        pred = rows["v"][lead].astype(np.int64)
        if own:
            before = np.full(n + 1, -1)
            keep = self.member & (rows["slot"] < h)
            np.maximum.at(before, self.seg[keep], idx[keep])
            got = before[self.seg[lead]]
            pred = np.where(got >= 0, rows["out"][np.maximum(got, 0)], pred)
        others = np.bincount(self.seg, weights=~self.member, minlength=n)
        ref = rows["out"][self.last[lead]].astype(np.int64)
        return pred, ref, others[self.seg[lead]] == 0, rows["N"][lead]


def _firings(rows: np.ndarray, v: np.ndarray, out: np.ndarray) -> np.ndarray:
    rec = np.zeros(len(rows), dtype=FIRING)
    for name in ("t", "voice", "phi", "N", "n"):
        rec[name] = rows[name]
    rec["v"], rec["out"] = v, out
    return rec


# pylint: disable-next=too-many-locals
# pylint: disable-next=too-many-arguments,too-many-positional-arguments
def plan(
    group: Group, tr: Trace, slots: tuple, tol: fit.Tolerance, clock: int, prelude: bool = True
) -> tuple:
    """``(plans, reasons)``: each :class:`Plan` of ``group`` whose calls all fit within ``tol``
    (``slots`` the names of its cell's slots; prelude forms where ``prelude``), and why each
    other plan was refused."""
    ticks = _Ticks.of(tr, group)
    calls = {k: call for k, (call, _) in enumerate(tr.keys) if call.name in group.templates}
    cells = {call.name for call, cell in tr.keys if cell != group.cell}
    if cells & set(group.templates):
        return [], [f"{', '.join(sorted(cells & set(group.templates)))} fire on two cells"]
    if not ticks.member.any():
        return [], ["no call fires"]
    between = ticks.interleaved()
    if between.any():
        slot = slots[int(ticks.rows["slot"][between][0])]
        return [], [f"slot {slot} fires between the group's slots"]
    rows, member = ticks.rows, ticks.member
    out, reasons = [], []
    for h in np.unique(rows["slot"][member]):
        for own in (False, True):
            got = _plan(ticks, calls, (int(h), own), tol, (clock, prelude), group.cell)
            if isinstance(got, str):
                reasons.append(f"host {slots[h]}{' after own' if own else ''}: {got}")
            else:
                got.host = slots[h]
                out.append(got)
            if not (rows["slot"][member] < h).any():
                break
    return out, reasons


# pylint: disable-next=too-many-arguments,too-many-positional-arguments,too-many-locals
def _plan(ticks: _Ticks, calls: dict, host: tuple, tol, clock: tuple, cell: str):
    """The :class:`Plan` of ``host``, ``(h, own)``: host slot index ``h`` (``own``: the slots
    before it keep their own output), or why there is none; ``clock`` ``(clock_hz, prelude)``."""
    (h, own), (clock, prelude) = host, clock
    rows, member = ticks.rows, ticks.member
    at = np.flatnonzero(member & (rows["slot"] == h))
    v_in = rows["v"][at] if own else rows["v"][ticks.first[at]]
    target = rows["out"][ticks.last[at]]
    result = Plan("", {})
    for k in np.unique(rows["key"][at]):
        sel = rows["key"][at] == k
        rec = _firings(rows[at][sel], v_in[sel], target[sel])
        fir = fit.Firings(rec, cell, clock)
        forms = fit.fits(fir, tol, prelude)
        if not forms:
            return f"{calls[k]} fits no form (nearest {fit.nearest(fir, tol)})"
        result.hosted[calls[k]] = forms
    for k in np.unique(rows["key"][member & (rows["slot"] != h)]):
        sel = member & (rows["key"] == k)
        before = rows["slot"][sel] < h
        if own and before.all():
            rec = _firings(rows[sel], rows["v"][sel], rows["out"][sel])
            forms = fit.fits(fit.Firings(rec, cell, clock), tol, prelude)
            if not forms:
                return f"{calls[k]} fits no form of its own"
            if any(f.key == IDENTITY for f in forms):
                result.ended.add(calls[k])
            else:
                result.own[calls[k]] = forms
        elif own and before.any():
            return f"{calls[k]} fires before and after the host"
        else:
            result.ended.add(calls[k])
    pred, ref, alone, notes = ticks.orphans(at, own, h)
    note = np.array([frequency(int(k), clock) if 0 <= k < 256 else 0 for k in notes], dtype=int)
    err = np.minimum(
        fit.error(cell, tol, pred, ref), np.where(alone, fit.error(cell, tol, note, ref), np.inf)
    )
    if not fit.within(err, tol):
        return f"the group moves the cell on {int((err > tol.p95).sum())} ticks its host does not"
    return result
