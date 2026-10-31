"""Music Assembler's stateful sweeps as 6502 modulators, each from the matching spec proc.

State: freq ``dl``, ``sc``, ``ph`` (arpeggio delay, substep, phase), ``wl``, ``wh`` (its word),
``as``, ``ak``, ``vb`` (its step, steps a phase, whether a vibrato runs); pw ``d``, ``q`` (ping-pong
direction, substep); cut ``acc``, ``ctr``, ``uu``, ``cc``, ``st``, ``own``, ``ini``.
"""

from unitracker_grammar import code

STATE = {
    "freq": (
        ("dl", 1),
        ("sc", 1),
        ("ph", 1),
        ("wl", 1),
        ("wh", 1),
        ("as", 1),
        ("ak", 1),
        ("vb", 1),
    ),
    "pw": (("d", 1), ("q", 1)),
    "cut": (("acc", 1), ("ctr", 1), ("uu", 1), ("cc", 1), ("st", 1), ("own", 1), ("ini", 1)),
}

_NOTE = "ldy ne\nlda F.lo,y\nsta wl\nlda F.hi,y\nsta wh\n"
_ARP = (
    "run:\nlda wl\nsta v\nlda wh\nsta v+1\nlda vb\nbne done\ndec dl\nbpl done\ninc dl\n"
    "lda ph\nand #3\ntax\nlda #m\nshift:\ndex\nbmi got\nlsr\njmp shift\ngot:\nand #1\nbne up\n"
    "sec\nlda v\nsbc as\nsta v\nbcs count\ndec v+1\nbne count\nup:\nclc\nlda v\nadc as\n"
    "sta v\nbcc count\ninc v+1\ncount:\nlda v\nsta wl\nlda v+1\nsta wh\n"
    "inc sc\nlda sc\ncmp ak\nbne done\nlda #0\nsta sc\n"
    "inc ph\ndone:\nrts\n"
)
_PONG = (
    "step:\nlda d\nbne up\nsec\nlda v\nsbc #s\nsta v\nlda v+1\nsbc #0\nsta v+1\njmp count\n"
    "up:\nclc\nlda v\nadc #s\nsta v\nlda v+1\nadc #0\nsta v+1\n"
    "count:\ninc q\nlda q\ncmp #k\nbne done\nlda #0\nsta q\nlda d\neor #1\nsta d\ndone:\nrts\n"
)
_STEP = (
    "lda ctr\nbeq done\ndec ctr\nclc\nlda acc\nadc st\nsta acc\nlda v\nand #7\ntax\nlda #0\n"
    "sta v+1\nlda acc\nasl\nrol v+1\nasl\nrol v+1\nasl\nrol v+1\nsta v\ntxa\nora v\nsta v\n"
    "done:\nrts\n"
)

CODE = {
    "arp": "lda phi\nora phi+1\nbne run\n"
    + _NOTE
    + "lda #w\nsta dl\nlda #0\nsta sc\nsta ph\nlda wl\nsta v\nlda wh\nsta v+1\nrts\n"
    + _ARP,
    "arpl": "lda phi\nora phi+1\nbne run\n" + _NOTE + "lda #0\nsta sc\nsta ph\n" + _ARP,
    "arps": _ARP,
    "arpz": (
        "lda phi\nora phi+1\nbne run\nlda #b\nbne fixed\nlda ne\nclc\nadc #p\nand #127\n"
        "tay\njmp load\nfixed:\nldy #p\n"
        "load:\n" + _NOTE.replace("ldy ne\n", "") + _ARP
    ),
    "arpc": "lda #s\nsta as\nlda #k\nsta ak\nlda #b\nsta vb\nrts\n",
    "pong": "lda phi\nora phi+1\nbne step\nlda #p\nsta v\nsta v+1\nlda #0\nsta d\nsta q\nrts\n"
    + _PONG,
    "pongs": _PONG,
    "fset": (
        "lda #u\nsta uu\nlda #s\nsta st\nlda #o\nsta own\nlda #0\nsta ini\n"
        "lda #c\nbeq done\nsta cc\ndone:\nrts\n"
    ),
    "fseti": (
        "lda #u\nsta uu\nlda #s\nsta st\nlda #o\nsta own\nlda #c\nsta cc\nlda #a\nsta acc\n"
        "lda #r\nsta ctr\nlda #i\nsta ini\nrts\n"
    ),
    "fsweep": (
        "lda #t\ncmp own\nbne done\nlda ini\nbne first\nlda phi\nora phi+1\nbne step\n"
        "lda uu\nsta acc\nlda cc\nsta ctr\nrts\n"
        "first:\nlda #0\nsta ini\nstep:\n" + _STEP
    ),
    "poke": "lda #u\nsta acc\nrts\n",
}
ROUTINES = {name: code.read(text) for name, text in CODE.items()}
