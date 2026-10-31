"""Lift invariants: a document's objects of each kind against its native model's."""

import pytest

from unitracker.lift import invariants
from unitracker.lift.invariants import Origin as O

VOICES = """unitracker:2.4
meta:{title:t}
ins:
 i1:{ad:9,ctrl:c1}
ctrl:
 c1:[{ctrl:65},end]
phrase:
 p1:[C4.i1,-]
 p2:[D4.{ad:1,ctrl:[{ctrl:17},end]},-]
song:
 - voice:[p1,[p1+2,p2]x4|end]
 - voice:[p2|loop:0]
 - voice:[p1|end]
"""

ROWS = """unitracker:2.3
meta:{title:t}
section:
 s1:[play:[[C4,-],~,~],play:[[E4,-],~,~]]
song:
 - s1x3
 - play:[[C4,-],~,~]
 - end
"""


def test_a_repeated_group_and_its_phrases_count_once():
    have = invariants.lifted(VOICES)
    assert have["song"] == (3, 1, 1)
    assert (have["instruments"], have["ctrl"], have["phrases"]) == (2, 2, 2)


def test_a_section_played_three_times_counts_its_rows_once():
    have = invariants.lifted(ROWS)
    assert have["song"] == (3, 3, 3) and have["phrases"] == 2


def test_a_kind_past_its_native_count_is_refused_and_named():
    assert invariants.check(VOICES, {"ctrl": 2, "song": (3, 1, 1)})["ctrl"] == 2
    with pytest.raises(invariants.ModelError, match="3 lifted song on voice 1 > 2 native"):
        invariants.check(VOICES, {"song": (2, 1, 1)})
    with pytest.raises(invariants.ModelError, match="2 lifted phrases > 1 native"):
        invariants.check(VOICES, {"phrases": 1})


def test_an_unrolled_loop_exceeds_the_native_sequence():
    unrolled = VOICES.replace("[p1,[p1+2,p2]x4|end]", "[p1" + ",p1+2,p2" * 4 + "|end]")
    assert invariants.lifted(unrolled)["song"] == (9, 1, 1)
    assert invariants.exceeded(invariants.lifted(unrolled), {"song": (3, 1, 1)})


def test_a_run_program_and_a_definition_at_first_use_count():
    text = VOICES.replace("p1:[C4.i1,-]", "p1:[C4.i1 run:ctrl[{ctrl:33},end],-]").replace(
        "voice:[p1|end]", "voice:[p3=[E4.i1,-]|end]"
    )
    have = invariants.lifted(text)
    assert (have["ctrl"], have["phrases"], have["song"]) == (3, 3, (3, 1, 1))


ORIGINS = {
    "instruments": {"i1": O(("ins", 1))},
    "ctrl": {"c1": O(("wave", 0))},
    "phrases": {"p1": O(("pattern", 0)), "p2": O(("pattern", 1))},
}
NAMED = VOICES.replace("p2:[D4.{ad:1,ctrl:[{ctrl:17},end]},-]", "p2:[D4.i1,-]")


def test_every_named_object_stands_for_one_native_object():
    assert invariants.provenance(NAMED, ORIGINS)["phrases"] == (2, 2)


@pytest.mark.parametrize(
    "text, origins, why",
    [
        (VOICES, ORIGINS, "unnamed lifted"),
        (NAMED, ORIGINS | {"ctrl": {}}, "ctrl c1 has no origin"),
        (NAMED, ORIGINS | {"pw": {"pw1": O(("pulse", 0))}}, "no pw pw1"),
        (
            NAMED,
            ORIGINS | {"phrases": {"p1": O(("pattern", 0)), "p2": O(("pattern", 0))}},
            "one origin",
        ),
        (
            NAMED.replace("p2:[D4.i1,-]", "p2:[C4.i1,-]"),
            ORIGINS | {"phrases": {"p1": O(("pattern", 0)), "p2": O(("pattern", 0), ("entered",))}},
            "two lifted phrases of one value",
        ),
    ],
)
def test_a_lift_without_provenance_is_refused(text, origins, why):
    with pytest.raises(invariants.ModelError, match=why):
        invariants.provenance(text, origins)


def test_the_song_bytes_are_kept_apart_from_the_format_s_and_ratcheted():
    have = invariants.sizes(NAMED)
    assert have["fixed"] == len("unitracker:2.4\nmeta:{title:t}\n") and have["song"] > 0
    assert invariants.grown({"a": 10, "b": 5, "c": 1}, {"a": 10, "b": 4}) == ["b", "c"]


def test_a_definition_at_first_use_is_named_and_a_run_program_inline_is_not():
    first = NAMED.replace("voice:[p1|end]", "voice:[p3=[E4.i1,-]|end]").replace(
        "p2:[D4.i1,-]", "p2:[D4.i1,F4.{ad:2}]"
    )
    phrases = ORIGINS["phrases"] | {"p3": O(("pattern", 2))}
    with pytest.raises(invariants.ModelError, match="unnamed lifted instruments"):
        invariants.provenance(first, ORIGINS | {"phrases": phrases})
    first = first.replace("F4.{ad:2}", "F4.i1")
    assert invariants.provenance(first, ORIGINS | {"phrases": phrases})["phrases"] == (3, 3)
    run = NAMED.replace("p1:[C4.i1,-]", "p1:[C4.i1 run:ctrl[{ctrl:33},end],-]")
    with pytest.raises(invariants.ModelError, match="unnamed lifted ctrl"):
        invariants.provenance(run, ORIGINS)


def test_a_phrase_played_in_place_is_unnamed():
    with pytest.raises(invariants.ModelError, match="unnamed lifted phrases"):
        invariants.provenance(ROWS, {})
