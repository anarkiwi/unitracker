"""A GoatTracker 2 song whose table pointers land on jump rows, restated in rows the typed model
has.

The table executors follow a jump row only when they advance onto it. A pointer set onto one
(an instrument's, a pattern or wavetable command's) or a jump whose target is one runs it as an
ordinary row of its bytes ``$FF,n`` and then advances from the row after it. :func:`landings`
appends, for each jump row a played pointer lands on, the ordinary row it runs as followed by a
jump to where the executor goes next, and points the landing there. Of the three tables only the
pulse table's ordinary row has a typed form: a pulse set, whose high byte the chip reads through
four bits. The wavetable's is the tempo command F and the filter table's a filter set of every
mode bit, whose left bytes read back as jump rows.
"""

from dataclasses import replace

from pygoattracker import typed as T
from pygoattracker.model import PlayPattern

KINDS = ("wave", "pulse", "filter")
POINTERS = {
    "wave": ("wave_ptr", T.SetWavePtr),
    "pulse": ("pulse_ptr", T.SetPulsePtr),
    "filter": ("filter_ptr", T.SetFilterPtr),
}
UNSTATED = {
    "wave": "the tempo command F",
    "filter": "a filter set of every mode bit",
}


class Landing(ValueError):
    """A landing on a jump row whose ordinary row the typed model cannot state."""


def played(song: T.TypedSong, subtune: int) -> tuple:
    """``(pattern numbers, rows, instruments)`` the subtune's orderlists play."""
    orders = song.subtunes[subtune].channels
    nums = {e.num for o in orders for e in o.entries if isinstance(e, PlayPattern)}
    nums = {n for n in nums if n < len(song.patterns)}
    rows = [r for n in sorted(nums) for r in song.patterns[n]]
    return nums, rows, {r.instrument for r in rows if r.instrument} | {1}


def _jumps(row) -> bool:
    return isinstance(row, (T.Jump, T.Stop))


def _right(row) -> int:
    return row.row if isinstance(row, T.Jump) else 0


class _Table:
    """One table's rows with the ordinary twins of the jump rows landed on."""

    def __init__(self, kind: str, rows, simple: bool):
        self.kind, self.simple = kind, simple
        self.original = tuple(rows)
        self.rows = list(rows)
        self.twins = {}
        self.seen = set()
        self.overrun = False

    def row(self, ptr: int):
        """The original row at 1-based ``ptr``, None past the table."""
        return self.original[ptr - 1] if 0 < ptr <= len(self.original) else None

    def land(self, ptr: int) -> int:
        """The pointer an executor landing on ``ptr`` runs from: the twin of a jump row."""
        if not ptr or not _jumps(self.row(ptr)):
            return ptr
        if ptr not in self.twins:
            if self.kind in UNSTATED:
                raise Landing(
                    f"a landing on a {self.kind} jump row runs it as {UNSTATED[self.kind]}, "
                    "which the typed row of its bytes cannot state"
                )
            at = self.twins[ptr] = len(self.rows) + 1
            self.rows += [None, None]
            self.rows[at - 1 : at + 1] = self.twin(ptr)
        return self.twins[ptr]

    def step(self, ptr: int) -> int:
        """Where the executor goes after running row ``ptr``: a jump row next is followed, and
        points where its target lands."""
        nxt = self.row(ptr + 1)
        if nxt is None:
            self.overrun = True
        if not _jumps(nxt):
            return ptr + 1
        target = self.land(_right(nxt))
        if isinstance(nxt, T.Jump):
            self.rows[ptr] = T.Jump(target)
        return target

    def twin(self, ptr: int) -> list:
        """The rows a landing on jump row ``ptr`` runs: its bytes as a pulse set, then the jump
        onward. The chip reads a pulse set's high byte through four bits, so the set stands as
        the width ``$F00`` plus the row's right byte."""
        if self.row(ptr + 1) is None:
            raise Landing(
                "a landing on the last row of a pulse table runs on into the bytes after it"
            )
        right = _right(self.row(ptr))
        row = T.SimplePulseSet(right) if self.simple else T.PulseSet(0xF00 | right)
        target = self.step(ptr)
        return [row, T.Jump(target) if target else T.Stop()]


def landings(song: T.TypedSong, subtune: int = 0) -> T.TypedSong:
    # pylint: disable=too-many-locals
    """``song`` with every landing of a played pointer on a jump row pointed at that row's
    ordinary twin."""
    tables = {
        k: _Table(k, getattr(song, f"{k}table"), song.simplepulse and k == "pulse") for k in KINDS
    }
    work = []

    def enter(kind: str, ptr: int) -> int:
        landed = tables[kind].land(ptr)
        work.append((kind, landed))
        return landed

    def command(cmd):
        for kind, (_, setter) in POINTERS.items():
            if cmd.__class__ is setter:
                return setter(enter(kind, cmd.row))  # pylint: disable=not-callable
        return cmd

    nums, _, used = played(song, subtune)
    patterns = tuple(
        (
            tuple(replace(r, command=command(r.command)) if r.command else r for r in pattern)
            if n in nums
            else pattern
        )
        for n, pattern in enumerate(song.patterns)
    )
    instruments = tuple(
        (
            replace(ins, **{f: enter(k, getattr(ins, f)) for k, (f, _) in POINTERS.items()})
            if n in used
            else ins
        )
        for n, ins in enumerate(song.instruments, 1)
    )
    while work:
        kind, ptr = work.pop()
        table = tables[kind]
        if not ptr or ptr in table.seen:
            continue
        table.seen.add(ptr)
        if ptr in table.twins.values():
            jump = table.rows[ptr]
            work.append((kind, jump.row if isinstance(jump, T.Jump) else 0))
            continue
        if ptr > len(table.original):
            continue
        row = table.row(ptr)
        if isinstance(row, T.WaveCommand):
            table.rows[ptr - 1] = replace(row, command=command(row.command))
        work.append((kind, table.step(ptr)))
    for kind, table in tables.items():
        if table.twins and table.overrun:
            raise Landing(f"a {kind} table runs on past its last row beside a landing")
    return replace(
        song,
        patterns=patterns,
        instruments=instruments,
        wavetable=tuple(tables["wave"].rows),
        pulsetable=tuple(tables["pulse"].rows),
        filtertable=tuple(tables["filter"].rows),
    )
