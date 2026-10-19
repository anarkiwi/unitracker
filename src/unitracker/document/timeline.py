"""Per-voice tracks to one timeline of arrangement rows."""

MAX_ROWS = 4096
END = "end"


class TimelineError(ValueError):
    """A timeline breaks the row rules or cannot be represented."""


def _items(track) -> tuple[list, list]:
    """A track's intro and loop as lists of ``(phrase, transpose)``, repeats expanded."""
    items = [
        (play["phrase"], int(play.get("transpose", 0)))
        for play in track["plays"]
        for _ in range(int(play.get("repeat", 1)))
    ]
    loop = track.get("loop")
    if loop is None:
        return items, []
    start = sum(int(p.get("repeat", 1)) for p in track["plays"][:loop])
    return items[:start], items[start:]


class _Voice:
    """One voice's position while the timeline is built."""

    def __init__(self, track, lengths):
        self.intro, self.cycle = _items(track)
        self.lengths = lengths
        self.pos = 0
        self.end = 0
        self.done = False

    def item(self):
        """The next item, or None once the track has ended."""
        if self.pos < len(self.intro):
            return self.intro[self.pos]
        if not self.cycle:
            return None
        return self.cycle[(self.pos - len(self.intro)) % len(self.cycle)]

    def key(self, t):
        """This voice's part of the joint state at time ``t``."""
        if self.done:
            return ("end",)
        if self.pos < len(self.intro) or not self.cycle:
            return ("i", self.pos, self.end - t)
        return ("c", (self.pos - len(self.intro)) % len(self.cycle), self.end - t)

    def start(self, t):
        """Start the next item at ``t``; the row entry."""
        item = self.item()
        if item is None:
            self.done = True
            return END
        self.pos += 1
        self.end = t + self.lengths[item[0]]
        return item


def minimal_loop(seq: list, loop: int) -> tuple[int, int]:
    """``(start, period)`` of the earliest start and shortest period of the sequence that plays
    ``seq`` and then repeats ``seq[loop:]``."""
    cycle = seq[loop:]
    size = len(cycle)
    period = next(
        p
        for p in range(1, size + 1)
        if size % p == 0 and all(cycle[i] == cycle[i - p] for i in range(p, size))
    )
    start = loop
    while start and seq[start - 1] == seq[start - 1 + period]:
        start -= 1
    return start, period


def folded(track: dict, key) -> dict:
    """``track`` with its loop at its earliest start and of its shortest period, one play per
    phrase play, its plays compared by ``key(play)``."""
    if track.get("loop") is None:
        return track
    plays = [p for p in track["plays"] for _ in range(int(p.get("repeat", 1)))]
    loop = sum(int(p.get("repeat", 1)) for p in track["plays"][: track["loop"]])
    start, period = minimal_loop([key(p) for p in plays], loop)
    cycle = plays[loop:]
    kept = plays[:start] + [cycle[(k - loop) % len(cycle)] for k in range(start, start + period)]
    plays = [{f: v for f, v in p.items() if f != "repeat"} for p in kept]
    return {**track, "plays": plays, "loop": start}


def to_rows(tracks, lengths) -> tuple[list[list], int | None]:
    """Rows of per-voice entries (``(phrase, transpose)``, ``END`` or None), and the loop row.

    Rows run until the joint state of every voice repeats; ``loop`` is that row. A song that ends
    has no row that only ends voices: its end ends them.
    """
    voices = [_Voice(track, lengths) for track in tracks]
    rows, seen, t = [], {}, 0
    while True:
        due = [not v.done and v.end == t for v in voices]
        key = tuple(v.key(t) for v in voices)
        if key in seen:
            return rows, seen[key]
        if all(v.done for v in voices):
            return _ended(rows), None
        if len(rows) >= MAX_ROWS:
            raise TimelineError(f"the voices' loops do not align within {MAX_ROWS} rows")
        seen[key] = len(rows)
        rows.append([v.start(t) if d else None for v, d in zip(voices, due)])
        live = [v.end for v in voices if not v.done]
        if not live:
            return _ended(rows), None
        t = min(live)


def _ended(rows: list) -> list:
    """``rows`` without the last rows that only end voices."""
    while rows and _only_ends(rows[-1]):
        rows = rows[:-1]
    return rows


def _only_ends(row) -> bool:
    return all(e in (END, None) for e in row)
