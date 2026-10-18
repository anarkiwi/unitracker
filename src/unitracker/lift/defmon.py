"""Lift a defMON replay (.sid) to a unitracker song.

The replay keeps its state in self-modified operands; init runs on pydefmon's DefmonPlayer, and
that memory, relocated to the $1000 build's layout, gives the tables and state. Song timing comes
from simulating the replay's arranger and pattern counters.
"""

import itertools

import numpy as np
import yaml
from pysidtracker import CodePattern, SidImage, find_code_all
from pysidtracker.mos6502 import OP_LEN, OPCODES

from unitracker.format.songio import library, song_meta
from unitracker.modfit.convert import convert, window
from unitracker.player.vm import SongPlayer, first_mismatch

PAL_HZ = 985248
NTSC_HZ = 1022727
VOICE_OFFSETS = (0x00, 0x31, 0x62)
MAX_STEP_TICKS = 1 << 14
MAX_SONG_STEPS = 4096

# The $1000 build's replay code by region, a part code or each build's variant of it (see locate).
REGIONS = (
    (
        0x1022,
        "SID band",
        "A2 ?? A9 ?? 8E 02 D4 8D 03 D4 A2 ?? A9 ?? 8E 00 D4 8D 01 D4 A2 ?? A0 ?? A9 ?? 49 ??"
        " 8E 06 D4 8C 05 D4 8D 04 D4",
    ),
    (
        0x10A9,
        "ACID step and arranger",
        "A9 ?? 8D 17 D4 A9 ?? 09 0F 8D 18 D4 A9 ?? 18 ?? ?? 8D B6 10 A9 ?? ?? ?? 10 03 AD CE 10"
        " 8D BE 10 69 ?? 30 04 C9 ?? B0 03 AD CE 10 ?? 8D 16 D4 A9 ?? 10 ?? 29 0F 8D D9 10 8D"
        " 4A 11 8D D2 11 8D 5A 12 A0 ?? BE 00 1B 10",
        {
            "counted": "28 B9 00 1D F0 1C CC EC 14 F0 09 8C EC 14 8D ED 14 4C 12 11 CE ED 14 D0"
            " 09 A9 00 8D EC 14 C8 4C 16 11",
            "plain": "07",
        },
        "B9 00 1C A8 BE 00 1B BD 00 1A 8D 86 11 BD 80 1A 8D 87 11 BE 00 1C BD 00 1A 8D 0E 12 BD"
        " 80 1A 8D 0F 12 BE 00 1D BD 00 1A 8D 96 12 BD 80 1A 8D 97 12 C8 8C EB 10",
    ),
    (
        0x117F,
        "pattern event",
        "A0 01 A9 ?? 10 0A B9 ?? ?? 8D EF 12 8E E0 12 C8 A9 ?? 10 0A B9 ?? ?? 8D 82 13 8E 73 13"
        " C8 A9 ?? 10 13 B9 ?? ?? 8D ED 12 8D 7F 13 8E 19 10 8E 1A 10 8E 1B 10 C8 A2 ?? 10 05"
        " 8E D9 10 30 12 98 18 6D 86 11 8D 86 11 90 03 EE 87 11 A9 0F 8F 4A 11",
    ),
    (
        0x12DF,
        "sidcall cascade",
        "A9 ?? F0 0B 30 2B CE E0 12 4C 10 13 ?? ?? ?? A0 ?? B9 00 19 D0 07 B9 00 18 A8 B9 00 19"
        " 85 FC B9 00 1E 8D E0 12 B9 00 18 C8 8C EF 12 A2 00 20 B0 16",
    ),
    (
        0x1405,
        "pitch and pulse steps",
        "A2 62 BD 1B 10 F0 47 10 5B 0A A8 10 15 BD 19 10 F9 F8 14 9D 19 10 BD 1A 10 F9 94 15 9D"
        " 1A 10 4C 3A 14 B9 78 15 18 7D 19 10 9D 19 10 B9 14 16 7D 1A 10 9D 1A 10 18 BC 7F 13"
        " BD 19 10 79 9C 15 9D 2D 10 B9 38 16 7D 1A 10 9D 2F 10 4C 95 14 BC 7F 13 B9 9C 15 18"
        " 7D 1F 10 9D 2D 10 B9 38 16 9D 2F 10 4C 95 14 18 7D 7F 13 A8 B9 84 15 38 F9 83 15 8D"
        " 89 14 B9 20 16 F9 1F 16 8D 91 14 BC 7F 13 B9 9C 15 18 69 ?? 9D 2D 10 B9 38 16 69 ??"
        " 9D 2F 10 BC 1E 10 F0 49 10 34 98 0B|2B 7F 7D 23 10 9D 23 10 90 3C BD 25 10 C9 0F F0 06"
        " FE 25 10 4C E3 14 A9 F8 9D 23 10 98 49 80 9D 1E 10 4C E3 14 A9 01 9D 23 10 98 49 80"
        " 9D 1E 10 4C E3 14 BD 23 10 FD 1E 10 9D 23 10 B0 08 BD 25 10 F0 E2 DE 25 10 8A CB 31"
        " 30 03 4C 07 14 60",
    ),
    (
        0x16B0,
        "sidTAB walk",
        "85 FB A0 00 B1 FB F0 ?? 0A 85 96 10 06 C8 B1 FB 9D 3B 10 90 06 C8 B1 FB 9D 3D 10 24 96"
        " 50 06 C8 B1 FB 9D 39 10 A5 96 29 20 F0 06 C8 B1 FB 9D 37 10 A5 96 29 10 F0 ?? C8 B1"
        " FB",
        {
            "absolute": "30 04 18 7D ED 12 29 7F 9D 7F 13",
            "relative": "18 7D ED 12 9D 7F 13",
        },
        "A5 96 29 08 F0 06 C8 B1 FB 9D 1B 10 A5 96 29 04"
        " F0 0B C8 B1 FB 9D 25 10 29 F0 9D 23 10 C8 B1 FB F0 7E 0A 85 96 90 06 C8 B1 FB 9D 1E"
        " 10 A5 96 F0 6F 29 80 F0 24 C8 B1 FB F0 09 29 08 D0 0E B1 FB 4C 4B 17 AD AA 10 3D 21"
        " 10 4C 4B 17 AD AA 10 29 0F 11 FB 1D 20 10 8D AA 10 A7 96 F0 43 29 40 F0 06 C8 B1 FB"
        " 8D AF 10 8A 29 20 F0 06 C8 B1 FB 8D CA 10 8A 29 10 F0 29 C8 B3 FB C8 B1 FB 30 0F 8E"
        " B6 10 8D BE 10 A9 00 8D B9 10 8D C0 10 60 0A 30 10 8E B9 10 4A 8D C0 10 A9 69 8D B8"
        " 10 8D BF 10 60 8E B9 10 4B 7F 8D C0 10 A9 E9 8D B8 10 8D BF 10 60",
    ),
)

# Replay tables: (name, address, length).
TABLES = (
    ("note_lo", 0x159C, 256),
    ("note_hi", 0x1638, 256),
    ("por_lo", 0x1583, 257),
    ("por_hi", 0x161F, 257),
    ("rate_lo", 0x1578, 128),
    ("rate_hi", 0x1614, 128),
)
SIDTAB_LO, SIDTAB_HI, SIDTAB_DL = 0x1800, 0x1900, 0x1E00
PATTERN_LO, PATTERN_HI = 0x1A00, 0x1A80
ARRANGER = (0x1B00, 0x1C00, 0x1D00)
UNRELOCATED_OPS = {0x20, 0x4C, 0x6C}
FIXED_MODES = {"imm", "rel", "impl", "acc"}
SID_PAGE = 0xD4
# Each voice's detune, routing bit and routing mask.
DETUNE = (0x101F, 0x1020, 0x1021)
GATE_A, GATE_B, GATE_N, ALT = 0x40, 0x20, 0x10, 0x80

# Voice state operands at base + voice offset: (cell, lo address, hi address or None).
VOICE_STATE = (
    ("pw", 0x1023, 0x1025),
    ("flo", 0x102D, None),
    ("fhi", 0x102F, None),
    ("sr", 0x1037, None),
    ("ad", 0x1039, None),
    ("ctrl", 0x103B, None),
    ("wgl", 0x103D, None),
    ("sacc", 0x1019, 0x101A),
    ("af", 0x101B, None),
    ("ps", 0x101E, None),
    ("note", 0x137F, None),
    ("tbase", 0x12ED, None),
    ("rowA", 0x12EF, None),
    ("cntA", 0x12E0, None),
    ("rowB", 0x1382, None),
    ("cntB", 0x1373, None),
)
# The voice cells the replay's init ($1522) sets, with their values; the module carries the rest,
# of which the frequency registers sound only before the first pitch step, and are dropped once a
# replay proves them unheard.
VOICE_UNHEARD = ("flo", "fhi")
VOICE_SET = {
    **dict.fromkeys(("sr", "ad", "ctrl", "wgl", "af", "ps", "note", "rowA", "rowB"), 0),
    "cntA": 0xFF,
    "cntB": 0xFF,
}


class LiftError(ValueError):
    """The tune is not a replay build the defMON playroutine covers."""


UNREPEATED = "the play routine's sub-tick calls do not repeat within the song"


def code_pattern(code: str) -> tuple[CodePattern, dict]:
    """``code`` as a pattern capturing each relocatable operand by its $1000 build value, a zero
    page one as ``zXX`` and an absolute one as ``aXXXX``, and the offsets of its ``a|b`` tokens
    with their encodings."""
    toks, out, alts, i = code.split(), [], {}, 0
    while i < len(toks):
        if toks[i] == "??":
            out.append("??")
            i += 1
            continue
        ops = [int(b, 16) for b in toks[i].split("|")]
        if len(ops) > 1:
            alts[i] = set(ops)
        ins = toks[i : i + OP_LEN[ops[0]]]
        i += len(ins)
        head, mode = "??" if len(ops) > 1 else ins[0], OPCODES[ops[0]][1]
        if "??" in ins or mode in FIXED_MODES:
            out += [head, *ins[1:]]
        elif ops[0] in UNRELOCATED_OPS:
            out += [head, "??", "??"]
        elif len(ins) == 2:
            out += [head, f"{{z{ins[1]}}}"]
        else:
            out += (
                [head, *ins[1:]]
                if int(ins[2], 16) == SID_PAGE
                else [head, f"{{a{ins[2]}{ins[1]}:w}}"]
            )
    return CodePattern(" ".join(out)), alts


def region_builds(parts):
    """``(variants, code, layout)`` of each build's code of a region's ``parts``: ``layout`` lists
    each part's ``($1000 build offset, offset, length)``."""
    options = [list(p.items()) if isinstance(p, dict) else [(None, p)] for p in parts]
    canon = [len(opts[0][1].split()) for opts in options]
    for combo in itertools.product(*options):
        sizes = [len(code.split()) for _, code in combo]
        starts = zip(itertools.accumulate([0, *canon]), itertools.accumulate([0, *sizes]), sizes)
        yield {name for name, _ in combo if name}, " ".join(c for _, c in combo), list(starts)


def locate(mem) -> tuple[dict, set]:
    """``({$1000 build address: address}, variants)`` of a replay build in ``mem``: each region's
    code is found wherever it lies, and its relocatable operands and its own bytes must place each
    address of the $1000 build once."""
    at, variants = {}, set()
    for addr, name, *parts in REGIONS:
        for names, code, layout in region_builds(parts):
            pattern, alts = code_pattern(code)
            match = next(
                (
                    m.addr
                    for m in find_code_all(mem, pattern, start=0, end=len(mem))
                    if all(mem[m.addr + k] in v for k, v in alts.items())
                ),
                None,
            )
            if match is not None:
                break
        else:
            raise LiftError(
                f"not a defMON replay build: no {name} code (${addr:04X} in the $1000 build)"
            )
        variants |= names
        places = [
            (int(n[1:], 16), int.from_bytes(mem[match + k : match + k + w], "little"))
            for n, k, w in pattern.captures
        ]
        places += [(addr + c + k, match + o + k) for c, o, n in layout for k in range(n)]
        for canon, actual in places:
            if at.setdefault(canon, actual) != actual:
                raise LiftError(
                    f"the {name} code places ${canon:04X} of the $1000 build at both"
                    f" ${at[canon]:04X} and ${actual:04X}"
                )
    return at, variants


class Calls:
    """The calls the tune's play routine makes to the replay, run on ``native`` as far as they are
    asked for: each call's register grid row, and whether it ran the pattern step, stepping voice
    0's pattern counter or arming the arranger (a byte at ``marks``), as a multispeed play
    routine's sub-tick calls do not."""

    def __init__(self, native, marks):
        self.native, self.marks, self.rows, self.full = native, marks, [], []

    def run(self, ticks: int) -> None:
        """Run the calls to ``ticks``; a call lost while the one before it still runs is refused."""
        mem, cpu = self.native._mem, self.native._mpu  # pylint: disable=protected-access
        while len(self.rows) < ticks:
            before, cycles = [mem[a] for a in self.marks], cpu.processorCycles
            self.native.play_frame()
            if cpu.processorCycles == cycles:
                raise LiftError(f"call {len(self.rows)} is lost: the call before it still runs")
            self.rows.append(self.native.regs[:])
            self.full.append([mem[a] for a in self.marks] != before)

    def grid(self, ticks: int) -> np.ndarray:
        """The register grid of the first ``ticks`` calls."""
        self.run(ticks)
        return np.asarray(self.rows[:ticks], dtype=np.uint8)

    def period(self, ticks: int) -> int:
        """The least period of the first ``ticks`` calls' pattern steps."""
        self.run(ticks)
        full = np.asarray(self.full[:ticks])
        return next((p for p in range(1, ticks) if (full[p:] == full[:-p]).all()), ticks)


class Replay:
    """A replay build's memory after init, read at the $1000 build's addresses (see :func:`locate`);
    ``mem`` is the build's own memory, which pattern and sidTAB row pointers address. With a
    ``native`` player of the tune its :class:`Calls` time the pattern steps; else every call runs
    one; ``cycle`` is the period of the calls the song's steps are timed with."""

    def __init__(self, mem, native=None):
        self.mem = mem
        self.at, self.variants = locate(mem)
        self.calls = None if native is None else Calls(native, [self.at[0x114A], self.at[0x10D9]])
        self.cycle = 1

    def full(self, tick: int) -> bool:
        """Whether call ``tick`` runs the pattern step."""
        if self.calls is None:
            return True
        self.calls.run(tick + 1)
        return self.calls.full[tick]

    def period(self, ticks: int) -> int:
        """The least period of the first ``ticks`` calls' pattern steps."""
        return 1 if self.calls is None else self.calls.period(ticks)

    def __call__(self, addr, off=0) -> int:
        """The byte ``off`` past the build's place of ``addr``."""
        return self.mem[self.at[addr] + off]

    def word(self, lo, hi, off=0) -> int:
        """The word of the bytes ``off`` past ``lo`` and ``hi``."""
        return self(lo, off) | (self(hi, off) << 8)

    def span(self, addr, n) -> list:
        """The ``n`` bytes from ``addr``."""
        return list(self.mem[self.at[addr] : self.at[addr] + n])


def voice_init(rep: Replay) -> dict:
    """Each voice's state the module carries through init, as ``vinit`` records; the cells init
    sets (:data:`VOICE_SET`) must hold the playroutine's values."""
    out = {}
    for v, off in enumerate(VOICE_OFFSETS):
        if tuple(rep(a, off) for a in DETUNE) != (
            v,
            1 << v,
            0xFF ^ (1 << v),
        ):
            raise LiftError(f"voice {v} detune/routing constants are not the standard ones")
        rec = {}
        for cell, lo, hi in VOICE_STATE:
            rec[cell] = rep(lo, off) if hi is None else rep.word(lo, hi, off)
            if rec[cell] != VOICE_SET.get(cell, rec[cell]):
                raise LiftError(f"init leaves voice {v}'s {cell} at ${rec[cell]:02X}")
        out[v] = {
            cell: value
            for cell, value in rec.items()
            if cell not in VOICE_SET and cell not in VOICE_UNHEARD
        }
    return out


def unheard_init(ir: dict, theirs: np.ndarray) -> dict:
    """``ir`` without the ``vinit`` notes its replay does not hear, once a replay to the song's
    recurrence and one period more proves it plays the replay's grid ``theirs`` (the module's
    frequency registers, which the playroutine does not carry, unheard)."""
    ticks = len(theirs)

    def mismatch(song):
        player = SongPlayer(song)
        return first_mismatch(player.render_grid(ticks), theirs, player.starts)

    found = mismatch(ir)
    if found is not None:
        raise LiftError(
            f"the init's frequency registers are heard: tick {found[0]}, register {found[1]}"
        )
    voices = ir["records"]["vinit"]
    for v in sorted(voices):
        if voices[v].get("tbase"):
            rest = {k: x for k, x in voices[v].items() if k != "tbase"}
            trial = {**ir, "records": {**ir["records"], "vinit": {**voices, v: rest}}}
            if mismatch(trial) is None:
                voices = trial["records"]["vinit"]
    return {**ir, "records": {**ir["records"], "vinit": voices}}


def global_init(rep: Replay) -> dict:
    """The ACID step's direction the module carries through init, as the ``ginit`` record.

    The build's init zeroes the filter registers, the accumulator, its step and its offset ``cp``
    (the accumulator holds any later offset), so an init that leaves another value is refused."""
    ops = {rep(0x10B8), rep(0x10BF)}
    if len(ops) != 1 or not ops <= {0xE9, 0x69}:
        raise LiftError("ACID opcodes are not a matching SBC/ADC pair")
    cleared = {
        "the ACID offset cp": rep(0x10CA),
        "resfilt": rep(0x10AA),
        "fv": rep(0x10AF),
        "acc": rep.word(0x10B6, 0x10BE),
        "astep": rep.word(0x10B9, 0x10C0),
    }
    for cell, value in cleared.items():
        if value:
            raise LiftError(f"init leaves {cell} at ${value:02X}, not 0")
    return {"sub": int(rep(0x10B8) == 0xE9)}


def model_constants(rep: Replay) -> dict:
    """The SID-model constants init's $D41B probe patches into the cutoff output, and ``trabs``,
    whether the build's sidTAB note column has an absolute form."""
    if rep(0x10D4) not in (0xEA, 0x0A):
        raise LiftError("cutoff output opcode is neither NOP nor ASL")
    trabs = int("absolute" in rep.variants)
    return {"floor": rep(0x10CE), "dbl": int(rep(0x10D4) == 0x0A), "trabs": trabs}


def resolve_row(rep: Replay, row):
    """The row a sidcall fetch of ``row`` reads after the JP redirect."""
    return row if rep(SIDTAB_HI, row) else rep(SIDTAB_LO, row)


# sidTAB columns in the walk's read order: (bitmap, bit, value bytes, field).
COLUMNS = (
    (0, 0x40, 1, "wgh"),
    (0, 0x80, 1, "wgl"),
    (0, 0x20, 1, "ad"),
    (0, 0x10, 1, "sr"),
    (0, 0x08, 1, "tr"),
    (0, 0x04, 1, "af"),
    (0, 0x02, 1, "pw"),
    (1, 0x80, 1, "ps"),
    (1, 0x40, 1, "re"),
    (1, 0x20, 1, "fv"),
    (1, 0x10, 1, "cp"),
    (1, 0x08, 2, "acid"),
)


def row_fields(mem, addr) -> dict:
    """The present columns of the sidTAB row body at ``addr`` ($16B0 bitmap walk)."""
    pos, out, bitmap, half = addr, {}, mem[addr], 0
    for col_half, bit, size, field in COLUMNS:
        if col_half != half:
            pos += 1
            bitmap, half = mem[pos], col_half
        if bitmap & bit:
            out[field] = int.from_bytes(bytes(mem[pos + 1 : pos + 1 + size]), "little")
            pos += size
    return out


def sidtab_records(rep: Replay, seeds) -> dict:
    """Records for every sidTAB row a cascade can fetch from ``seeds``."""
    out, todo = {}, sorted(set(seeds))
    while todo:
        row = todo.pop()
        if row in out:
            continue
        eff = resolve_row(rep, row)
        if not rep(SIDTAB_HI, eff):
            raise LiftError(f"sidTAB row {row} resolves to row {eff}, which has no body")
        dl = rep(SIDTAB_DL, eff)
        rec = {"dl": dl, "next": (eff + 1) & 0xFF}
        rec.update(row_fields(rep.mem, rep.word(SIDTAB_LO, SIDTAB_HI, eff)))
        out[row] = rec
        if dl < 0x80:
            todo.append(rec["next"])
    return dict(sorted(out.items()))


def decode_event(mem, ptr):
    """``(flag, slot_a, slot_b, note, size)`` of the compacted pattern event at ``ptr``."""
    flag = mem[ptr & 0xFFFF]
    vals, size = {}, 1
    for gate in (GATE_A, GATE_B, GATE_N):
        if flag & gate:
            vals[gate] = mem[(ptr + size) & 0xFFFF]
            size += 1
    return flag, vals.get(GATE_A), vals.get(GATE_B), vals.get(GATE_N), size


def arranger_step(rep: Replay, y, jr, jc):
    """The replay's arranger at $10EA: ``(pattern numbers, next y, jr, jc)``; a jump row of a
    build without the ``counted`` arranger jumps every time."""
    if rep(ARRANGER[0], y) & 0x80:
        count = rep(ARRANGER[2], y) if "counted" in rep.variants else 0
        if count == 0:
            y = rep(ARRANGER[1], y)
        elif y == jr:
            jc = (jc - 1) & 0xFF
            if jc:
                y = rep(ARRANGER[1], y)
            else:
                jr, y = 0, (y + 1) & 0xFF
        else:
            jr, jc, y = y, count, rep(ARRANGER[1], y)
    pats = tuple(rep(base, y) for base in ARRANGER)
    return pats, (y + 1) & 0xFF, jr, jc


def simulate_step(rep: Replay, pats, e, start=0):
    """One song step from call ``start``: per voice ``[(tick, flag, a, b, n)]`` applied, step
    length, next e."""
    ptrs = [rep.word(PATTERN_LO, PATTERN_HI, x) for x in pats]
    cnt = [e] * 3
    applied = [[], [], []]
    for tick in range(MAX_STEP_TICKS):
        if not rep.full(start + tick):
            continue
        alt = None
        for v in range(3):
            if cnt[v] & 0x80:
                flag, a, b, n, size = decode_event(rep.mem, ptrs[v])
                applied[v].append((tick, flag, a, b, n))
                if flag & ALT:
                    alt = flag
                else:
                    ptrs[v] += size
                    cnt[v] = flag & 0x0F
            else:
                cnt[v] = (cnt[v] - 1) & 0xFF
        if alt is not None:
            return applied, tick + 1, alt & 0x0F
    raise LiftError(f"song step with patterns {pats} never reaches an ALT event")


def phrase_events(applied, length) -> list:
    """A voice's applied events within one song step as unitracker events."""
    events = []
    marks = [(0, {})] if not applied or applied[0][0] else []
    for tick, _flag, a, b, n in applied:
        event = {}
        if n is not None:
            event["note"] = n
        if a is not None:
            event["instrument"] = a + 1
        if b is not None:
            event["command"] = [1, b]
        marks.append((tick, event))
    for i, (tick, event) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else length
        if not event and events:
            events[-1]["steps"] = events[-1].get("steps", 1) + end - tick
            continue
        if end - tick > 1:
            event["steps"] = end - tick
        events.append(event)
    return events


def song_steps(rep: Replay, subtune):
    """The song steps the arranger plays from init, and the step index the song loops to: the
    song's state recurs at a call of the same phase of the calls' pattern-step period, which the
    calls must repeat within the song."""
    if not rep(0x10D9) & 0x80:
        raise LiftError("init did not arm the arranger")
    period = 1
    while True:
        jr, jc = (rep(0x14EC), rep(0x14ED)) if "counted" in rep.variants else (0, 0)
        y, e, tick = subtune & 0xFF, rep(0x10D9) & 0x0F, 0
        seen, steps = {}, []
        while (y, jr, jc, e, tick % period) not in seen:
            if len(steps) >= MAX_SONG_STEPS:
                raise LiftError("arranger does not loop")
            seen[(y, jr, jc, e, tick % period)] = len(steps)
            pats, y, jr, jc = arranger_step(rep, y, jr, jc)
            applied, length, e = simulate_step(rep, pats, e, tick)
            steps.append((applied, length))
            tick += length
        found = rep.period(tick)
        if found == period:
            rep.cycle = period
            return steps, seen[(y, jr, jc, e, tick % period)]
        if 2 * found > tick:
            raise LiftError(UNREPEATED)
        period = found


def _compress(names, loop):
    """Plays for a voice, consecutive repeats folded within the intro and the loop."""
    plays, loop_play = [], 0
    for i, name in enumerate(names):
        if i == loop:
            loop_play = len(plays)
        if plays and i != loop and plays[-1]["phrase"] == name:
            plays[-1]["repeat"] = plays[-1].get("repeat", 1) + 1
        else:
            plays.append({"phrase": name})
    return {"plays": plays, "loop": loop_play}


def structure(rep: Replay, subtune):
    """Tracks, phrases, and the sidTAB rows the patterns arm."""
    steps, loop = song_steps(rep, subtune)
    phrases, names_of = {}, {}

    def name(events):
        key = yaml.safe_dump(events, sort_keys=True)
        if key not in names_of:
            names_of[key] = f"p{len(names_of):03d}"
            phrases[names_of[key]] = events
        return names_of[key]

    tracks = [
        _compress([name(phrase_events(applied[v], length)) for applied, length in steps], loop)
        for v in range(3)
    ]
    seeds = {
        x
        for applied, _ in steps
        for voice in applied
        for _tick, _flag, a, b, _n in voice
        for x in (a, b)
        if x is not None
    }
    return tracks, phrases, seeds


def lift(raw: bytes, subtune=None, expressions: bool = True) -> dict:
    """A unitracker song dict for a defMON replay ``.sid``.

    With ``expressions`` its modulations are written as the format's expressions.
    """
    from pydefmon import DefmonPlayer  # pylint: disable=import-outside-toplevel

    header = SidImage.from_bytes(raw).header
    if header.second_sid:
        raise LiftError(f"a multi-SID tune (SID 2 at ${0xD000 | header.second_sid << 4:04X})")
    if subtune is None:
        subtune = (header.start_song or 1) - 1
    player = DefmonPlayer(raw, subtune=subtune)
    mem = player._mem  # pylint: disable=protected-access
    rep = Replay(mem, DefmonPlayer(raw, subtune=subtune))
    vinit = voice_init(rep)
    tracks, phrases, seeds = structure(rep, subtune)
    playroutine = library("defmon")
    playroutine["constants"] = model_constants(rep)
    ir = {
        "unitracker": "ir",
        "meta": song_meta(header, f"defmon subtune {subtune}"),
        "clock": {
            "cycles_per_tick": int(player.cycles_per_frame),
            "clock_hz": NTSC_HZ if (header.flags >> 2) & 3 == 2 else PAL_HZ,
        },
        "voices": 3,
        "tables": {name: rep.span(addr, n) for name, addr, n in TABLES},
        "playroutine": playroutine,
        "records": {
            "vinit": vinit,
            "ginit": {0: global_init(rep)},
            "sidtab": sidtab_records(rep, seeds),
        },
        "tracks": tracks,
        "phrases": phrases,
    }
    ticks = window(ir)
    if rep.period(ticks) != rep.cycle:
        raise LiftError(UNREPEATED)
    ir = unheard_init(ir, rep.calls.grid(ticks))
    return convert(ir) if expressions else ir
