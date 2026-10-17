"""Lift a GoatTracker 2 tune (pygoattracker's decompiled .sid) to a unitracker song."""

from importlib import resources

import yaml
from pygoattracker import constants as C
from pygoattracker.model import PlayPattern, Repeat, Transpose

from unitracker.songio import modulated, song_meta
from unitracker.vm import fold_empty

PAL_CLOCK = {"cycles_per_tick": 19656, "clock_hz": 985248}
NTSC_CLOCK = {"cycles_per_tick": 17095, "clock_hz": 1022727}
# The playroutine's legato command: the note does not retrigger, as a toneporta's.
LEGATO = 17


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
    ("gt", "gateoff_timer"),
    ("fw", "first_wave"),
)


def instrument_records(instruments) -> dict:
    """Instruments 1..63 as records of their nonzero byte fields."""
    out = {}
    for num, ins in enumerate(instruments[: C.MAX_INSTR - 1], start=1):
        rec = {k: getattr(ins, attr) & 0xFF for k, attr in _INS_FIELDS}
        out[num] = {k: v for k, v in rec.items() if v}
    return out


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


def ticks_per_step(phrases, tracks, deftempo):
    """Ticks per row when the whole song runs at one global tempo, else None.

    That holds without funk tempo or per-voice tempo, with every tempo command naming
    the same tempo, and with one on a first row unless it equals the default tempo.
    """
    tempos, first = set(), False
    starts = {t["plays"][0]["phrase"] for t in tracks}
    for name, events in phrases.items():
        for k, event in enumerate(events):
            cmd, arg = event.get("command") or (0, 0)
            if cmd == C.CMD_FUNKTEMPO or (cmd == C.CMD_SETTEMPO and arg >= 0x80):
                return None
            if cmd == C.CMD_SETTEMPO:
                tempo = arg & 0x7F
                tempos.add(tempo - 1 if tempo >= 3 else tempo)
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


def lift(dec, expressions: bool = True) -> dict:
    """A unitracker song dict for a ``pygoattracker.decompile_sid`` result.

    With ``expressions`` its modulations are written as the format's expressions.
    """
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
    phrases = _phrases(song, tracks)
    header = dec.header
    registers = [0] * 25
    registers[0x18] = 0x0F
    hints = {}
    tps = ticks_per_step(phrases, tracks, deftempo)
    if tps is not None:
        hints["ticks_per_step"] = tps
    ir = {
        "unitracker": "ir",
        "hints": hints,
        "meta": song_meta(header, f"goattracker2 subtune {dec.subtune}"),
        "clock": NTSC_CLOCK if (header.flags >> 2) & 3 == 2 else PAL_CLOCK,
        "voices": 3,
        "registers": registers,
        "tables": {
            "ftab": [int(v) for v in freq],
        },
        "records": {
            "ins": instrument_records(song.instruments),
            "ctrltab": wave_records(song.wavetable),
            "pulsetab": pulse_records(song.pulsetable),
            "filttab": filter_records(song.filtertable),
            "speedtab": speed_records(song.speedtable),
        },
        "tracks": tracks,
        "phrases": phrases,
        "playroutine": playroutine,
    }
    return modulated(ir) if expressions else ir
