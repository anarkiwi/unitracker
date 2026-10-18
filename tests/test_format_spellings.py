"""One spelling per meaning: forms that play as another are refused by the reader, and the
writer writes each meaning one way."""

import pytest

from unitracker.format import songio
from unitracker.format.codec import CodecError, row_place

FIRST, LATER = row_place(0, 2, True), row_place(1, 2, True)


def doc(pr: str, defs: str, phrases: dict, rows: str = "    play: [p1, p1, p1]\n", end="end"):
    """A document of playroutine ``pr``: ``defs`` and ``phrases`` defined in its first row."""
    phrase = "".join(f"        {name}: [{atoms}]\n" for name, atoms in phrases.items())
    return (
        f"unitracker: {songio.VERSION}\nplayroutine: {{ref: {pr}}}\nsong:\n  - def:\n{defs}"
        f"      phrase:\n{phrase}{rows}  - {end}\n"
    )


def refused(text: str, match: str) -> None:
    with pytest.raises(songio.SongFormatError, match=match):
        songio.loads(text)


def canonical(text: str) -> str:
    """The document the writer writes for ``text``, which must write back as itself."""
    out = songio.dumps(songio.loads(text))
    assert songio.dumps(songio.loads(out)) == out
    return out


# -- 1: an expression is read on the row a start reads it from --

LATER_EXPRESSIONS = [
    ("goattracker2", "filttab", {"hold": 5, "cut": ("x", 1)}),
    ("goattracker2", "filttab", {"cut": ("x", 1), "res": 12, "route": 1, "mode": 16}),
    ("defmon", "sidtab", {"pw": ("x", 1)}),
    ("defmon", "sidtab", {"freq": ("x", 1)}),
    ("defmon", "sidtab", {"cut": ("x", 1)}),
    ("sidwizard", "flttab", {"mode": 16, "res": 0, "cut": ("x", 1)}),
]


@pytest.mark.parametrize("player,name,written", LATER_EXPRESSIONS)
def test_an_expression_goes_only_on_a_programs_first_row(player, name, written):
    rows = songio.codecs(songio.library(player), None)[name]
    assert rows.encode(rows.decode(written, FIRST), FIRST) == rows.normalize(written)
    with pytest.raises(CodecError, match="can't express"):
        rows.decode(written, LATER)


def test_an_expression_a_row_starts_goes_on_any_row():
    rows = songio.codecs(songio.library("goattracker2"), None)["ctrltab"]
    assert rows.encode(rows.decode({"freq": ("x", 1)}, LATER), LATER) == {"freq": ("x", 1)}


GT_FILTER = """      ins:
        i1: {{ctrl: [{{ctrl: 65, note: +0}}, end], filter: [{{cut: 1024, res: 12, route: 1, mode: 16}}, {row},
          end]}}
"""


def test_the_reader_refuses_and_the_writer_drops_an_expression_no_start_reads():
    held = doc("goattracker2", GT_FILTER.format(row="{hold: 5}"), {"p1": "C4.i1*8"})
    refused(held.replace("{hold: 5}", "{hold: 5, cut: v + 3}"), "can't express")
    ir = songio.loads(held)
    rows = ir["records"]["filttab"]
    later = max(rows)
    ir["exprs"][len(ir["exprs"]) + 1] = "v + 3"
    rows[later] = {**rows[later], "x": len(ir["exprs"])}
    assert songio.dumps(ir) == canonical(held)
