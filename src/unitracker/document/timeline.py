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


class _Voice:  # pylint: disable=too-many-instance-attributes
    """One voice's position while the timeline is built: its next item, the step it is at in
    its current phrase, the tick of its next step, its tempo pair and swing phase."""

    def __init__(self, track, lengths, tempo):
        self.intro, self.cycle = _items(track)
        self.lengths = lengths
        self.pos = self.step = self.length = self.next = self.phase = 0
        self.tempo = tempo
        self.phrase = None
        self.done = False

    def item(self):
        """The next item, or None once the track has ended."""
        if self.pos < len(self.intro):
            return self.intro[self.pos]
        if not self.cycle:
            return None
        return self.cycle[(self.pos - len(self.intro)) % len(self.cycle)]

    @property
    def ending(self) -> bool:
        """Whether the voice's phrase ends at its next step."""
        return not self.done and self.step == self.length

    def key(self, t):
        """This voice's part of the joint state at tick ``t``."""
        if self.done:
            return ("end",)
        where = (
            ("i", self.pos)
            if self.pos < len(self.intro) or not self.cycle
            else ("c", (self.pos - len(self.intro)) % len(self.cycle))
        )
        swing = self.phase if self.tempo[0] != self.tempo[1] else 0
        return (*where, self.length - self.step, self.next - t, self.tempo, swing)

    def start(self):
        """Start the next item; the row entry."""
        item = self.item()
        if item is None:
            self.done = True
            return END
        self.pos += 1
        self.phrase, self.step, self.length = item[0], 0, self.lengths[item[0]]
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


def to_rows(tracks, lengths, tempo=(1, 1), retimes=None) -> tuple[list[list], int | None]:
    """Rows of per-voice entries (``(phrase, transpose)``, ``END`` or None), and the loop row.

    ``lengths`` are the phrases' steps and ``retimes`` ``{phrase: {step: (tempo, vtempo)}}`` the
    tempo pairs (or None) the event of a step sets: every voice's, resetting every swing, and its
    voice's, as unitracker-player times steps from the initial pair ``tempo``. A row starts at the
    tick a voice's phrase ends, with an entry for each voice whose phrase ends there. Rows run
    until the joint state of every voice repeats; ``loop`` is that row. A song that ends has no
    row that only ends voices: its end ends them.
    """
    retimes = retimes or {}
    voices = [_Voice(track, lengths, tuple(tempo)) for track in tracks]
    rows, seen = [], {}
    while True:
        live = [v for v in voices if not v.done]
        if not live:
            return _ended(rows), None
        t = min(v.next for v in live)
        due = [v for v in live if v.next == t]
        if any(v.ending for v in due):
            key = tuple(v.key(t) for v in voices)
            if key in seen:
                return rows, seen[key]
            if len(rows) >= MAX_ROWS:
                raise TimelineError(f"the voices' loops do not align within {MAX_ROWS} rows")
            seen[key] = len(rows)
            rows.append([v.start() if v in due and v.ending else None for v in voices])
            due = [v for v in due if not v.done]
        for v in due:
            every, own = retimes.get(v.phrase, {}).get(v.step, (None, None))
            v.step += 1
            if every is not None:
                for w in voices:
                    w.tempo, w.phase = tuple(every), 0
            if own is not None:
                v.tempo, v.phase = tuple(own), 0
        for v in due:
            v.next = t + v.tempo[v.phase]
            v.phase ^= 1


def _ended(rows: list) -> list:
    """``rows`` without the last rows that only end voices."""
    while rows and _only_ends(rows[-1]):
        rows = rows[:-1]
    return rows


def _only_ends(row) -> bool:
    return all(e in (END, None) for e in row)
