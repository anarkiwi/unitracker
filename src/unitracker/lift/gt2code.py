"""GoatTracker 2's stateful frequency effects as 6502 modulators of the ``freq`` cell.

State: ``t``, the vibrato phase (``vibtime``), and ``d``, the instrument vibrato's delay left
(``vibdelay``). Each routine is the matching procedure of ``goattracker.play``; ``s`` is a
16-bit step, ``c`` the vibrato depth and ``n`` the delay to load.
"""

from unitracker_grammar import code

STATE = (("t", 1), ("d", 1))
_ADD = "lda v\nadc {0}\nsta v\nlda v+1\nadc {1}\nsta v+1\n"
_SUB = "lda v\nsbc {0}\nsta v\nlda v+1\nsbc {1}\nsta v+1\n"
_VIB = (
    "lda t\nbmi up\ncmp #c\nbeq up\nbcc up\neor #255\nup:\nclc\nadc #2\nsta t\nlsr\nbcs dn\n"
    + _ADD.format("#s", "#0")
    + "rts\ndn:\n"
    + _SUB.format("#s", "#0")
    + "rts"
)
_TOWARD = (
    "lda ne\nand #127\ntay\nsec\nlda v\nsbc F.lo,y\ntax\nlda v+1\nsbc F.hi,y\nbcc up\n"
    "bne dn\ntxa\nbeq done\ndn:\nsec\n"
    + _SUB.format("#<s", "#>s")
    + "lda v\ncmp F.lo,y\nlda v+1\nsbc F.hi,y\nbcs done\n"
    "snap:\nlda F.lo,y\nsta v\nlda F.hi,y\nsta v+1\nlda #0\nsta t\ndone:\nrts\nup:\nclc\n"
    + _ADD.format("#<s", "#>s")
    + "lda F.lo,y\ncmp v\nlda F.hi,y\nsbc v+1\nbcc snap\nrts"
)
CODE = {
    "note": ("lda #0\nsta t\nlda nn\nand #127\ntay\nlda F.lo,y\nsta v\nlda F.hi,y\nsta v+1\nrts"),
    "portau": "lda #0\nsta t\nclc\n" + _ADD.format("#<s", "#>s") + "rts",
    "portad": "lda #0\nsta t\nsec\n" + _SUB.format("#<s", "#>s") + "rts",
    "tporta": _TOWARD,
    "tsnap": ("lda #0\nsta t\nlda ne\nand #127\ntay\nlda F.lo,y\nsta v\nlda F.hi,y\nsta v+1\nrts"),
    "vib": _VIB,
    "vibi": "lda d\nbeq go\ndec d\nrts\ngo:\n" + _VIB,
}
CODE = {name: text + "\n" for name, text in CODE.items()}
ROUTINES = {name: code.read(text) for name, text in CODE.items()}
