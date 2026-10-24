"""GoatTracker 2's per-tick modulators as format 1.23 6502 code templates.

State: the freq cell's ``t`` (vibrato phase), the pw cell's ``r``, ``q`` (pulse row and time).
Params: ``s`` step, ``k`` shift of ``G[nn + 1] - G[nn]`` (computed into ``phi``), ``g`` sign,
``c`` depth, ``l`` delay, ``b`` first pulse row; a ``1`` variant fires on ``phi`` 0 only.
"""

STATE = {"freq": {"t": 1}, "pw": {"r": 1, "q": 1}}

_ONCE = "lda phi\nora phi+1\nbeq first\nrts\nfirst:\n"
_DELAY = "lda phi+1\nbne go\nlda phi\ncmp #l\nbcs go\nrts\ngo:\n"
_CALC = (
    "lda nn\nand #127\ntax\nclc\nadc #1\nand #127\ntay\nsec\n"
    "lda G.lo,y\nsbc G.lo,x\nsta phi\nlda G.hi,y\nsbc G.hi,x\nsta phi+1\n"
    "ldx #k\nbeq sped\nshift:\nlsr phi+1\nror phi\ndex\nbne shift\nsped:\n"
)
_ADD = "lda v\nadc {0}\nsta v\nlda v+1\nadc {1}\nsta v+1\n"
_SUB = "lda v\nsbc {0}\nsta v\nlda v+1\nsbc {1}\nsta v+1\n"
_PORTA = "lda #g\nbmi dn\nclc\n" + _ADD + "rts\ndn:\nsec\n" + _SUB + "rts"
_VIB = (
    "lda t\nbmi up\ncmp #c\nbeq up\nbcc up\neor #255\nup:\nclc\nadc #2\nsta t\nlsr\nbcs dn\n"
    + _ADD
    + "rts\ndn:\n"
    + _SUB
    + "rts"
)
_TOWARD = (
    "lda ne\nand #127\ntay\nsec\nlda v\nsbc G.lo,y\ntax\nlda v+1\nsbc G.hi,y\nbcc up\n"
    "bne dn\ntxa\nbeq done\ndn:\n" + _SUB + "lda v\ncmp G.lo,y\nlda v+1\nsbc G.hi,y\nbcs done\n"
    "snap:\nlda G.lo,y\nsta v\nlda G.hi,y\nsta v+1\nlda #0\nsta t\ndone:\nrts\nup:\n"
    + _ADD
    + "lda G.lo,y\ncmp v\nlda G.hi,y\nsbc v+1\nbcc snap\nrts"
)
_SNAP = "sta t\nlda ne\nand #127\ntay\nlda G.lo,y\nsta v\nlda G.hi,y\nsta v+1\nrts"
_NOTE = (
    "lda phi\nora phi+1\nbne done\nsta t\nlda nn\nand #127\ntay\n"
    "lda G.lo,y\nsta v\nlda G.hi,y\nsta v+1\ndone:\nrts"
)
_PTAB_ROW = (
    "lda phi\nora phi+1\nbne go\nsta q\nlda #b\nsta r\ngo:\nldx r\n{0}lda PL.lo,x\ncmp #255\n"
    "bne row\nlda PR.lo,x\nsta r\n{0}tax\nrow:\nldy q\nbne mod\nlda PL.lo,x\n"
)
_PTAB = _PTAB_ROW.format("beq done\n") + (
    "bpl time\nsta v+1\nlda PR.lo,x\nsta v\ninc r\ndone:\nrts\ntime:\ntay\nmod:\ndey\nsty q\n"
    "bne keep\ninc r\nkeep:\nldy #0\nlda PR.lo,x\nbpl pos\ndey\npos:\nclc\nadc v\nsta v\ntya\n"
    "adc v+1\nsta v+1\nrts"
)
# PL[0] = 0 stops ptabs at row 0, where ptab tests r itself.
_PTABS = _PTAB_ROW.format("") + (
    "bpl time\ninc r\nlda PR.lo,x\njmp put\ntime:\ntay\nbeq done\nmod:\ndey\nsty q\n"
    "bne keep\ninc r\nkeep:\nlda v\nclc\nadc PR.lo,x\nadc #0\nput:\nsta v\nsta v+1\ndone:\nrts"
)


CODE = {"note": _NOTE, "snap": _SNAP, "ptab": _PTAB, "ptabs": _PTABS}
for _name, _prefix, _body, _step in (
    ("porta", "", _PORTA, ("#<s", "#>s")),
    ("tporta", "", _TOWARD, ("#<s", "#>s")),
    ("vib", _DELAY, _VIB, ("#s", "#0")),
):
    CODE[_name] = _prefix + _body.format(*_step)
    CODE[f"{_name}n"] = _prefix + _CALC + _body.format("phi", "phi+1")
CODE.update(
    (f"{base}1", _ONCE + CODE[base]) for base in ("porta", "portan", "tporta", "tportan", "snap")
)
