"""Commands whose written form depends on what the voice played before them: each voice walks its
plays, and a phrase played in contexts that give it different commands becomes one phrase per
context. A loop whose passes give different commands is written out from the first pass that
starts in a state an earlier one started in, where it loops."""

import copy


def with_commands(event: dict, commands) -> dict:
    """``event`` with ``commands`` as its commands."""
    out = {k: v for k, v in event.items() if k not in ("command", "commands")}
    if len(commands) == 1:
        out["command"] = list(commands[0])
    elif commands:
        out["commands"] = [list(c) for c in commands]
    return out


def _items(trk: dict) -> tuple[list, int | None]:
    """A track's plays one per phrase play, repeats written out, and the item its loop returns
    to (None for a track that ends)."""
    items, loop = [], None
    for k, play in enumerate(trk["plays"]):
        loop = len(items) if k == trk.get("loop") else loop
        one = {key: v for key, v in play.items() if key != "repeat"}
        items += [one] * int(play.get("repeat", 1))
    return items, loop


def _plays(items: list) -> list:
    """Plays of one phrase play each, equal neighbours as a repeat."""
    out = []
    for item in items:
        if out and {k: v for k, v in out[-1].items() if k != "repeat"} == item:
            out[-1]["repeat"] = out[-1].get("repeat", 1) + 1
        else:
            out.append(dict(item))
    return out


def _walked(trk: dict, phrases: dict, convert, state) -> tuple[list, int, int]:
    """``(walk, loop, pass)``: the voice's phrase plays ``(play, events)`` with each event as the
    events ``convert(state, event)`` gives where it plays (``state.enter()``, if it has one, at
    each phrase play), its loop walked until a pass starts in the state an earlier pass started
    in and one more period; ``loop`` the loop's first item, ``pass`` its length, 0 for none."""
    items, loop = _items(trk)
    enter = getattr(state, "enter", lambda: None)

    def walk(part):
        out = []
        for item in part:
            enter()
            got = tuple(e for event in phrases[item["phrase"]] for e in convert(state, event))
            out.append((item, got))
        return out

    out = walk(items if loop is None else items[:loop])
    if loop is None:
        return out, len(out), 0
    starts = {}
    while True:
        key = repr(copy.deepcopy(vars(state)))
        if key in starts:
            return out + out[starts[key] : len(out)], loop, len(items) - loop
        starts[key] = len(out)
        out += walk(items[loop:])


def _period(seq: list, loop: int, size: int) -> tuple[int, int]:
    """``(start, period)`` of the shortest form of the walk ``seq`` (see :func:`_walked`): from
    ``start`` on it repeats every ``period`` items, a multiple of the pass ``size``."""
    for start in range(loop, len(seq)):
        for period in range(size, len(seq) - start, size):
            if all(seq[n] == seq[n + period] for n in range(start, len(seq) - period)):
                return start, period
    return loop, len(seq) - loop


def _named(walked: list, variants: dict, out: dict) -> list:
    """The walk's plays, each naming the variant of its phrase its events are (``name/k`` past
    the first), added to ``variants`` (``{name: [events]}``) and ``out`` (``{alias: events}``)."""
    items = []
    for item, got in walked:
        name = item["phrase"]
        seen = variants.setdefault(name, [])
        if got not in seen:
            seen.append(got)
        alias = name if seen.index(got) == 0 else f"{name}/{seen.index(got)}"
        out[alias] = list(got)
        items.append({**item, "phrase": alias})
    return items


def rewrite(song: dict, convert, state) -> dict:
    """``song`` with each event as the events ``convert(voice state, event)`` gives where the
    voice plays it, each voice's state a new ``state()``; a phrase played in contexts that give it
    different events becomes one phrase per context, and a loop whose passes differ is written out
    from where they repeat."""
    variants, out, tracks = {}, {}, []
    for trk in song["tracks"]:
        walked, loop, size = _walked(trk, song["phrases"], convert, state())
        items = _named(walked, variants, out)
        if not size:
            tracks.append({**trk, "plays": _plays(items), "loop": None})
            continue
        start, period = _period(items, loop, size)
        intro = _plays(items[:start])
        tracks.append(
            {**trk, "plays": intro + _plays(items[start : start + period]), "loop": len(intro)}
        )
    return {**song, "tracks": tracks, "phrases": out}
