"""What the lifts share: note names, meta quoting, rest runs, repeat detection and the CLI."""

import numpy as np
import pytest
import yaml
from unitracker_grammar.document import Event

from unitracker.lift import cli
from unitracker.lift import shared as s


def test_note_names():
    assert [s.note_name(k) for k in (0, 24, 37, 95)] == ["C0", "C2", "C#3", "B7"]


@pytest.mark.parametrize(
    "value",
    ["Metal Warrior", "Cadaver", "it's", "yes", "12", "a: b", "x #y", " pad ", "-dash", "[x]", ""],
)
def test_meta_text_reads_back_as_the_text(value):
    assert yaml.safe_load(f"k: {s.meta_text(value)}")["k"] == value


def test_meta_text_leaves_plain_text_bare():
    assert s.meta_text("Metal Warrior") == "Metal Warrior"
    assert s.meta_text("it's") == "it's"
    assert s.meta_text("a: b") == "'a: b'"
    assert s.meta_text("x'y: z") == "'x''y: z'"


def test_repeat_finds_the_earliest_shortest_period():
    assert s.repeat([1, 2, 3, 2, 3, 2], [5, 6, 7, 6, 7, 6]) == (1, 2)
    assert s.repeat([1, 1, 1, 1], [1, 1, 1, 1]) == (0, 1)
    assert s.repeat([1, 2, 3], [1, 2, 3]) is None
    assert s.repeat([1, 2, 1, 2], [1, 1, 1, 2]) is None


def test_rests_join_bare_runs_only():
    events = [Event(n) for n in ("C2", "-", "-", "-", "off", "-")]
    assert s.rests(events) == [Event("C2"), Event("-", steps=3), Event("off"), Event("-")]
    assert s.rests([Event("-", steps=2), Event("-")]) == [Event("-", steps=3)]
    assert s.rests([Event("-", ins=1), Event("-")]) == [Event("-", ins=1), Event("-")]


CELLS = np.array([[7, 4, 1, 2], [9, 0, 3, 4]])


def test_summary():
    assert cli.summary(np.empty((0, 4), int)) == "exact"
    assert cli.summary(CELLS) == "first (7, 4) cells 2"


def lifted(raw, subtune):
    """A stand-in lift: the bytes and subtune as text."""
    return f"{raw.decode()}:{subtune}\n"


def verdict(raw, _subtune, _frames):
    """A stand-in verify: exact for ``ok``, else two differing cells."""
    return (np.empty((0, 4), int) if raw == b"ok" else CELLS), ""


def failing(_raw, _subtune, _frames):
    """A stand-in verify that raises."""
    raise ValueError("bad")


def test_main_prints_the_document(tmp_path, capsys):
    sid = tmp_path / "a.sid"
    sid.write_bytes(b"ok")
    assert cli.main([str(sid), "--subtune", "2"], "d", lifted, verdict) == 0
    assert capsys.readouterr().out == "ok:2\n"


def test_main_verifies_each_sid(tmp_path, capsys):
    good, bad = tmp_path / "good.sid", tmp_path / "bad.sid"
    good.write_bytes(b"ok")
    bad.write_bytes(b"no")
    assert cli.main(["--verify", str(good), str(bad)], "d", lifted, verdict) == 0
    out = capsys.readouterr().out
    assert f"{good}: exact" in out and f"{bad}: first (7, 4) cells 2" in out


def test_main_reports_a_failure(tmp_path, capsys):
    sid = tmp_path / "a.sid"
    sid.write_bytes(b"ok")
    assert cli.main(["--verify", str(sid)], "d", lifted, failing) == 0
    assert f"{sid}: ValueError: bad" in capsys.readouterr().out
