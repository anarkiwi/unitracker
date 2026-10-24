"""defMON's per-tick units as 6502 code templates (docs/direct/defmon.md).

defMON note index ``k`` is generic note ``k - 24``: its word is the standard note table's ``F``
from index 24 to 118, else the replay's table ``L`` of the words under and over those. ``L``, the
portamento table ``P`` and the slide rates ``R`` are the replay's; :data:`STATE` names each cell's
variables.
"""

STATE = {
    "freq": {"s": 2, "d": 1, "i": 1, "fq": 2},
    "pw": {"w": 2, "q": 1, "e": 1, "mm": 1, "t": 2, "fp": 2},
    "cut": {"r": 2, "z": 2, "o": 1, "cp": 1, "l": 1, "b": 1},
}

_FIRST = "lda phi\nora phi+1\nbne {0}\n"
# the note's word in {0} and its defMON index in y: F from index 24 to 118, L under and over it
_WORD = (
    "lda nn\nclc\nadc #24\nand #127\ntay\nsec\nsbc #24\nbcc low\ncmp #95\nbcs high\ntax\n"
    "lda F.lo,x\nsta {0}\nlda F.hi,x\njmp put\nhigh:\nsbc #71\ntax\njmp tab\nlow:\ntya\ntax\n"
    "tab:\nlda L.lo,x\nsta {0}\nlda L.hi,x\nput:\nsta {0}+1\n"
)
_NOTE = _WORD.format("fq")
_SUM = _NOTE + "clc\nlda s\nadc fq\nsta v\nlda fq+1\nadc s+1\nsta v+1\nrts\n"
_W = "lda w\nsta v\nlda w+1\nsta v+1\nrts\n"
# $10B5: step the accumulator, saturate to the floor, add cp and the carry, clamp, double.
_ACID = (
    "lda o\nbne sb\nclc\nlda r\nadc z\nsta r\nlda r+1\nadc z+1\njmp hi\n"
    "sb:\nclc\nlda r\nsbc z\nsta r\nlda r+1\nsbc z+1\n"
    "hi:\nbpl keep\nlda l\nkeep:\nsta r+1\nadc cp\nbmi low\ncmp l\nbcs put\nlow:\nlda l\n"
    "put:\nldx b\nbeq out\nasl\nout:\nsta v\nlda #0\nsta v+1\n" + "asl v\nrol v+1\n" * 3 + "rts\n"
)

CODE = {
    # init's detune, frequency and slide accumulator; i marks them until nr's first firing
    "ninit": _FIRST.format("done")
    + "lda #k\nsta d\nlda #<u\nsta s\nlda #>u\nsta s+1\nlda #1\nsta i\n"
    "lda #<f\nsta v\nlda #>f\nsta v+1\ndone:\nrts\n",
    # a note (phi 0) clears the slide accumulator
    "nr": _FIRST.format("done")
    + "lda i\nbne keep\nsta s\nsta s+1\nkeep:\nlda #0\nsta i\ndone:\nrts\n",
    # $1453 mode 0: the detune adds to the low byte only
    "pitch": _NOTE + "lda fq\nclc\nadc d\nsta v\nlda fq+1\nsta v+1\nrts\n",
    # $140E mode $80+: the accumulator slides by the rate, down with bit 6
    "slide": "lda #m\nasl\nand #127\ntax\nlda #m\nand #64\nbne dn\nclc\n"
    "lda R.lo,x\nadc s\nsta s\nlda R.hi,x\nadc s+1\nsta s+1\njmp sum\n"
    "dn:\nsec\nlda s\nsbc R.lo,x\nsta s\nlda s+1\nsbc R.hi,x\nsta s+1\nsum:\n" + _SUM,
    # $1469 mode $01-$7F: the portamento step at note + mode
    "por": _NOTE + "tya\nclc\nadc #m\ntay\nsec\nlda P.lo+1,y\nsbc P.lo,y\nsta v\n"
    "lda P.hi+1,y\nsbc P.hi,y\nsta v+1\nclc\nlda fq\nadc v\nsta v\nlda fq+1\nadc v+1\n"
    "sta v+1\nrts\n",
    "pinit": _FIRST.format("out")
    + "lda #k\nsta e\nlda #<f\nsta w\nlda #>f\nsta w+1\nlda #<u\nsta t\nlda #>u\nsta t+1\nout:\n"
    + _W,
    # a note (phi 0) clears the pitch step's mode and the pulse cell's slide accumulator
    "pzero": _FIRST.format("done") + "sta t\nsta t+1\nsta mm\ndone:\nrts\n",
    # a row's pitch mode (phi 0), then the slide accumulator the pulse step's carry reads
    "pmode": _FIRST.format("go") + "lda #m\nsta mm\ngo:\nlda mm\nbpl done\nasl\nand #127\ntax\n"
    "lda mm\nand #64\nbne dn\nclc\nlda R.lo,x\nadc t\nsta t\nlda R.hi,x\nadc t+1\nsta t+1\nrts\n"
    "dn:\nsec\nlda t\nsbc R.lo,x\nsta t\nlda t+1\nsbc R.hi,x\nsta t+1\ndone:\nrts\n",
    # g bit 0 sets the pulse, bit 1 the step; $1495 then sweeps, down with the pitch step's carry
    "pul": _FIRST.format("step") + "lda #g\nlsr\nbcc pss\nlda #p\nsta w+1\nand #240\nsta w\n"
    "pss:\nlda #g\nand #2\nbeq step\nlda #h\nsta q\n"
    "step:\nldy q\nbne sw\njmp out\nsw:\nbmi up\njmp dn\nup:\ntya\nand #127\nclc\nadc w\nsta w\n"
    "bcs hi\njmp out\nhi:\nlda w+1\n"
    "cmp #15\nbeq top\ninc w+1\njmp out\ntop:\nlda #248\nsta w\ntya\neor #128\nsta q\njmp out\n"
    "dn:\n" + _WORD.format("fp") + "lda mm\nbeq m0\nbmi sl\ntya\nclc\nadc mm\ntay\nsec\n"
    "lda P.lo+1,y\nsbc P.lo,y\nsta v\nlda P.hi+1,y\nsbc P.hi,y\nsta v+1\nclc\nlda fp\nadc v\n"
    "lda fp+1\nadc v+1\njmp sub\nsl:\nclc\nlda t\nadc fp\n"
    "lda fp+1\nadc t+1\njmp sub\nm0:\nlda fp\nclc\nadc e\n"
    "sub:\nldy q\nlda w\nsbc q\nsta w\nbcs out\n"
    "lda w+1\nbeq bot\ndec w+1\njmp out\nbot:\nlda #1\nsta w\ntya\neor #128\nsta q\nout:\n" + _W,
    "cinit": _FIRST.format("step") + "lda #u\nsta o\nlda #f\nsta l\nlda #k\nsta b\nstep:\n" + _ACID,
    # g bit 0 sets cp, bit 1 the accumulator, bits 2/3 an adding/subtracting step
    "acid": _FIRST.format("done") + "lda #g\nlsr\nbcc acc\nlda #c\nsta cp\n"
    "acc:\nlda #g\nand #2\nbeq add\nlda #<j\nsta r\nlda #>j\nsta r+1\nlda #0\nsta z\nsta z+1\n"
    "add:\nlda #g\nand #12\nbeq done\nlsr\nlsr\nlsr\nsta o\nlda #<k\nsta z\nlda #>k\nsta z+1\n"
    "done:\nrts\n",
}
