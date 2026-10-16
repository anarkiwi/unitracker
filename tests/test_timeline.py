"""Per-voice tracks to a timeline of rows, and back."""

import pytest

from unitracker import timeline as tl
from unitracker.timeline import END, TimelineError


def play(name, transpose=0, repeat=1):
    return {"phrase": name, "transpose": transpose, "repeat": repeat}


def expand(track, ticks, lengths):
    """The ``(time, phrase, transpose)`` starts of a track over ``ticks`` steps."""
    intro, cycle = tl._items(track)  # pylint: disable=protected-access
    out, t, k = [], 0, 0
    while t < ticks:
        if k < len(intro):
            item = intro[k]
        elif cycle:
            item = cycle[(k - len(intro)) % len(cycle)]
        else:
            break
        out.append((t, *item))
        t += lengths[item[0]]
        k += 1
    return out


def roundtrip(tracks, lengths, ticks=200):
    rows, loop = tl.to_rows(tracks, lengths)
    back = tl.from_rows(rows, loop, lengths, len(tracks))
    for orig, new in zip(tracks, back):
        assert expand(orig, ticks, lengths) == expand(new, ticks, lengths)
    return rows, loop


def test_aligned_voices_one_row_per_phrase():
    lengths = {"a": 4, "b": 4}
    tracks = [{"plays": [play("a"), play("b", 2)], "loop": 0}, {"plays": [play("b")], "loop": 0}]
    rows, loop = roundtrip(tracks, lengths)
    assert rows == [[("a", 0), ("b", 0)], [("b", 2), ("b", 0)]]
    assert loop == 0


def test_unaligned_loops_unroll_to_the_joint_period():
    lengths = {"a": 2, "b": 3}
    tracks = [{"plays": [play("a")], "loop": 0}, {"plays": [play("b")], "loop": 0}]
    rows, loop = roundtrip(tracks, lengths)
    assert loop == 0
    assert [r[0] for r in rows].count(("a", 0)) == 3
    assert [r[1] for r in rows].count(("b", 0)) == 2


def test_intro_repeat_and_mid_phrase_loop_row():
    lengths = {"i": 3, "a": 2, "b": 4}
    tracks = [
        {"plays": [play("i"), play("a", repeat=2)], "loop": 1},
        {"plays": [play("b", 1)], "loop": 0},
    ]
    roundtrip(tracks, lengths, ticks=400)


def test_ending_voices():
    lengths = {"a": 2, "b": 5}
    tracks = [{"plays": [play("a")], "loop": None}, {"plays": [play("b")], "loop": 0}]
    rows, _ = roundtrip(tracks, lengths)
    assert rows[1][0] == END
    tracks = [{"plays": [play("a")], "loop": None}, {"plays": [play("b")], "loop": None}]
    rows, loop = roundtrip(tracks, lengths)
    assert loop is None and rows[-1] == [None, END]


def test_unaligned_beyond_the_limit(monkeypatch):
    monkeypatch.setattr(tl, "MAX_ROWS", 3)
    with pytest.raises(TimelineError, match="align"):
        tl.to_rows(
            [{"plays": [play("a")], "loop": 0}, {"plays": [play("b")], "loop": 0}],
            {"a": 2, "b": 3},
        )


@pytest.mark.parametrize(
    "rows,loop,match",
    [
        ([], 0, "no rows"),
        ([[("a", 0)]], 0, "entries"),
        ([[("a", 0), None]], 0, "must start"),
        ([[("a", 0), ("b", 0)], [("a", 0), ("a", 0)]], 0, "still playing"),
        ([[("z", 0), ("a", 0)]], 0, "unknown phrase"),
        ([[("a", 0), ("a", 0)]], 5, "outside"),
        ([[END, ("a", 0)]], 0, "never starts"),
    ],
)
def test_from_rows_validation(rows, loop, match):
    with pytest.raises(TimelineError, match=match):
        tl.from_rows(rows, loop, {"a": 2, "b": 4}, 2)
