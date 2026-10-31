"""A defMON replay's song as its readers see it: the arranger rows, the pattern streams and the
sidTAB rows in the memory its init leaves (docs/lift/defmon.md)."""

from pydefmon.defmon import SidtabRow
from pydefmon.tune import Event, Row

ALT = 0x80
GATES = (0x40, 0x20, 0x10)
STOP = 0x80
SPAN = 1 << 16
ROW = 15


class LiftError(ValueError):
    """A tune the lift cannot state exactly, or whose document differs from the reference."""


class Refusal(LiftError):
    """A construct of a tune the document cannot state; ``kind`` names its class."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


class Replay:
    """The tables of a replay in ``mem``, read at the addresses ``binding`` names, as the
    replay's walker, step readers and cascade blocks read them: an arranger row is three bytes,
    a pattern is the stream at its pointer up to the first ALT step wherever that is, a sidTAB
    row is the stream at its pointer."""

    def __init__(self, mem: bytes, binding: dict, fixed: bool, counted: bool):
        self.mem, self.at, self.fixed, self.counted = mem, binding, bool(fixed), bool(counted)
        self._streams, self._rows, self._sizes = {}, {}, {}

    def byte(self, table: str, index: int) -> int:
        """Entry ``index`` of the table ``table``."""
        return self.mem[(self.at[table] + index) & 0xFFFF]

    def arranger(self, y: int) -> tuple:
        """The arranger row ``y``: ``(V1, V2, V3)``."""
        return tuple(self.byte(page, y) for page in ("v1", "v2", "v3"))

    def stream(self, p: int) -> tuple:
        """``(at, flag, a, b, note)`` of each step of pattern ``p`` that gates or ends, ``at``
        the full calls from its start; a gate not set is None."""
        if p not in self._streams:
            self._streams[p] = self._scan(p)
        return self._streams[p]

    def _scan(self, p: int) -> tuple:
        mem = self.mem
        ptr = self.byte("pat_lo", p) | self.byte("pat_hi", p) << 8
        steps, at, size = [], 0, 0
        for _ in range(SPAN):
            flag, y, payload = mem[ptr], 1, []
            for gate in GATES:
                payload.append(mem[(ptr + y) & 0xFFFF] if flag & gate else None)
                y += bool(flag & gate) or self.fixed
            if flag & (ALT | sum(GATES)):
                steps.append((at, flag, *payload))
            size += y
            if flag & ALT:
                self._sizes[p] = size
                return tuple(steps)
            at += (flag & 15) + 2
            ptr = (ptr + y) & 0xFFFF
        raise Refusal("no ending pattern", f"pattern ${p:02x} has no ALT step in {SPAN} bytes")

    def size(self, p: int) -> int:
        """The bytes of pattern ``p``'s stream, to the end of its ALT step."""
        self.stream(p)
        return self._sizes[p]

    def end(self, p: int) -> tuple:
        """``(at, delay)`` of pattern ``p``'s ALT step."""
        at, flag, *_ = self.stream(p)[-1]
        return at, flag & 15

    def events(self, p: int, last: int) -> tuple:
        """The events of pattern ``p`` at or before the full call ``last``, their rows the
        effective ones."""
        return tuple(
            Event(
                at,
                None if a is None else self.resolve(a),
                None if b is None else self.resolve(b),
                note,
                flag & 15 if flag & ALT else None,
            )
            for at, flag, a, b, note in self.stream(p)
            if at <= last
        )

    def row_size(self, y: int) -> int:
        """The bytes of the effective row ``y`` and its DL: two bitmaps, a byte per column and
        two for ACID."""
        columns = dict(self.row(y).columns)
        return 3 + len(columns) + ("ACID" in columns)

    def resolve(self, y: int) -> int:
        """The effective row of index ``y``: a JP row, whose page is 0, names its target."""
        return y if self.byte("rows_hi", y) else self.byte("rows_lo", y)

    def row(self, y: int) -> Row:
        """The effective row ``y``: its columns, its hold and the effective row after it; a
        stop row has neither."""
        if y not in self._rows:
            at = self.byte("rows_hi", y) << 8 | self.byte("rows_lo", y)
            if {self.at[n] for n in ("rowptr_lo", "rowptr_hi", "scratch")} & set(
                range(at, at + ROW)
            ):
                raise Refusal("jp onto jp", f"row ${y:02x} is read from cells the replay writes")
            columns = SidtabRow.parse(y, bytes(self.mem[at : at + ROW])).values()
            hold = self.byte("dl", y)
            stop = hold & STOP
            nxt = None if stop else self.resolve((y + 1) & 0xFF)
            self._rows[y] = Row(tuple(columns.items()), None if stop else hold + 1, nxt)
        return self._rows[y]

    def reach(self, start: int) -> tuple:
        """``(plays, jumps)``: the arranger rows the walker can reach from row ``start``. A row
        stepped onto is a jump if its V1 has bit 7; the row a jump lands on, or falls through to
        after its count, is played as it is, a V1 with bit 7 naming a pattern all the same.
        ``plays`` are the rows played, ``jumps`` the jump rows."""
        plays, jumps, todo, seen = set(), set(), [(start, True)], set()
        while todo:
            y, checked = todo.pop()
            if (y, checked) in seen:
                continue
            seen.add((y, checked))
            v1, v2, v3 = self.arranger(y)
            if checked and v1 & 0x80:
                jumps.add(y)
                todo.append((v2, False))
                if self.counted and v3:
                    todo.append(((y + 1) & 0xFF, False))
            else:
                plays.add(y)
                todo.append(((y + 1) & 0xFF, True))
        return plays, jumps
