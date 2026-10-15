"""Lift a GoatTracker 2 tune (pygoattracker's decompiled .sid) to a unitracker song."""

from importlib import resources

import yaml
from pygoattracker import constants as C
from pygoattracker.model import PlayPattern, Repeat, Transpose

PAL_CLOCK = {"cycles_per_tick": 19656, "clock_hz": 985248}
NTSC_CLOCK = {"cycles_per_tick": 17095, "clock_hz": 1022727}


class LiftError(ValueError):
    """The tune uses something the GoatTracker 2 machine does not cover."""


def load_machine() -> dict:
    """The GoatTracker 2 machine definition."""
    text = resources.files("unitracker").joinpath("machines/goattracker2.yaml").read_text()
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


def _note_actions(right):
    if right == 0x80:
        return []
    if right >= 0x80:
        n = right & 0x7F
    else:
        n = f"(note + {right}) & 0x7F" if right else "note & 0x7F"
    return [{"call": "wnote", "with": {"n": n}}]


def wave_records(table) -> dict:
    """Wavetable rows as records: delay rows wait, then each row's actions run."""
    tab, out = _Table(table), {}
    for i in range(1, len(tab) + 1):
        left, right = tab.lt(i), tab.rt(i)
        if left == C.TABLEJUMP:
            continue
        rec = {"next": tab.fold(i + 1)}
        if left <= C.WAVELASTDELAY:
            rec.update(delay=1, wait=left, do=_note_actions(right))
        elif left < C.WAVESILENT:
            rec["do"] = [{"set": {"wave": left}}] + _note_actions(right)
        elif left <= C.WAVELASTSILENT:
            rec["do"] = [{"set": {"wave": left & 0x0F}}] + _note_actions(right)
        else:
            rec["do"] = [
                {"set": {"arg": right}},
                {"run": {"record": "wavecmd", "index": left & 0x0F, "field": "do"}},
                {"set": {"skip": 1}},
            ]
        if not rec["do"]:
            del rec["do"]
        out[i] = rec
    return out


def pulse_records(table) -> dict:
    """Pulsetable rows: set rows and timed signed sweeps of the pulse accumulator."""
    tab, out = _Table(table), {}
    for i in range(1, len(tab) + 1):
        left, right = tab.lt(i), tab.rt(i)
        if left == C.TABLEJUMP:
            continue
        if left >= 0x80:
            value = ((left & 0x0F) << 8) | right
            out[i] = {"set": 1, "do": [{"set": {"pulse": value}}], "next": tab.fold(i + 1)}
        else:
            step = right - 0x100 if right >= 0x80 else right
            out[i] = {
                "time": left,
                "each": [{"accumulate": "pulse", "step": step}],
                "next": tab.fold(i + 1),
            }
    return out


def filter_records(table) -> dict:
    """Filtertable rows: parameter rows (absorbing a following cutoff row), cutoffs, sweeps."""
    tab, out = _Table(table), {}
    for i in range(1, len(tab) + 1):
        left, right = tab.lt(i), tab.rt(i)
        if left == C.TABLEJUMP:
            continue
        if left >= 0x80:
            do = [{"set": {"filtertype": left & 0x70, "filterctrl": right}}]
            nxt = i + 1
            if tab.lt(nxt) == 0x00:
                do.append({"set": {"filtercutoff": tab.rt(nxt)}})
                nxt += 1
            out[i] = {"set": 1, "do": do, "next": tab.fold(nxt)}
        elif left == 0:
            out[i] = {"set": 1, "do": [{"set": {"filtercutoff": right}}], "next": tab.fold(i + 1)}
        else:
            out[i] = {
                "time": left,
                "each": [{"accumulate": "filtercutoff", "step": right}],
                "next": tab.fold(i + 1),
            }
    return out


_INS_FIELDS = (
    ("ad", "attack_decay"),
    ("sr", "sustain_release"),
    ("wave_ptr", "wave_ptr"),
    ("pulse_ptr", "pulse_ptr"),
    ("filter_ptr", "filter_ptr"),
    ("speed", "vibrato_param"),
    ("vibdelay", "vibrato_delay"),
    ("gatetimer", "gateoff_timer"),
    ("firstwave", "first_wave"),
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
        if row.command or row.data:
            event["command"] = [row.command, row.data]
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


def _phrases(song, tracks) -> dict:
    """Every pattern the tracks play, lifted to a phrase."""
    out = {}
    for name in sorted({p["phrase"] for t in tracks for p in t["plays"]}):
        num = int(name[1:], 16)
        if num >= len(song.patterns):
            raise LiftError(f"orderlist plays missing pattern {num}")
        out[name] = phrase(song.patterns[num].rows)
    return out


def lift(dec) -> dict:
    """A unitracker song dict for a ``pygoattracker.decompile_sid`` result."""
    info = dec.info
    unsupported = {
        "SIMPLEPULSE pulse tables": info.simplepulse,
        "full-ghosting builds": info.state is not None,
        "init-routine SID writes": bool(info.init_regs),
        "instrument-vibrato-only builds": info.live_vibrato,
    }
    missing = [name for name, used in unsupported.items() if used]
    if missing:
        raise LiftError("not covered by the goattracker2 machine yet: " + ", ".join(missing))
    song = dec.typed.to_song()
    build = info.build
    machine = load_machine()
    adparam = C.DEFAULT_ADPARAM
    if build.adparam is not None:
        adparam = build.adparam << 8 | build.srparam
    deftempo = build.deftempo if build.deftempo is not None else _default_tempo(song)
    machine["constants"] = {"adparam": adparam, "deftempo": deftempo}
    freq = list(info.note_read[:128]) + [0] * max(0, 128 - len(info.note_read))
    tracks = [track(ol) for ol in song.subtunes[dec.subtune].channels]
    phrases = _phrases(song, tracks)
    header = dec.header
    registers = [0] * 25
    registers[0x18] = 0x0F
    return {
        "unitracker": 0,
        "meta": {
            "title": header.name,
            "author": header.author,
            "released": header.released,
            "source": f"goattracker2 subtune {dec.subtune}",
        },
        "clock": NTSC_CLOCK if (header.flags >> 2) & 3 == 2 else PAL_CLOCK,
        "voices": 3,
        "registers": registers,
        "tables": {
            "ftab": [int(v) for v in freq],
            "speed_l": [0] + [v & 0xFF for v in song.speedtable.left],
            "speed_r": [0] + [v & 0xFF for v in song.speedtable.right],
        },
        "records": {
            "ins": instrument_records(song.instruments),
            "wavetab": wave_records(song.wavetable),
            "pulsetab": pulse_records(song.pulsetable),
            "filttab": filter_records(song.filtertable),
        },
        "tracks": tracks,
        "phrases": phrases,
        "machine": machine,
    }
