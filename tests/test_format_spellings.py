"""One spelling per meaning: forms that play as another are refused by the reader, and the
writer writes each meaning one way."""

import numpy as np
import pytest

from unitracker.format import songio, timeline
from unitracker.format.codec import CodecError, row_place
from unitracker.player.vm import SongPlayer

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


# -- 2: a song loops from its earliest repeat, with its shortest loop --


@pytest.mark.parametrize(
    "seq,loop,expect",
    [
        ("ab", 0, (0, 2)),
        ("abab", 0, (0, 2)),
        ("xabab", 1, (1, 2)),
        ("babab", 1, (0, 2)),
        ("abcbc", 3, (1, 2)),
        ("aaaa", 2, (0, 1)),
        ("abcabd", 3, (3, 3)),
    ],
)
def test_minimal_loop(seq, loop, expect):
    assert timeline.minimal_loop(list(seq), loop) == expect


GT_DEFS = "      ins:\n        i1: {ctrl: [{ctrl: 65, note: +0}, end]}\n"
TWO = {"p1": "C4.i1*4", "p2": "D4*4"}


@pytest.mark.parametrize(
    "rows,loop",
    [
        ("    play: [p1, p1, p1]\n  - play: [p2, p2, p2]\n  - play: [p1, p1, p1]\n", 1),
        ("    play: [p1, p1, p1]\n  - play: [p2, p2, p2]\n  - play: [p1, p1, p1]\n"
         "  - play: [p2, p2, p2]\n", 0),
    ],
)  # fmt: skip
def test_the_reader_refuses_a_loop_longer_or_later_than_the_songs(rows, loop):
    refused(doc("goattracker2", GT_DEFS, TWO, rows, f"{{loop: {loop}}}"), "write that loop")


def test_the_writer_folds_each_voices_loop():
    two = "    play: [p1, p1, p1]\n  - play: [p2, p2, p2]\n"
    want = canonical(doc("goattracker2", GT_DEFS, TWO, two, "{loop: 0}"))
    ir = songio.loads(want)
    for trk in ir["tracks"]:
        trk["plays"] = trk["plays"] * 3
        trk["loop"] = 2
    assert songio.dumps(ir) == want


# -- 3: a note names its instrument where naming it changes what it plays --

SW_DEFS = """      ctrl:
        c1: [{ctrl: 33, note: +0}, end]
      ins:
        i1: {ctrl: [{ctrl: 65, note: +0}, end], pw: [{pw: 2048}, end]}
"""


@pytest.mark.parametrize(
    "pr,defs",
    [("goattracker2", GT_DEFS), ("sidwizard", SW_DEFS)],
)
def test_a_note_naming_the_instrument_its_phrase_plays_is_refused_and_written_without(pr, defs):
    named = doc(pr, defs, {"p1": "C4.i1*4, D4.i1*4"})
    refused(named, "names the instrument the phrase plays")
    plain = canonical(doc(pr, defs, {"p1": "C4.i1*4, D4*4"}))
    ir = songio.loads(plain)
    ir["phrases"]["p1"][1]["instrument"] = ir["phrases"]["p1"][0]["instrument"]
    assert songio.dumps(ir) == plain


@pytest.mark.parametrize(
    "pr,defs,atoms",
    [
        ("sidwizard", SW_DEFS, "C4.i1*4, D4!pw*4, E4.i1*4"),
        ("sidwizard", SW_DEFS, "C4.i1*4, D4.i1!run:c1*4"),
        ("defmon", "      ctrl:\n        c1: [{ctrl: 65, gate: 1}, end]\n      ins:\n"
         "        i1: {ctrl: c1}\n", "C4.i1*4, D4.i1*4"),
    ],
)  # fmt: skip
def test_a_note_names_its_instrument_where_naming_it_plays_otherwise(pr, defs, atoms):
    text = doc(pr, defs, {"p1": atoms})
    assert atoms in canonical(text)


def test_a_lifetime_flag_no_note_reads_is_no_reason_to_name_the_instrument():
    plain = canonical(doc("sidwizard", SW_DEFS, {"p1": "C4.i1*4, D4*4"}))
    ir = songio.loads(plain)
    ir["records"]["ins"][1]["flags"] = ir["records"]["ins"][1].get("flags", 16) | 0x40
    ir["phrases"]["p1"][1]["instrument"] = 1
    assert songio.dumps(ir) == plain
    grid = SongPlayer(ir).render_grid(200)
    assert np.array_equal(SongPlayer(songio.loads(plain)).render_grid(200), grid)
