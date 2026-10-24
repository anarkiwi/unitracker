"""The ``repeats`` pass: phrases with groups, the song in sections and groups, their inline
forms, and lifted reference documents."""

import os
import re
from pathlib import Path

import numpy as np
import pytest
from unitracker_grammar.document import Group, Part, SongRow, printed, read
from unitracker_grammar.expand import expanded, flat

import optimiser_docs as D

from unitracker.optimiser import Guarantee, optimise
from unitracker.optimiser.inline import inline
from unitracker.optimiser.repeats import encoded, grouped, lcp, repeats, song_rows

REFERENCE = Path(os.environ.get("UNITRACKER_REF_DOCS", "out/ref22/lifted"))
ONLY = (("repeats", repeats),)

PHRASES = D.doc(
    "      ins:\n"
    "        i1:{ad:9,sr:240}\n"
    "      phrase:\n"
    "        p1:[C4.i1,D,E,D,E,D,E,D,E,F,-*3]\n"
    "        p2:[C4.i1,D,E,F,D,E,G,D,E,A,-*2]\n"
    "        p3:[C4.i1,D,C,D,E,C,D,C,D,E,-*2]\n",
    play="[p1]\n - play:[p2]\n - play:[p3]\n - play:[[C3.i1,G,C3.i1,G,C3.i1,G,-]]",
)


def song(rows: list, loop: int | None) -> str:
    """A one-voice document playing a row ``play:[p1+t]`` for each transpose ``t`` of ``rows``."""
    lines = "".join(f" - play:[p1{t:+d}]\n" for t in rows)
    end = " - end\n" if loop is None else f" - {{loop:{loop}}}\n"
    head = "unitracker:2.3\nmeta:{title:T}\nvoices:1\ntempo:2\n"
    return f"{head}ins:\n i1:{{ad:9,sr:240}}\nphrase:\n p1:[C4.i1,-]\nsong:\n{lines}{end}"


SONG = song([0, 1, 2, 3, 0, 1, 2, 3, 0, 1, 2, 3, 5, 5, 5, 5, 0, 1, 2, 3, 0, 1, 2, 3, 7], 4)


def applied(doc, edits):
    for edit in edits:
        doc = edit.apply(doc)
    return doc


def test_lcp_is_the_longest_common_prefix_of_every_two_suffixes():
    ids = np.array([0, 1, 0, 1, 0, 2, 0, 1])
    table = lcp(ids)
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            k = 0
            while b + k < len(ids) and ids[a + k] == ids[b + k]:
                k += 1
            assert table[a, b] == k


def test_the_encoder_finds_repeats_voltas_and_nested_groups():
    _, items = encoded(list("xababababy"), [3] * 10)
    assert items == (0, ("rep", (1, 2), 4), 9)
    _, items = encoded(list("abcabdabe"), [3] * 9)
    assert items == (("volta", (0, 1), ((2,), (5,), (8,))),)
    _, items = encoded(list("aabaab"), [3] * 6)
    assert items == (("rep", (("rep", (0,), 2), 2), 2),)
    assert encoded(list("abcd"), [3] * 4)[1] == (0, 1, 2, 3)


def test_phrases_take_repeats_voltas_and_nested_groups_and_play_the_same():
    doc = read(PHRASES)
    edits = repeats(doc)
    assert [e.label for e in edits] == ["phrase p1", "phrase p2", "phrase p3", "inline phrase 1"]
    out = applied(doc, edits)
    text = printed(out)
    assert "p1:[C4.i1,[D,E]x4,F,-*3]" in text
    assert "p2:[C4.i1,[D,E|F|G|A],-*2]" in text
    assert "p3:[C4.i1,[[D|C|E]|C|-*2]]" in text
    assert "play:[[[C3.i1,G]x3,-]]" in text
    assert expanded(out) == expanded(doc)
    guarantee = Guarantee(PHRASES)
    assert all(guarantee.holds(printed(e.apply(doc))) for e in edits)
    assert not repeats(out)


def test_a_phrase_with_nothing_to_repeat_keeps_its_events():
    events = read(PHRASES).records("phrase")["p1"]
    assert grouped(events[-2:]) == events[-2:]
    assert flat(grouped(events)) == events


def test_the_song_takes_sections_and_groups_and_loops_into_the_middle():
    doc = read(SONG)
    edits = repeats(doc)
    assert [e.label for e in edits] == ["song"]
    out = edits[0].apply(doc)
    assert song_rows(out) == song_rows(doc)
    assert any(s.kind == "section" for s in out.defs)
    assert any(isinstance(i, (Part, Group)) for i in out.rows)
    assert 0 < out.loop < len(out.rows)
    assert Guarantee(SONG).holds(printed(out))
    assert not repeats(out)
    result = optimise(SONG, order=ONLY)
    assert result.after < result.before and ("repeats", "song") in result.kept
    assert Guarantee(SONG).holds(result.text) and read(result.text) == result.doc


def test_a_song_of_distinct_rows_proposes_nothing():
    text = song([0, 1, 2, 3, 4], None)
    assert not repeats(read(text))
    result = optimise(text, order=ONLY)
    assert not result.kept and result.text == printed(read(text))


def test_a_song_ending_at_its_loop_keeps_the_loop_after_its_items():
    text = song([0, 1, 0, 1, 0, 1, 0, 1], 8)
    doc = read(text)
    out = applied(doc, repeats(doc))
    assert out.loop == len(out.rows) and song_rows(out) == song_rows(doc)


def test_single_inlines_a_section_played_once_and_all_defines_sections_at_first_use():
    doc = read(SONG)
    out = repeats(doc)[0].apply(doc)
    text = printed(out)
    holds = Guarantee(SONG).holds
    for mode in ("single", "all"):
        inlined, refused = inline(out, mode, holds)
        assert refused == [] and holds(printed(inlined))
        assert song_rows(inlined) == song_rows(out)
    single, _ = inline(out, "single", holds)
    used = re.findall(r"\bs\d+\b", text.split("song:")[1] + text.split("section:")[1])
    once = {s for s in used if used.count(s) == 1}
    assert once and not any(f" {s}:" in printed(single) for s in once)
    first, _ = inline(out, "all", holds)
    assert not first.defs and "=[" in printed(first).split("song:")[1]


def test_single_splices_a_section_into_the_song_and_keeps_the_loop_item():
    text = (
        "unitracker:2.3\nmeta:{title:T}\nvoices:1\ntempo:2\nins:\n i1:{ad:9}\nphrase:\n"
        " p1:[C4.i1,-]\nsection:\n s1:[play:[p1],play:[p1+1]]\n s2:[play:[p1+3]]\nsong:\n"
        " - s1\n - play:[p1+2]\n - s2x2\n - {loop:1}\n"
    )
    out, refused = inline(read(text), "single", Guarantee(text).holds)
    assert refused == [] and not out.defs[-1].kind == "section"
    assert out.loop == 2 and out.rows[out.loop] == SongRow(read(text).rows[1].play)
    assert isinstance(out.rows[-1], Group) and out.rows[-1].times == 2
    assert Guarantee(text).holds(printed(out))


@pytest.mark.parametrize("name", ["Trapped_Again_tune_3_6581", "Metal_Warrior_Title_Remix"])
def test_a_lifted_reference_document_takes_repeats_and_plays_its_grid(name):
    path = REFERENCE / f"{name}.yaml"
    if not path.exists():
        pytest.skip(f"{path} unavailable")
    text = "unitracker:2.3\n" + path.read_text(encoding="utf-8").split("\n", 1)[1]
    text = re.sub(
        r"\b(slide|sweep)\(", lambda m: {"slide": "pslide(", "sweep": "csweep("}[m[1]], text
    )
    result = optimise(text, ticks=3000, order=ONLY)
    assert result.after < result.before and any(p == "repeats" for p, _ in result.kept)
    assert Guarantee(text, 3000).holds(result.text) and read(result.text) == result.doc
