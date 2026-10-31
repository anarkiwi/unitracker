"""defMON's stateful units as 6502 modulators, each the matching statements of the spec.

State: ``freq`` ``s`` the pitch slide, ``g`` the voice's detune; ``pw`` ``l h`` the pulse bytes,
``d`` the sweep depth, ``m`` the slide mirror, ``k`` the detune; ``cut`` ``w z`` the accumulator, ``t u`` its step, ``o`` its operation (0 until
set, then 1 add or 2 subtract), ``e`` the extra a CP column sets.
"""

from unitracker_grammar import code

STATE = {
    "freq": (("s", 2), ("g", 1)),
    "pw": (("l", 1), ("h", 1), ("d", 1), ("m", 2), ("k", 1)),
    "cut": (("w", 1), ("z", 1), ("t", 1), ("u", 1), ("o", 1), ("e", 1)),
}

_WORD = "lda nn\nclc\nadc #24\n{0}tay\nlda F.lo-24,y\nclc\nadc {1}\n{2}lda F.hi-24,y\nadc {1}+1\n"
_MASK = "and #127\n"
_UP = "lda {0}\nclc\nadc #<r\nsta {0}\nlda {0}+1\nadc #>r\nsta {0}+1\n"
_DN = "lda {0}\nsec\nsbc #<r\nsta {0}\nlda {0}+1\nsbc #>r\nsta {0}+1\n"


_DET = "lda nn\nclc\nadc #24\n{0}tay\nlda F.lo-24,y\nclc\nadc {1}\n"


def _sum(mask: str) -> str:
    """The note's word plus the slide accumulator ``s``, into ``v``."""
    return _WORD.format(mask, "s", "sta v\n") + "sta v+1\nrts\n"


def _carry(mask: str) -> str:
    """The carry out of the note's word plus the accumulator ``m``, into ``v``."""
    return _WORD.format(mask, "m", "") + "lda #0\nrol\nsta v\nlda #0\nsta v+1\nrts\n"


def _det(mask: str) -> str:
    """The note's word with the detune ``g`` added to its low byte, into ``v``."""
    return _DET.format(mask, "g") + "sta v\nlda F.hi-24,y\nsta v+1\nrts\n"


def _cdet(mask: str) -> str:
    """The carry out of the note's low byte plus the detune ``k``, into ``v``."""
    return _DET.format(mask, "k") + "lda #0\nrol\nsta v\nlda #0\nsta v+1\nrts\n"


_PSW = (
    "lda d\nbeq out\nbmi up\nlda v\nlsr\nlda l\nsbc d\nsta l\nbcs out\n"
    "lda h\nbne dec\nlda #1\nsta l\njmp flip\ndec:\ndec h\njmp out\n"
    "up:\nand #127\nclc\nadc l\nsta l\nbcc out\nlda h\ncmp #15\nbeq top\ninc h\njmp out\n"
    "top:\nlda #248\nsta l\nflip:\nlda d\neor #128\nsta d\n"
    "out:\nlda l\nsta v\nlda h\nsta v+1\nrts\n"
)


def _cut(shift: bool) -> str:
    """The cutoff step: the accumulator by its step, the floor, the extra and the carry."""
    return (
        "lda o\nbne have\nlda #i\nhave:\ncmp #2\nbeq sb\n"
        "clc\nlda w\nadc t\nsta w\nlda z\nadc u\nsta z\njmp fin\n"
        "sb:\nclc\nlda w\nsbc t\nsta w\nlda z\nsbc u\nsta z\n"
        "fin:\nlda z\nbpl ok\nlda #b\nsta z\nok:\nadc e\nbmi low\ncmp #b\nbcs put\n"
        "low:\nlda #b\nput:\n" + ("asl\n" if shift else "") + "sta v\nlda #0\nsta v+1\n"
        "asl v\nrol v+1\nasl v\nrol v+1\nasl v\nrol v+1\nrts\n"
    )


CODE = {
    "clr": "lda #0\nsta s\nsta s+1\nrts\n",
    "seed": "lda #p\nsta s\nlda #q\nsta s+1\nrts\n",
    "up": _UP.format("s") + _sum(""),
    "dn": _DN.format("s") + _sum(""),
    "up7": _UP.format("s") + _sum(_MASK),
    "dn7": _DN.format("s") + _sum(_MASK),
    "pclr": "lda #0\nsta m\nsta m+1\nrts\n",
    "pseed": "lda #p\nsta m\nlda #q\nsta m+1\nrts\n",
    "cup": _UP.format("m") + _carry(""),
    "cdn": _DN.format("m") + _carry(""),
    "cup7": _UP.format("m") + _carry(_MASK),
    "cdn7": _DN.format("m") + _carry(_MASK),
    "tset": "lda #p\nsta g\nrts\n",
    "det": _det(""),
    "det7": _det(_MASK),
    "cdet": _cdet(""),
    "cdet7": _cdet(_MASK),
    "psw": _PSW,
    "pwinit": "lda #p\nsta l\nlda #q\nsta h\nlda #x\nsta k\nrts\n",
    "pwset": "lda #p\nsta h\nand #240\nsta l\nrts\n",
    "psset": "lda #q\nsta d\nrts\n",
    "pwps": "lda #p\nsta h\nand #240\nsta l\nlda #q\nsta d\nrts\n",
    "cp": "lda #p\nsta e\nrts\n",
    "acc": "lda #p\nsta w\nlda #q\nsta z\nlda #0\nsta t\nsta u\nrts\n",
    "add": "lda #p\nsta t\nlda #q\nsta u\nlda #1\nsta o\nrts\n",
    "sub": "lda #p\nsta t\nlda #q\nsta u\nlda #2\nsta o\nrts\n",
    "step": _cut(False),
    "stepx": _cut(True),
}
ROUTINES = {name: code.read(text) for name, text in CODE.items()}
