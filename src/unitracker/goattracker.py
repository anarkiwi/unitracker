"""Lift a GoatTracker 2 tune (pygoattracker's decompiled .sid) to a unitracker song.

Table rows become shared-vocabulary rows, and every effect becomes a spec derived from
the player's arithmetic (gplay.c / player.s); see docs/format.md.
"""

from importlib import resources

import yaml
from pygoattracker import constants as C
from pygoattracker.model import PlayPattern, Repeat, Transpose

from unitracker.songio import song_meta

PAL_CLOCK = {"cycles_per_tick": 19656, "clock_hz": 985248}
NTSC_CLOCK = {"cycles_per_tick": 17095, "clock_hz": 1022727}
FREQ_WIDTH = 16


class LiftError(ValueError):
    """The tune uses something the GoatTracker 2 playroutine does not cover."""


def load_playroutine() -> dict:
    """The GoatTracker 2 playroutine definition."""
    text = resources.files("unitracker").joinpath("playroutines/goattracker2.yaml").read_text()
    return yaml.safe_load(text)


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


def _signed(byte: int) -> int:
    return byte - 0x100 if byte >= 0x80 else byte


class Effects:
    """Specs for the player's speed-table effects, from its arithmetic.

    ``speed[i]`` is the 1-based speedtable row ``(left, right)``; row 0 reads as zero.
    """

    def __init__(self, speedtable):
        tab = _Table(speedtable)
        self.speed = {i: (tab.lt(i), tab.rt(i)) for i in range(1, len(tab) + 1)}
        self.flips = set()

    def row(self, i: int) -> tuple[int, int]:
        """Speedtable row ``i``; 0 and rows past the end read as zero."""
        return self.speed.get(i, (0, 0)) if i else (0, 0)

    def rate(self, i: int):
        """``spd(i)``: a fixed 16-bit rate, or ``semi>>r`` for a calculated one."""
        if not i:
            return 0
        left, right = self.row(i)
        if left >= 0x80:
            return "semi" if right == 0 else f"semi>>{right}"
        return left << 8 | right

    def slide(self, i: int, sign: int, row: bool = False) -> dict:
        """Portamento up (+1) or down (-1): ``freq += sign * spd(i)`` each tick."""
        rate = self.rate(i)
        if isinstance(rate, str):
            step = rate if sign > 0 else f"-({rate})"
        else:
            step = sign * rate
        if step == 0:
            step = 0
        return {"step": step, "for": 1} if row else {"step": step, "reset": ["command"]}

    def porta(self, i: int, row: bool = False) -> dict:
        """Tone portamento: toward the note at ``spd(i)``; speed 0 ties at once."""
        spec = {"toward": "note"} if not i else {"step": self.rate(i), "toward": "note"}
        if i:
            spec["width"] = FREQ_WIDTH
        if row:
            spec["for"] = 1
        return spec

    def vibrato(self, i: int, row: bool = False) -> dict:
        """Vibrato: ``vibtime`` flips its direction after m ticks, then every 2m (+1 if odd).

        With compare value c (left & $7F), up lasts m = c // 2 + 1 ticks; every later
        swing lasts 2m ticks when c is even and 2m + 1 when odd. c >= $7E never flips.
        """
        left, right = self.row(i)
        cmp_ = left & 0x7F if i else 0
        depth = (right if left < 0x80 else ("semi" if right == 0 else f"semi>>{right}")) if i else 0
        spec = {"step": depth}
        flip = None
        if cmp_ <= 0x7D:
            m = cmp_ // 2 + 1
            flip = (m, 2 * m + (cmp_ & 1))
            spec["flip"] = list(flip)
        self.flips.add(flip)
        if row:
            spec["for"] = 1
        return spec

    def check_flips(self):
        """Raise unless every vibrato the song can run flips on the same schedule.

        The player keeps one vibrato phase per voice; a phase carried from one schedule
        into another is not one schedule's position, so mixed schedules are not exact.
        """
        if len(self.flips) > 1:
            raise LiftError(f"vibratos with different flip schedules share a phase: {self.flips}")


def _note(right: int) -> dict:
    """The note column of a wavetable row."""
    if right == 0x80:
        return {}
    if right < 0x80:
        return {"note": right if right < 0x40 else right - 0x80}
    return {"noteabs": right & 0x7F}


def _wave_command(left: int, right: int, fx: Effects) -> dict:
    """A wavetable command row ($F0-$FE) as its one shared key."""
    # pylint: disable=too-many-return-statements
    cmd = left & 0x0F
    if cmd in (1, 2):
        return {"freq": fx.slide(right, 1 if cmd == 1 else -1, row=True)}
    if cmd == 3:
        return {"freq": fx.porta(right, row=True)}
    if cmd == 4:
        return {"freq": fx.vibrato(right, row=True)}
    simple = {5: "ad", 6: "sr", 9: "pwprog", 10: "filtprog", 11: "resfilt", 12: "cut"}
    if cmd in simple:
        return {simple[cmd]: right}
    if cmd == 7 and right >= 0xE0:
        return {"ctrl": right}
    if cmd == 13 and right < 0x10:
        return {"vol": right}
    raise LiftError(f"wavetable command ${left:02X} ${right:02X} is not covered")


def wave_records(table, fx: Effects) -> dict:
    """Wavetable rows as control-program rows, jump rows folded into ``next``."""
    tab, out = _Table(table), {}
    for i in range(1, len(tab) + 1):
        left, right = tab.lt(i), tab.rt(i)
        if left == C.TABLEJUMP:
            continue
        if left >= 0xF0:
            row = _wave_command(left, right, fx)
        else:
            row = {}
            if left <= 0x0F:
                if left:
                    row["wait"] = left
            else:
                row["ctrl"] = left & 0x0F if left >= 0xE0 else left
            row.update(_note(right))
        row["next"] = tab.fold(i + 1)
        out[i] = row
    return out


def pulse_records(table) -> dict:
    """Pulsetable rows: a width set, or a spec of ``l`` ticks of a signed step."""
    tab, out = _Table(table), {}
    for i in range(1, len(tab) + 1):
        left, right = tab.lt(i), tab.rt(i)
        if left == C.TABLEJUMP:
            continue
        if left >= 0x80:
            row = {"pw": (left & 0x0F) << 8 | right}
        else:
            row = {"pw": {"step": _signed(right), "for": left}}
        row["next"] = tab.fold(i + 1)
        out[i] = row
    return out


def filter_records(table) -> dict:
    """Filtertable rows; a parameter row takes a following cutoff row's value as ``cut``
    and continues after it, as the player reads them in one tick."""
    tab, out = _Table(table), {}
    for i in range(1, len(tab) + 1):
        left, right = tab.lt(i), tab.rt(i)
        if left == C.TABLEJUMP:
            continue
        nxt = i + 1
        if left >= 0x80:
            row = {"filtmode": left & 0x70, "resfilt": right}
            if tab.lt(nxt) == 0x00:
                row["cut"] = tab.rt(nxt)
                nxt += 1
        elif left == 0:
            row = {"cut": right}
        else:
            row = {"cut": {"step": _signed(right), "for": left}}
        row["next"] = tab.fold(nxt)
        out[i] = row
    return out


def instrument_records(instruments, fx: Effects) -> dict:
    """Instruments 1..63 in shared keys."""
    out = {}
    for num, ins in enumerate(instruments[: C.MAX_INSTR - 1], start=1):
        rec = {}
        for key, value in (
            ("ad", ins.attack_decay),
            ("sr", ins.sustain_release),
            ("ctrlprog", ins.wave_ptr),
            ("pwprog", ins.pulse_ptr),
            ("filtprog", ins.filter_ptr),
            ("delay", ins.vibrato_delay),
        ):
            if value & 0xFF:
                rec[key] = value & 0xFF
        if ins.vibrato_param & 0xFF:
            rec["freq"] = fx.vibrato(ins.vibrato_param & 0xFF)
        gate = ins.gateoff_timer & 0xFF
        if gate & 0x3F:
            rec["gateoff"] = gate & 0x3F
        if gate & 0x40:
            rec["keepgate"] = 1
        if gate & 0x80:
            rec["nohr"] = 1
        first = ins.first_wave & 0xFF
        if first >= 0xFE:
            rec["firstgate"] = first & 1
        elif first:
            rec["firstctrl"] = first
        out[num] = rec
    return out


def command(cmd: int, data: int, fx: Effects):
    """A pattern command as a shared ``[name, arg]``, or None when it is no command."""
    # pylint: disable=too-many-return-statements
    if cmd == C.CMD_DONOTHING:
        return None
    if cmd in (C.CMD_PORTAUP, C.CMD_PORTADOWN):
        return ["freq", fx.slide(data, 1 if cmd == C.CMD_PORTAUP else -1)]
    if cmd == C.CMD_TONEPORTA:
        return ["freq", fx.porta(data)]
    if cmd == C.CMD_VIBRATO:
        return ["freq", fx.vibrato(data)]
    if cmd == C.CMD_FUNKTEMPO:
        return ["funk", list(fx.row(data)) if data else None]
    if cmd == C.CMD_SETTEMPO:
        ticks = data & 0x7F
        return ["vtempo" if data >= 0x80 else "tempo", 3 if ticks == 2 else ticks]
    names = {
        C.CMD_SETAD: "ad",
        C.CMD_SETSR: "sr",
        C.CMD_SETWAVE: "ctrl",
        C.CMD_SETWAVEPTR: "ctrlprog",
        C.CMD_SETPULSEPTR: "pwprog",
        C.CMD_SETFILTERPTR: "filtprog",
        C.CMD_SETFILTERCTRL: "resfilt",
        C.CMD_SETFILTERCUTOFF: "cut",
        C.CMD_SETMASTERVOL: "vol",
    }
    return [names[cmd], data]


def phrase(rows, fx: Effects) -> list:
    """Raw pattern rows as events, folding fully empty rows into ``steps``."""
    events = []
    for row in rows:
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
        cmd = command(row.command, row.data, fx)
        if cmd is not None:
            event["command"] = cmd
        if not event and events:
            events[-1]["steps"] = events[-1].get("steps", 1) + 1
        else:
            events.append(event)
    if not events:
        raise LiftError("empty pattern")
    return events


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


def _phrases(song, tracks, fx: Effects) -> dict:
    """Every pattern the tracks play, lifted to a phrase."""
    out = {}
    for name in sorted({p["phrase"] for t in tracks for p in t["plays"]}):
        num = int(name[1:], 16)
        if num >= len(song.patterns):
            raise LiftError(f"orderlist plays missing pattern {num}")
        out[name] = phrase(song.patterns[num].rows, fx)
    return out


def ticks_per_step(phrases, tracks, deftempo):
    """Ticks per row when the whole song runs at one global tempo, else None.

    That holds without funk tempo or per-voice tempo, with every tempo command naming
    the same tempo, and with one on a first row unless it equals the default tempo.
    """
    tempos, first = set(), False
    starts = {t["plays"][0]["phrase"] for t in tracks}
    for name, events in phrases.items():
        for k, event in enumerate(events):
            name_, arg = event.get("command") or (None, None)
            if name_ in ("funk", "vtempo"):
                return None
            if name_ == "tempo":
                tempos.add(arg - 1 if arg >= 3 else arg)
                first = first or (k == 0 and name in starts)
    if not tempos:
        tempos, first = {deftempo}, True
    if len(tempos) != 1 or not (first or tempos == {deftempo}):
        return None
    tempo = tempos.pop()
    return tempo + 1 if tempo >= 2 else None


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


def _song_data(song, tracks) -> tuple[dict, dict]:
    """The phrases the tracks play and the records they use, effects derived as specs."""
    fx = Effects(song.speedtable)
    phrases = _phrases(song, tracks, fx)
    records = {
        "ins": instrument_records(song.instruments, fx),
        "ctrlprog": wave_records(song.wavetable, fx),
        "pwprog": pulse_records(song.pulsetable),
        "filtprog": filter_records(song.filtertable),
    }
    fx.check_flips()
    return phrases, records


def lift(dec) -> dict:
    """A unitracker song dict for a ``pygoattracker.decompile_sid`` result."""
    info = dec.info
    _check_build(info)
    song = dec.typed.to_song()
    build = info.build
    playroutine = load_playroutine()
    adparam = C.DEFAULT_ADPARAM
    if build.adparam is not None:
        adparam = build.adparam << 8 | build.srparam
    deftempo = build.deftempo if build.deftempo is not None else _default_tempo(song)
    playroutine["constants"] = {"adparam": adparam, "deftempo": deftempo}
    freq = list(info.note_read[:128]) + [0] * max(0, 128 - len(info.note_read))
    tracks = [track(ol) for ol in song.subtunes[dec.subtune].channels]
    phrases, records = _song_data(song, tracks)
    header = dec.header
    registers = [0] * 25
    registers[0x18] = 0x0F
    tps = ticks_per_step(phrases, tracks, deftempo)
    hints = {} if tps is None else {"ticks_per_step": tps}
    return {
        "unitracker": "ir",
        "hints": hints,
        "meta": song_meta(header, f"goattracker2 subtune {dec.subtune}"),
        "clock": NTSC_CLOCK if (header.flags >> 2) & 3 == 2 else PAL_CLOCK,
        "voices": 3,
        "registers": registers,
        "tables": {"ftab": [int(v) for v in freq]},
        "playroutine": playroutine,
        "records": records,
        "tracks": tracks,
        "phrases": phrases,
    }
