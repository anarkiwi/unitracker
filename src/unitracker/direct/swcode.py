"""SID-Wizard's per-tick frequency modulators as format 1.23 6502 code templates.

The freq cell's state: ``o`` vibrato_offset, ``s`` set while a WF row's pitch drives the
frequency, ``dc`` VIDELCNT, ``vc`` VIBRACNT, ``fm`` FREQMOD; ``W[k]`` is generic note k's word.
Params: ``d`` delay byte, ``k`` first counter, ``p`` VIBFREQU, ``f`` amplitude.
"""

STATE = {"freq": {"o": 2, "s": 1, "dc": 1, "vc": 1, "fm": 2}}

_FIRST = "lda phi\nora phi+1\nbne step\n"
_AMP = "lda #<f\nsta fm\nlda #>f\nsta fm+1\n"
_NORMVIB = (
    "step:\nlda dc\nbmi run\ndec dc\nrts\nrun:\nlda vc\nbne cnt\nlda #p\nsta vc\ncnt:\ndec vc\n"
    "lda vc\nasl\ncmp #p\nbcc add\nsec\nlda o\nsbc fm\nsta o\nlda o+1\nsbc fm+1\nsta o+1\nrts\n"
    "add:\nclc\nlda o\nadc fm\nsta o\nlda o+1\nadc fm+1\nsta o+1\nrts\n"
)
_PORTAME = (
    "step:\nlda o+1\nbmi neg\nora o\nbeq neg\nlda fm\ncmp o\nlda fm+1\nsbc o+1\nbcs zero\nclc\n"
    "lda o\nsbc fm\nsta o\nlda o+1\nsbc fm+1\nsta o+1\nrts\nneg:\nsec\nlda #0\nsbc o\ntax\n"
    "lda #0\nsbc o+1\ncpx fm\nsbc fm+1\nbcc zero\nsec\nlda o\nadc fm\nsta o\nlda o+1\nadc fm+1\n"
    "sta o+1\nrts\nzero:\nlda #0\nsta o\nsta o+1\nrts\n"
)

CODE = {
    # WRPITCH: a WF pitch row (phi 0) clears the offset and drives the frequency, W[N] + o.
    "tone": (
        "lda phi\nora phi+1\nbne go\nsta o\nsta o+1\nlda #1\nsta s\ngo:\nlda s\nbeq done\n"
        "ldx nn\nclc\nlda W.lo,x\nadc o\nsta v\nlda W.hi,x\nadc o+1\nsta v+1\ndone:\nrts\n"
    ),
    # STRTSND (phi 0: SETVIB0, SETVIBR, the pitch held), then NORMVIB and DOVIBRA.
    "vib": _FIRST
    + "sta o\nsta o+1\nsta s\nlda #d\nsta dc\nlda #k\nsta vc\n"
    + _AMP
    + "rts\n"
    + _NORMVIB,
    # Note-column vibrato $60-$77: SETVIBR on the row's amplitude, the delay left running.
    "vibfx": _FIRST + "sta o\nsta o+1\nlda #k\nsta vc\n" + _AMP + _NORMVIB,
    # Legato $3F: the offset from the new note's word, FREQMODH $7F, then PORTAME.
    "leg": _FIRST
    + "ldx ne\nsec\nlda v\nsbc W.lo,x\nsta o\nlda v+1\nsbc W.hi,x\nsta o+1\nlda #127\nsta fm+1\n"
    + "rts\n"
    + _PORTAME,
}
