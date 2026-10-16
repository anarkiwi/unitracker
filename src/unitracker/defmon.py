"""Lift a defMON replay (.sid) to a unitracker song.

The $1000 replay build keeps its state in self-modified operands; init runs on
pydefmon's DefmonPlayer and tables and state are read from that memory. Song timing
comes from simulating the replay's arranger and pattern counters.
"""

from importlib import resources

import yaml
from pysidtracker import SidImage

from unitracker.songio import song_meta

PAL_HZ = 985248
NTSC_HZ = 1022727
VOICE_OFFSETS = (0x00, 0x31, 0x62)
MAX_STEP_TICKS = 1 << 14
MAX_SONG_STEPS = 4096

# Replay code that must be present byte-for-byte; ?? are self-modified operands.
ANCHORS = (
    (0x1003, "4C 22 10"),
    (
        0x1022,
        "A2 ?? A9 ?? 8E 02 D4 8D 03 D4 A2 ?? A9 ?? 8E 00 D4 8D 01 D4 A2 ?? A0 ?? A9 ?? 49 ??"
        " 8E 06 D4 8C 05 D4 8D 04 D4",
    ),
    (
        0x10A9,
        "A9 ?? 8D 17 D4 A9 ?? 09 0F 8D 18 D4 A9 ?? 18 ?? ?? 8D B6 10 A9 ?? ?? ?? 10 03 AD CE 10"
        " 8D BE 10 69 ?? 30 04 C9 ?? B0 03 AD CE 10 ?? 8D 16 D4 A9 ?? 10 6B",
    ),
    (
        0x10EA,
        "A0 ?? BE 00 1B 10 28 B9 00 1D F0 1C CC EC 14 F0 09 8C EC 14 8D ED 14 4C 12 11 CE ED"
        " 14 D0 09 A9 00 8D EC 14 C8 4C 16 11 B9 00 1C A8 BE 00 1B BD 00 1A 8D 86 11 BD 80 1A"
        " 8D 87 11 BE 00 1C BD 00 1A 8D 0E 12 BD 80 1A 8D 0F 12 BE 00 1D BD 00 1A 8D 96 12 BD"
        " 80 1A 8D 97 12 C8 8C EB 10",
    ),
    (
        0x117F,
        "A0 01 A9 ?? 10 0A B9 ?? ?? 8D EF 12 8E E0 12 C8 A9 ?? 10 0A B9 ?? ?? 8D 82 13 8E 73 13"
        " C8 A9 ?? 10 13 B9 ?? ?? 8D ED 12 8D 7F 13 8E 19 10 8E 1A 10 8E 1B 10 C8 A2 ?? 10 05"
        " 8E D9 10 30 12 98 18 6D 86 11 8D 86 11 90 03 EE 87 11 A9 0F 8F 4A 11",
    ),
    (
        0x12DF,
        "A9 ?? F0 0B 30 2B CE E0 12 4C 10 13 ?? ?? ?? A0 ?? B9 00 19 D0 07 B9 00 18 A8 B9 00 19"
        " 85 FC B9 00 1E 8D E0 12 B9 00 18 C8 8C EF 12 A2 00 20 B0 16",
    ),
    (
        0x1405,
        "A2 62 BD 1B 10 F0 47 10 5B 0A A8 10 15 BD 19 10 F9 F8 14 9D 19 10 BD 1A 10 F9 94 15 9D"
        " 1A 10 4C 3A 14 B9 78 15 18 7D 19 10 9D 19 10 B9 14 16 7D 1A 10 9D 1A 10 18 BC 7F 13"
        " BD 19 10 79 9C 15 9D 2D 10 B9 38 16 7D 1A 10 9D 2F 10 4C 95 14 BC 7F 13 B9 9C 15 18"
        " 7D 1F 10 9D 2D 10 B9 38 16 9D 2F 10 4C 95 14 18 7D 7F 13 A8 B9 84 15 38 F9 83 15 8D"
        " 89 14 B9 20 16 F9 1F 16 8D 91 14 BC 7F 13 B9 9C 15 18 69 ?? 9D 2D 10 B9 38 16 69 ??"
        " 9D 2F 10 BC 1E 10 F0 49 10 34 98 0B 7F 7D 23 10 9D 23 10 90 3C BD 25 10 C9 0F F0 06"
        " FE 25 10 4C E3 14 A9 F8 9D 23 10 98 49 80 9D 1E 10 4C E3 14 A9 01 9D 23 10 98 49 80"
        " 9D 1E 10 4C E3 14 BD 23 10 FD 1E 10 9D 23 10 B0 08 BD 25 10 F0 E2 DE 25 10 8A CB 31"
        " 30 03 4C 07 14 60",
    ),
    (
        0x16B0,
        "85 FB A0 00 B1 FB F0 5A 0A 85 96 10 06 C8 B1 FB 9D 3B 10 90 06 C8 B1 FB 9D 3D 10 24 96"
        " 50 06 C8 B1 FB 9D 39 10 A5 96 29 20 F0 06 C8 B1 FB 9D 37 10 A5 96 29 10 F0 0E C8 B1"
        " FB 30 04 18 7D ED 12 29 7F 9D 7F 13 A5 96 29 08 F0 06 C8 B1 FB 9D 1B 10 A5 96 29 04"
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
GATE_A, GATE_B, GATE_N, ALT = 0x40, 0x20, 0x10, 0x80

# Voice state operands at base + voice offset: (cell, lo address, hi address or None).
VOICE_STATE = (
    ("pw", 0x1023, 0x1025),
    ("flo", 0x102D, None),
    ("fhi", 0x102F, None),
    ("sr", 0x1037, None),
    ("ad", 0x1039, None),
    ("ctrl", 0x103B, None),
    ("eor", 0x103D, None),
    ("sacc", 0x1019, 0x101A),
    ("mode", 0x101B, None),
    ("ps", 0x101E, None),
    ("note", 0x137F, None),
    ("tbase", 0x12ED, None),
    ("rowA", 0x12EF, None),
    ("cntA", 0x12E0, None),
    ("rowB", 0x1382, None),
    ("cntB", 0x1373, None),
)


class LiftError(ValueError):
    """The tune is not a replay build the defMON playroutine covers."""


def load_playroutine() -> dict:
    """The defMON playroutine definition."""
    text = resources.files("unitracker").joinpath("playroutines/defmon.yaml").read_text()
    return yaml.safe_load(text)


def check_build(mem) -> None:
    """Raise unless every anchor of the $1000 replay build is in ``mem``."""
    for addr, pattern in ANCHORS:
        for i, tok in enumerate(pattern.split()):
            if tok != "??" and mem[addr + i] != int(tok, 16):
                raise LiftError(
                    f"not the $1000 defMON replay build (code differs at ${addr + i:04X})"
                )


def _word(mem, lo, hi):
    return mem[lo] | (mem[hi] << 8)


def voice_init(mem) -> dict:
    """Each voice's state as init leaves it, as ``vinit`` records."""
    out = {}
    for v, off in enumerate(VOICE_OFFSETS):
        if (mem[0x101F + off], mem[0x1020 + off], mem[0x1021 + off]) != (
            v,
            1 << v,
            0xFF ^ (1 << v),
        ):
            raise LiftError(f"voice {v} detune/routing constants are not the standard ones")
        rec = {}
        for cell, lo, hi in VOICE_STATE:
            rec[cell] = mem[lo + off] if hi is None else _word(mem, lo + off, hi + off)
        out[v] = rec
    return out


def global_init(mem) -> dict:
    """The filter, volume and ACID state as init leaves it, as the ``ginit`` record."""
    ops = {mem[0x10B8], mem[0x10BF]}
    if len(ops) != 1 or not ops <= {0xE9, 0x69}:
        raise LiftError("ACID opcodes are not a matching SBC/ADC pair")
    return {
        "res": mem[0x10AA],
        "fv": mem[0x10AF],
        "cp": mem[0x10CA],
        "acc": _word(mem, 0x10B6, 0x10BE),
        "astep": _word(mem, 0x10B9, 0x10C0),
        "sub": int(mem[0x10B8] == 0xE9),
    }


def model_constants(mem) -> dict:
    """The SID-model constants init's $D41B probe patches into the cutoff output."""
    if mem[0x10D4] not in (0xEA, 0x0A):
        raise LiftError("cutoff output opcode is neither NOP nor ASL")
    return {"floor": mem[0x10CE], "dbl": int(mem[0x10D4] == 0x0A)}


def resolve_row(mem, row):
    """The row a sidcall fetch of ``row`` reads after the JP redirect."""
    return row if mem[SIDTAB_HI + row] else mem[SIDTAB_LO + row]


def _set(acts, cell, value):
    if acts and "set" in acts[-1] and cell not in acts[-1]["set"]:
        acts[-1]["set"][cell] = value
    else:
        acts.append({"set": {cell: value}})


def _re(v):
    if v == 0:
        return {"res": "res & rmask"}
    return {"res": f"(res & 0x0F) | {v} | rbit" if v & 0x08 else v}


def _acid(lo, hi):
    if not hi & 0x80:
        return {"acc": (hi << 8) | lo, "astep": 0}
    if hi & 0x40:
        return {"astep": ((hi & 0x3F) << 8) | lo, "sub": 1}
    return {"astep": ((hi & 0x7F) << 8) | lo, "sub": 0}


# sidTAB columns in the walk's read order: (bitmap, bit, value bytes, cells set).
COLUMNS = (
    (0, 0x40, 1, lambda v: {"ctrl": v}),
    (0, 0x80, 1, lambda v: {"eor": v}),
    (0, 0x20, 1, lambda v: {"ad": v}),
    (0, 0x10, 1, lambda v: {"sr": v}),
    (0, 0x08, 1, lambda v: {"note": v & 0x7F if v & 0x80 else f"(tbase + {v}) & 0x7F"}),
    (0, 0x04, 1, lambda v: {"mode": v}),
    (0, 0x02, 1, lambda v: {"pw": (v << 8) | (v & 0xF0)}),
    (1, 0x80, 1, lambda v: {"ps": v}),
    (1, 0x40, 1, _re),
    (1, 0x20, 1, lambda v: {"fv": v}),
    (1, 0x10, 1, lambda v: {"cp": v}),
    (1, 0x08, 2, _acid),
)


def row_actions(mem, addr) -> list:
    """The sidTAB bitmap walk of the row body at ``addr`` ($16B0), as actions."""
    pos, acts, bitmap, half = addr, [], mem[addr], 0
    for col_half, bit, size, cells in COLUMNS:
        if col_half != half:
            pos += 1
            bitmap, half = mem[pos], col_half
        if bitmap & bit:
            values = mem[pos + 1 : pos + 1 + size]
            pos += size
            for cell, value in cells(*values).items():
                _set(acts, cell, value)
    return acts


def sidtab_records(mem, seeds) -> dict:
    """Records for every sidTAB row a cascade can fetch from ``seeds``."""
    out, todo = {}, sorted(set(seeds))
    while todo:
        row = todo.pop()
        if row in out:
            continue
        eff = resolve_row(mem, row)
        if not mem[SIDTAB_HI + eff]:
            raise LiftError(f"sidTAB row {row} resolves to row {eff}, which has no body")
        dl = mem[SIDTAB_DL + eff]
        rec = {"dl": dl, "next": (eff + 1) & 0xFF}
        acts = row_actions(mem, _word(mem, SIDTAB_LO + eff, SIDTAB_HI + eff))
        if acts:
            rec["do"] = acts
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


def arranger_step(mem, y, jr, jc):
    """The replay's arranger at $10EA: ``(pattern numbers, next y, jr, jc)``."""
    if mem[ARRANGER[0] + y] & 0x80:
        count = mem[ARRANGER[2] + y]
        if count == 0:
            y = mem[ARRANGER[1] + y]
        elif y == jr:
            jc = (jc - 1) & 0xFF
            if jc:
                y = mem[ARRANGER[1] + y]
            else:
                jr, y = 0, (y + 1) & 0xFF
        else:
            jr, jc, y = y, count, mem[ARRANGER[1] + y]
    pats = tuple(mem[base + y] for base in ARRANGER)
    return pats, (y + 1) & 0xFF, jr, jc


def simulate_step(mem, pats, e):
    """One song step: per voice ``[(tick, flag, a, b, n)]`` applied, step length, next e."""
    ptrs = [_word(mem, PATTERN_LO + x, PATTERN_HI + x) for x in pats]
    cnt = [e] * 3
    applied = [[], [], []]
    for tick in range(MAX_STEP_TICKS):
        alt = None
        for v in range(3):
            if cnt[v] & 0x80:
                flag, a, b, n, size = decode_event(mem, ptrs[v])
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


def song_steps(mem, subtune):
    """The song steps the arranger plays from init, and the step index the song loops to."""
    if not mem[0x10D9] & 0x80:
        raise LiftError("init did not arm the arranger")
    y, jr, jc, e = subtune & 0xFF, mem[0x14EC], mem[0x14ED], mem[0x10D9] & 0x0F
    seen, steps = {}, []
    while (y, jr, jc, e) not in seen:
        if len(steps) >= MAX_SONG_STEPS:
            raise LiftError("arranger does not loop")
        seen[(y, jr, jc, e)] = len(steps)
        pats, y, jr, jc = arranger_step(mem, y, jr, jc)
        applied, length, e = simulate_step(mem, pats, e)
        steps.append((applied, length))
    return steps, seen[(y, jr, jc, e)]


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


def structure(mem, subtune):
    """Tracks, phrases, and the sidTAB rows the patterns arm."""
    steps, loop = song_steps(mem, subtune)
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


def _seeds(vinit, seeds) -> set:
    """Pattern-armed rows plus each voice's initial rows whose counters run."""
    for rec in vinit.values():
        seeds.update(rec[r] for r, c in (("rowA", "cntA"), ("rowB", "cntB")) if rec[c] < 0x80)
    return seeds


def lift(raw: bytes, subtune=None) -> dict:
    """A unitracker song dict for a defMON replay ``.sid``."""
    from pydefmon import DefmonPlayer  # pylint: disable=import-outside-toplevel

    header = SidImage.from_bytes(raw).header
    if subtune is None:
        subtune = (header.start_song or 1) - 1
    player = DefmonPlayer(raw, subtune=subtune)
    mem = player._mem  # pylint: disable=protected-access
    check_build(mem)
    vinit = voice_init(mem)
    tracks, phrases, seeds = structure(mem, subtune)
    playroutine = load_playroutine()
    playroutine["constants"] = model_constants(mem)
    return {
        "unitracker": 0,
        "meta": song_meta(header, f"defmon subtune {subtune}"),
        "clock": {
            "cycles_per_tick": int(player.cycles_per_frame),
            "clock_hz": NTSC_HZ if (header.flags >> 2) & 3 == 2 else PAL_HZ,
        },
        "voices": 3,
        "registers": [int(r) for r in player.regs],
        "tables": {name: list(mem[addr : addr + n]) for name, addr, n in TABLES},
        "playroutine": playroutine,
        "records": {
            "vinit": vinit,
            "ginit": {0: global_init(mem)},
            "sidtab": sidtab_records(mem, _seeds(vinit, seeds)),
        },
        "tracks": tracks,
        "phrases": phrases,
    }
