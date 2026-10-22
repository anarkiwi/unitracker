"""A song's one recorded run, to its state's recurrence and a period more.

Every lift stage reads a song's run from its :class:`Trace`, recorded once per song and cached by
the song's content and the code (:func:`unitracker.cache.ir_key`).
"""

import dataclasses
import itertools
from pathlib import Path

import numpy as np

from unitracker.cache import CACHE, cached, ir_key
from unitracker.player import reach
from unitracker.player.checkpoint import Baseline
from unitracker.player.modulation import Modulation, declared
from unitracker.player.vm import SongPlayer

KEPT = 4


def fired_log(player) -> list:
    """The list to which each firing of an expression the player's units play appends
    ``(tick, cell, occurrence, expression index)``: the cell ``(key, voice)`` (voice None for
    a global unit's), an occurrence numbered from 1 per start."""
    log: list = []
    modulation = player.modulation
    if modulation is None:
        return log
    cells = {u["name"]: (u["key"], u.get("scope") == "global") for u in modulation.units}
    last: dict = {}
    serials = itertools.count(1)
    prior = modulation.fired

    def fired(unit: dict, occ) -> None:
        prior(unit, occ)
        if occ.derived is None:
            return
        key, glob = cells[unit["name"]]
        name = (unit["name"], occ.voice)
        if last.get(name, (None,))[0] is not occ:
            last[name] = (occ, next(serials))
        log.append((modulation.tick, (key, None if glob else occ.voice), last[name][1], occ.key))

    modulation.fired = fired
    return log


def spec_tables(player) -> dict:
    """Each modulator table the playroutine declares, read entry by entry."""
    spec = declared(player.song["playroutine"]) or {}
    lookup = player.namespace["_T"]
    return {
        name: [int(lookup[name](i)) for i in range(int(t["size"]))]
        for name, t in (spec.get("tables") or {}).items()
    }


class Attribution:  # pylint: disable=too-few-public-methods
    """The event instance ``(voice, play count, step)`` each occurrence of an observing
    modulation's units belongs to: ``instance`` the voice's last event at its start,
    ``fetched`` at its first firing; ``events`` maps ``(unit, instance)`` to the occurrence its
    hook applied, ``plays`` each voice's play indices in the order it starts them."""

    def __init__(self, mod: Modulation):
        voices = len(mod.player.voice_cells)
        self.count, self.last = [0] * voices, [None] * voices
        self.plays = [[] for _ in range(voices)]
        self.hooked, self.started, self.events, self.occurrences = {}, {}, {}, []
        fetch, voice_of = mod.ns["_fetch"], mod.voice_of
        applied, restart, opened, fired = mod.applied, mod.restart, mod.open, mod.fired

        def observed(v):
            voice, cursor = voice_of[id(v)], v[-1]
            self.last[voice] = None
            if not cursor.ended:
                if cursor.step == 0:
                    self.plays[voice].append(cursor.index)
                    self.count[voice] += 1
                self.last[voice] = (voice, self.count[voice] - 1, cursor.step)
            fetch(v)

        def on_applied(unit: dict, voice) -> None:
            self.hooked[(unit["name"], voice)] = None if voice is None else self.last[voice]
            applied(unit, voice)

        def on_restart(unit: dict, voice, hook) -> None:
            restart(unit, voice, hook)
            key = (unit["name"], None if unit.get("scope") == "global" else voice)
            self.started[key] = None if voice is None else self.last[voice]

        def on_open(unit: dict, name: tuple, voice, snap: dict, before: int) -> None:
            opened(unit, name, voice, snap, before)
            occ = mod.active[name]
            occ.instance, occ.index = self.started.pop(name, None), len(self.occurrences)
            occ.fetched = None if voice is None else self.last[voice]
            self.occurrences.append(occ)

        def on_fired(unit: dict, occ) -> None:
            fired(unit, occ)
            inst = self.hooked.pop((unit["name"], occ.voice), None)
            if inst is not None:
                self.events.setdefault((unit["name"], inst), occ.index)

        mod.ns["_fetch"] = observed
        mod.applied, mod.restart, mod.open, mod.fired = on_applied, on_restart, on_open, on_fired


@dataclasses.dataclass
class Trace:  # pylint: disable=too-many-instance-attributes
    """One run of a song: to its state's recurrence at ``tick`` and a period more, or ``limit +
    1`` ticks where it does not recur by ``limit`` (``tick`` None).

    Attributes:
        rows: the registers after each tick played.
        starts: each voice's onset tick.
        occurrences, events, plays: the units' occurrences (:class:`Attribution`).
        fired: ``(tick, cell, voice, occurrence, expression)`` per expression firing, ``cell``
            indexing ``cells``, ``voice`` -1 for a global unit's.
        reads: each table's in-range entries read, ``(index, first tick)`` rows.
        words: the frequency word of each internal note read (None for the format's ``F``).
        tables: the modulator tables.
        baseline: the :class:`Baseline` a candidate resumes from.
    """

    rows: np.ndarray
    starts: tuple
    tick: int | None
    period: int | None
    occurrences: list
    events: dict
    plays: list
    fired: np.ndarray
    cells: tuple
    reads: dict
    words: dict | None
    tables: dict
    baseline: Baseline | None

    @property
    def frames(self) -> int:
        """The ticks played."""
        return len(self.rows)

    @property
    def ticks(self) -> int:
        """The window: to the recurrence and a period more."""
        return self.tick + self.period

    def recurs(self, limit: int = reach.MAX_TICKS) -> bool:
        """Whether the state recurs within ``limit`` ticks (:func:`reach.recurrence`)."""
        return self.tick is not None and self.tick <= limit

    def covers(self, limit: int) -> bool:
        """Whether the trace answers a run bounded by ``limit``."""
        return self.tick is not None or self.frames > limit

    def grid(self, n: int) -> np.ndarray:
        """The ``(n, 25)`` register grid, past the trace by the period."""
        if n <= self.frames:
            return self.rows[:n]
        if self.tick is None:
            raise reach.ReachabilityError(f"no recurrence within {self.frames} ticks")
        mu = self.tick - self.period
        t = np.arange(n)
        return self.rows[np.where(t < mu, t, mu + (t - mu) % self.period)]

    def onsets(self, frames: int) -> tuple:
        """Each voice's onset tick within the first ``frames`` ticks."""
        return tuple(s if s is not None and s < frames else None for s in self.starts)

    def firings(self, ticks: int) -> list:
        """``(tick, cell, occurrence, expression)`` of each firing before ``ticks``, as
        :func:`fired_log` logs it."""
        rows = self.fired[self.fired[:, 0] < ticks].tolist()
        return [(t, (self.cells[c], None if v < 0 else v), s, x) for t, c, v, s, x in rows]

    def read(self, table: str, frames: int) -> set:
        """The entries of ``table`` read within the first ``frames`` ticks."""
        got = self.reads.get(table)
        return set() if got is None else set(got[got[:, 1] < frames, 0].tolist())


def unlogged(mod: Modulation) -> Modulation:
    """``mod`` with its reads of the units' values and notes kept out of the player's logged
    table reads: an observer's reads are not the song's."""
    ns = mod.ns
    plain = ns["_tg"]

    def quiet(fn):
        def call(*args):
            logged, ns["_tg"] = ns["_tg"], plain
            try:
                return fn(*args)
            finally:
                ns["_tg"] = logged

        return call

    mod.read, mod.note_of = quiet(mod.read), quiet(mod.note_of)
    return mod


def record(ir: dict, limit: int = reach.MAX_TICKS, progress: bool = True) -> Trace:
    """Play ``ir`` once to its state's recurrence and a period more, or ``limit + 1`` ticks
    where it does not recur by ``limit``, recording its :class:`Trace`; the occurrences are an
    observing modulation's, which snapshots every cell at each start."""
    player = SongPlayer(ir, watch=True)
    seen = Attribution(unlogged(Modulation(player, lambda unit, occ: None)))
    fired, log = fired_log(player), {}
    base = Baseline(player, limit, log, progress)
    rows = list(base.rows)
    if base.found is not None:
        while player.ticks < sum(base.found):
            player.play_frame()
            rows.append(tuple(player.regs))
    tick, period = base.found or (None, None)
    for occ in seen.occurrences:
        occ.derived = occ.derived if occ.derived is None else True
    tables = player.codegen.tables
    reads: dict = {}
    for (kind, name, index), at in log.items():
        if kind == "T" and 0 <= index < len(tables[name]):
            reads.setdefault(name, []).append((index, at))
    spec = declared(ir["playroutine"]) or {}
    offset, word = int(ir["playroutine"].get("note_offset", 0)), player.namespace.get("_F")
    notes = {i - offset for t in (spec.get("freq") or {}).values() for i, _ in reads.get(t, ())}
    cells = tuple(dict.fromkeys(cell[0] for _, cell, _, _ in fired))
    index = {c: i for i, c in enumerate(cells)}
    return Trace(
        rows=np.asarray(rows, dtype=np.uint8).reshape(len(rows), -1),
        starts=tuple(player.starts),
        tick=tick,
        period=period,
        occurrences=seen.occurrences,
        events=seen.events,
        plays=seen.plays,
        fired=np.asarray(
            [(t, index[c[0]], -1 if c[1] is None else c[1], s, x) for t, c, s, x in fired],
            dtype=np.int64,
        ).reshape(len(fired), 5),
        cells=cells,
        reads={n: np.asarray(r, dtype=np.int64) for n, r in reads.items()},
        words=None if word is None else {k: int(word(k)) for k in notes if k >= 0},
        tables=spec_tables(player),
        baseline=base if base.found is not None else None,
    )


_KEPT: dict = {}


def trace(ir: dict, limit: int = reach.MAX_TICKS, cache: Path | None = CACHE) -> Trace:
    """``ir``'s :class:`Trace`, to its recurrence or ``limit + 1`` ticks: recorded once, kept
    in memory for the last :data:`KEPT` songs and cached under ``cache`` (None: neither)."""
    if cache is None:
        return record(ir, limit)
    key = ir_key(ir)
    got = _KEPT.pop(key, None)
    if got is None or not got.covers(limit):
        got = cached(
            cache / f"trace-{key}.pkl", lambda: record(ir, limit), valid=lambda t: t.covers(limit)
        )
    _KEPT[key] = got
    while len(_KEPT) > KEPT:
        _KEPT.pop(next(iter(_KEPT)))
    return got
