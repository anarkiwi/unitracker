"""Small hand-written documents for the optimiser's tests."""

HEAD = "unitracker: 2.1\nmeta: {title: T, author: A}\n"
MODS = "mod:\n  note(): F(N)\n  vib(d): v + d*(phi % 4)\n  wob(e): v + e*(phi % 4)\n"


TOP = ("restart", "ins", "ctrl", "pw", "filter", "phrase")


def doc(defs: str, play: str = "[p1]", head: str = "voices: 1\n", mods: str = MODS) -> str:
    """A one-row ending document of ``defs`` (sections at 6 spaces, records at 8) playing
    ``play``: each section top-level, after the templates, in the grammar's order."""
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
    return f"{HEAD}{head}{mods}{top}song:\n  - play: {play}\n  - end\n"


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
    mods="mod:\n  vib(d): v + d*(phi % 4)\n",
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
    "mod:\n  note(): F(N)\n  vib(d): v + d*(phi % 4)\n  wob(e): v + e*(phi % 4)\n"
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

# code modulators that are exactly expressions: a 16-bit slide, a note on φ 0 kept after, a
# square-wave vibrato (two calls) on a second slot and a pulse width sweep
EXACT_CODE = (
    "mod:\n"
    "  slide(c): |\n    clc\n    lda v\n    adc #<c\n    sta v\n    lda v+1\n    adc #>c\n"
    "    sta v+1\n    rts\n"
    "  start(): |\n    lda phi\n    ora phi+1\n    bne done\n    sta s\n    ldy nn\n"
    "    lda F.lo,y\n    sta v\n    lda F.hi,y\n    sta v+1\n    done:\n    rts\n"
    "  wob(c): |\n    lda phi\n    and #2\n    bne dn\n    clc\n    lda v\n    adc #c\n"
    "    sta v\n    lda v+1\n    adc #0\n    sta v+1\n    rts\n    dn:\n    sec\n    lda v\n"
    "    sbc #c\n    sta v\n    lda v+1\n    sbc #0\n    sta v+1\n    rts\n"
)
EXACT = doc(
    "      ins:\n"
    "        i1: {ad: 9, ctrl: c1, pw: slide(40), a.freq: start(), b.freq: wob(8)}\n"
    "        i2: {ad: 9, ctrl: c1, pw: slide(24), a.freq: start(), b.freq: wob(16)}\n"
    "      ctrl:\n"
    "        c1: [{ctrl: 65}, end]\n"
    "      phrase:\n"
    "        p1: [C4.i1, -*9, E4.i2, -*9, G4.i1, -*9]\n",
    head="voices: 1\nslots: {freq: {a: {}, b: {}}}\nstate: {freq: {s: 1}}\n",
    mods=EXACT_CODE,
)

# a pulse width of 2048 plus 0 to 7 of a shift register's noise
NOISY = doc(
    "      ins:\n"
    "        i1: {ad: 9, ctrl: [{ctrl: 65}, end], pw: jit()}\n"
    "      phrase:\n"
    "        p1: [C4.i1, -*30]\n",
    head="voices: 1\nstate: {pw: {r: 1}}\n",
    mods="mod:\n  jit(): |\n    lda r\n    bne go\n    lda #1\n    go:\n    asl\n    bcc nx\n"
    "    eor #29\n    nx:\n    sta r\n    and #7\n    sta v\n    lda #8\n    sta v+1\n    rts\n",
)

# put() keeps the frequency but stores the note that get() adds: put alone fits and is refused,
# while slide() on the pulse width and the cutoff is kept
COUPLED = doc(
    "      ins:\n"
    "        i1: {ad: 9, ctrl: c1, pw: slide(8), filter: f1, a.freq: put(), b.freq: get()}\n"
    "      ctrl:\n"
    "        c1: [{ctrl: 65}, end]\n"
    "      filter:\n"
    "        f1: [{cut: slide(3), res: 12, route: 1, mode: 16}, end]\n"
    "      phrase:\n"
    "        p1: [C4.i1, -*5, E4.i1, -*5]\n",
    head="voices: 1\nslots: {freq: {a: {}, b: {}}}\nstate: {freq: {s: 1}}\n",
    mods="mod:\n  put(): |\n    lda nn\n    sta s\n    rts\n"
    "  get(): |\n    clc\n    lda v\n    adc s\n    sta v\n    lda v+1\n    adc #0\n    sta v+1\n"
    "    rts\n" + EXACT_CODE[5 : EXACT_CODE.index("  start")],
)

# p2 is p1 two semitones up, p3 is p1 but for one interval
TRANSPOSE = doc(
    "      ins:\n"
    "        i1: {ad: 9, ctrl: [{ctrl: 65}, end]}\n"
    "      phrase:\n"
    "        p1: [C4.i1, -, E4, G4, -, C5, -*3]\n"
    "        p2: [D4.i1, -, F#4, A4, -, D5, -*3]\n"
    "        p3: [C4.i1, -, F4, G4, -, C5, -*3]\n",
    play="[p1]\n  - play: [p2-1]\n  - play: [p3]",
    mods="",
)

# a legato note bound to a modulator that jumps to the note: alone on its slot (dropped), after
# an instrument's binding on an earlier slot (that slot's none), not at the note (kept), and
# writing state a later binding reads (refused)
SNAP_MODS = (
    "mod:\n  nt(): (phi == 0)*F(N) + (phi > 0)*v\n  snap(): F(n)\n  up(): F(n) + 5\n"
    "  snapc(): |\n    lda ne\n    sta s\n    tay\n    lda F.lo,y\n    sta v\n    lda F.hi,y\n"
    "    sta v+1\n    rts\n"
    "  get(): |\n    clc\n    lda v\n    adc s\n    sta v\n    lda v+1\n    adc #0\n    sta v+1\n"
    "    rts\n"
)
SNAP = doc(
    "      ins:\n"
    "        i1: {ad: 9, ctrl: [{ctrl: 65}, end]}\n"
    "        i2: {ad: 9, ctrl: [{ctrl: 65}, end], wave.freq: nt()}\n"
    "      phrase:\n"
    "        p1: [C4.i1, -, E4!tick.freq:snap()!legato, -, D4.i2, -, G4!tick.freq:snap()!legato, -,"
    " D4.i2, -, E4!tick.freq:up()!legato, -*3]\n",
    head="voices: 1\nslots: {freq: {wave: {}, tick: {}}}\n",
    mods=SNAP_MODS,
)
SNAP_STATE = doc(
    "      ins:\n"
    "        i1: {ad: 9, ctrl: [{ctrl: 65}, end]}\n"
    "      phrase:\n"
    "        p1: [C4.i1, -, E4!freq:snapc()!legato, -!freq:none, -!freq:get(), -*3]\n",
    head="voices: 1\nstate: {freq: {s: 1}}\n",
    mods=SNAP_MODS,
)

# i1's none ends no binding at its notes, i2's ends the one C4's command made
INS_NONE = doc(
    "      ins:\n"
    "        i1: {ad: 9, ctrl: c1, freq: none}\n"
    "        i2: {ad: 8, ctrl: c1, freq: none}\n"
    "      ctrl:\n"
    "        c1: [{hold: 2, ctrl: 65}, {}, {ctrl: 64}, end]\n"
    "      phrase:\n"
    "        p1: [C4.i1!freq:vib(2), -, D4.i2, -*3, E4.i1, -*3]\n",
    mods="mod:\n  vib(d): v + d*(phi % 4)\n",
)

# a restart whose last row is empty: the row before it falls a tick before the note
RESTART_EMPTY = doc(
    "      ins:\n"
    "        i1: {ad: 9, ctrl: [{ctrl: 65}, end], restart: [{gate: 0, ad: 15},"
    " {hold: 2, ad: 9, sr: 240}, {}, end]}\n"
    "      phrase:\n"
    "        p1: [C4.i1, -*3, D4, -*3]\n",
    mods="",
)
