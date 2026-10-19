"""The canonical grammar: every reference document is in its language, and each form of the
vocabulary has one spelling."""

import pytest

from unitracker.format import canonical

HEADER = "unitracker: 1.20\nplayroutine: {ref: goattracker2}\nmeta: {title: T, author: A}\n"


def doc(
    defs: str, head: str = "", play: str = "[p1, p1, p1]", ending: str = "  - {loop: 0}"
) -> str:
    """A document of one row defining ``defs`` and phrase ``p1``."""
    phrase = "      phrase:\n        p1: [C4.i1, -*3]\n"
    return f"{HEADER}{head}song:\n  - def:\n{defs}{phrase}    play: {play}\n{ending}\n"


def test_the_reference_documents_are_canonical(references):
    """Metal Warrior and Csillag parse; Crosseyed's fixed records are outside the grammar."""
    canonical.parse(references["metal_warrior"].text)
    canonical.parse(references["csillag"].text)
    with pytest.raises(canonical.CanonicalError, match="line 6"):
        canonical.parse(references["crosseyed"].text)


INS = "      ins:\n        i1: "
CASES = [
    INS + "{ad: 1, sr: 2, ctrl: [{ctrl: 65, note: +0}, {wait: 2, ctrl: 17, note: C5}, {loop: 1}],"
    " pw: 2048, restart: r1}\n      restart:\n"
    "        r1: [{hold: 2, gate: 0, ad: 15, sr: 0}, {ctrl: 9}, end]\n",
    INS + "{filter: [{cut: '-12*min(1, max(0, φ - 5)) + 512', res: 15, route: +, mode: 16},"
    " {hold: 5}, {}, {loop: 2}]}\n",
    "      x:\n        x1: (F(N) < (v + F(N)) % 65536)*(v - 263) + 767*(v % 32768 < 1)\n"
    + INS
    + "{pw: 16*abs((φ - 32) % 128 - 64) + 512, freq: v - 139}\n",
    INS + "{ctrl: c1, filter: f1}\n      ctrl:\n        c1: [{ctrl: 129^8, note: G#5}, end]\n"
    "      filter:\n        f1: 1024\n"
    "      pw:\n        pw1: [{pw: none}, {run: c1}, {filter: none}, end]\n",
    INS + "{pw: 95*φ\n            + 296, restart: [{hold: 3, ctrl: ^8, gate: 0}, {},\n"
    "            end]}\n",
    INS + "{ad: 1}\n      phrase:\n"
    "        p2: [C4.i1!run:c1!ad:1!sr:2!ctrl:65!resfilt:15/1!vol:15!cut:x1!tempo:6/7!vtempo:8"
    "!freq:x1!legato!pw!filter*2, -!pw:none!filter:none,\n"
    "          off!cut:80, on!freq:none, B-1!freq:-139, C10!pw:256, -!pw:x1!cut:512]\n",
]


@pytest.mark.parametrize("defs", CASES)
def test_each_form_of_the_vocabulary_parses(defs):
    """Rows, instruments, programs, expressions, wraps and every command shape."""
    canonical.parse(doc(defs))


def test_the_header_forms_parse():
    """Block playroutine with constants and tables, clock, voices, a swing tempo, the init row."""
    head = (
        "clock: {cycles_per_tick: 17095, clock_hz: 1022727}\nvoices: 3\ntempo: [6, 7]\n"
        "init: [E2!pw:256!cut:x1!freq:-139!legato, -!pw:2048, -]\n"
    )
    text = doc("      ins:\n        i1: {ad: 1}\n", head, ending="  - end")
    canonical.parse(text)
    block = (
        "unitracker: 1.20\nplayroutine:\n  ref: defmon\n  constants: {floor: 2, dbl: 0}\n"
        "  tables:\n    note_lo: {18: 922, 22: 967,\n      42: 3178}\n    por_lo: [1, 2, 3]\n"
        "meta: {title: T}\n"
    )
    canonical.parse(
        doc("      ins:\n        i1: {ad: 1}\n", play="[p1, p1+5, ~]").replace(HEADER, block)
    )


@pytest.mark.parametrize(
    "text, where",
    [
        (doc(INS + "{sr: 2, ad: 1}\n"), "line 7"),
        (doc(INS + "{ctrl: [{note: C4, ctrl: 65}, end]}\n"), "line 7"),
        (doc("      ins:\n       i1: {ad: 1}\n"), "line 7"),
        (doc("      phrase:\n        p2: [C4!legato!freq:x1]\n"), "line 7"),
        (doc("      phrase:\n        p2: [C4 .i1]\n"), "line 7"),
        (doc(INS + "{pw: v+1}\n"), "line 7"),
        (doc("      vinit:\n        0: {pw: 256}\n"), "line 6"),
        (doc(INS + "{ad: 1}\n", ending="  - loop: 0"), "line 11"),
        (HEADER.replace("meta", "title") + "song:\n  - end\n", "line 3"),
    ],
)
def test_other_spellings_are_refused(text, where):
    """Key order, indentation, command order, spaces in an event, operator spacing, fixed records,
    the song's ending and the header keys each have one form."""
    with pytest.raises(canonical.CanonicalError, match=where):
        canonical.parse(text)
