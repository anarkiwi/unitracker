"""Resuming a song's playback from another's: songs with the same code play alike until one
first reads a datum the other holds differently. A :class:`Baseline` plays a song once, noting
each datum's first read and a snapshot every :data:`EVERY` ticks; another song resumes from the
last snapshot before the first datum the two hold differently."""

import dataclasses

from unitracker.player import reach

EVERY = 64
MISSING = ("<missing>",)


class _Reads(dict):
    """A mapping noting the tick each key is first read; always true, so that ``or {}`` keeps it
    when empty."""

    def __init__(self, data, item, player, log):
        super().__init__(data)
        self.item, self.player, self.log = item, player, log

    def get(self, key, default=None):
        self.log.setdefault((*self.item, key), self.player.ticks)
        return super().get(key, default)

    def __bool__(self):
        return True

    def __contains__(self, key):
        self.log.setdefault((*self.item, key), self.player.ticks)
        return super().__contains__(key)

    def __getitem__(self, key):
        self.log.setdefault((*self.item, key), self.player.ticks)
        return super().__getitem__(key)


def snapshot(player) -> tuple:
    """Everything a played tick changes in a player, its voices by index."""
    voice = {id(cells): v for v, cells in enumerate(player.voice_cells)}
    writes = {(voice.get(k[0]), k[1]): n for k, n in player.namespace["_W"].items()}
    cursors = [(c.index, c.repeat, c.step, c.ended) for c in (v[-1] for v in player.voice_cells)]
    mod, occurrences = player.modulation, None
    if mod is not None:
        active = {
            k: dataclasses.replace(o, derived=o.derived is not None) for k, o in mod.active.items()
        }
        occurrences = (active, dict(mod.pending), dict(mod.pre))
    return (
        list(player.globals),
        [list(cells[:-1]) for cells in player.voice_cells],
        cursors,
        list(player.regs),
        list(player.starts),
        player.ticks,
        writes,
        occurrences,
    )


def restore(player, snap: tuple) -> None:  # pylint: disable=too-many-locals
    """Put a fresh player of a song with the same code in the state of ``snap``."""
    gcells, vcells, cursors, regs, starts, ticks, writes, occurrences = snap
    player.globals[:] = gcells
    for cells, saved, cursor in zip(player.voice_cells, vcells, cursors):
        cells[:-1] = saved
        c = cells[-1]
        c.index, c.repeat, c.step, c.ended = cursor
    player.regs, player.starts, player.ticks = list(regs), list(starts), ticks
    ids = [id(cells) for cells in player.voice_cells]
    player.namespace["_W"].clear()
    player.namespace["_W"].update(
        {(0 if v is None else ids[v], cell): n for (v, cell), n in writes.items()}
    )
    mod = player.modulation
    if mod is not None:
        active, pending, pre = occurrences
        mod.active = {k: dataclasses.replace(o) for k, o in active.items()}
        for occ in mod.active.values():
            occ.derived = mod.steppers.get(occ.key) if occ.derived else None
        mod.pending, mod.pre = dict(pending), dict(pre)


def value(player, item: tuple):
    """What ``player`` holds for a datum ``(kind, name, key)``."""
    # pylint: disable=too-many-return-statements
    kind, name, key = item
    gen = player.codegen
    if kind == "F":
        fields = player.namespace["F"].get(name)
        return MISSING if fields is None else dict.get(fields, key, MISSING)
    if kind == "T":
        table = gen.tables.get(name)
        return MISSING if table is None else (table[key] if 0 <= key < len(table) else 0)
    if kind == "D":
        entry = gen.dispatch.get(name, {}).get(key)
        return entry if entry is None else (gen.sources[entry[0]], entry[1])
    if kind in "PLS":
        cursor = player.voice_cells[name if kind != "S" else name[0]][-1]
        if kind == "L":
            return len(cursor.plays), cursor.loop
        index = key if kind == "P" else name[1]
        if not 0 <= index < len(cursor.plays):
            return MISSING
        rows, transpose, repeat = cursor.plays[index]
        if kind == "P":
            return len(rows), transpose, repeat
        return tuple(rows[key].tolist()), transpose
    if kind == "X":
        return {int(k): str(v) for k, v in (player.song.get("exprs") or {}).items()}.get(key)
    return (player.song.get("records") or {}).get(name, {}).get(key, MISSING)


def _track(player, log: dict) -> None:
    """Note in ``log`` the tick ``player`` first reads each datum."""
    ns = player.namespace
    ns["F"] = {k: _Reads(v, ("F", k), player, log) for k, v in ns["F"].items()}
    for name in player.codegen.dispatch:
        ns[name] = _Reads(ns[name], ("D", name), player, log)
    names, tg = {id(t): n for n, t in ns["T"].items()}, ns["_tg"]

    def table(t, index):
        log.setdefault(("T", names[id(t)], index), player.ticks)
        return tg(t, index)

    voice, fetch = {id(cells): v for v, cells in enumerate(player.voice_cells)}, ns["_fetch"]

    def fetched(v):
        n, c = voice[id(v)], v[-1]
        before = c.index
        if not c.ended:
            log.setdefault(("P", n, before), player.ticks)
            log.setdefault(("S", (n, before), c.step), player.ticks)
        fetch(v)
        if c.step == 0:
            log.setdefault(("L", n, 0), player.ticks)
            log.setdefault(("P", n, c.index), player.ticks)

    ns["_tg"], ns["_fetch"] = table, fetched
    mod = player.modulation
    if mod is not None:
        select = mod.select

        def chosen(unit, occ):
            out = select(unit, occ)
            log.setdefault(("X", 0, occ.key), player.ticks)
            return out

        mod.select = chosen
        records = dict(player.song.get("records") or {})
        for unit in mod.units:
            name = (unit.get("program") or {}).get("record")
            if name is not None:
                records[name] = _Reads(records.get(name) or {}, ("M", name), player, log)
        player.song = {**player.song, "records": records}


class Baseline:  # pylint: disable=too-few-public-methods
    """A song played to its recurrence (``found``, as :func:`reach.recurrence` returns it): its
    rows, the first read of each datum, its kept states and a snapshot every :data:`EVERY`
    ticks; ``found`` is None where the state does not recur within ``max_ticks``. ``log``
    receives each datum's first read as the player goes on, past the recurrence too, and
    ``watch(player)`` sees each tick played to the recurrence."""

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, player, max_ticks=reach.MAX_TICKS, log=None, progress=False, watch=None
    ):
        self.skeleton = (player.codegen.skeleton(), player.modulation is not None)
        log = {} if log is None else log
        log.update({("P", v, 0): 0 for v in range(len(player.voice_cells))})
        _track(player, log)
        self.rows, self.kept, self.snaps = [], {}, {}
        last = reach.STRIDE * (reach.REMEMBERED - 1)

        def each(p):
            tick = p.ticks - 1
            if watch is not None:
                watch(p)
            self.rows.append(tuple(p.regs))
            if tick % reach.STRIDE == 0 and tick < last:
                self.kept[p.state()] = tick
            if p.ticks % EVERY == 0 and p.ticks < last:
                self.snaps[p.ticks] = snapshot(p)
            return False

        try:
            self.found = reach.recurrence(player, max_ticks, progress, each=each)
        except reach.ReachabilityError:
            self.found = None
        self.reads = sorted(log.items(), key=lambda kv: kv[1])
        self.values = [value(player, item) for item, _ in self.reads]

    def resume(self, player) -> tuple | None:
        """``(ticks, rows, kept)`` with ``player`` restored to the last snapshot before it first
        reads a datum held differently, or None to play from the start."""
        if (player.codegen.skeleton(), player.modulation is not None) != self.skeleton:
            return None
        first = len(self.rows)
        for (item, tick), held in zip(self.reads, self.values):
            if tick >= first:
                break
            if value(player, item) != held:
                first = tick
                break
        at = max((t for t in self.snaps if t <= first), default=0)
        if not at:
            return None
        restore(player, self.snaps[at])
        return at, self.rows[:at], {s: t for s, t in self.kept.items() if t < at}
