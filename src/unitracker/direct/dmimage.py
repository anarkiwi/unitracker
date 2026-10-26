"""defMON replay images: locating a replay build's code wherever it lies, and reading sidTAB
rows."""

import itertools

from pysidtracker import CodePattern, find_code_all
from pysidtracker.mos6502 import OP_LEN, OPCODES


class LiftError(ValueError):
    """The tune uses something its replay build or the lifter does not cover."""


# The $1000 build's replay code by region, a part code or each build's variant of it (see locate).
REGIONS = (
    (
        0x1022,
        "SID band",
        "A2 ?? A9 ?? 8E 02 D4 8D 03 D4 A2 ?? A9 ?? 8E 00 D4 8D 01 D4 A2 ?? A0 ?? A9 ?? 49|09 ??"
        " 8E 06 D4 8C 05 D4 8D 04 D4",
    ),
    (
        0x10A9,
        "ACID step and arranger",
        "A9 ?? 8D 17 D4 A9 ?? 09 0F 8D 18 D4 A9 ?? 18 ?? ?? 8D B6 10 A9 ?? ?? ??",
        {
            "": "10 03 AD CE 10 8D BE 10 69 ?? 30 04 C9 ?? B0 03 AD CE 10",
            "direct": "10 02 A9 ?? 8D BE 10 69 ?? 10 03 A9 ??",
        },
        {"": "??", "uncut": ""},
        "8D 16 D4 A9 ?? 10 ?? 29 0F 8D D9 10 8D 4A 11 8D D2 11 8D 5A 12 A0 ?? BE 00 1B 10",
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
        "A0 01 A9 ??",
        {"": "10 0A", "fixed": "10 09"},
        "B9 ?? ?? 8D EF 12 8E E0 12 C8 A9 ??",
        {"": "10 0A", "fixed": "10 09"},
        "B9 ?? ?? 8D 82 13 8E 73 13 C8 A9 ??",
        {"": "10 13", "fixed": "10 12"},
        "B9 ?? ?? 8D ED 12 8D 7F 13 8E 19 10 8E 1A 10 8E 1B 10 C8 A2 ?? 10 05 8E D9 10 30 12 98"
        " 18 6D 86 11 8D 86 11 90 03 EE 87 11 A9 0F 8F 4A 11",
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
UNPLACED = {"direct"}  # variants whose own bytes are not the $1000 build's addresses
UNRELOCATED_OPS = {0x20, 0x4C, 0x6C}
FIXED_MODES = {"imm", "rel", "impl", "acc"}
SID_PAGE = 0xD4


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
        layout = zip(itertools.accumulate([0, *canon]), itertools.accumulate([0, *sizes]), sizes)
        placed = zip(layout, combo)
        yield (
            {name for name, _ in combo if name},
            " ".join(c for _, c in combo),
            [part for part, (name, _) in placed if name not in UNPLACED],
        )


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
