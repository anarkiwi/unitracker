"""One spelling per meaning: forms that play as another are refused by the reader, and the
writer writes each meaning one way."""

import itertools

import numpy as np
import pytest

from unitracker.format import idle, songio, timeline, xexpr
from unitracker.format.codec import CodecError, row_place
from unitracker.player.vm import SongPlayer

FIRST, LATER = row_place(0, 2, True), row_place(1, 2, True)


ONCE = "    play: [p1, p1, p1]\n"


def doc(pr: str, defs: str, phrases: dict, rows: str = ONCE, end="end"):
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
          {{cut: 512}}, end]}}
"""


def test_the_reader_refuses_and_the_writer_drops_an_expression_no_start_reads():
    held = doc("goattracker2", GT_FILTER.format(row="{hold: 5}"), {"p1": "C4.i1*8"})
    refused(held.replace("{hold: 5}", "{hold: 5, cut: v + 3}"), "can't express")
    ir = songio.loads(held)
    rows = ir["records"]["filttab"]
    later = max(rows) - 1
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


# -- 4: where a !freq runs from, and !freq:none only where one runs --

GT_FREQ = """      x:
        x1: v + 300
        x2: v
      ins:
        i1: {sr: 240, ctrl: [{ctrl: 65, note: +0}, end]}
        i2: {sr: 240, ctrl: [{ctrl: 65, note: +0}, end], freq: v + 3}
"""
THEN = "    play: [p1, p1, p1]\n  - play: [p2, p2, p2]\n"


@pytest.mark.parametrize(
    "phrases,match",
    [
        ({"p1": "C4.i1!freq:x1, D4!freq:none*3"}, "sets what the voice already has"),
        ({"p1": "C4.i1, -!freq:none*3"}, "sets what the voice already has"),
        ({"p1": "C4.i1!freq:x1, -!freq:none, -!freq:none*2"}, "sets what the voice already has"),
        ({"p1": "C4.i1, -!freq:x2*3"}, "sets what the voice already has"),
        ({"p1": "C4.i1!freq:x1, -!freq:x2*3"}, "is written !freq:none"),
        ({"p1": "C4.i1!freq:x1*4", "p2": "-*2, D4*2"}, "continues the !freq of the phrase before"),
    ],
)
def test_the_reader_refuses_a_freq_none_where_none_runs_and_a_freq_it_does_not_write(
    phrases, match
):
    refused(doc("goattracker2", GT_FREQ, phrases, THEN if "p2" in phrases else ONCE), match)


@pytest.mark.parametrize(
    "phrases",
    [
        {"p1": "C4.i1!freq:x1, -!freq:none*3"},
        {"p1": "C4.i2!freq:x1, -!freq:x2*3"},
        {"p1": "C4.i1!freq:x1*4", "p2": "-!freq:x1*2, D4*2"},
    ],
)
def test_a_freq_written_where_it_runs_from_is_kept(phrases):
    text = canonical(doc("goattracker2", GT_FREQ, phrases, THEN if "p2" in phrases else ONCE))
    assert all(f"{n}: [{a}]" in text for n, a in phrases.items())


def test_the_writer_writes_a_held_freq_as_none_and_none_only_where_a_freq_runs():
    text = canonical(doc("goattracker2", GT_FREQ, {"p1": "C4.i1!freq:x1, -!freq:none, D4*2"}))
    ir = songio.loads(text)
    events = ir["phrases"]["p1"]
    ir["exprs"][len(ir["exprs"]) + 1] = "v"
    events[1]["command"] = [16, len(ir["exprs"])]
    events[2]["command"] = [16, 0]
    assert songio.dumps(ir) == text


# -- 5: rows that change nothing --

IDLE = [
    ("goattracker2", "ins", "i1: {ctrl: [{ctrl: 65, note: +0}, end], filter: [{cut: 1024, res: 12,"
     " route: 1, mode: 16}, ROW, end]}", "{hold: 5}"),
    ("goattracker2", "ins", "i1: {ctrl: [{ctrl: 65, note: +0}, ROW, end]}", "{}"),
    ("goattracker2", "ins", "i1: {ctrl: [{ctrl: 65, note: +0}, ROW, end]}", "{wait: 3}"),
    ("defmon", "ins", "i1: {ctrl: [{hold: 4, ctrl: 65, gate: 1}, ROW, end]}", "{}"),
]  # fmt: skip


def unchecked(monkeypatch, text: str) -> dict:
    """What the reader reads ``text`` as, without refusing rows that change nothing."""
    monkeypatch.setattr(idle, "check", lambda *_: None)
    ir = songio.loads(text)
    monkeypatch.undo()
    return ir


def same_play(text: str, ir: dict, ticks: int = 400) -> bool:
    return np.array_equal(
        SongPlayer(songio.loads(text)).render_grid(ticks), SongPlayer(ir).render_grid(ticks)
    )


@pytest.mark.parametrize("pr,tname,ins,row", IDLE)
def test_a_last_row_that_only_lasts_is_refused_and_left_out(monkeypatch, pr, tname, ins, row):
    text = doc(pr, f"      {tname}:\n        {ins.replace('ROW', row)}\n", {"p1": "C4.i1*16"})
    refused(text, "a last row that only lasts changes nothing")
    ir = unchecked(monkeypatch, text)
    out = canonical(songio.dumps(ir))
    assert f"{row}, end]" not in out
    assert same_play(out, ir)


HELD = {
    "goattracker2": "i1: {ctrl: [{ctrl: 65, note: +0}, end], filter: [{cut: 1024, res: 12,"
    " route: 1, mode: 16}, ROW, {cut: 512}, end]}",
    "defmon": "i1: {ctrl: [{ctrl: 65, gate: 1}, ROW, {ctrl: 33}, end]}",
    "sidwizard": "i1: {ctrl: [{ctrl: 65}, ROW, {ctrl: 33}, end]}",
    "goattracker2c": "i1: {ctrl: [{ctrl: 65, note: +0}, ROW, {ctrl: 33, note: +12}, end]}",
}


@pytest.mark.parametrize(
    "pr,rows,merged",
    [
        ("goattracker2", "{hold: 2}, {hold: 3}", "{hold: 5}"),
        ("goattracker2", "{hold: 100}, {hold: 100}", "{hold: 127}, {hold: 73}"),
        ("goattracker2", "{}, {}", "{hold: 2}"),
        ("defmon", "{hold: 2}, {hold: 3}", "{hold: 5}"),
        ("goattracker2c", "{wait: 2}, {wait: 3}", "{wait: 6}"),
        ("goattracker2c", "{wait: 2}, {}", "{wait: 3}"),
        ("goattracker2c", "{wait: 9}, {wait: 9}", "{wait: 15}, {wait: 3}"),
    ],
)
def test_rows_that_only_last_are_one_row(monkeypatch, pr, rows, merged):
    defs = f"      ins:\n        {HELD[pr].replace('ROW', rows)}\n"
    text = doc(pr.removesuffix("c"), defs, {"p1": "C4.i1*16"})
    refused(text, "rows that only last are one row")
    ir = unchecked(monkeypatch, text)
    out = canonical(songio.dumps(ir))
    assert merged in " ".join(out.split())
    assert np.array_equal(
        SongPlayer(songio.loads(out)).render_grid(400), SongPlayer(ir).render_grid(400)
    )


def test_rows_that_only_hold_stay_where_the_player_plays_them_otherwise():
    defs = f"      ins:\n        {HELD['sidwizard'].replace('ROW', '{hold: 2}, {hold: 3}')}\n"
    assert "{hold: 2}, {hold: 3}" in canonical(doc("sidwizard", defs, {"p1": "C4.i1*16"}))
    last = "      ins:\n        i1: {ctrl: [{ctrl: 65}, {ctrl: 33}, {hold: 3}, end]}\n"
    assert "{hold: 3}, end]" in canonical(doc("sidwizard", last, {"p1": "C4.i1*16"}))


GT_HOLD = (
    "      ins:\n        i1: {ctrl: [{ctrl: 65, note: +0}, end], filter: [{hold: 5, cut: v}, ROW,"
    " end]}\n"
)


def test_an_expression_that_holds_what_its_program_holds_is_refused_and_left_out(monkeypatch):
    text = doc(
        "goattracker2", GT_HOLD.replace("ROW", "{res: 12, route: 1, mode: 16}"), {"p1": "C4.i1*16"}
    )
    refused(text, "cut: v on a program that sets no cut")
    ir = unchecked(monkeypatch, text)
    out = canonical(songio.dumps(ir))
    assert "filter: [{hold: 5}, {res: 12, route: 1, mode: 16}, end]" in out
    assert same_play(out, ir)
    kept = doc("goattracker2", GT_HOLD.replace("ROW", "{cut: 512}"), {"p1": "C4.i1*16"})
    assert "{hold: 5, cut: v}" in canonical(kept)


# -- 6: one spelling per integer, sign and quote --

GT_ONE = """      ins:
        i1: {ad: 1, sr: 240, ctrl: [{ctrl: 65, note: +0}, {wait: 1, note: +12}, end], pw: 2048,
          restart: r1}
      restart:
        r1: [{hold: 4, gate: 0, ad: 15, sr: 0}, {ctrl: 9}, end]
      x:
        x1: v + 32
"""
GT_PHRASE = {"p1": "C4.i1!freq:x1*4, G#5*4"}


@pytest.mark.parametrize(
    "old,new,match",
    [
        ("ad: 1,", "ad: +1,", r"ad: \+1 is written 1"),
        ("hold: 4", "hold: +4", r"hold: \+4 is written 4"),
        ("gate: 0", "gate: -0", "gate: -0 is written 0"),
        ("note: +0", "note: -0", r"note: -0 is written \+0"),
        ("note: +0", "note: '+00'", "is written unquoted"),
        ("note: +12", "note: '+12'", "is written unquoted"),
        ("play: [p1, p1, p1]", "play: [p1+0, p1, p1]", "bad play entry"),
        ("play: [p1, p1, p1]", "play: [p1-0, p1, p1]", "bad play entry"),
        ("play: [p1, p1, p1]", "play: [p1+00, p1, p1]", "bad play entry"),
        ("G#5*4", "C-0*4", "bad atom"),
        ("note: +12", "note: C-0", "bad note"),
        ("note: +12", "note: 'G#5'", "written without quotes"),
        ("pw: 2048", "pw: '2048'", "'2048' is written 2048"),
        ("pw: 2048", 'pw: "2048"', "is quoted '...'"),
        ("x1: v + 32", "x1: '32'", "'32' is written 32"),
        ("C4.i1!freq:x1*4", "'C4.i1!freq:x1*4'", "written without quotes"),
        ("x1: v + 32", "x1: v + 0x20", "0x20 .* is not a decimal integer"),
        ("x1: v + 32", "x1: v + 3_2", "3_2 .* is not a decimal integer"),
        ("x1: v + 32", "x1: '1 + v'", "written without quotes"),
        ("x1: v + 32", "'x1': v + 32", "written without quotes"),
        ("unitracker: 1.20", "unitracker: '1.20'", "not a unitracker"),
        ("      x:\n", "      x:\n        x1: v\n", "appears twice"),
    ],
)
def test_each_value_has_one_spelling(old, new, match):
    text = canonical(doc("goattracker2", GT_ONE, GT_PHRASE))
    assert old in text
    refused(text.replace(old, new, 1), match)


def test_quotes_stay_where_the_plain_scalar_reads_otherwise():
    text = canonical(doc("goattracker2", GT_ONE, GT_PHRASE))
    meta = "meta: {title: '1984', author: '', released: 'a: b'}"
    assert canonical(text.replace("meta: {}", meta)).count(meta) == 1
    comma = "x1: '2*min(64, φ) + v'"
    refused(text.replace("x1: v + 32", comma), "written without quotes")
    flow = text.replace("pw: 2048", "pw: '2*min(64, φ) + 2048'")
    assert "pw: '2*min(64, φ) + 2048'" in " ".join(canonical(flow).split())
    refused(flow.replace("'2*min(64, φ) + 2048'", '"2*min(64, φ) + 2048"'), "is quoted '...'")


@pytest.mark.parametrize(
    "pr,ins",
    [
        ("goattracker2", "i1: {ctrl: [{ctrl: 65, note: +0}, end], filter: [{cut: CUT, res: 12,"
         " route: 1, mode: 16}, {cut: 512}, end]}"),
        ("sidwizard", "i1: {ctrl: [{ctrl: 65, note: +0}, end], filter: [{cut: CUT, res: 0,"
         " mode: 16}, {cut: 512, res: 3, mode: 32}, end]}"),
    ],
)  # fmt: skip
def test_a_constant_is_written_as_the_value_where_the_value_plays_it(pr, ins):
    defs = f"      ins:\n        {ins}\n"
    for cut in ("'1024'", "'0'"):
        refused(doc(pr, defs.replace("CUT", cut), {"p1": "C4.i1*8"}), "is written as its value")
    text = canonical(doc(pr, defs.replace("CUT", "1024"), {"p1": "C4.i1*8"}))
    assert "cut: 1024" in text
    if pr == "goattracker2":
        assert "cut: '1027'" in canonical(doc(pr, defs.replace("CUT", "'1027'"), {"p1": "C4.i1*8"}))


# -- 7: one text per expression --


@pytest.mark.parametrize(
    "text,written",
    [
        ("v+32", "v + 32"),
        ("32 + v", "v + 32"),
        ("v - -32", "v + 32"),
        ("((v + 32))", "v + 32"),
        ("v + 3 + 5", "v + 8"),
        ("2*(v + 3)", "2*v + 6"),
        ("3*v*2 - v", "5*v"),
        ("max(φ, 0)", "max(0, φ)"),
        ("-v % 3", "-v % 3"),
        ("-(v % 3)", "-(v % 3)"),
        ("v - (φ % 4)", "v - φ % 4"),
        ("(v - 1) % 256", "(v - 1) % 256"),
        ("v*(φ >= 1)*2", "2*v*(φ >= 1)"),
        (
            "v - ((-F(N) + F((N + 1) % 128)) % 65536) // 2",
            "v - (F((N + 1) % 128) - F(N)) % 65536 // 2",
        ),
        ("(N - 3)*φ % (4*v + 1)", "φ*(N - 3) % (4*v + 1)"),
        ("0*φ + v", "v"),
    ],
)
def test_an_expression_is_written_in_one_form(text, written):
    assert xexpr.canonical(text) == written and xexpr.canonical(written) == written
    tables = {"_F": lambda k: (37 * k) % 65536, "_T": {}}
    for v, phi, n in itertools.product((-70001, -5, 0, 7, 65535, 99999), (0, 1, 13), (0, 24, 127)):
        assert xexpr.compile_expr(text, tables)(v, phi, n) == xexpr.compile_expr(written, tables)(
            v, phi, n
        )


def test_equal_expressions_are_one_definition():
    phrases = {"p1": "C4.i1!freq:x1*4, D4!freq:x2*4, E4!freq:x3*4"}
    defs = GT_FREQ.replace("        x2: v\n", "        x2: 300 + v\n        x3: v - -300\n")
    text = canonical(doc("goattracker2", defs, phrases))
    assert "x1: v + 300" in text and "x2" not in text
    assert "C4.i1!freq:x1*4, D4!freq:x1*4, E4!freq:x1*4" in text
