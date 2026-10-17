"""Lift a SID-Wizard 1.x tune (pysidwizard's decoded .sid) to a unitracker song."""

from pysidtracker import SidImage
from pysidwizard import End, Loop, PlayPattern, parse_sid_song
from pysidwizard.features import features_for_driver
from pysidwizard.player import freq_tables

from unitracker.songio import library, modulated, song_meta
from unitracker.vm import fold_empty

PAL_CLOCK = {"cycles_per_tick": 19656, "clock_hz": 985248}
TABLE_JUMP = 0xFE
TABLE_END = 0xFF
CHORD_RETURN = 0x7E
CHORD_LOOP = 0x7F
MAX_INSTRUMENT = 0x3E
LEGATO = 0x3F
GATE_ON, GATE_OFF = 0x7D, 0x7E
NOTE_FX, NOTE_GLIDE = 0x60, 0x78
# Small-FX classes the playroutine covers: attack, decay, sustain, release, chord.
SMALL_FX = (0x20, 0x30, 0x50, 0x60, 0x70)
CHORD_FX = 0x70
BIG_FX = (0x01, 0x02, 0x03)
GLIDE = 0x03
INST_COLUMN = 0x100
NOTE_COLUMN = 0x200


class LiftError(ValueError):
    """The tune uses something the sidwizard playroutine does not cover."""


class Tables:
    """Record allocation for the per-instrument WF, pulse and filter programs."""

    # pylint: disable=too-few-public-methods

    def __init__(self):
        self.records = {"wftab": {}, "pwtab": {}, "flttab": {}}

    def program(self, name: str, data: bytes, decode) -> int:
        """Allocate ``data``'s 3-byte rows as a program; its entry index, 0 when empty."""
        rows = table_rows(name, data)
        if not rows:
            return 0
        recs = self.records[name]
        start = len(recs) + 1
        for k, row in enumerate(rows):
            rec = decode(row)
            rec["next"] = start + k + 1 if k + 1 < len(rows) else 0
            recs[start + k] = rec
        return start


def table_rows(name: str, data: bytes) -> list[bytes]:
    """A table's rows up to its end marker; jumps and partial rows are not covered."""
    rows = []
    for pos in range(0, len(data), 3):
        if data[pos] == TABLE_END:
            break
        if data[pos] == TABLE_JUMP:
            raise LiftError(f"{name}: table jumps are not covered")
        if pos + 3 > len(data):
            raise LiftError(f"{name}: a table ends in a partial row")
        rows.append(data[pos : pos + 3])
    return rows


def wf_record(row: bytes) -> dict:
    """A WF/arp row's waveform, arp and detune bytes."""
    return {"w": row[0], "a": row[1], "d": row[2]}


def pw_record(row: bytes) -> dict:
    """A pulse row's two bytes and keyboard-track amount."""
    return {"a": row[0], "b": row[1], "k": row[2]}


def filter_record(row: bytes) -> dict:
    """A filter row; a set row's first byte splits into its mode bits and resonance."""
    if row[0] >= 0x80:
        return {"a": row[0] & 0xF0, "res": row[0] & 0x0F, "b": row[1], "k": row[2]}
    return {"a": row[0], "b": row[1], "k": row[2]}


def chords(table: bytes) -> tuple[dict, dict]:
    """Chord programs: ``{index: record}`` and ``{chord number: entry index}``.

    A chord ending in $7F loops to its start; one ending in $7E ends, returning to the WF
    table. The last chord may run to the end of the table, which loops.
    """
    records, starts, rows = {}, {}, []
    number = 1

    def close(loop: bool):
        nonlocal rows, number
        if rows:
            start = len(records) + 1
            for k, offset in enumerate(rows):
                last = k + 1 == len(rows)
                nxt = (start if loop else 0) if last else start + k + 1
                records[start + k] = {"n": offset, "next": nxt}
            starts[number] = start
        number += 1
        rows = []

    for byte in table:
        if byte in (CHORD_RETURN, CHORD_LOOP):
            close(byte == CHORD_LOOP)
        else:
            rows.append(byte)
    if rows:
        close(True)
    return records, starts


def instrument_record(ins, tables: Tables, chord_starts: dict) -> dict:
    """An instrument's header fields and its table programs."""
    if ins.filter_table and ins.filter_table[0] == TABLE_END:
        raise LiftError("instruments marked unfiltered ($FF filter table) are not covered")
    rec = {
        "flags": ins.control,
        "hrad": (ins.hr_attack << 4) | ins.hr_decay,
        "hrsr": (ins.hr_sustain << 4) | ins.hr_release,
        "ad": (ins.attack << 4) | ins.decay,
        "sr": (ins.sustain << 4) | ins.release,
        "vibrato": ins.vibrato,
        "vibdelay": ins.vibrato_delay,
        "arpspeed": ins.arp_speed,
        "chord": chord_starts.get(ins.default_chord, 0),
        "octave": ins.octave_shift,
        "firstctrl": ins.effective_first_waveform,
        "ctrl": tables.program("wftab", ins.wf_table, wf_record),
        "pw": tables.program("pwtab", ins.pw_table, pw_record),
        "filter": tables.program("flttab", ins.filter_table, filter_record),
    }
    return rec


def header_rows_walkable(ins) -> None:
    """Before the first note the player walks instrument 1's header as WF rows 0 and 1."""
    if TABLE_JUMP in (ins.control, (ins.attack << 4) | ins.decay):
        raise LiftError("instrument 1's header reads as a WF-table jump")


class Events:
    """Pattern rows as events: commands in column order, instrument column first."""

    def __init__(self, ins_index, chord_starts):
        self.ins_index = ins_index
        self.chord_starts = chord_starts

    def small(self, byte: int, column: int) -> tuple[int, int]:
        """A small FX in ``column`` (0: the FX column) as ``(command, argument)``."""
        cls, low = byte & 0xF0, byte & 0x0F
        if cls not in SMALL_FX:
            raise LiftError(f"small FX ${byte:02X} is not covered")
        if cls == CHORD_FX:
            return column | cls, self.chord_starts.get(low, 0)
        return column | cls, low

    def instrument_column(self, ins, event, commands) -> None:
        """An instrument, legato or a small FX."""
        if 1 <= ins <= MAX_INSTRUMENT:
            event["instrument"] = self.ins_index(ins)
        elif ins == LEGATO:
            commands.append((LEGATO, 0))
        else:
            commands.append(self.small(ins, INST_COLUMN))

    @staticmethod
    def note_column(note, event, commands) -> None:
        """A note, a gate change or a note-column effect."""
        if 1 <= note < NOTE_FX:
            event["note"] = note
        elif note in (GATE_OFF, GATE_ON):
            event["note"] = "off" if note == GATE_OFF else "on"
        elif NOTE_FX <= note < NOTE_GLIDE:
            commands.append((NOTE_COLUMN | NOTE_FX, note & 0x0F))
        elif note == NOTE_GLIDE:
            commands.append((NOTE_COLUMN | NOTE_GLIDE, 0))
        else:
            raise LiftError(f"note-column value ${note:02X} is not covered")

    def fx_column(self, row, commands) -> None:
        """A small FX, or a big FX with its value."""
        if row.fx >= 0x20:
            commands.append(self.small(row.fx, 0))
        elif row.fx in BIG_FX:
            commands.append((row.fx, row.fx_value or 0))
        else:
            raise LiftError(f"big FX ${row.fx:02X} is not covered")

    def event(self, row) -> dict:
        """One pattern row as an event."""
        event, commands = {}, []
        if row.instrument is not None:
            self.instrument_column(row.instrument, event, commands)
        if row.note is not None:
            self.note_column(row.note, event, commands)
        if row.fx is not None:
            self.fx_column(row, commands)
        if row.fx == GLIDE and "note" in event and isinstance(event["note"], int):
            if (LEGATO, 0) not in commands:
                commands.insert(0, (LEGATO, 0))
        if len(commands) > 2:
            raise LiftError("a row with three effects is not covered")
        if len(commands) == 1:
            event["command"] = list(commands[0])
        elif commands:
            event["commands"] = [list(c) for c in commands]
        return event

    def phrase(self, pattern) -> list:
        """A pattern's rows as events, empty rows folded into ``steps``."""
        events = fold_empty(self.event(row) for row in pattern.rows)
        if not events:
            raise LiftError("empty pattern")
        return events


def track(sequence, patterns: int) -> dict:
    """An orderlist as plays; ``Loop`` gives the loop play and the end of the track."""
    plays, index_of = [], {}
    for pos, cmd in enumerate(sequence):
        if isinstance(cmd, PlayPattern):
            if not 1 <= cmd.pattern <= patterns:
                raise LiftError(f"orderlist plays missing pattern {cmd.pattern}")
            index_of[pos] = len(plays)
            plays.append({"phrase": f"p{cmd.pattern:02x}"})
        elif isinstance(cmd, Loop):
            if cmd.position not in index_of:
                raise LiftError(f"orderlist loop to {cmd.position} is not a pattern")
            return {"plays": plays, "loop": index_of[cmd.position]}
        elif isinstance(cmd, End):
            break
        else:
            raise LiftError(f"orderlist {type(cmd).__name__} is not covered")
    if not plays:
        raise LiftError("an orderlist plays no pattern")
    return {"plays": plays, "loop": None}


def first_note_selects(tracks, phrases) -> None:
    """Each voice's first pitched note selects an instrument, as the header walk needs."""
    for voice, trk in enumerate(tracks):
        for play in trk["plays"]:
            notes = [e for e in phrases[play["phrase"]] if isinstance(e.get("note"), int)]
            if notes:
                if "instrument" not in notes[0]:
                    raise LiftError(f"voice {voice}'s first note selects no instrument")
                break


def check_build(swm, features) -> None:
    """Raise for builds and tunes the sidwizard playroutine does not cover yet."""
    unsupported = {
        f"the {features.name} player build": features.name != "normal",
        "multispeed": swm.frame_speed != 1,
        "init-routine voice-register writes": any(swm.sid_init[:21]),
        "no instruments": not swm.instruments,
    }
    missing = [name for name, used in unsupported.items() if used]
    if missing:
        raise LiftError("not covered by the sidwizard playroutine yet: " + ", ".join(missing))


class Instruments:
    """The instrument records, plus the player's empty instrument once a row needs it."""

    # pylint: disable=too-few-public-methods

    def __init__(self, swm, tables: Tables, chord_starts: dict):
        self.records = {
            num: instrument_record(ins, tables, chord_starts)
            for num, ins in enumerate(swm.instruments, start=1)
        }
        self.count = len(swm.instruments)
        self.chord = chord_starts.get(1, 0)

    def index(self, ref: int) -> int:
        """The record an instrument-column reference plays: past the count, the empty one."""
        if ref <= self.count:
            return ref
        empty = self.count + 1
        if empty not in self.records:
            self.records[empty] = {"flags": 0x1A, "hrad": 0x0F, "sr": 0xF0, "chord": self.chord}
        return empty


def tempo(swm) -> tuple[dict, dict]:
    """The subtune's tempo pair as playroutine constants, and the hints it gives."""
    left, right = swm.subtune_tempos[0] if swm.subtune_tempos else (6, 6)
    if left & 0x80 and right & 0x80:
        right = left
    hints = {}
    if left & 0x7F == right & 0x7F:
        hints["ticks_per_step"] = (left & 0x7F) or 6
    return {"tempol": left & 0x7F, "tempor": right & 0x7F}, hints


def freq(swm, features) -> tuple[bytes, bytes]:
    """FREQTBL/FREQTBH as the tune's image holds them, else as the build assembles them."""
    if swm.freq_lo_image and swm.freq_hi_image:
        return swm.freq_lo_image, swm.freq_hi_image
    return freq_tables(features, swm.freq_table_tail)


def initial_registers(sid_init: bytes) -> list[int]:
    """The SID registers before the first play call: what init wrote over the reset state."""
    regs = [0] * 24 + [0x0F]
    regs[: len(sid_init[:25])] = sid_init[:25]
    return list(regs)


def lift(raw: bytes, subtune: int = 0, expressions: bool = True) -> dict:
    """A unitracker song dict for SID-Wizard tune ``raw``.

    With ``expressions`` its modulations are written as the format's expressions.
    """
    swm = parse_sid_song(raw, subtune + 1)
    features = features_for_driver(swm.driver_type)
    check_build(swm, features)
    header_rows_walkable(swm.instruments[0])
    lo, hi = freq(swm, features)
    chord_records, chord_starts = chords(swm.chord_table)
    tables = Tables()
    instruments = Instruments(swm, tables, chord_starts)
    events = Events(instruments.index, chord_starts)
    tracks = [track(seq, len(swm.patterns)) for seq in swm.sequences[:3]]
    if len(tracks) != 3:
        raise LiftError("a subtune needs three orderlists")
    phrases = {
        name: events.phrase(swm.patterns[int(name[1:], 16) - 1])
        for name in sorted({p["phrase"] for t in tracks for p in t["plays"]})
    }
    first_note_selects(tracks, phrases)
    playroutine = library("sidwizard")
    playroutine["constants"], hints = tempo(swm)
    ir = {
        "unitracker": "ir",
        "hints": hints,
        "meta": song_meta(SidImage.from_bytes(raw).header, f"sidwizard subtune {subtune}"),
        "clock": PAL_CLOCK,
        "voices": 3,
        "registers": initial_registers(swm.sid_init),
        "tables": {
            "freqlo": list(lo),
            "freqhi": list(hi),
            "exptab": list(playroutine["tables"]["exptab"]),
        },
        "records": {
            "ins": instruments.records,
            "wftab": tables.records["wftab"],
            "pwtab": tables.records["pwtab"],
            "flttab": tables.records["flttab"],
            "chordtab": chord_records,
        },
        "tracks": tracks,
        "phrases": phrases,
        "playroutine": playroutine,
    }
    return modulated(ir) if expressions else ir
