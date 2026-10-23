"""Lift a GoatTracker 2 tune (pygoattracker's decompiled .sid) to a unitracker song."""

import functools

import numpy as np
from pygoattracker import constants as C
from pygoattracker import decompile_sid
from pygoattracker.model import PlayPattern, Repeat, Transpose

from unitracker.lift.base import song_meta
from unitracker.playroutine.library import library
from unitracker.lift import placed
from unitracker.lift.base import Lift, LiftError
from unitracker.modfit.convert import grid, window
from unitracker.player.vm import SongPlayer, event_commands, fold_empty

PAL_CLOCK = {"cycles_per_tick": 19656, "clock_hz": 985248}
NTSC_CLOCK = {"cycles_per_tick": 17095, "clock_hz": 1022727}
# The playroutine's legato command: the note does not retrigger, as a toneporta's; its swing of
# one voice on a named pair.
LEGATO, VOICE_SWING, FREQ = 17, 18, 16


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


def _rows(table, data) -> dict:
    """Each row ``i`` as ``data(left, right)`` with ``next``, the row after it or, when that is
    a jump row, the jump's target. The player follows one jump and reads the row it lands on as
    data, so a jump row is a record too: its own bytes as data, which a jump to it or a pointer
    at it reaches."""
    tab = _Table(table)
    return {
        i: {**data(tab.lt(i), tab.rt(i)), "next": tab.fold(i + 1)} for i in range(1, len(tab) + 1)
    }


def wave_records(table) -> dict:
    """Wavetable rows as ``w``/``n`` records; a jump row read as data changes no waveform and
    waits no tick, as a delay row of 0."""
    return _rows(table, lambda w, n: {"w": 0 if w == C.TABLEJUMP else w, "n": n})


def pulse_records(table) -> dict:
    """Pulsetable rows as ``l``/``r`` records; a width row's ``l`` keeps the bits it plays, and
    a sweep row's is its ticks, 256 for the byte 0."""
    return _rows(table, lambda l, r: {"l": 0x80 | (l & 0x0F) if l >= 0x80 else l or 0x100, "r": r})


def filter_records(table) -> dict:
    """Filtertable rows as ``l``/``r`` records; a parameter row keeps the bits of ``l`` it plays,
    takes a following cutoff row's value as ``cut`` and continues after it, as the player reads
    them in one tick."""
    tab, out = _Table(table), _rows(
        table, lambda l, r: {"l": 0x80 | (l & 0x70) if l >= 0x80 else l, "r": r}
    )
    for i, rec in out.items():
        if rec["l"] >= 0x80 and tab.lt(i + 1) == 0x00:
            rec.update(cut=tab.rt(i + 1), next=tab.fold(i + 2))
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


def native_start(player) -> dict:
    """The tempo cells native ``player`` (a pygoattracker player) starts each voice on, read
    after its init: ``tempo`` and the funk pair ``funk0``, ``funk1``."""
    # pylint: disable=protected-access
    player.render_grid(1)
    tempos = {chan.tempo for chan in player._channels}
    if len(tempos) != 1:
        raise LiftError(f"the voices start on different tempos {sorted(tempos)}")
    funk = player._funktable
    return {"tempo": tempos.pop(), "funk0": funk[0], "funk1": funk[1]}


def build_options(player) -> list[int]:
    """The ``build`` table of native ``player`` (a pygoattracker player): its
    PULSEOPTIMIZATION and REALTIMEOPTIMIZATION."""
    return [int(player.optimize_pulse), int(player.optimize_realtime)]


TABLE_RUNS = (C.CMD_SETWAVEPTR, C.CMD_SETPULSEPTR, C.CMD_SETFILTERPTR)


def _needs_instrument(trk: dict, phrases: dict) -> bool:
    """Whether a voice plays a note or runs a table before its steps name an instrument."""
    for play in trk["plays"]:
        for event in phrases[play["phrase"]]:
            if event.get("instrument"):
                return False
            if isinstance(event.get("note"), int) or any(
                code in TABLE_RUNS and arg for code, arg in event_commands(event)
            ):
                return True
    return False


def voice_init(player, tracks: list, phrases: dict) -> dict:
    """``vinit`` records: the instrument native ``player`` (a pygoattracker player) gives a voice
    in its init (``mt_initchn``), for each voice that needs one before it names one."""
    player.render_grid(1)
    # pylint: disable-next=protected-access
    chans = player._channels
    return {
        v: {"ins": chan.instr & 0x3F}
        for v, (trk, chan) in enumerate(zip(tracks, chans))
        if _needs_instrument(trk, phrases)
    }


def _phrases(song, tracks) -> dict:
    """Every pattern the tracks play, lifted to a phrase."""
    out = {}
    for name in sorted({p["phrase"] for t in tracks for p in t["plays"]}):
        num = int(name[1:], 16)
        if num >= len(song.patterns):
            raise LiftError(f"orderlist plays missing pattern {num}")
        out[name] = phrase(song.patterns[num].rows)
    return out


def initial_tempo(start: dict):
    """The written initial tempo of the native tempo cells ``start`` (see :func:`native_start`):
    a tempo cell ``t`` from 2 is ``t + 1`` ticks; 0 and 1 swing on the funk pair from its first or
    second tempo."""
    t = start["tempo"]
    if t >= 2:
        return t + 1
    a, b = (start[f"funk{k}"] + 1 for k in (t, t ^ 1))
    return a if a == b else [a, b]


# The commands that swing on the stored funk pair: E00 and $F00/$F01 every voice, $F80/$F81 this
# voice, each as the command that names its pair ((l, r) reversed for $x1) as a speed-table row.
STORED = {
    (C.CMD_FUNKTEMPO, 0): (C.CMD_FUNKTEMPO, False),
    (C.CMD_SETTEMPO, 0): (C.CMD_FUNKTEMPO, False),
    (C.CMD_SETTEMPO, 1): (C.CMD_FUNKTEMPO, True),
    (C.CMD_SETTEMPO, 0x80): (VOICE_SWING, False),
    (C.CMD_SETTEMPO, 0x81): (VOICE_SWING, True),
}


def _native(ir: dict, start: dict) -> SongPlayer:
    """A player of ``ir`` starting on the native tempo cells ``start``, whose funk pair the
    commands of :data:`STORED` read."""
    player = SongPlayer(ir)
    player.set_cells(start)
    return player


def _set_commands(event: dict, cmds: list) -> None:
    event.pop("command", None)
    event.pop("commands", None)
    event.update({"command": cmds[0]} if len(cmds) == 1 else {"commands": cmds} if cmds else {})


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
    _set_commands(event, cmds)


def _rewritten(ir: dict, start: dict, rewrite: placed.Rewrite, what: str) -> dict:
    """``ir``, played from the native tempo cells ``start``, with the commands ``rewrite`` picks
    rewritten by the value each meets where it plays, once a replay proves the song unchanged."""
    if not any(placed.users(ir, rewrite.wanted).values()):
        return ir
    ticks = 2 * window(ir)
    try:
        observed = placed.observe(_native(ir, start), ir, ticks, rewrite)
        out = placed.Placed(ir, rewrite, observed).song()
    except placed.PlacedError as exc:
        raise LiftError(f"{what}: {exc}") from exc
    diff = np.argwhere(_native(ir, start).render_grid(ticks) != SongPlayer(out).render_grid(ticks))
    if len(diff):
        tick, reg = (int(x) for x in diff[0])
        raise LiftError(f"{what} play differently at tick {tick}, register {reg}")
    return out


def explicit_swing(lift: "GoatTrackerLift", ir: dict) -> dict:
    """``ir``, played from the native tempo cells ``lift.start``, with each command that swings
    on the stored funk pair (:data:`STORED`) naming the pair it meets where it plays."""
    start = lift.start
    speed = dict(ir["records"]["speedtab"])
    rewrite = placed.Rewrite(
        table="D_v_tick0_do",
        wanted=lambda cmd: cmd in STORED,
        value=lambda player, v: tuple((player.cell(f"funk{i}", v) + 1) & 0xFF for i in (0, 1)),
        default=tuple(int(start[f"funk{i}"]) + 1 for i in (0, 1)),
        apply=lambda event, j, pair: _named(event, j, pair, speed),
        finish=lambda out: {**out, "records": {**out["records"], "speedtab": speed}},
    )
    return _rewritten(ir, start, rewrite, "the named swings")


def _unmarked(event: dict, j: int, running: tuple) -> None:
    """Command ``j`` of ``event``, a $D10-$DFF, as the continuous command ``running`` it keeps
    running, or nothing when that is none."""
    cmds = [list(c) for c in event_commands(event)]
    cmds[j : j + 1] = [list(running)] if running[0] else []
    _set_commands(event, cmds)


def unmark(lift: "GoatTrackerLift", ir: dict) -> dict:
    """``ir``, played from the native tempo cells ``lift.start``, with each $D10-$DFF, which keeps
    the voice's continuous command and plays nothing else, as that command, or nothing when it is
    none, once a replay proves the song unchanged."""
    rewrite = placed.Rewrite(
        table="D_v_cmd_do",
        wanted=lambda cmd: cmd[0] == C.CMD_SETMASTERVOL and cmd[1] >= 0x10,
        value=lambda player, v: (player.cell("command", v), player.cell("cmddata", v)),
        default=(0, 0),
        apply=_unmarked,
    )
    return _rewritten(ir, lift.start, rewrite, "the rewritten $D10-$DFF commands")


SNAP = (C.CMD_TONEPORTA, 0)
# The modulator expression of a toneporta of speed 0: the frequency of the note.
AT_NOTE = "0"


def snapped(ir: dict) -> dict:
    """``ir`` (its modulations expressions) with each toneporta of speed 0 the conversion left,
    which sets the frequency to the note's on each tick, as ``!freq`` of the expression that does
    (:data:`AT_NOTE`), once a replay to the song's recurrence and one period more proves the song
    unchanged."""
    where = [
        (name, k)
        for name, events in ir["phrases"].items()
        for k, e in enumerate(events)
        if SNAP in map(tuple, event_commands(e))
    ]
    if not where:
        return ir
    exprs = dict(ir.get("exprs") or {})
    index = next((i for i, t in exprs.items() if t == AT_NOTE), max(exprs, default=0) + 1)
    exprs[index] = AT_NOTE
    phrases = {name: [dict(e) for e in events] for name, events in ir["phrases"].items()}
    for name, k in where:
        cmds = [
            [FREQ, index] if tuple(c) == SNAP else list(c) for c in event_commands(phrases[name][k])
        ]
        _set_commands(phrases[name][k], cmds)
    out = {**ir, "phrases": phrases, "exprs": exprs}
    ticks = window(ir)
    diff = np.argwhere(grid(ir, ticks) != SongPlayer(out).render_grid(ticks))
    if len(diff):
        tick, reg = (int(x) for x in diff[0])
        raise LiftError(
            f"a toneporta of speed 0 as !freq of the note's frequency plays differently at tick"
            f" {tick}, register {reg}"
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


def unpause(_lift: "GoatTrackerLift", ir: dict) -> dict:
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
    diff = np.argwhere(grid(ir, ticks) != SongPlayer(out).render_grid(ticks))
    if len(diff):
        tick, reg = (int(x) for x in diff[0])
        raise LiftError(
            f"ctrltab rows {sorted(new)} pause the tick's continuous effect, which the song hears"
            f" at tick {tick}, register {reg}"
        )
    return out


class GoatTrackerLift(Lift):
    """A GoatTracker 2 tune, ``tune`` a ``pygoattracker.decompile_sid`` result."""

    playroutine = "goattracker2"
    normalisations = (unpause, explicit_swing, unmark)

    @staticmethod
    def decode(raw: bytes, subtune: int):
        return decompile_sid(raw, subtune=subtune)

    def native(self):
        return self.tune.player()

    @functools.cached_property
    def start(self) -> dict:
        """The native tempo cells after init (see :func:`native_start`)."""
        return native_start(self.native())

    def check_build(self) -> None:
        info = self.tune.info
        unsupported = {
            "SIMPLEPULSE pulse tables": info.simplepulse,
            "full-ghosting builds": info.state is not None,
            "init-routine SID writes": bool(info.init_regs),
            "instrument-vibrato-only builds": info.live_vibrato,
        }
        missing = [name for name, used in unsupported.items() if used]
        if missing:
            raise LiftError(
                f"not covered by the {self.playroutine} playroutine yet: " + ", ".join(missing)
            )

    def structure(self) -> dict:
        dec = self.tune
        info = dec.info
        song = dec.typed.to_song()
        start = self.start
        build = info.build
        adparam = C.DEFAULT_ADPARAM
        if build.adparam is not None:
            adparam = build.adparam << 8 | build.srparam
        freq = list(info.note_read[:128]) + [0] * max(0, 128 - len(info.note_read))
        instruments, restarts = instrument_records(song.instruments, adparam)
        tracks = [track(ol) for ol in song.subtunes[dec.subtune].channels]
        phrases = _phrases(song, tracks)
        vinit = voice_init(self.native(), tracks, phrases)
        header = dec.header
        return {
            "unitracker": "ir",
            "tempo": initial_tempo(start),
            "meta": song_meta(header, f"{self.playroutine} subtune {dec.subtune}"),
            "clock": NTSC_CLOCK if (header.flags >> 2) & 3 == 2 else PAL_CLOCK,
            "voices": 3,
            "tables": {
                "ftab": [int(v) for v in freq],
                "build": build_options(self.native()),
            },
            "records": {
                "ins": instruments,
                "restart": restarts,
                "ctrltab": wave_records(song.wavetable),
                "pulsetab": pulse_records(song.pulsetable),
                "filttab": filter_records(song.filtertable),
                "speedtab": speed_records(song.speedtable),
                **({"vinit": vinit} if vinit else {}),
            },
            "tracks": tracks,
            "phrases": phrases,
            "playroutine": library(self.playroutine),
        }

    def convert(self, song: dict) -> dict:
        return snapped(super().convert(song))
