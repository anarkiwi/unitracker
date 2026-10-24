"""Small hand-written documents for the optimiser's tests."""

HEAD = "unitracker: 2.0\nmeta: {title: T, author: A}\n"
MODS = "mod:\n  note(): F(N)\n  vib(d): v + d*(φ % 4)\n  wob(e): v + e*(φ % 4)\n"


TOP = ("restart", "ins", "ctrl", "pw", "filter")


def doc(defs: str, play: str = "[p1]", head: str = "voices: 1\n", mods: str = MODS) -> str:
    """A one-row ending document of ``defs`` (sections as a row's ``def`` writes them) playing
    ``play``: each section but ``phrase`` top-level, after the templates, in the grammar's order."""
    sections, kind = {}, None
    for line in defs.splitlines(keepends=True):
        if not line.startswith("        "):
            kind = line.strip()[:-1]
            sections[kind] = ""
        else:
            sections[kind] += line
    top = "".join(
        f"{k}:\n" + "".join(l[6:] for l in sections[k].splitlines(keepends=True))
        for k in TOP
        if k in sections
    )
    row = f"  - def:\n      phrase:\n{sections['phrase']}"
    return f"{HEAD}{head}{mods}{top}song:\n{row}    play: {play}\n  - end\n"


DEFAULTS = doc(
    "      ins:\n"
    "        i1: {ad: 9, sr: 240, ctrl: c1, pw: 2048, tick.freq: none}\n"
    "      ctrl:\n"
    "        c1: [{wait: 0, ctrl: 65, note: +0}, {hold: 1, ctrl: 64}, end]\n"
    "      phrase:\n"
    "        p1: [C4.i1, -!legato!pw!filter*1, D4, -*3]\n",
    head="voices: 1\ntempo: 6\nslots: {freq: {base: {}, tick: {}}}\n",
    mods="",
)

DEFAULTS_BOUND = doc(
    "      ins:\n"
    "        i1: {ad: 9, ctrl: c1, tick.freq: none}\n"
    "      ctrl:\n"
    "        c1: [{ctrl: 65}, end]\n"
    "      phrase:\n"
    "        p1: [C4.i1!tick.freq:vib(3), -, D4, -*3]\n",
    head="voices: 1\nslots: {freq: {base: {}, tick: {}}}\n",
    mods="mod:\n  vib(d): v + d*(φ % 4)\n",
)

MERGE = doc(
    "      ins:\n"
    "        i1: {ad: 9, ctrl: c1}\n"
    "        i2: {ad: 9, ctrl: c2}\n"
    "      ctrl:\n"
    "        c1: [{ctrl: 65}, end]\n"
    "        c2: [{ctrl: 65}, end]\n"
    "      phrase:\n"
    "        p1: [C4.i1!freq:vib(2), -, D4.i2!freq:wob(2), -*3]\n"
    "        p2: [C4.i1!freq:vib(2), -, D4.i2!freq:wob(2), -*3]\n",
    play="[p1]\n  - play: [p2]",
)

MERGE_PHI = doc(
    "      ins:\n"
    "        i1: {ad: 9, ctrl: [{ctrl: 65}, end]}\n"
    "      phrase:\n"
    "        p1: [C4.i1!freq:vib(2), -!freq:wob(2), -*3]\n",
)

HOIST = doc(
    "      ins:\n"
    + "".join(f"        i{k}: {{ad: {k}, ctrl: c1, pw: 2048}}\n" for k in range(1, 6))
    + "      ctrl:\n"
    "        c1: [{ctrl: 65}, end]\n"
    "      phrase:\n"
    "        p1: [C4.i1, -, D4.i2, E4.i3, F4.i4, G4.i5, -*3]\n",
    mods="",
)

HOIST_LATE = HOIST.replace("p1: [C4.i1,", "p1: [-, C4.i1,")

LOOPING = doc(
    "      ins:\n"
    "        i1: {ad: 9, ctrl: [{ctrl: 65}, end], pw: 2048}\n"
    "        i2: {ad: 9, ctrl: [{ctrl: 65}, end], pw: 2048}\n"
    "      phrase:\n"
    "        p1: [C4.i1, -, D4.i2, -]\n",
    mods="",
).replace("  - end\n", "  - {loop: 0}\n")

CODE = (
    "mod:\n  note(): F(N)\n  vib(d): v + d*(φ % 4)\n  wob(e): v + e*(φ % 4)\n"
    "  up(c): |\n    lda v\n    clc\n    adc #c\n    sta v\n    rts\n"
)
LOOPING_CODE = doc(
    "      ins:\n"
    "        i1: {ad: 9, ctrl: [{ctrl: 65}, end], pw: up(3)}\n"
    "      phrase:\n"
    "        p1: [C4.i1, -, D4, -]\n",
    mods=CODE,
).replace("  - end\n", "  - {loop: 0}\n")

TEMPLATE = doc(
    "      ins:\n"
    "        i1: {ad: 9, sr: 240, ctrl: c1,"
    " restart: [{hold: 2, gate: 0, ad: 15}, {ad: 9, sr: 240}, end]}\n"
    "        i2: {ad: 8, sr: 120, ctrl: c2, restart: [{gate: 0, ad: 15}, {ad: 8, sr: 120}, end]}\n"
    "        i3: {ad: 7, sr: 100, ctrl: c3, restart: [{gate: 0, ad: 15}, {ad: 7, sr: 100}, end]}\n"
    "      ctrl:\n"
    "        c1: [{ctrl: 65, note: +7}, {wait: 1, note: +4}, {wait: 1, note: +0}, {loop: 0}]\n"
    "        c2: [{ctrl: 65, note: +7}, {wait: 1, note: +3}, {wait: 1, note: +0}, {loop: 0}]\n"
    "        c3: [{ctrl: 65, note: +5}, {wait: 1, note: +2}, {wait: 1, note: +0}, {loop: 0}]\n"
    "      phrase:\n"
    "        p1: [C4.i1, -*3, D4.i2, -*3, E4.i3, -*3, F4.i1!run:c2, -*3]\n",
    mods="",
)
