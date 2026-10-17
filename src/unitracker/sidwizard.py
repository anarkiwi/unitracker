"""Lift a SID-Wizard 1.x tune (pysidwizard's decoded .sid) to a unitracker song."""

from pysidtracker import SidImage
from pysidwizard import End, Loop, PlayPattern, parse_sid_song
from pysidwizard.features import features_for_driver
from pysidwizard.player import freq_tables

from unitracker.songio import library, song_meta
from unitracker.modfit.convert import convert
from unitracker.vm import fold_empty

PAL_CLOCK = {"cycles_per_tick": 19656, "clock_hz": 985248}
TABLE_JUMP = 0xFE
TABLE_END = 0xFF
CHORD_RETURN = 0x7E
CHORD_LOOP = 0x7F
# A WF row's arp byte that plays the chord; a row's waveform byte from $10; the arp speed bits
# the normal build reads; the detune byte that leaves the detune.
CHORD_ROW = 0x7F
WAVEFORM = 0x10
ARP_SPEED = 0x3F
NO_DETUNE = 0xFF
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
HR_TIMER, HR_STACCATO, HR_FIRSTCTRL, FLAGS = 0x03, 0x04, 0x08, 0xF0
STACCATO = 0x18
# The player's empty instrument: its control byte and first waveform.
EMPTY_CONTROL, EMPTY_FIRST = 0x1A, 0x09


class LiftError(ValueError):
    """The tune uses something the sidwizard playroutine does not cover."""


class Tables:
    """Record allocation for the per-instrument WF, pulse and filter programs."""

    # pylint: disable=too-few-public-methods

    def __init__(self):
        self.records = {"wftab": {}, "pwtab": {}, "flttab": {}}

    def program(self, name: str, data: bytes, decode) -> int:
        """Allocate ``data``'s 3-byte rows as a program; its entry index, 0 when empty."""
        return self.allocate(name, [decode(row) for row in table_rows(name, data)])

    def allocate(self, name: str, rows: list, loop: int | None = None) -> int:
        """Allocate ``rows`` as a program that ends, or loops to row ``loop``; its entry, 0 when
        empty."""
        if not rows:
            return 0
        recs = self.records[name]
        start = len(recs) + 1
        for k, rec in enumerate(rows):
            last = 0 if loop is None else start + loop
            recs[start + k] = {**rec, "next": start + k + 1 if k + 1 < len(rows) else last}
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


def pw_record(row: bytes) -> dict:
    """A pulse row's two bytes and keyboard-track amount."""
    return {"a": row[0], "b": row[1], "k": row[2]}


def filter_record(row: bytes) -> dict:
    """A filter row; a set row's first byte splits into its mode bits and resonance."""
    if row[0] >= 0x80:
        return {"a": row[0] & 0xF0, "res": row[0] & 0x0F, "b": row[1], "k": row[2]}
    return {"a": row[0], "b": row[1], "k": row[2]}


def chords(table: bytes) -> dict:
    """``{chord number: (note offsets, loops)}`` of the chord table.

    A chord ending in $7F loops to its start; one ending in $7E ends, returning to the WF
    table. The last chord may run to the end of the table, which loops.
    """
    out, rows, number = {}, [], 1
    for byte in table:
        if byte in (CHORD_RETURN, CHORD_LOOP):
            if rows:
                out[number] = (tuple(rows), byte == CHORD_LOOP)
            number, rows = number + 1, []
        else:
            rows.append(byte)
    if rows:
        out[number] = (tuple(rows), True)
    return out


class Restarts:
    """The hard-restart programs, one per distinct instrument restart."""

    # pylint: disable=too-few-public-methods

    def __init__(self):
        self.records, self.entry = {}, {}

    def program(self, rows: tuple) -> int:
        """The entry index of the program of ``rows``, 0 when it has none."""
        if not rows:
            return 0
        if rows not in self.entry:
            self.entry[rows] = start = len(self.records) + 1
            for k, row in enumerate(rows):
                nxt = start + k + 1 if k + 1 < len(rows) else 0
                self.records[start + k] = {**dict(row), "next": nxt}
        return self.entry[rows]


def restart_rows(control: int, hrad: int, hrsr: int, first: int) -> tuple:
    """The hard-restart program of an instrument header's control byte, as hashable rows.

    Bits 0 and 1 fire the restart one and two ticks before the row applies; bit 2 makes it
    staccato (control $18); bit 3 writes the first waveform on the note's first tick.
    """
    timer = control & HR_TIMER
    if timer == HR_TIMER:
        raise LiftError("a hard restart on both ticks before the note is not covered")
    rows = []
    if timer:
        ahead = {"hold": 2 if timer & 2 else 1, "gate": 0, "ad": hrad, "sr": hrsr}
        if control & HR_STACCATO:
            ahead["ctrl"] = STACCATO
        rows.append(ahead)
    note = {"ctrl": first} if control & HR_FIRSTCTRL else {}
    if rows or note:
        rows.append(note)
    return tuple(tuple(sorted(row.items())) for row in rows)


def wf_rows(ins, chord) -> tuple[list, int | None]:
    """An instrument's WF rows with ``chord`` (from :func:`chords`) inlined at each $7F row,
    and the row a looping chord returns to.

    Every row holds for the instrument's arp speed. A chord step writes the $7F row's waveform
    and the next offset, so each chord row carries both; it leaves the detune, which a row
    clears unless the program sets none.
    """
    rows = table_rows("wftab", ins.wf_table)
    hold = ins.arp_speed & ARP_SPEED
    detune = 0 if all(row[2] in (0, NO_DETUNE) for row in rows) else NO_DETUNE
    out = []
    for k, (wave, arp, det) in enumerate(rows):
        if arp != CHORD_ROW:
            out.append({"w": wave, "a": arp, "d": det, "h": hold})
            continue
        if chord is None:
            raise LiftError("a WF row plays a chord the instrument does not have")
        offsets, loops = chord
        if max(offsets) >= 0x80:
            raise LiftError(f"chord offset ${max(offsets):02X} is not covered")
        if not loops and (wave < WAVEFORM or (k + 1 < len(rows) and rows[k + 1][0] < WAVEFORM)):
            raise LiftError("a chord returning to or from a WF hold row is not covered")
        start = len(out)
        out += [{"w": wave, "a": n, "d": detune, "h": hold} for n in offsets]
        if loops:
            return settled(out, start), start
    return settled(out, None), None


def settled(rows: list, loop: int | None) -> list:
    """WF rows with each detune a row keeps written as the one it keeps, and each hold row's
    reload equal to its count.

    A note starts the program with no detune. A row below $10 lasts its count whatever its
    reload, which paces the program only after a last row that ends it.
    """
    kept, detune = {}, 0
    again = range(loop, len(rows)) if loop is not None else range(0)
    for k in [*range(len(rows)), *again]:
        if rows[k]["d"] == NO_DETUNE:
            kept.setdefault(k, set()).add(detune)
        else:
            detune = rows[k]["d"]
    out = []
    for k, row in enumerate(rows):
        row = dict(row)
        if k in kept:
            if len(kept[k]) > 1:
                raise LiftError(f"WF row {k} keeps a detune that differs between loop passes")
            (row["d"],) = kept[k]
        if row["w"] < WAVEFORM and row["h"] != row["w"]:
            if loop is None and k + 1 == len(rows):
                raise LiftError(
                    "a WF hold row that ends its program with an arp speed other than its"
                    " count is not covered"
                )
            row["h"] = row["w"]
        out.append(row)
    return out


class Waves:
    """Each instrument's WF program with the chord its $7F rows play."""

    # pylint: disable=too-few-public-methods

    def __init__(self, instruments, chord_table: bytes, tables: Tables):
        self.instruments = instruments
        self.chords = chords(chord_table)
        self.tables = tables
        self.entries = {}

    def entry(self, ins: int, chord: int) -> int:
        """The program instrument record ``ins`` plays with chord number ``chord``, 0 for none.

        A chord the table does not hold leaves the instrument's own.
        """
        if not 1 <= ins <= len(self.instruments):
            return 0
        native = self.instruments[ins - 1]
        chosen = None
        if any(row[1] == CHORD_ROW for row in table_rows("wftab", native.wf_table)):
            chosen = self.chords.get(chord, self.chords.get(native.default_chord))
        key = (ins, chosen)
        if key not in self.entries:
            self.entries[key] = self.tables.allocate("wftab", *wf_rows(native, chosen))
        return self.entries[key]


def instrument_record(ins, tables: Tables, wave: int, restarts: Restarts) -> dict:
    """An instrument's header fields and its table and restart programs, ``wave`` its WF
    program."""
    if ins.filter_table and ins.filter_table[0] == TABLE_END:
        raise LiftError("instruments marked unfiltered ($FF filter table) are not covered")
    hrad = (ins.hr_attack << 4) | ins.hr_decay
    hrsr = (ins.hr_sustain << 4) | ins.hr_release
    rows = restart_rows(ins.control, hrad, hrsr, ins.effective_first_waveform)
    rec = {
        "flags": ins.control & FLAGS,
        "ad": (ins.attack << 4) | ins.decay,
        "sr": (ins.sustain << 4) | ins.release,
        "vibrato": ins.vibrato,
        "vibdelay": ins.vibrato_delay,
        "octave": ins.octave_shift,
        "ctrl": wave,
        "pw": tables.program("pwtab", ins.pw_table, pw_record),
        "filter": tables.program("flttab", ins.filter_table, filter_record),
        "restart": restarts.program(rows),
    }
    return rec


class Events:
    """Pattern rows as events: commands in column order, instrument column first."""

    def __init__(self, ins_index):
        self.ins_index = ins_index

    def small(self, byte: int, column: int) -> tuple[int, int]:
        """A small FX in ``column`` (0: the FX column) as ``(command, argument)``; a chord's
        argument is its number until :func:`chord_runs` resolves it."""
        cls, low = byte & 0xF0, byte & 0x0F
        if cls not in SMALL_FX:
            raise LiftError(f"small FX ${byte:02X} is not covered")
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
    """Each voice's first pitched note selects an instrument.

    A note that selects none on a voice that has never had one has no instrument state
    to play.
    """
    for voice, trk in enumerate(tracks):
        for play in trk["plays"]:
            notes = [e for e in phrases[play["phrase"]] if isinstance(e.get("note"), int)]
            if notes:
                if "instrument" not in notes[0]:
                    raise LiftError(f"voice {voice}'s first note selects no instrument")
                break


def _commands(event: dict) -> list:
    if "commands" in event:
        return event["commands"]
    return [event["command"]] if "command" in event else []


def _gliding(event: dict, glide: bool) -> bool:
    """Whether a note glide is pending after ``event``: a note-column glide sets it, and a
    note (which slides) or another pitch effect ends it."""
    codes = {code for code, _ in _commands(event)}
    if NOTE_COLUMN | NOTE_GLIDE in codes:
        return True
    if isinstance(event.get("note"), int) or codes & {*BIG_FX, NOTE_COLUMN | NOTE_FX}:
        return False
    return glide


def _chord_targets(event: dict, ins: int, glide: bool, waves: Waves) -> dict:
    """``{command position: WF program}`` for each chord FX of ``event``, played on ``ins``."""
    cmds = _commands(event)
    starts = isinstance(event.get("note"), int) and [LEGATO, 0] not in cmds and not glide
    out = {}
    for j, (code, number) in enumerate(cmds):
        if code not in (CHORD_FX, INST_COLUMN | CHORD_FX):
            continue
        if not starts:
            raise LiftError("a chord FX on a row that starts no note is not covered")
        if code == CHORD_FX and "instrument" not in event:
            raise LiftError("an FX-column chord on a note without an instrument")
        out[j] = waves.entry(ins, number)
    return out


def chord_runs(tracks, phrases, waves: Waves) -> None:
    """Each chord FX as ``!run`` of the WF program its note starts.

    A chord FX selects the chord a $7F row plays until the voice's next note, so on a note
    that starts the instrument it starts that instrument's program with the chord inlined.
    A note that names no instrument plays the voice's last one, found by walking each
    orderlist twice through its loop. The playroutine plays ``!run`` in the instrument
    column on a row that names no instrument, else in the FX column.
    """
    runs = {}
    for trk in tracks:
        loop = trk["loop"]
        ins, glide = 0, False
        for play in trk["plays"] + (trk["plays"][loop:] if loop is not None else []):
            for k, event in enumerate(phrases[play["phrase"]]):
                ins = event.get("instrument", ins)
                for j, target in _chord_targets(event, ins, glide, waves).items():
                    if runs.setdefault((play["phrase"], k, j), target) != target:
                        raise LiftError("a chord FX's note plays different instruments")
                glide = _gliding(event, glide)
    for (name, k, j), target in runs.items():
        _commands(phrases[name][k])[j][:] = [CHORD_FX, target]


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

    def __init__(self, swm, tables: Tables):
        self.restarts = Restarts()
        self.waves = Waves(swm.instruments, swm.chord_table, tables)
        self.records = {
            num: instrument_record(
                ins, tables, self.waves.entry(num, ins.default_chord), self.restarts
            )
            for num, ins in enumerate(swm.instruments, start=1)
        }
        self.count = len(swm.instruments)

    def index(self, ref: int) -> int:
        """The record an instrument-column reference plays: past the count, the empty one."""
        if ref <= self.count:
            return ref
        empty = self.count + 1
        if empty not in self.records:
            rows = restart_rows(EMPTY_CONTROL, 0x0F, 0x00, EMPTY_FIRST)
            self.records[empty] = {
                "flags": EMPTY_CONTROL & FLAGS,
                "sr": 0xF0,
                "restart": self.restarts.program(rows),
            }
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


def lift(raw: bytes, subtune: int = 0, expressions: bool = True) -> dict:
    """A unitracker song dict for SID-Wizard tune ``raw``.

    With ``expressions`` its modulations are written as the format's expressions.
    """
    # pylint: disable=too-many-locals
    swm = parse_sid_song(raw, subtune + 1)
    features = features_for_driver(swm.driver_type)
    check_build(swm, features)
    lo, hi = freq(swm, features)
    tables = Tables()
    instruments = Instruments(swm, tables)
    events = Events(instruments.index)
    tracks = [track(seq, len(swm.patterns)) for seq in swm.sequences[:3]]
    if len(tracks) != 3:
        raise LiftError("a subtune needs three orderlists")
    phrases = {
        name: events.phrase(swm.patterns[int(name[1:], 16) - 1])
        for name in sorted({p["phrase"] for t in tracks for p in t["plays"]})
    }
    first_note_selects(tracks, phrases)
    chord_runs(tracks, phrases, instruments.waves)
    playroutine = library("sidwizard")
    playroutine["constants"], hints = tempo(swm)
    ir = {
        "unitracker": "ir",
        "hints": hints,
        "meta": song_meta(SidImage.from_bytes(raw).header, f"sidwizard subtune {subtune}"),
        "clock": PAL_CLOCK,
        "voices": 3,
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
            "restart": instruments.restarts.records,
        },
        "tracks": tracks,
        "phrases": phrases,
        "playroutine": playroutine,
    }
    return convert(ir) if expressions else ir
