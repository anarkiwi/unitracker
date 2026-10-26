"""Music Assembler's per-tick pulse and filter sweeps as 6502 code templates.

State: freq ``dl``, ``sc``, ``ph`` the arpeggio's delay, step count and phase; ``wl``, ``wh``
the slide's word, ``tg`` its toggle; pw ``d``, ``q``
the ping-pong's direction and step count; cut ``acc``, ``ctr`` the accumulator and ticks left.
Params: ``p`` the pulse byte, ``s`` a step, ``k`` steps per phase (0 for 256), ``w`` the delay,
``i``, ``j`` the slide's first word, ``m`` the phases that ascend (bit j phase j), ``l``, ``h``
the slide's step bytes, ``u``, ``c`` the trigger's accumulator and count (``u`` also the
accumulator an inner program writes).
"""

STATE = {
    "freq": {"dl": 1, "sc": 1, "ph": 1, "wl": 1, "wh": 1, "tg": 1},
    "pw": {"d": 1, "q": 1},
    "cut": {"acc": 1, "ctr": 1},
}

_FIRST = "lda phi\nora phi+1\nbne step\n"
_TRIGGER = _FIRST + "lda #p\nsta v\nsta v+1\n"
_NOTE = "ldy nn\nlda F.lo,y\nsta v\nlda F.hi,y\nsta v+1\n"
_ARP = (
    "dec dl\nbpl done\ninc dl\n"
    + "lda ph\nand #3\ntax\nlda #m\nshift:\ndex\nbmi got\nlsr\njmp shift\ngot:\nand #1\nbne up\n"
    + "sec\nlda v\nsbc #s\nsta v\nbcs count\ndec v+1\nbne count\nup:\nclc\nlda v\nadc #s\n"
    + "sta v\nbcc count\ninc v+1\ncount:\ninc sc\nlda sc\ncmp #k\nbne done\nlda #0\nsta sc\n"
    + "inc ph\ndone:\nrts\n"
)
_PULSE = "clc\nlda v\nadc #s\nsta v\nlda v+1\nadc #s\nsta v+1\nrts\n"
_PONG = (
    "lda d\nbne up\nsec\nlda v\nsbc #s\nsta v\nlda v+1\n"
    + "sbc #0\nsta v+1\njmp count\nup:\nclc\nlda v\nadc #s\nsta v\nlda v+1\nadc #0\nsta v+1\n"
    + "count:\ninc q\nlda q\ncmp #k\nbne done\nlda #0\nsta q\nlda d\neor #1\nsta d\ndone:\nrts\n"
)

CODE = {
    # From the sounding note's frequency, after w calls, each phase k steps of the word down (a
    # borrow to 0 then up) or up by s.
    "vib": "lda phi\nora phi+1\nbne run\n"
    + _NOTE
    + "lda #w\nsta dl\nlda #0\nsta sc\nsta ph\nrun:\n"
    + _ARP,
    # A legato note's: from its frequency at phase 0, the delay left running.
    "vibl": "lda phi\nora phi+1\nbne run\n" + _NOTE + "lda #0\nsta sc\nsta ph\nrun:\n" + _ARP,
    # Another instrument's step and speed, the arpeggio running on.
    "vibs": _ARP,
    # The event note's frequency, then a 16-bit word stepped by h:l a tick; with l odd every
    # other tick sounds the sounding note's frequency instead (tg the toggle).
    "vslide": "lda phi\nora phi+1\nbne run\nldy ne\nlda F.lo,y\nsta wl\nlda F.hi,y\nsta wh\n"
    + "lda #0\nsta tg\nrun:\nlda #l\nand #1\nbeq step\nlda tg\neor #1\nsta tg\nbne step\n"
    + _NOTE
    + "rts\nstep:\nclc\nlda wl\nadc #l\nsta wl\nsta v\nlda wh\nadc #h\nsta wh\nsta v+1\nrts\n",
    # The trigger's width, then $40: lo += s and hi += s with the carry, the word + 257 s.
    "pulse": _TRIGGER + "rts\nstep:\n" + _PULSE,
    "pstep": _PULSE,
    # The trigger's width, then $20: the word - s, or + s, turning every k steps.
    "pong": _TRIGGER + "lda #0\nsta d\nsta q\nrts\nstep:\n" + _PONG,
    "pongs": _PONG,
    # The owner's trigger loads acc and ctr; while ctr lasts each tick steps acc into FC_HI.
    "csweep": _FIRST
    + "lda #u\nsta acc\nlda #c\nsta ctr\nrts\nstep:\nlda ctr\nbeq done\ndec ctr\nclc\nlda acc\n"
    + "adc #s\nsta acc\nlda #0\nsta v+1\nlda acc\nasl\nrol v+1\nasl\nrol v+1\nasl\nrol v+1\n"
    + "sta v\ndone:\nrts\n",
    # The slide of an image: from the word i, j init left, stepped by h:l (l even) a tick.
    "vslidew": "lda phi\nora phi+1\nbne step\nlda #i\nsta wl\nlda #j\nsta wh\nstep:\nclc\nlda wl\n"
    + "adc #l\nsta wl\nsta v\nlda wh\nadc #h\nsta wh\nsta v+1\nrts\n",
    # The sweep taken from its owner, still until the new owner's trigger.
    "idle": "rts\n",
    # An inner program's filter byte: the sweep's accumulator, which the sweep steps from.
    "poke": "lda #u\nsta acc\nrts\n",
}
