"""Lift a GoatTracker 2 tune (pygoattracker's decompiled .sid) to a unitracker song."""

from collections import Counter

import numpy as np
from tqdm import tqdm
from pygoattracker import constants as C
from pygoattracker.model import PlayPattern, Repeat, Transpose

from unitracker.format.songio import library, song_meta
from unitracker.modfit.convert import convert, window
from unitracker.player.vm import SongPlayer, event_commands, fold_empty

PAL_CLOCK = {"cycles_per_tick": 19656, "clock_hz": 985248}
NTSC_CLOCK = {"cycles_per_tick": 17095, "clock_hz": 1022727}
# The playroutine's legato command: the note does not retrigger, as a toneporta's; its swing of
# one voice on a named pair.
LEGATO, VOICE_SWING = 17, 18


class LiftError(ValueError):
    """The tune uses something the GoatTracker 2 playroutine does not cover."""


class _Table:
    """1-based left/right table access with the player's zero padding."""

    def __init__(self, table):
        self.left = [v & 0xFF for v in table.left]
        self.right = [v & 0xFF for v in table.right]

    def __len__(self):
        return len(self.left)

    def lt(self, ptr):
        """Left byte of row ``ptr``."""
        return self.left[ptr - 1] if 1 <= ptr <= len(self.left) else 0

    def rt(self, ptr):
        """Right byte of row ``ptr``."""
        return self.right[ptr - 1] if 1 <= ptr <= len(self.right) else 0

    def fold(self, ptr):
        """Row ``ptr``, or its jump target when it is a jump row."""
        ptr &= 0xFF
        return self.rt(ptr) if self.lt(ptr) == C.TABLEJUMP else ptr


def wave_records(table) -> dict:
    """Wavetable rows as ``w``/``n`` records, jump rows folded into ``next``."""
    tab = _Table(table)
    return {
        i: {"w": tab.lt(i), "n": tab.rt(i), "next": tab.fold(i + 1)}
        for i in range(1, len(tab) + 1)
        if tab.lt(i) != C.TABLEJUMP
    }


def pulse_records(table) -> dict:
    """Pulsetable rows as ``l``/``r`` records, jump rows folded into ``next``."""
    tab = _Table(table)
    return {
        i: {"l": tab.lt(i), "r": tab.rt(i), "next": tab.fold(i + 1)}
        for i in range(1, len(tab) + 1)
        if tab.lt(i) != C.TABLEJUMP
    }


def filter_records(table) -> dict:
    """Filtertable rows as ``l``/``r`` records; a parameter row takes a following cutoff row's
    value as ``cut`` and continues after it, as the player reads them in one tick."""
    tab, out = _Table(table), {}
    for i in range(1, len(tab) + 1):
        left = tab.lt(i)
        if left == C.TABLEJUMP:
            continue
        rec, nxt = {"l": left, "r": tab.rt(i)}, i + 1
        if left >= 0x80 and tab.lt(nxt) == 0x00:
            rec["cut"] = tab.rt(nxt)
            nxt += 1
        rec["next"] = tab.fold(nxt)
        out[i] = rec
    return out


def speed_records(table) -> dict:
    """Speedtable rows 1..n as ``l``/``r`` byte records."""
    tab = _Table(table)
    return {i: {"l": tab.lt(i), "r": tab.rt(i)} for i in range(1, len(tab) + 1)}


_INS_FIELDS = (
    ("ad", "attack_decay"),
    ("sr", "sustain_release"),
    ("ctrl", "wave_ptr"),
    ("pw", "pulse_ptr"),
    ("filter", "filter_ptr"),
    ("speed", "vibrato_param"),
    ("vd", "vibrato_delay"),
)
NO_GATEOFF, NO_PRESET = 0x40, 0x80
# The wavetable commands the playroutine stops on.
STOPS = (C.CMD_DONOTHING, C.CMD_SETWAVEPTR, C.CMD_FUNKTEMPO)


def restart_rows(timer: int, first: int, adparam: int) -> list[dict]:
    """The hard-restart program of gate timer byte ``timer`` and first wave ``first``.

    Bits 0-5 of the timer are the lookahead; unless bit 6, the gate goes off then and, unless
    bit 7 too, the envelope is preset to ``adparam``. A first wave $FE/$FF writes only the
    gate on the note's first tick, another nonzero one the waveform with the gate on.
    """
    ahead = {"hold": timer & 0x3F}
    if not timer & NO_GATEOFF:
        ahead["gate"] = 0
        if not timer & NO_PRESET:
            ahead.update(ad=adparam >> 8, sr=adparam & 0xFF)
    if first >= 0xFE:
        note = {"gate": first & 1}
    else:
        note = {"ctrl": first, "gate": 1} if first else {}
    return [ahead, note]


def instrument_records(instruments, adparam: int) -> tuple[dict, dict]:
    """Instruments 1..63 as records of their nonzero byte fields, and their restart programs.

    A zero vibrato delay is no vibrato, as the player packs it. Instruments with one gate timer
    and first wave share one program. An instrument without one reads the voice's next step on
    its note's first tick, as gate timer 0 does: an empty instrument, or one whose gate timer 0
    leaves the gate and first wave alone.
    """
    out, restarts, entry = {}, {}, {}
    for num, ins in enumerate(instruments[: C.MAX_INSTR - 1], start=1):
        rec = {k: getattr(ins, attr) & 0xFF for k, attr in _INS_FIELDS}
        rec["speed"] *= bool(rec["vd"])
        rec = {k: v for k, v in rec.items() if v}
        key = (ins.gateoff_timer & 0xFF, ins.first_wave & 0xFF)
        out[num] = rec
        if not key[0] & 0x3F:
            idle = key[0] & NO_GATEOFF and not key[1]
            if not idle and (rec or key != (0, 0)):
                raise LiftError(f"instrument {num}: a hard restart on gate timer 0 is not covered")
            continue
        if key not in entry:
            entry[key] = start = len(restarts) + 1
            rows = restart_rows(*key, adparam)
            for k, row in enumerate(rows):
                restarts[start + k] = {**row, "next": start + k + 1 if k + 1 < len(rows) else 0}
        rec["restart"] = entry[key]
    return out, restarts


def phrase(rows) -> list:
    """Raw pattern rows as events, folding fully empty rows into ``steps``."""
    events = fold_empty(_row_event(row) for row in rows)
    if not events:
        raise LiftError("empty pattern")
    return events


def _row_event(row) -> dict:
    event = {}
    if C.FIRSTNOTE <= row.note <= C.LASTNOTE:
        event["note"] = row.note - C.FIRSTNOTE
    elif row.note == C.KEYOFF:
        event["note"] = "off"
    elif row.note == C.KEYON:
        event["note"] = "on"
    elif row.note != C.REST:
        raise LiftError(f"bad note byte {row.note:#04x}")
    if row.instrument:
        event["instrument"] = row.instrument
    if row.command == C.CMD_TONEPORTA and isinstance(event.get("note"), int):
        event["commands"] = [[LEGATO, 0], [row.command, row.data]]
    elif row.command:
        event["command"] = [row.command, row.data]
    return event


def track(orderlist) -> dict:
    """An orderlist as plays with absolute transposes, its restart as a play index."""
    entries = orderlist.entries
    plays, index_of, trans, pos = [], {}, 0, 0

    def group(pos, trans):
        start = pos
        if isinstance(entries[pos], Transpose):
            trans = entries[pos].semitones
            pos += 1
        repeat = 1
        if pos < len(entries) and isinstance(entries[pos], Repeat):
            repeat = entries[pos].count + 1
            pos += 1
        if pos >= len(entries) or not isinstance(entries[pos], PlayPattern):
            raise LiftError(f"orderlist entry {start} is not [transpose][repeat] pattern")
        return pos + 1, trans, entries[pos].num, repeat

    while pos < len(entries):
        start = pos
        pos, trans, num, repeat = group(pos, trans)
        for i in range(start, pos):
            index_of[i] = len(plays)
        plays.append({"phrase": f"p{num:02x}", "transpose": trans, "repeat": repeat})
    if orderlist.restart not in index_of:
        raise LiftError(f"restart {orderlist.restart} is outside the orderlist")
    loop = index_of[orderlist.restart]
    pos = orderlist.restart
    for k in range(loop, len(plays)):
        pos, trans, _, _ = group(pos, trans)
        if trans != plays[k]["transpose"]:
            raise LiftError("the loop pass plays a different transpose")
    for play in plays:
        if play["transpose"] == 0:
            del play["transpose"]
        if play["repeat"] == 1:
            del play["repeat"]
    return {"plays": plays, "loop": loop}


def _default_tempo(song) -> int:
    last = song.instruments[C.MAX_INSTR - 2] if len(song.instruments) >= C.MAX_INSTR - 1 else None
    if last is not None and last.attack_decay >= 2 and not last.wave_ptr:
        return last.attack_decay - 1
    return 5


def _phrases(song, tracks) -> dict:
    """Every pattern the tracks play, lifted to a phrase."""
    out = {}
    for name in sorted({p["phrase"] for t in tracks for p in t["plays"]}):
        num = int(name[1:], 16)
        if num >= len(song.patterns):
            raise LiftError(f"orderlist plays missing pattern {num}")
        out[name] = phrase(song.patterns[num].rows)
    return out


def initial_tempo(deftempo: int, pr: dict):
    """The written initial tempo of default tempo byte ``deftempo``: ``deftempo + 1`` ticks, or
    for 0 the swing on the player's initial funk pair; a swing from its second tempo (1) is not
    covered."""
    if deftempo >= 2:
        return deftempo + 1
    if deftempo == 1:
        raise LiftError("a default tempo of 1, a swing from its second tempo, is not covered")
    return [int(pr["voice"][f"funk{k}"]["init"]) + 1 for k in (0, 1)]


# The commands that swing on the stored funk pair: E00 and $F00/$F01 every voice, $F80/$F81 this
# voice, each as the command that names its pair ((l, r) reversed for $x1) as a speed-table row.
STORED = {
    (C.CMD_FUNKTEMPO, 0): (C.CMD_FUNKTEMPO, False),
    (C.CMD_SETTEMPO, 0): (C.CMD_FUNKTEMPO, False),
    (C.CMD_SETTEMPO, 1): (C.CMD_FUNKTEMPO, True),
    (C.CMD_SETTEMPO, 0x80): (VOICE_SWING, False),
    (C.CMD_SETTEMPO, 0x81): (VOICE_SWING, True),
}


def _native(ir: dict) -> SongPlayer:
    """A player of ``ir`` starting on the native player's funk pair, which the commands of
    :data:`STORED` read."""
    player = SongPlayer(ir)
    player.set_cells({f"funk{i}": ir["playroutine"]["voice"][f"funk{i}"]["init"] for i in (0, 1)})
    return player


def stored_pairs(ir: dict, ticks: int) -> dict:
    """``{(phrase, event, command index): Counter of (l, r)}``: the funk pair stored each time a
    command of :data:`STORED` is read, over ``ticks`` ticks."""
    player = _native(ir)
    fetch, starts = player.namespace["_fetch"], {}
    for name, events in ir["phrases"].items():
        steps = np.cumsum([0] + [int(e.get("steps", 1)) for e in events[:-1]])
        starts[name] = {int(s): k for k, s in enumerate(steps)}
    voice_of = {id(cells): v for v, cells in enumerate(player.voice_cells)}
    seen = {}

    def observed(cells):
        cursor = cells[-1]
        if not cursor.ended:
            name = ir["tracks"][voice_of[id(cells)]]["plays"][cursor.index]["phrase"]
            k = starts[name].get(cursor.step)
            for j, cmd in enumerate(
                event_commands(ir["phrases"][name][k]) if k is not None else ()
            ):
                if tuple(cmd) in STORED:
                    voice = voice_of[id(cells)]
                    pair = tuple((player.cell(f"funk{i}", voice) + 1) & 0xFF for i in (0, 1))
                    seen.setdefault((name, k, j), Counter())[pair] += 1
        fetch(cells)

    player.namespace["_fetch"] = observed
    for _ in tqdm(range(ticks), desc="stored swing", unit="tick", leave=False):
        player.play_frame()
    return seen


def _named(event: dict, j: int, pair: tuple, speed: dict) -> None:
    """Rewrite command ``j`` of ``event``, a :data:`STORED` swing, as the command naming
    ``pair``, its speed-table row found in ``speed`` or added to it."""
    cmds = [list(c) for c in event_commands(event)]
    code, reverse = STORED[tuple(cmds[j])]
    left, right = pair[::-1] if reverse else pair
    row = {"l": left, "r": right}
    index = next((i for i, r in speed.items() if r == row), max(speed, default=0) + 1)
    speed[index] = row
    cmds[j] = [code, index]
    event.pop("command", None)
    event.update({"command": cmds[0]} if len(cmds) == 1 else {"commands": cmds})


def explicit_swing(ir: dict) -> dict:
    """``ir`` with each command that swings on the stored funk pair (:data:`STORED`) naming the
    pair it meets most, once a replay to the song's recurrence and one period more proves it
    unheard (a pair another voice replaces on the same tick is not heard); else LiftError."""
    users = [
        (name, k, j)
        for name, events in ir["phrases"].items()
        for k, event in enumerate(events)
        for j, cmd in enumerate(event_commands(event))
        if tuple(cmd) in STORED
    ]
    if not users:
        return ir
    ticks = window(ir)
    seen = stored_pairs(ir, ticks)
    phrases = {name: [dict(e) for e in events] for name, events in ir["phrases"].items()}
    speed = dict(ir["records"]["speedtab"])
    initial = Counter(
        {tuple(int(ir["playroutine"]["voice"][f"funk{i}"]["init"]) + 1 for i in (0, 1)): 1}
    )
    met = set()
    for name, k, j in users:
        met.update(seen.get((name, k, j), initial))
        _named(phrases[name][k], j, seen.get((name, k, j), initial).most_common(1)[0][0], speed)
    out = {**ir, "phrases": phrases, "records": {**ir["records"], "speedtab": speed}}
    diff = np.argwhere(_native(ir).render_grid(ticks) != SongPlayer(out).render_grid(ticks))
    if len(diff):
        tick, reg = (int(x) for x in diff[0])
        raise LiftError(
            f"a swing on the stored pair meets the pairs {sorted(met)}; named, it plays"
            f" differently at tick {tick}, register {reg}"
        )
    return out


def wave_rows(records: dict, phrases: dict) -> list:
    """The wavetable rows the instruments and the wavetable pointer command reach, in order."""
    rows = records["ctrltab"]
    entries = [ins.get("ctrl", 0) for ins in records["ins"].values()]
    entries += [
        arg
        for events in phrases.values()
        for event in events
        for code, arg in event.get("commands") or [event.get("command") or (0, 0)]
        if code == C.CMD_SETWAVEPTR
    ]
    seen = {}
    for i in entries:
        while i in rows and i not in seen:
            seen[i] = None
            i = rows[i]["next"]
    return sorted(seen)


def _unpaused(row: dict) -> dict | None:
    """The row that does what wavetable command row ``row`` does but pause the tick's
    continuous effect, or None when it does more.

    $F7 sets the waveform, as a waveform row does, and $FD $10-$FF does nothing, as an empty
    row does. Raises LiftError for the commands the playroutine stops on.
    """
    w, n = row["w"], row["n"]
    if w >= C.WAVECMD and w & 0x0F in STOPS:
        raise LiftError(f"wavetable command ${w:02X} stops the playroutine: not lifted")
    if w == 0xF0 | C.CMD_SETMASTERVOL and n >= 0x10:
        return {**row, "w": 0, "n": 0x80}
    if w == 0xF0 | C.CMD_SETWAVE:
        if n >= C.WAVESILENT:
            raise LiftError(f"$F7 sets waveform ${n:02X}, which no waveform row sets")
        return {**row, "w": n | C.WAVESILENT if n <= C.WAVELASTDELAY else n, "n": 0x80}
    return None


def unpause(ir: dict) -> dict:
    """``ir`` with each wavetable row whose only effect beyond its row form is to pause the
    tick's continuous effect (see :func:`_unpaused`) written in that form, once a replay to the
    song's recurrence and one period more proves the pause unheard.

    Raises LiftError naming the rows otherwise.
    """
    rows = ir["records"]["ctrltab"]
    new = {}
    for i in wave_rows(ir["records"], ir["phrases"]):
        try:
            row = _unpaused(rows[i])
        except LiftError as exc:
            raise LiftError(f"ctrltab row {i}: {exc}") from exc
        if row is not None:
            new[i] = row
    if not new:
        return ir
    out = {**ir, "records": {**ir["records"], "ctrltab": {**rows, **new}}}
    ticks = window(ir)
    diff = np.argwhere(SongPlayer(ir).render_grid(ticks) != SongPlayer(out).render_grid(ticks))
    if len(diff):
        tick, reg = (int(x) for x in diff[0])
        raise LiftError(
            f"ctrltab rows {sorted(new)} pause the tick's continuous effect, which the song hears"
            f" at tick {tick}, register {reg}"
        )
    return out


def _check_build(info) -> None:
    """Raise for player builds the goattracker2 playroutine does not cover yet."""
    unsupported = {
        "SIMPLEPULSE pulse tables": info.simplepulse,
        "full-ghosting builds": info.state is not None,
        "init-routine SID writes": bool(info.init_regs),
        "instrument-vibrato-only builds": info.live_vibrato,
    }
    missing = [name for name, used in unsupported.items() if used]
    if missing:
        raise LiftError("not covered by the goattracker2 playroutine yet: " + ", ".join(missing))


def lift(dec, expressions: bool = True) -> dict:
    """A unitracker song dict for a ``pygoattracker.decompile_sid`` result.

    With ``expressions`` its modulations are written as the format's expressions.
    """
    # pylint: disable=too-many-locals
    info = dec.info
    _check_build(info)
    song = dec.typed.to_song()
    build = info.build
    playroutine = library("goattracker2")
    adparam = C.DEFAULT_ADPARAM
    if build.adparam is not None:
        adparam = build.adparam << 8 | build.srparam
    deftempo = build.deftempo if build.deftempo is not None else _default_tempo(song)
    playroutine["constants"] = {"adparam": adparam}
    freq = list(info.note_read[:128]) + [0] * max(0, 128 - len(info.note_read))
    instruments, restarts = instrument_records(song.instruments, adparam)
    tracks = [track(ol) for ol in song.subtunes[dec.subtune].channels]
    phrases = _phrases(song, tracks)
    header = dec.header
    ir = {
        "unitracker": "ir",
        "tempo": initial_tempo(deftempo, playroutine),
        "meta": song_meta(header, f"goattracker2 subtune {dec.subtune}"),
        "clock": NTSC_CLOCK if (header.flags >> 2) & 3 == 2 else PAL_CLOCK,
        "voices": 3,
        "tables": {
            "ftab": [int(v) for v in freq],
        },
        "records": {
            "ins": instruments,
            "restart": restarts,
            "ctrltab": wave_records(song.wavetable),
            "pulsetab": pulse_records(song.pulsetable),
            "filttab": filter_records(song.filtertable),
            "speedtab": speed_records(song.speedtable),
        },
        "tracks": tracks,
        "phrases": phrases,
        "playroutine": playroutine,
    }
    ir = explicit_swing(unpause(ir))
    return convert(ir) if expressions else ir
