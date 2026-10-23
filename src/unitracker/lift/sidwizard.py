"""Lift a SID-Wizard 1.x tune (pysidwizard's decoded .sid) to a unitracker song."""

import copy
from functools import cache, cached_property, partial
from typing import NamedTuple

from pysidtracker import SidImage
from pysidwizard import End, Loop, PlayPattern, SWMPlayer, parse_sid_song
from pysidwizard.constants import INST_WF_TABLE_POS, TABLE_END
from pysidwizard.features import PlayerFeatures, features_for_driver
from pysidwizard.player import DEFAULT_HR_ADSR, freq_tables

from unitracker.playroutine.codec import CodecError, CommandCodec
from unitracker.playroutine.records import chained, record_types
from unitracker.lift.base import song_meta
from unitracker.playroutine.library import library
from unitracker.playroutine.tempo import written
from unitracker.lift.base import Lift, LiftError
from unitracker.player.vm import fold_empty

PAL_CLOCK = {"cycles_per_tick": 19656, "clock_hz": 985248}
TABLE_JUMP = 0xFE
CHORD_RETURN = 0x7E
CHORD_LOOP = 0x7F
# A WF row's arp byte that plays the chord; a row's waveform byte from $10; the arp speed bits
# the normal build reads; the detune byte that leaves the detune.
CHORD_ROW = 0x7F
WAVEFORM = 0x10
ARP_SPEED = 0x3F
NO_DETUNE = 0xFF
# A filter row's third byte from which it sets the routing nibble instead of the tracking.
ROUTING = 0x80
MAX_INSTRUMENT = 0x3E
LEGATO = 0x3F
GATE_ON, GATE_OFF = 0x7D, 0x7E
NOTE_FX, NOTE_GLIDE = 0x60, 0x78
# Small-FX classes the playroutine covers: attack, decay, sustain, release, chord.
SMALL_FX = (0x20, 0x30, 0x50, 0x60, 0x70)
CHORD_FX = 0x70
BIG_FX = (0x01, 0x02, 0x03)
GLIDE = 0x03
# The tempo FX as the playroutine's codes: every voice's tempo (the player's $10, whose code
# !freq takes) and swing, this voice's tempo and swing.
TEMPO_FX = {0x10: 0x12, 0x11: 0x11, 0x13: 0x13, 0x14: 0x14}
INST_COLUMN = 0x100
NOTE_COLUMN = 0x200
HR_TIMER, HR_STACCATO, HR_FIRSTCTRL, FLAGS = 0x03, 0x04, 0x08, 0xF0
STACCATO = 0x18
# The player's empty instrument: its control byte and first waveform.
EMPTY_CONTROL, EMPTY_FIRST = 0x1A, 0x09
# The ticks before a voice's first note applies (its TICK_2), each reading a WF row.
SETUP_TICKS = 2
NORMAL = features_for_driver(0)
# The arp speed's bits that also run the pulse and filter programs on multispeed calls.
MUL_TABLES = 0xC0
# EXPTABH's zero bytes before the tune's FREQTBH.
EXPTAB_ZEROS = 11


class Tables:
    """Record allocation for the per-instrument WF, pulse and filter programs."""

    # pylint: disable=too-few-public-methods

    def __init__(self):
        self.records = {"wftab": {}, "pwtab": {}, "flttab": {}}

    def program(self, name: str, ins, decode) -> int:
        """Allocate instrument ``ins``'s ``name`` program; its entry index, 0 when empty."""
        rows, loop = table_rows(name, ins)
        return self.allocate(name, [decode(row) for row in rows], loop)

    def allocate(self, name: str, rows: list, loop: int | None = None) -> int:
        """Allocate ``rows`` as a program that ends, or loops to row ``loop``; its entry, 0 when
        empty."""
        if not rows:
            return 0
        start = len(self.records[name]) + 1
        self.records[name].update(chained(rtypes()[name], rows, start, loop))
        return start


def table_rows(name: str, ins) -> tuple[list[bytes], int | None]:
    """The 3-byte rows instrument ``ins``'s ``name`` program plays, following its jumps, and the
    row its last continues to (None: it ends).

    A jump's target is an offset in the instrument's memory image. A pulse or filter jump to
    itself, and a WF jump past $7F (which the player abandons), rest as the end does; a pulse or
    filter jump to a sweep row spends a tick (a sweep of none).
    """
    image, wave = ins.memory_image(), name == "wftab"
    tables = (ins.wf_table, ins.pw_table)[: ("wftab", "pwtab", "flttab").index(name)]
    pos = INST_WF_TABLE_POS + sum(len(table) + 1 for table in tables)
    rows, seen = [], {}
    while pos < len(image) and image[pos] != TABLE_END:
        if pos + 3 > len(image):
            raise LiftError(f"{name}: a table ends in a partial row")
        if image[pos] != TABLE_JUMP:
            seen[pos] = len(rows)
            rows.append(image[pos : pos + 3])
            pos += 3
            continue
        target = image[pos + 1]
        if not rows:
            raise LiftError(f"{name}: a program that starts with a jump is not covered")
        if (wave and target >= 0x80) or (target == pos and not wave):
            break
        first = image[target] if target + 3 <= len(image) else TABLE_END
        if first in (TABLE_JUMP, TABLE_END):
            raise LiftError(
                f"{name}: a jump to ${target:02X}, which lands on no row, is not covered"
            )
        if wave and first < WAVEFORM:
            raise LiftError(f"wftab: a jump to ${target:02X}, a hold row, is not covered")
        if not wave and first < 0x80:
            rows.append(bytes([0, 0, rows[-1][2]]))
        if target in seen:
            return rows, seen[target]
        pos = target
    return rows, None


def ram(ins) -> bytes:
    """The bytes the player reads from instrument ``ins``'s address: the image's own, past its
    tables too, while they hold its encoding."""
    image = ins.memory_image()
    return ins.ram if ins.ram.startswith(image) else image


def idle_rows(ins, features: PlayerFeatures = NORMAL, reads: int = SETUP_TICKS) -> list[int]:
    """The WF rows a voice reads on the ``reads`` ticks before its first note applies, before it
    has an instrument, ending in $FF: ``ins`` is the player's instrument 0 (see
    :func:`dummy`), whose memory the cursor init zeroes reads from the control byte, following
    its jumps; one past $7F the player abandons, resting as at the end. A hold row's count keeps
    that many ticks from reading a row, in a build with arp speeds."""
    image, out, pos = ram(ins) + bytes([TABLE_END]), [], 0
    while reads > 0:
        jump = image[pos] == TABLE_JUMP
        if jump and image[pos + 1] >= 0x80:
            break
        if jump and image[pos + 1] < len(image) - 1 and image[image[pos + 1]] != TABLE_JUMP:
            pos = image[pos + 1]
        row = image[pos : pos + 3]
        if row[0] == TABLE_END:
            break
        if row[0] == TABLE_JUMP or (row[1] == CHORD_ROW and features.chordsupport):
            raise LiftError(
                f"instrument 1's header byte ${pos + (row[0] != TABLE_JUMP):02X}, which a voice"
                " reads as a WF jump or chord before its first note, is not covered"
            )
        out += row
        pos, reads = pos + 3, reads - 1 - (row[0] if 0 < row[0] < WAVEFORM else 0)
        if 0 < row[0] < WAVEFORM and not features.arpspeedsupp:
            reads += row[0]
    return [*out, TABLE_END]


def pw_record(row: bytes, features: PlayerFeatures = NORMAL) -> dict:
    """A pulse row's two bytes and keyboard-track amount, which a build without pulse keyboard
    tracking never reads."""
    return {"a": row[0], "b": row[1], "k": row[2] if features.pwkeybtrack else 0}


def filter_record(row: bytes, features: PlayerFeatures = NORMAL) -> dict:
    """A filter row; a set row's first byte splits into its mode bits and resonance. A third
    byte of $80-$8F sets the routing nibble, which has no shared form here; a build without
    filter keyboard tracking reads neither."""
    if not features.filtkbtrack:
        row = bytes([row[0], row[1], 0])
    if ROUTING <= row[2] < ROUTING + 0x10:
        raise LiftError(f"filter row routing byte ${row[2]:02X} is not covered")
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
            self.records.update(chained(rtypes()["restart"], [dict(r) for r in rows], start))
        return self.entry[rows]


def restart_rows(control: int, hrad: int, hrsr: int, first: int) -> tuple:
    """The hard-restart program of an instrument header's control byte, as hashable rows.

    Bits 0 and 1 fire the restart one and two ticks before the row applies; bit 2 makes it
    staccato (control $18); bit 3 writes the first waveform on the note's first tick, which gates
    the voice on.
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
    if rows or control & HR_FIRSTCTRL:
        rows.append({"gate": 1, **({"ctrl": first} if control & HR_FIRSTCTRL else {})})
    return tuple(tuple(sorted(row.items())) for row in rows)


def build_wf(ins, features: PlayerFeatures) -> tuple[list[bytes], int | None, int]:
    """An instrument's WF rows and loop (:func:`table_rows`) and the ticks each holds after its
    first, as the build plays them: without arp speeds a row is read every tick, and without
    detune no row sets one."""
    rows, loop = table_rows("wftab", ins)
    if not features.detunesupport:
        rows = [bytes([wave, arp, 0]) for wave, arp, _ in rows]
    return rows, loop, ins.arp_speed & ARP_SPEED if features.arpspeedsupp else 0


def wf_rows(ins, chord, features: PlayerFeatures = NORMAL) -> tuple[list, int | None]:
    """An instrument's WF rows with ``chord`` (from :func:`chords`) inlined at each $7F row,
    and the row a looping chord returns to.

    Every row holds for the instrument's arp speed. A chord step writes the $7F row's waveform
    and the next offset, so each chord row carries both; it leaves the detune, which a row
    clears unless the program sets none. A build without arp speeds reads a row every tick, and
    one without detune plays none.
    """
    rows, loop, hold = build_wf(ins, features)
    detune = 0 if all(row[2] in (0, NO_DETUNE) for row in rows) else NO_DETUNE
    out, index = [], []
    for k, (wave, arp, det) in enumerate(rows):
        index.append(len(out))
        if arp != CHORD_ROW or not features.chordsupport:
            out.append({"w": wave, "a": arp, "d": det, "h": hold})
            continue
        if chord is None:
            raise LiftError("a WF row plays a chord the instrument does not have")
        offsets = chord[0]
        if max(offsets) >= 0x80:
            raise LiftError(f"chord offset ${max(offsets):02X} is not covered")
        after = k + 1 if k + 1 < len(rows) else loop
        if not chord[1] and (wave < WAVEFORM or (after is not None and rows[after][0] < WAVEFORM)):
            raise LiftError("a chord returning to or from a WF hold row is not covered")
        out += [{"w": wave, "a": n, "d": detune, "h": hold} for n in offsets]
        if chord[1]:
            return settled(out, index[-1], features.arpspeedsupp), index[-1]
    loop = None if loop is None else index[loop]
    return settled(out, loop, features.arpspeedsupp), loop


def settled(rows: list, loop: int | None, holds: bool = True) -> list:
    """WF rows with each detune a row keeps written as the one it keeps, and each hold row's
    reload equal to its count.

    A note starts the program with no detune. A row below $10 lasts its count whatever its
    reload, which paces the program only after a last row that ends it; without ``holds`` (arp
    speeds) it is a waveform row.
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
        if holds and row["w"] < WAVEFORM and row["h"] != row["w"]:
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

    def __init__(
        self, instruments, chord_table: bytes, tables: Tables, features: PlayerFeatures = NORMAL
    ):
        self.instruments, self.features = instruments, features
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
        rows = table_rows("wftab", native)[0]
        if self.features.chordsupport and any(row[1] == CHORD_ROW for row in rows):
            chosen = self.chords.get(chord, self.chords.get(native.default_chord))
        key = (ins, chosen)
        if key not in self.entries:
            self.entries[key] = self.tables.allocate(
                "wftab", *wf_rows(native, chosen, self.features)
            )
        return self.entries[key]


def build_restart(control: int, hrad: int, hrsr: int, first: int, features: PlayerFeatures):
    """``restart_rows``' arguments as the build plays them: without hard-restart types every
    restart fires from TICK_0 with the default envelope and none is staccato, and without the
    first-frame switch the first tick always writes control $09."""
    if not features.hardrestypes:
        control = (control & ~(HR_TIMER | HR_STACCATO)) | 2
        hrad, hrsr = DEFAULT_HR_ADSR >> 8, DEFAULT_HR_ADSR & 0xFF
    if not features.frame1switch:
        control, first = control | HR_FIRSTCTRL, EMPTY_FIRST
    return control, hrad, hrsr, first


def instrument_record(
    ins, tables: Tables, wave: int, restarts: Restarts, features: PlayerFeatures = NORMAL
) -> dict:
    """An instrument's header fields and its table and restart programs, ``wave`` its WF
    program, each as the player build plays it: a build without octave shifts, or without the
    pulse or filter reset switch, ignores that field or flag."""
    if ins.filter_table and ins.filter_table[0] == TABLE_END:
        raise LiftError("instruments marked unfiltered ($FF filter table) are not covered")
    hrad = (ins.hr_attack << 4) | ins.hr_decay
    hrsr = (ins.hr_sustain << 4) | ins.hr_release
    rows = restart_rows(
        *build_restart(ins.control, hrad, hrsr, ins.effective_first_waveform, features)
    )
    kept = FLAGS & ~(0x40 * (not features.pwresetsw) | 0x80 * (not features.filtresetsw))
    rec = {
        "flags": ins.control & kept,
        "ad": (ins.attack << 4) | ins.decay,
        "sr": (ins.sustain << 4) | ins.release,
        "vibrato": ins.vibrato,
        "vibdelay": ins.vibrato_delay,
        "octave": ins.octave_shift if features.octaveshift else 0,
        "ctrl": wave,
        "pw": tables.program("pwtab", ins, partial(pw_record, features=features)),
        "filter": tables.program("flttab", ins, partial(filter_record, features=features)),
        "restart": restarts.program(rows),
        "mul": ins.arp_speed & MUL_TABLES,
    }
    return rec


@cache
def rtypes() -> dict:
    """The sidwizard playroutine's record types."""
    return record_types(library("sidwizard"))


@cache
def shared_commands() -> CommandCodec:
    """The sidwizard playroutine's commands."""
    playroutine = library("sidwizard")
    return CommandCodec(playroutine["commands"], rtypes(), playroutine["constants"])


def tempo_command(fx: int, value: int) -> tuple[int, int]:
    """A tempo FX as ``(command, argument)``."""
    code, arg = TEMPO_FX[fx], value
    try:
        shared_commands().encode(code, arg)
    except CodecError as exc:
        raise LiftError(
            f"tempo FX ${fx:02X} ${value:02X} is not covered: a tempo is 3-127 ticks, a swing"
            " tempo 3-15 (0: 6)"
        ) from exc
    return code, arg


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
        elif row.fx in TEMPO_FX:
            commands.append(tempo_command(row.fx, row.fx_value or 0))
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

    def phrase(self, pattern, number: int = 0) -> list:
        """Pattern ``number``'s rows as events, empty rows folded into ``steps``."""
        rows = []
        for k, row in enumerate(pattern.rows):
            try:
                rows.append(self.event(row))
            except LiftError as exc:
                raise LiftError(f"pattern ${number:02X} row ${k:02X}: {exc}") from exc
        events = fold_empty(rows)
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


def first_note_selects(_lift, ir: dict) -> dict:
    """``ir``, once each voice's first pitched note selects an instrument.

    A note that selects none on a voice that has never had one has no instrument state
    to play.
    """
    phrases = ir["phrases"]
    for voice, trk in enumerate(ir["tracks"]):
        for play in trk["plays"]:
            notes = [e for e in phrases[play["phrase"]] if isinstance(e.get("note"), int)]
            if notes:
                if "instrument" not in notes[0]:
                    raise LiftError(f"voice {voice}'s first note selects no instrument")
                break
    return ir


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


def chord_runs(lift, ir: dict) -> dict:
    """``ir`` with each chord FX as ``!run`` of the WF program its note starts, allocated in
    ``lift.waves``.

    A chord FX selects the chord a $7F row plays until the voice's next note, so on a note
    that starts the instrument it starts that instrument's program with the chord inlined.
    A note that names no instrument plays the voice's last one, found by walking each
    orderlist twice through its loop. The playroutine plays ``!run`` in the instrument
    column on a row that names no instrument, else in the FX column.
    """
    phrases, runs = copy.deepcopy(ir["phrases"]), {}
    for trk in ir["tracks"]:
        ins, glide = 0, False
        for name, k, event in _voice_events(trk, phrases):
            ins = event.get("instrument", ins)
            for j, target in _chord_targets(event, ins, glide, lift.waves).items():
                if runs.setdefault((name, k, j), target) != target:
                    raise LiftError("a chord FX's note plays different instruments")
            glide = _gliding(event, glide)
    for (name, k, j), target in runs.items():
        _commands(phrases[name][k])[j][:] = [CHORD_FX, target]
    wftab = dict(lift.waves.tables.records["wftab"])
    return {**ir, "phrases": phrases, "records": {**ir["records"], "wftab": wftab}}


def _voice_events(trk: dict, phrases: dict):
    """``(phrase, index, event)`` in a voice's play order, twice through its loop."""
    loop = trk["loop"]
    for play in trk["plays"] + (trk["plays"][loop:] if loop is not None else []):
        for k, event in enumerate(phrases[play["phrase"]]):
            yield play["phrase"], k, event


def fold_octaves(_lift, ir: dict) -> dict:
    """``ir`` with each instrument's octave shift moved into the notes it plays.

    The player adds the selected instrument's shift to every note, so each note becomes its
    sounding pitch and every shift 0. A row that selects an instrument of another shift
    without a note retunes the sounding note, and a phrase note played under instruments of
    different shifts has no one pitch: neither is covered.
    """
    phrases, records, shifts = copy.deepcopy(ir["phrases"]), ir["records"]["ins"], {}
    for trk in ir["tracks"]:
        octave, sounding = 0, False
        for name, k, event in _voice_events(trk, phrases):
            note = isinstance(event.get("note"), int)
            if "instrument" in event:
                new = records[event["instrument"]].get("octave", 0)
                if sounding and not note and new != octave:
                    raise LiftError("an octave change without a note is not covered")
                octave = new
            if note and shifts.setdefault((name, k), octave) != octave:
                raise LiftError("a note played under instruments of different octaves")
            sounding = sounding or note
    for (name, k), octave in shifts.items():
        event = phrases[name][k]
        event["note"] += octave
        if event["note"] < 1:
            raise LiftError(f"a note shifted by {octave} semitones sounds below C0")
    ins = {num: {k: v for k, v in rec.items() if k != "octave"} for num, rec in records.items()}
    return {**ir, "phrases": phrases, "records": {**ir["records"], "ins": ins}}


class Instruments:
    """The instrument records, plus the player's empty instrument once a row needs it."""

    # pylint: disable=too-few-public-methods

    def __init__(self, swm, tables: Tables, features: PlayerFeatures = NORMAL):
        self.restarts, self.features = Restarts(), features
        self.waves = Waves(swm.instruments, swm.chord_table, tables, features)
        self.records = {
            num: instrument_record(
                ins, tables, self.waves.entry(num, ins.default_chord), self.restarts, features
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
            rows = restart_rows(
                *build_restart(EMPTY_CONTROL, 0x0F, 0x00, EMPTY_FIRST, self.features)
            )
            self.records[empty] = {
                "flags": EMPTY_CONTROL & FLAGS,
                "sr": 0xF0,
                "restart": self.restarts.program(rows),
            }
        return empty


def tempo(swm):
    """The subtune's initial tempo as written, in play calls: both bytes of its pair, or the first
    when it has bit 7 set (the player's single tempo), each a tempo byte (0 plays 6) of player
    ticks, ``frame_speed`` calls each."""
    left, right = swm.subtune_tempos[0] if swm.subtune_tempos else (6, 6)
    if left & 0x80:
        right = left
    try:
        pair = (shared_commands().encode(0x12, b & 0x7F)[1] for b in (left, right))
        return written(*(swm.frame_speed * t for t in pair))
    except CodecError as exc:
        raise LiftError(f"the subtune's tempo ${left:02X} ${right:02X} is not covered") from exc


def freq(swm, features) -> tuple[bytes, bytes]:
    """FREQTBL/FREQTBH as the tune's image holds them, else as the build assembles them."""
    if swm.freq_lo_image and swm.freq_hi_image:
        return swm.freq_lo_image, swm.freq_hi_image
    return freq_tables(features, swm.freq_table_tail)


def exptab(hi: bytes, size: int) -> list[int]:
    """EXPTABH's first ``size`` bytes, which the vibrato and slide amplitudes read: zeros, then
    the tune's FREQTBH ``hi``."""
    return ([0] * EXPTAB_ZEROS + list(hi))[:size]


def first_notes(swm, frames: int = 1 << 14) -> list[tuple[int, int, int]]:
    """Each voice's ``(call, pulse word, pulse sweep count)`` as the native player starts its
    first note (``frames`` and 0s for a voice that plays none within ``frames``): the play calls
    before it, and the 12-bit pulse its set-up ticks leave."""
    player, out = SWMPlayer(swm), [None] * 3
    for call in range(frames):
        player.play_frame()
        for k, voice in enumerate(player.voices[:3]):
            if out[k] is None and voice.instrument is not None:
                pulse = (voice.pw_hi & 0x0F) << 8 | voice.pw_lo
                out[k] = (call, pulse, voice.pw_sweep_count)
        if None not in out:
            break
    return [first or (frames, 0, 0) for first in out]


def dummy(swm):
    """The instrument INSPTLO/HI slot 0 points at, which the player reads for a voice without an
    instrument: instrument 1, unless the image links another."""
    return swm.dummy_instrument or swm.instruments[0]


def idle_reads(swm, firsts: list) -> int:
    """The ticks a voice may read a WF row before its first note: its set-up ticks, and with
    multispeed every call before the latest voice's first note."""
    return SETUP_TICKS if swm.frame_speed == 1 else max(call for call, _, _ in firsts) + 1


class Decoded(NamedTuple):
    """A SID-Wizard tune's subtune: its pysidwizard module, PSID header and 0-based number."""

    swm: object
    header: object
    subtune: int


class SidWizardLift(Lift):
    """A SID-Wizard 1.x tune, ``tune`` a :class:`Decoded`."""

    playroutine = "sidwizard"
    normalisations = (first_note_selects, chord_runs, fold_octaves)

    def __init__(self, tune: Decoded, expressions: bool = True):
        super().__init__(tune, expressions)
        self.waves: Waves | None = None

    @staticmethod
    def decode(raw: bytes, subtune: int) -> Decoded:
        return Decoded(parse_sid_song(raw, subtune + 1), SidImage.from_bytes(raw).header, subtune)

    def native(self):
        return SWMPlayer(self.tune.swm)

    @cached_property
    def features(self):
        """The player build the tune was exported for: the flags its player code was assembled
        with, else its driver byte's build."""
        swm = self.tune.swm
        return swm.features or features_for_driver(swm.driver_type)

    def check_build(self) -> None:
        swm = self.tune.swm
        unsupported = {
            "init-routine voice-register writes": any(swm.sid_init[:21]),
            "no instruments": not swm.instruments,
        }
        missing = [name for name, used in unsupported.items() if used]
        if missing:
            raise LiftError(
                f"not covered by the {self.playroutine} playroutine yet: " + ", ".join(missing)
            )

    def structure(self) -> dict:
        swm = self.tune.swm
        lo, hi = freq(swm, self.features)
        firsts = first_notes(swm)
        tables = Tables()
        instruments = Instruments(swm, tables, self.features)
        self.waves = instruments.waves
        events = Events(instruments.index)
        tracks = [track(seq, len(swm.patterns)) for seq in swm.sequences[:3]]
        if len(tracks) != 3:
            raise LiftError("a subtune needs three orderlists")
        phrases = {
            name: events.phrase(swm.patterns[int(name[1:], 16) - 1], int(name[1:], 16))
            for name in sorted({p["phrase"] for t in tracks for p in t["plays"]})
        }
        playroutine = library(self.playroutine)
        playroutine["constants"] = {
            name: int(getattr(self.features, name, False)) for name in playroutine["constants"]
        } | {"speed": swm.frame_speed}
        return {
            "unitracker": "ir",
            "tempo": tempo(swm),
            "clock": {
                **PAL_CLOCK,
                "cycles_per_tick": PAL_CLOCK["cycles_per_tick"] // swm.frame_speed,
            },
            "meta": song_meta(self.tune.header, f"{self.playroutine} subtune {self.tune.subtune}"),
            "voices": 3,
            "tables": {
                "freqlo": list(lo),
                "freqhi": list(hi),
                "exptab": exptab(hi, len(playroutine["tables"]["exptab"])),
                "idle": idle_rows(dummy(swm), self.features, idle_reads(swm, firsts)),
                "pwcount": [count for _, _, count in firsts],
                "build": [int(swm.first_frame_freq_by_track)],
            },
            "records": {
                "ins": instruments.records,
                **{name: dict(rows) for name, rows in tables.records.items()},
                "restart": instruments.restarts.records,
                "vinit": {v: {"pw": pw} for v, (_, pw, _) in enumerate(firsts) if pw},
            },
            "tracks": tracks,
            "phrases": phrases,
            "playroutine": playroutine,
        }
