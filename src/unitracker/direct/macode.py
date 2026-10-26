"""Music Assembler's per-tick pulse and filter sweeps as 6502 code templates.

State: freq ``dl``, ``sc``, ``ph`` the arpeggio's delay, step count and phase; pw ``d``, ``q``
the ping-pong's direction and step count; cut ``acc``, ``ctr`` the accumulator and ticks left.
Params: ``p`` the pulse byte, ``s`` a step, ``k`` steps per phase (0 for 256), ``w`` the delay,
``m`` the phases that ascend (bit j phase j), ``u``, ``c`` the trigger's accumulator and count
(``u`` also the accumulator an inner program writes).
"""

STATE = {"freq": {"dl": 1, "sc": 1, "ph": 1}, "pw": {"d": 1, "q": 1}, "cut": {"acc": 1, "ctr": 1}}

_FIRST = "lda phi\nora phi+1\nbne step\n"
_TRIGGER = _FIRST + "lda #p\nsta v\nsta v+1\n"

CODE = {
    # After w calls, each phase k steps of the word down (a borrow to 0 then up) or up by s.
    "vib": "lda phi\nora phi+1\nbne run\nlda #w\nsta dl\nlda #0\nsta sc\nsta ph\nrun:\n"
    + "dec dl\nbpl done\ninc dl\n"
    + "lda ph\nand #3\ntax\nlda #m\nshift:\ndex\nbmi got\nlsr\njmp shift\ngot:\nand #1\nbne up\n"
    + "sec\nlda v\nsbc #s\nsta v\nbcs count\ndec v+1\nbne count\nup:\nclc\nlda v\nadc #s\n"
    + "sta v\nbcc count\ninc v+1\ncount:\ninc sc\nlda sc\ncmp #k\nbne done\nlda #0\nsta sc\n"
    + "inc ph\ndone:\nrts\n",
    # The trigger's width, then $40: lo += s and hi += s with the carry, the word + 257 s.
    "pulse": _TRIGGER + "rts\nstep:\nclc\nlda v\nadc #s\nsta v\nlda v+1\nadc #s\nsta v+1\nrts\n",
    # The trigger's width, then $20: the word - s, or + s, turning every k steps.
    "pong": _TRIGGER
    + "lda #0\nsta d\nsta q\nrts\nstep:\nlda d\nbne up\nsec\nlda v\nsbc #s\nsta v\nlda v+1\n"
    + "sbc #0\nsta v+1\njmp count\nup:\nclc\nlda v\nadc #s\nsta v\nlda v+1\nadc #0\nsta v+1\n"
    + "count:\ninc q\nlda q\ncmp #k\nbne done\nlda #0\nsta q\nlda d\neor #1\nsta d\ndone:\nrts\n",
    # The owner's trigger loads acc and ctr; while ctr lasts each tick steps acc into FC_HI.
    "csweep": _FIRST
    + "lda #u\nsta acc\nlda #c\nsta ctr\nrts\nstep:\nlda ctr\nbeq done\ndec ctr\nclc\nlda acc\n"
    + "adc #s\nsta acc\nlda #0\nsta v+1\nlda acc\nasl\nrol v+1\nasl\nrol v+1\nasl\nrol v+1\n"
    + "sta v\ndone:\nrts\n",
    # An inner program's filter byte: the sweep's accumulator, which the sweep steps from.
    "poke": "lda #u\nsta acc\nrts\n",
}
