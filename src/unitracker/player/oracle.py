"""unitracker-player, the generic player of the grammar's documents, with each voice's first
note (the onsets the project's mismatch criterion counts each voice from) and its modulators'
firings."""

import numpy as np
from tqdm import tqdm
from unitracker_player import Player, compile_document, load, read
from unitracker_player.player import FILTER, PULSE

from unitracker.player.canonical import REG_COUNT


class _Onsets(Player):
    """The generic player, recording the tick of each voice's first note but the init row's."""

    def __init__(self, song):
        super().__init__(song)
        self.starts = [None] * song.voices
        self._init_events = {id(e) for e in song.init}

    def _event(self, t, v, event, *rest):  # pylint: disable=arguments-differ
        if self.starts[v] is None and isinstance(event.note, int):
            if id(event) not in self._init_events:
                self.starts[v] = t
        super()._event(t, v, event, *rest)


def play(text: str, ticks: int, progress: bool = False) -> tuple[np.ndarray, list]:
    """``(grid, starts)``: the registers after each of up to ``ticks`` ticks of document
    ``text`` (fewer where it ends first) and the tick of each voice's first note, or None."""
    player = _Onsets(load(text))
    grid = np.zeros((ticks, REG_COUNT), dtype=np.uint8)
    played = 0
    for t in tqdm(range(ticks), desc="generic", unit="tick", disable=not progress, leave=False):
        regs = player.step()
        if regs is None:
            break
        grid[t], played = regs, t + 1
    return grid[:played], player.starts


class _Firings(_Onsets):
    """The generic player, recording each modulator's cell ``(key, voice)`` (voice None for the
    cutoff), the tick of each of its firings and its origin: ``(event, how)`` for one a voice's
    event starts on its own tick by its ``key`` command (``command``, ``note command`` with a
    note that starts its instrument), by a program it runs (``run``) or by its note's
    instrument's program (``instrument``), a program that sets the cell in its first row, on
    the event's tick, and in no other; else None."""

    def __init__(self, song):
        super().__init__(song)
        self.mods: dict[int, list] = {}
        self._now: list = [None] * song.voices

    def _event(self, t, v, event, *rest):  # pylint: disable=arguments-differ
        if id(event) not in self._init_events:
            self._now[v] = (t, event, rest[1])
        super()._event(t, v, event, *rest)

    def _origin(self, key: str, v) -> tuple | None:
        now = self._now[v]
        if now is None or now[0] != self.tick:
            return None
        _, event, ins = now
        ops = dict(event.ops)
        noted = isinstance(event.note, int) and not event.legato
        if key in ops:
            return event, "note command" if noted else "command"
        slot = PULSE if key == "pw" else FILTER
        run = ops.get("run")
        if run is not None and run[0] == slot and key != "freq":
            return (event, "run") if _sets_first(run[1], key) else None
        if not noted or ins is None:
            return None
        if key == "freq":
            return (event, "instrument") if ins.freq is not None else None
        keep = event.keep_pw if key == "pw" else event.keep_filter
        prog = ins.pw if key == "pw" else ins.filter
        return (event, "instrument") if not keep and _sets_first(prog, key) else None

    def _fire(self):
        owners = [("cut", None, self.cut_cmd or self.cut_prog)]
        for v, voice in enumerate(self.voices):
            owners += [("freq", v, voice.freq_cmd or voice.freq_prog)]
            owners += [("pw", v, voice.pw_cmd or voice.pw_prog)]
        for key, v, mod in owners:
            if mod:
                if id(mod) not in self.mods:
                    origin = self._origin(key, mod[2] if key == "cut" else v)
                    self.mods[id(mod)] = [(key, v), mod, [], origin]
                self.mods[id(mod)][2].append(self.tick)
        super()._fire()


def _sets_first(prog, key: str) -> bool:
    """Whether program ``prog`` sets ``key`` in its first row, which applies when it starts, and
    in no other row, and does not loop to its first row."""
    if prog is None or not prog.rows or prog.rows[0][0] or prog.loop == 0:
        return False
    sets = [any(k == key for k, _ in ops) for _, _, ops in prog.rows]
    return sets[0] and not any(sets[1:])


def _events(doc, song) -> dict:
    """``{id(compiled event): (phrase name, index)}`` of the events the timeline plays."""
    out = {}
    for names, entries in zip(doc.timeline, song.timeline):
        for entry, compiled in zip(names, entries):
            if entry.kind == "phrase":
                out.update((id(e), (entry.phrase, i)) for i, e in enumerate(compiled[1]))
    return out


def firings(text: str, ticks: int, progress: bool = False) -> list[tuple]:
    """``(cell, ticks, origin)`` of each modulator document ``text`` sets in up to ``ticks``
    ticks, in the order they first fire: the cell it drives, the tick of each of its firings
    (the ``φ``-th at index ``φ``) and its origin ``(phrase, event index, how)`` or None."""
    doc = read(text)
    song = compile_document(doc)
    player = _Firings(song)
    for _ in tqdm(range(ticks), desc="firings", unit="tick", disable=not progress, leave=False):
        if player.step() is None:
            break
    where = _events(doc, song)
    out = []
    for cell, _, fired, origin in player.mods.values():
        at = None if origin is None else where.get(id(origin[0]))
        out.append((cell, fired, None if at is None else (*at, origin[1])))
    return out
