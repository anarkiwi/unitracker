"""GoatTracker 2's stateful frequency effects as 6502 modulators of the ``freq`` cell.

State: ``t``, the vibrato phase (``vibtime``), ``d``, the instrument vibrato's delay left
(``vibdelay``), ``ln``, the note whose frequency was last loaded (``lastnote``), and ``sl``,
``sh``, the calculated step. Each routine is the matching procedure
of ``goattracker.play``; ``s`` is a 16-bit step, ``c`` the vibrato depth, ``k`` the shift of a
calculated step (the note's semitone interval, ``F(N + 1) - F(N)``, shifted right) and ``n`` the
delay to load. A routine named with a trailing ``c`` takes a calculated step.
"""

from unitracker_grammar import code

STATE = (("t", 1), ("d", 1), ("ln", 1), ("sl", 1), ("sh", 1))
_CALC = (
    "ldy ln\nsec\nlda F.lo+1,y\nsbc F.lo,y\nsta sl\nlda F.hi+1,y\nsbc F.hi,y\nsta sh\n"
    "ldx #k\nbeq cz\ncr:\nlsr sh\nror sl\ndex\nbne cr\ncz:\n"
)
_LOHI = {False: ("#<s", "#>s"), True: ("sl", "sh")}
_SIZE = {False: ("#s", "#0"), True: ("sl", "sh")}


def _add(lo: str, hi: str) -> str:
    return f"lda v\nadc {lo}\nsta v\nlda v+1\nadc {hi}\nsta v+1\n"


def _sub(lo: str, hi: str) -> str:
    return f"lda v\nsbc {lo}\nsta v\nlda v+1\nsbc {hi}\nsta v+1\n"


def _vib(calc: bool) -> str:
    return (
        ("lda t\nbmi up\ncmp #c\nbeq up\nbcc up\neor #255\nup:\nclc\nadc #2\nsta t\nlsr\nbcs dn\n")
        + _add(*_SIZE[calc])
        + "rts\ndn:\n"
        + _sub(*_SIZE[calc])
        + "rts"
    )


def _toward(calc: bool) -> str:
    lo, hi = _LOHI[calc]
    return (
        "lda ne\nand #127\ntay\nsec\nlda v\nsbc F.lo,y\ntax\nlda v+1\nsbc F.hi,y\nbcc up\n"
        "bne dn\ntxa\nbeq done\ndn:\nsec\n"
        + _sub(lo, hi)
        + "lda v\ncmp F.lo,y\nlda v+1\nsbc F.hi,y\nbcs done\n"
        "snap:\nlda F.lo,y\nsta v\nlda F.hi,y\nsta v+1\nlda #0\nsta t\ntya\nsta ln\ndone:\nrts\nup:\nclc\n"
        + _add(lo, hi)
        + "lda F.lo,y\ncmp v\nlda F.hi,y\nsbc v+1\nbcc snap\nrts"
    )


def _routines(calc: bool) -> dict:
    pre = _CALC if calc else ""
    lo, hi = _LOHI[calc]
    return {
        "portau": "lda #0\nsta t\n" + pre + "clc\n" + _add(lo, hi) + "rts",
        "portad": "lda #0\nsta t\n" + pre + "sec\n" + _sub(lo, hi) + "rts",
        "tporta": pre + _toward(calc),
        "vib": pre + _vib(calc),
        "vibi": "lda d\nbeq go\ndec d\nrts\ngo:\n" + pre + _vib(calc),
    }


CODE = {
    "note": (
        "lda #0\nsta t\nlda nn\nand #127\nsta ln\ntay\nlda F.lo,y\nsta v\nlda F.hi,y\nsta v+1\nrts"
    ),
    "tsnap": (
        "lda #0\nsta t\nlda ne\nand #127\nsta ln\ntay\nlda F.lo,y\nsta v\nlda F.hi,y\nsta v+1\nrts"
    ),
    **_routines(False),
    **{name + "c": text for name, text in _routines(True).items()},
}
CODE = {name: text + "\n" for name, text in CODE.items()}
ROUTINES = {name: code.read(text) for name, text in CODE.items()}
