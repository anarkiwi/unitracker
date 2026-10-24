"""SID-Wizard's per-tick frequency modulators as format 2.0 6502 code templates.

The freq cell's state is the player's: ``o`` vibrato_offset, ``s`` set once a WF row's pitch
drives the frequency, ``z`` FREQLO/FREQHI, ``dc`` VIDELCNT, ``vc`` VIBRACNT, ``p`` VIBFREQU,
``fm`` FREQMOD, ``k`` SLIDEVIB, ``dd`` DETUNER, ``cm``/``ca``/``cv`` WRPITCH's carry; the pw
cell's ``pl`` PWLOGHO/PWHIGHO, ``pk`` PKBDTRK, mirrored in the freq cell's ``ml``/``mk`` for
WRPULS's carry ``cs`` (``mt`` its scratch), ``vt``/``vi``/``vq``/``vr``/``vg`` the instrument's
vibrato a portamento's end restores; the cut cell's ``ch``, ``cl``, ``ck`` CTFH, CTFL and its
keyboard track.
"""

import re

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
        "ml": 2,
        "mk": 1,
        "mt": 2,
        "cs": 1,
        "vt": 1,
        "vi": 1,
        "vq": 1,
        "vr": 1,
        "vg": 2,
    },
    "pw": {"pl": 2, "pk": 1},
    "cut": {"ch": 1, "cl": 1, "ck": 1},
}

_FIRST = "lda phi\nora phi+1\nbne step\n"
_ADD = "clc\nlda o\nadc fm\nsta o\nlda o+1\nadc fm+1\nsta o+1\nrts\n"
_SUB = "sec\nlda o\nsbc fm\nsta o\nlda o+1\nsbc fm+1\nsta o+1\nrts\n"
_INCVIBR = "clc\nlda fm\nadc dc\nsta fm\nlda fm+1\nadc #0\nsta fm+1\njmp dov\n"
_STEP = (
    "step:\nlda k\ncmp #131\nbeq pm\ncmp #255\n{np}cmp #129\nbeq up\ncmp #130\nbeq dn\ntax\n"
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
# PORTAME with PORTAVIBRA: tested one unit nearer, its end (PORTEND) the instrument's vibrato.
_PORTAVIBRA = (
    "pm:\nlda o+1\nbmi neg\nlda o\ncmp fm\nlda o+1\nsbc fm+1\nbcc zero\nclc\nlda o\nsbc fm\nsta o\n"
    "lda o+1\nsbc fm+1\nsta o+1\nrts\nneg:\nlda o\neor #255\ntax\nlda o+1\neor #255\ncpx fm\n"
    "sbc fm+1\nbcc zero\nsec\nlda o\nadc fm\nsta o\nlda o+1\nadc fm+1\nsta o+1\nrts\nzero:\n"
    "lda #0\nsta o\nsta o+1\nlda vt\nsta k\nlda vi\nsta vc\nlda vq\nsta p\n{amp}rts\n"
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
    "jmp sum\nc3:\nlda cv\ncmp #255\nbne cvo\nlda ca\ncmp #2\nbne cvo\nlda cs\ncvo:\nlsr\njmp sum\n"
    "c1:\nsec\njmp sum\n"
    "c0:\nclc\nsum:\nlda z\nadc dd\nsta v\nlda z+1\nadc #0\nsta v+1\nrts\n"
)


# EXPTABH[idx] (12 bytes of zeros, then FREQTBH) for idx = (DPITCH & $7F) + the track amount.
_TRACK = (
    "lda ne\nclc\nadc #1\nand #127\nclc\nadc {k}\nphp\ntax\nsec\nsbc #12\ntay\nlda #0\n"
    "cpx #12\nbcc e0\nlda F.hi,y\ne0:\n"
)
_WRPULS = (
    "go:\nlda pk\nbeq plain\n"
    + _TRACK.format(k="pk")
    + "sta v+1\nlda #0\ncpx #13\nbcc e1\ndey\nlda F.hi,y\ne1:\nsta v\nplp\nlda v+1\nsbc v\n"
    "adc pl+1\nsta v+1\nlda pl\nsta v\nrts\nplain:\nlda pl\nsta v\nlda pl+1\nsta v+1\nrts\n"
)
_CUTOFF = (
    "go:\nlda ck\nbeq fp\n"
    + _TRACK.format(k="ck")
    + "plp\nadc ch\nadc #0\njmp fo\nfp:\nlda ch\nfo:\nsta v\nlda #0\nsta v+1\nasl v\nrol v+1\n"
    "asl v\nrol v+1\nasl v\nrol v+1\nlda cl\nand #7\nora v\nsta v\nrts\n"
)


def _mirrored(text: str) -> str:
    """A pulse template on the freq cell's copy: its WRPULS keeps only the carry, in ``cs``."""
    text = re.sub(r"\bpk\b", "mk", re.sub(r"\bpl\b", "ml", text)).replace("sta v\n", "sta mt\n")
    text = text.replace("sta v+1\n", "sta mt+1\n").replace("sbc v\n", "sbc mt\n")
    text = text.replace("lda v+1\n", "lda mt+1\n")
    text = text.replace(
        "adc ml+1\nsta mt+1\nlda ml\nsta mt\nrts\n", "adc ml+1\nlda #0\nrol\nsta cs\nrts\n"
    )
    return text.replace(
        "plain:\nlda ml\nsta mt\nlda ml+1\nsta mt+1\nrts\n", "plain:\nlda #0\nsta cs\nrts\n"
    )


def mirror_templates() -> dict:
    """The pulse templates mirrored on the freq cell, for WRPITCH's carry on skipped WF ticks."""
    pulse = track_templates(False)
    return {"m" + name[1:]: _mirrored(pulse[name]) for name in ("pst", "psw", "pid", "pwr")}


def track_templates(finefiltsweep: bool) -> dict:
    """The pulse and filter tables' templates where the tune tracks the keyboard: each binding
    keeps the player's own value and writes it with the note's track term."""
    return {
        # SETPULW: the width and PKBDTRK; then WRPULS.
        "pst": "lda phi\nora phi+1\nbne go\nlda #<w\nsta pl\nlda #>w\nsta pl+1\nlda #j\nsta pk\n"
        + _WRPULS,
        # PWSWEEP's step.
        "psw": "clc\nlda pl\nadc #<d\nsta pl\nlda pl+1\nadc #>d\nsta pl+1\n" + _WRPULS,
        # PWADVAN past a sweep: PKBDTRK.
        "pid": "lda phi\nora phi+1\nbne go\nlda #j\nsta pk\n" + _WRPULS,
        # WRPULS of the width the table rests on.
        "pwr": _WRPULS,
        # SETFILT: CTFH (CTFL cleared with FINEFILTSWEEP) and the track amount.
        "fst": "lda phi\nora phi+1\nbne go\nlda #c\nsta ch\n"
        + ("lda #0\nsta cl\n" if finefiltsweep else "")
        + "lda #j\nsta ck\n"
        + _CUTOFF,
        # FILTPRG's sweep step: the 11-bit cutoff (FINEFILTSWEEP) or CTFH.
        "fsw": (
            "lda ch\nsta v\nlda #0\nsta v+1\nasl v\nrol v+1\nasl v\nrol v+1\nasl v\nrol v+1\n"
            "lda cl\nand #7\nora v\nclc\nadc #<d\nsta v\nlda v+1\nadc #>d\nand #7\nsta v+1\n"
            "lda v\nand #7\nsta cl\nlsr v+1\nror v\nlsr v+1\nror v\nlsr v+1\nror v\nlda v\n"
            "sta ch\n"
            if finefiltsweep
            else "clc\nlda ch\nadc #d\nsta ch\n"
        )
        + _CUTOFF,
        # The cutoff and track the previous filter voice's walk left on a handover tick.
        "fho": "lda phi\nora phi+1\nbne go\nlda #c\nsta ch\nlda #l\nsta cl\nlda #j\nsta ck\n"
        + _CUTOFF,
        # FILTPRG past a sweep: the track amount.
        "fid": "lda phi\nora phi+1\nbne go\nlda #j\nsta ck\n" + _CUTOFF,
        # BIGFX0F: CTFH, under the filter walk's binding.
        "fhi": "lda phi\nora phi+1\nbne go\nlda #c\nsta ch\n" + _CUTOFF,
        # $D416/$D415 of the cutoff the table rests on.
        "frs": _CUTOFF,
    }


def templates(calcvibrato: bool, vibratotypes: bool, portavibra: bool = False) -> dict:
    """The templates' texts for a build: its SETFMOD, whether SLIDEVIB 0 is INCVIBR, and
    whether a portamento ends in the instrument's vibrato."""
    amp = _CALC if calcvibrato else _CONST
    target = "bne nf\nlda #0\nsta o\nsta o+1\nrts\nnf:\n" if portavibra else "beq pm\n"
    step = _STEP.format(inc=_INCVIBR if vibratotypes else "", np=target)
    keep = ""
    if portavibra:
        held = _CALC.replace("#r", "vr") if calcvibrato else _CONST.replace("#<g", "vg")
        held = re.sub(r"\ba([zyrd])\b", r"p\1", held.replace("#>g", "vg+1"))
        tail = step[step.index("pm:\n") :]
        step = step.replace(tail, _PORTAVIBRA.format(amp=held))
        stored = "lda #r\nsta vr\n" if calcvibrato else "lda #<g\nsta vg\nlda #>g\nsta vg+1\n"
        keep = "lda #c\nsta vt\nlda #i\nsta vi\nlda #q\nsta vq\n" + stored
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
        + keep
        + amp
        + "lda #h\nbeq nh\nldx ne\ncmp #1\nbeq hw\nldx #t\nhw:\nlda F.hi,x\nsta v+1\nnh:\nrts\n",
        # HARDRST's carry on a new note's skipped WF ticks.
        "hrc": "lda phi\nora phi+1\nbne kept\nlda #c\nsta cv\nkept:\nrts\n",
        # VIBSLIDE from the next tick: a tick the player's hard restart skips it.
        "wt": _FIRST + "rts\n" + step,
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
