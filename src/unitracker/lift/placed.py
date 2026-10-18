"""Rewrite native commands by what the player holds where each plays.

A replay records a value at each place a command runs (a voice's n-th phrase play, the event and
the command's index); a phrase whose commands meet different values at different places becomes
one phrase per place, and a loop whose passes meet different values is written out from where
they repeat.
"""

from dataclasses import dataclass
from typing import Callable

import numpy as np
from tqdm import tqdm

from unitracker.player.vm import SongPlayer, event_commands


class PlacedError(ValueError):
    """The values a command meets do not repeat within the replay."""


@dataclass(frozen=True)
class Rewrite:
    """A rewrite of the commands ``wanted(command)`` picks: before each runs (its entry of
    dispatch table ``table``, ``D_v_tick0_do``) the replay reads ``value(player, voice)``, and
    ``apply(event, command index, value)`` rewrites it (``default`` where it does not play);
    ``finish(song)`` completes the rewritten song."""

    # pylint: disable=too-many-instance-attributes

    table: str
    wanted: Callable
    value: Callable
    default: object
    apply: Callable
    finish: Callable = lambda song: song


def _event_steps(events: list) -> dict:
    """``{step: event index}`` of the steps that start each of a phrase's events."""
    steps = np.cumsum([0] + [int(e.get("steps", 1)) for e in events[:-1]])
    return {int(s): k for k, s in enumerate(steps)}


def _events(ir: dict):
    return (e for events in ir["phrases"].values() for e in events)


def users(ir: dict, wanted) -> dict:
    """``{phrase: [(event, command index)]}`` of the commands ``wanted(command)`` picks."""
    return {
        name: [
            (k, j)
            for k, e in enumerate(events)
            for j, c in enumerate(event_commands(e))
            if wanted(tuple(c))
        ]
        for name, events in ir["phrases"].items()
    }


def observe(player: SongPlayer, ir: dict, ticks: int, rewrite: Rewrite) -> tuple:
    """``({(voice, occurrence, event, command index): value}, occurrences per voice)``: over
    ``ticks`` ticks of ``player`` (playing ``ir``), the value of ``rewrite`` where each command it
    picks runs."""
    fetch, dispatch = player.namespace["_fetch"], player.namespace[rewrite.table]
    starts = {name: _event_steps(events) for name, events in ir["phrases"].items()}
    voice_of = {id(cells): v for v, cells in enumerate(player.voice_cells)}
    count, last, seen = [0] * len(voice_of), [None] * len(voice_of), {}

    def observed(cells):
        v, cursor = voice_of[id(cells)], cells[-1]
        last[v] = None
        if not cursor.ended:
            count[v] += cursor.step == 0
            name = ir["tracks"][v]["plays"][cursor.index]["phrase"]
            k = starts[name].get(cursor.step)
            last[v] = None if k is None else (count[v] - 1, name, k)
        fetch(cells)

    def recorded(run):
        def wrapped(cells, row=None):
            v = voice_of[id(cells)]
            if last[v] is not None:
                n, name, k = last[v]
                for j, cmd in enumerate(event_commands(ir["phrases"][name][k])):
                    if rewrite.wanted(tuple(cmd)):
                        seen.setdefault((v, n, k, j), rewrite.value(player, v))
            run(cells, row)

        return wrapped

    player.namespace["_fetch"] = observed
    codes = {int(c[0]) for e in _events(ir) for c in event_commands(e) if rewrite.wanted(tuple(c))}
    for code in codes & set(dispatch):
        dispatch[code] = (recorded(dispatch[code][0]), dispatch[code][1])
    for _ in tqdm(range(ticks), desc="placed commands", unit="tick", leave=False):
        player.play_frame()
    return seen, count


def _items(trk: dict) -> tuple[list, int | None]:
    """A track's ``(phrase, transpose)`` per phrase play, repeats written out, and the item its
    loop returns to (None for a track that ends)."""
    items, loop = [], None
    for k, play in enumerate(trk["plays"]):
        loop = len(items) if k == trk.get("loop") else loop
        items += [(play["phrase"], int(play.get("transpose", 0)))] * int(play.get("repeat", 1))
    return items, loop


def _plays(items: list) -> list:
    """Plays of ``(phrase, transpose)`` items, equal neighbours as a repeat."""
    out = []
    for name, trans in items:
        if out and (out[-1]["phrase"], out[-1].get("transpose", 0)) == (name, trans):
            out[-1]["repeat"] = out[-1].get("repeat", 1) + 1
            continue
        out.append({"phrase": name, **({"transpose": trans} if trans else {})})
    return out


def _unrolled(items: list, loop, variant, occurrences: int) -> tuple[list, int | None]:
    """``(items, loop)`` with each item ``(phrase, transpose, variant)``, ``variant(n)`` that of
    the voice's n-th phrase play, of which ``occurrences`` were observed: a loop is written out
    from the first item after which the variants repeat with a period of whole passes."""
    if loop is None:
        return [(*items[n], variant(n)) for n in range(len(items))], None
    size = len(items) - loop
    seen = [variant(n) for n in range(occurrences)]
    for start in range(loop, occurrences):
        for period in range(size, occurrences - start, size):
            if all(seen[n] == seen[n + period] for n in range(start, occurrences - period)):
                at = [n if n < loop else loop + (n - loop) % size for n in range(start + period)]
                return [(*items[k], seen[n]) for n, k in enumerate(at)], start
    raise PlacedError("the values do not repeat within the replay")


class Placed:
    """The phrases and tracks of a song whose commands ``rewrite`` picks are rewritten with the
    value each meets where it plays (``observed``, :func:`observe`)."""

    # pylint: disable=too-few-public-methods

    def __init__(self, ir: dict, rewrite: Rewrite, observed: tuple):
        self.ir, self.rewrite = ir, rewrite
        self.found = users(ir, rewrite.wanted)
        self.seen, self.counts = observed
        self.phrases, self.names = {}, {}

    def _phrase(self, name: str, values: tuple) -> str:
        """The phrase that plays ``name`` with its commands rewritten by ``values``."""
        if (name, values) not in self.names:
            alias = name if name not in self.phrases else f"{name}/{len(self.phrases)}"
            events = [dict(e) for e in self.ir["phrases"][name]]
            for (k, j), value in reversed(list(zip(self.found[name], values))):
                self.rewrite.apply(events[k], j, value)
            self.names[(name, values)], self.phrases[alias] = alias, events
        return self.names[(name, values)]

    def track(self, v: int) -> dict:
        """Voice ``v``'s track over the rewritten phrases."""
        trk = self.ir["tracks"][v]
        items, loop = _items(trk)

        def variant(n):
            k = n if loop is None or n < len(items) else loop + (n - loop) % (len(items) - loop)
            found = self.found[items[k][0]]
            return tuple(self.seen.get((v, n, e, j), self.rewrite.default) for e, j in found)

        unrolled, loop = _unrolled(items, loop, variant, self.counts[v] - 1)
        written = [(self._phrase(name, values), trans) for name, trans, values in unrolled]
        if loop is None:
            return {**trk, "plays": _plays(written), "loop": None}
        intro = _plays(written[:loop])
        return {**trk, "plays": intro + _plays(written[loop:]), "loop": len(intro)}

    def song(self) -> dict:
        """``ir`` over the rewritten phrases and tracks."""
        tracks = [self.track(v) for v in range(len(self.ir["tracks"]))]
        return self.rewrite.finish({**self.ir, "phrases": self.phrases, "tracks": tracks})
