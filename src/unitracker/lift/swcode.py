"""SID-Wizard's frequency machinery as 6502 modulators of the ``freq`` cell, one per procedure of
``sidwizard.play`` that moves ``freqlo``/``freqhi``. ``un`` and ``out`` take the detune and carry
the waveform table adds to the written register off and on again around the other slots."""

from unitracker_grammar import code as asm

STATE = tuple((name, 1) for name in "ml mh vd vf vk sv tl cw pl ph fb".split())
CTRL_STATE = (("pg", 1),)
CTRL = {
    "wv": "lda #w\nand pg\nsta v\nrts\n",
    "goff": "lda #254\nsta pg\nlda v\nand #254\nsta v\nrts\n",
    "gon": "lda #255\nsta pg\nlda v\nora #1\nsta v\nrts\n",
    "arm": "lda #255\nsta pg\nrts\n",
}
SUB = "sb:\nlda v\nsec\nsbc ml\nsta v\nlda v+1\nsbc mh\nsta v+1\nrts\n"
ADD = "ad:\nlda v\nclc\nadc ml\nsta v\nlda v+1\nadc mh\nsta v+1\nrts\n"
LOAD = "ldy nn\nlda F.lo,y\nsta v\nlda F.hi,y\nsta v+1\n"
FIX = "sec\nsbc v+1\nclc\nadc ph\nsta ph\n"
VIBRATO = "lda #t\nsta sv\nlda #f\nsta vf\nlda #k\nsta vk\n"
STEP = (
    "lda #q\nbeq zero\nlda ne\nsec\nadc #q\ncmp #END1\nbcc ok\nlda #END\nok:\ntay\n"
    "lda V.lo,y\nsta ml\nlda V.hi,y\nsta mh\nrts\nzero:\nlda #0\nsta ml\nsta mh\nrts\n"
)
TICK = (
    "lda sv\nbne nz\nlda ml\nclc\nadc vd\nsta ml\nbcc dv\ninc mh\n"
    "dv:\nlda vk\nbne c\nlda vf\nc:\nsec\nsbc #1\nsta vk\nasl\ncmp vf\nbcc ad\n"
    + SUB
    + ADD
    + "nz:\nbmi hi\nlda vd\nbmi dv\ndec vd\nrts\n"
    "hi:\ncmp #130\nbeq sb\nbcc ad\n"
    "ldy ne\nsec\nlda F.lo,y\nsbc v\nsta tl\nlda F.hi,y\nsbc v+1\nbcc dn\n"
    "tax\nlda tl\nsec\nsbc ml\ntxa\nsbc mh\nbcc snap\n"
    "lda v\nsec\nadc ml\nsta v\nlda v+1\nadc mh\nsta v+1\nrts\n"
    "snap:\nlda F.lo,y\nsta v\nlda F.hi,y\nsta v+1\nrts\n"
    "dn:\ntax\nlda tl\nclc\nadc ml\ntxa\nadc mh\nbcs snap\n"
    "lda v\nclc\nsbc ml\nsta v\nlda v+1\nsbc mh\nsta v+1\nrts\n"
)


def routines(exp_end: int) -> dict:
    """The templates by name, given the last index of the vibrato step table ``V``."""
    step = STEP.replace("#END1", f"#{exp_end + 1}").replace("#END", f"#{exp_end}")
    return {
        "un": "lda v\nsec\nsbc pl\nsta v\nlda v+1\nsbc ph\nsta v+1\nrts\n",
        "out": "lda cw\nsta pl\nlda #0\nsta ph\nsta cw\nlda v\nclc\nadc pl\nsta v\nlda v+1\nadc ph\n"
        "sta v+1\nrts\n",
        "ld": LOAD + "lda #c\nsta cw\nrts\n",
        "ldr": LOAD + "sec\nlda ne\nadc #p\nlda #0\nrol\nsta cw\nrts\n",
        "kp": "lda #1\nsta cw\nrts\n",
        "tick": TICK,
        "vset": "lda #d\nsta vd\n" + VIBRATO + step,
        "vibr": VIBRATO + step,
        "sld": "lda #t\nsta sv\n" + step,
        "tone": "lda #131\nsta sv\nrts\n",
        "leg": "lda #131\nsta sv\nlda #127\nsta mh\nrts\n",
        "fix": "ldy ne\nlda F.hi,y\n" + FIX + "lda F.hi,y\nsta v+1\nrts\n",
        "fixb": "lda fb\n" + FIX + "lda fb\nsta v+1\nrts\n",
        "seed": "lda #h\nsta fb\nrts\n",
    }


def assembled(exp_end: int) -> dict:
    """The routines read as code."""
    return {name: asm.read(text) for name, text in (routines(exp_end) | CTRL).items()}
