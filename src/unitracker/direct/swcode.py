"""SID-Wizard's per-tick frequency modulators as format 1.23 6502 code templates.

The freq cell's state is the player's: ``o`` vibrato_offset, ``s`` set once a WF row's pitch
drives the frequency, ``z`` FREQLO/FREQHI, ``dc`` VIDELCNT, ``vc`` VIBRACNT, ``p`` VIBFREQU,
``fm`` FREQMOD, ``k`` SLIDEVIB, ``dd`` DETUNER, ``cm``/``ca``/``cv`` WRPITCH's carry.
"""

STATE = {
    "freq": {
        "o": 2,
        "s": 1,
        "z": 2,
        "dc": 1,
        "vc": 1,
        "p": 1,
        "fm": 2,
        "k": 1,
        "dd": 1,
        "cm": 1,
        "ca": 1,
        "cv": 1,
    }
}

_FIRST = "lda phi\nora phi+1\nbne step\n"
_ADD = "clc\nlda o\nadc fm\nsta o\nlda o+1\nadc fm+1\nsta o+1\nrts\n"
_SUB = "sec\nlda o\nsbc fm\nsta o\nlda o+1\nsbc fm+1\nsta o+1\nrts\n"
_INCVIBR = "clc\nlda fm\nadc dc\nsta fm\nlda fm+1\nadc #0\nsta fm+1\njmp dov\n"
_STEP = (
    "step:\nlda k\ncmp #131\nbeq pm\ncmp #255\nbeq pm\ncmp #129\nbeq up\ncmp #130\nbeq dn\ntax\n"
    "bmi done\nbne nv\n{inc}nv:\nlda dc\nbmi dov\ndec dc\ndone:\nrts\ndov:\nlda vc\nbne cnt\n"
    "lda p\nsta vc\ncnt:\ndec vc\nlda vc\nasl\ncmp p\nbcc up\ndn:\n"
    + _SUB
    + "up:\n"
    + _ADD
    + "pm:\nlda o+1\nbmi neg\nora o\nbeq neg\nlda fm\ncmp o\nlda fm+1\nsbc o+1\nbcs zero\nclc\n"
    "lda o\nsbc fm\nsta o\nlda o+1\nsbc fm+1\nsta o+1\nrts\nneg:\nsec\nlda #0\nsbc o\ntax\n"
    "lda #0\nsbc o+1\ncpx fm\nsbc fm+1\nbcc zero\nsec\nlda o\nadc fm\nsta o\nlda o+1\nadc fm+1\n"
    "sta o+1\nrts\nzero:\nlda #0\nsta o\nsta o+1\nrts\n"
)
# SETFMOD with CALCVIBRATO: FREQMOD at ceil(A / 2) + DPITCH, r that half + 1 (0: none).
_CALC = (
    "lda #r\nbeq az\nlda ne\nclc\nadc #r\ncmp #204\nbcc ay\nlda #203\nay:\ncmp #107\n"
    "bcs ar\nsec\nsbc #12\nbcc az\ntax\nlda F.hi,x\nsta fm\nlda #0\nsta fm+1\njmp ad\naz:\nlda #0\n"
    "sta fm\nsta fm+1\njmp ad\nar:\nsec\nsbc #108\ntax\nlda F.lo,x\nsta fm\nlda F.hi,x\nsta fm+1\n"
    "ad:\n"
)
_CONST = "lda #<g\nsta fm\nlda #>g\nsta fm+1\n"
_TOWARD = "ldx ne\nsec\nlda z\nsbc F.lo,x\nsta o\nlda z+1\nsbc F.hi,x\nsta o+1\nlda #131\nsta k\n"
_WRPITCH = (
    "go:\nlda cm\nbeq c0\ncmp #1\nbeq c1\ncmp #3\nbeq c3\nlda ne\nclc\nadc #1\nclc\nadc ca\n"
    "jmp sum\nc3:\nlda cv\ncmp #255\nbne cvo\nlda ca\ncvo:\nlsr\njmp sum\nc1:\nsec\njmp sum\n"
    "c0:\nclc\nsum:\nlda z\nadc dd\nsta v\nlda z+1\nadc #0\nsta v+1\nrts\n"
)


def templates(calcvibrato: bool, vibratotypes: bool) -> dict:
    """The templates' texts for a build: its SETFMOD and whether SLIDEVIB 0 is INCVIBR."""
    amp = _CALC if calcvibrato else _CONST
    step = _STEP.format(inc=_INCVIBR if vibratotypes else "")
    return {
        # FREQLO/FREQHI of the note masked to 7 bits; a WF pitch row (phi 0) clears the offset.
        "tone": (
            "lda phi\nora phi+1\nbne go\nsta o\nsta o+1\nlda #1\nsta s\ngo:\nlda s\nbeq done\n"
            "lda nn\nclc\nadc #1\nand #127\ntax\ndex\nclc\nlda F.lo,x\nadc o\nsta z\n"
            "lda F.hi,x\nadc o+1\nsta z+1\ndone:\nlda z\nsta v\nlda z+1\nsta v+1\nrts\n"
        ),
        # WRPITCH: a WF row (phi 0) sets the detune (f 1) and the carry, mode c (3 skipped), b.
        "det": "lda phi\nora phi+1\nbne go\nlda #f\nbeq keep\nlda #d\nsta dd\nkeep:\nlda #c\n"
        "sta cm\nlda #b\nsta ca\n" + _WRPITCH,
        # SETDETU: a FX's detune, the carry left as the WF walk made it.
        "dfx": "lda phi\nora phi+1\nbne go\nlda #d\nsta dd\n" + _WRPITCH,
        # STRTSND: SLIDEVIB, SETVIB0, SETVIBR, no detune, the pitch held; h 1 or 2 writes FREQHI.
        "snd": "lda phi\nora phi+1\nbeq init\nrts\ninit:\nsta o\nsta o+1\nsta s\nsta dd\nlda #c\n"
        "sta k\n"
        "lda #255\nsta cv\n"
        "lda #d\nsta dc\nlda #i\nsta vc\nlda #q\nsta p\n"
        + amp
        + "lda #h\nbeq nh\nldx ne\ncmp #1\nbeq hw\nldx #t\nhw:\nlda F.hi,x\nsta v+1\nnh:\nrts\n",
        # HARDRST's carry on a new note's skipped WF ticks.
        "hrc": "lda phi\nora phi+1\nbne kept\nlda #c\nsta cv\nkept:\nrts\n",
        # VIBSLIDE: the vibrato or slide SLIDEVIB selects.
        "stp": step,
        # FORCVIB and SETVIBR (w 1 also clears the offset, as a note-column vibrato), then step.
        "vfx": _FIRST
        + "lda #c\nsta k\nlda #w\nbeq kept\nlda #0\nsta o\nsta o+1\nkept:\nlda #i\nsta vc\n"
        "lda #q\nsta p\n" + amp + step,
        # SMALFX9: VIBFREQU.
        "vfq": _FIRST + "lda #q\nsta p\n" + step,
        # BIGFX16: SLIDEVIB.
        "kty": _FIRST + "lda #c\nsta k\n" + step,
        # BIGFX01/02 (m 129, 130), BIGFX03 without a note (131), NPORTAM (255).
        "sld": _FIRST + "lda #m\nsta k\n" + amp + step,
        # BIGFX03 on a note, or a legato's BIGFX01/02 (m): toward its word, no step on the tick.
        "por": _FIRST + _TOWARD + "lda #m\nsta k\n" + amp + "rts\n" + step,
        # Legato $3F: as BIGFX03 with FREQMODH $7F.
        "leg": _FIRST + _TOWARD + "lda #127\nsta fm+1\nrts\n" + step,
        # A note after NPORTAM: toward its word at NPORTAM's FREQMOD.
        "npn": _FIRST + _TOWARD + "rts\n" + step,
    }
