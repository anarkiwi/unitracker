"""What the lifts share: note names, meta quoting, rest runs, repeat detection and the CLI."""

import os

import numpy as np
import pytest
import yaml
from unitracker_grammar.document import Event

from unitracker.lift import cli
from unitracker.lift import shared as s


def test_a_divergence_names_its_first_cell():
    with pytest.raises(s.Divergence, match="frame 3 register 5"):
        s.checked((np.array([[3, 5, 0, 1]]), ""), 10)
    assert s.checked((np.array([]), "text"), 10) == "text"


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


def _song(end: str) -> str:
    return (
        "unitracker:2.3\nmeta:{title:T}\nvoices:1\nins:\n"
        " i1:{ad:9,sr:240,ctrl:[{ctrl:65,note:+0},end]}"
        f"\nphrase:\n p1:[C4.i1,-*3]\nsong:\n - play:[p1]\n - {end}\n"
    )


def _grid(text: str, n: int) -> np.ndarray:
    return np.asarray(s.play(text, n), dtype=np.uint8)


@pytest.mark.parametrize("end, played", [("{loop:0}", 40), ("end", 24)])
def test_a_looping_document_is_checked_against_a_reference_played_alongside(end, played):
    """The reference of a looping document is asked for exactly the frames checked, in a worker;
    one that ends is asked for the frames it played."""
    text = _song(end)

    def reference(n):
        assert n == played
        return _grid(text, 40)[:n]

    assert not s.cells_of(text, 40, reference=reference).size
    bad = _grid(text, 40)
    bad[3, 2] ^= 1
    cells = s.cells_of(text, 40, reference=lambda n: bad[:n])
    assert [tuple(int(x) for x in c[:2]) for c in cells] == [(3, 2)]


def test_a_started_reference_is_played_in_its_worker_before_the_document_is():
    """A Reference runs its function over the frames asked at construction, in another process,
    and cells_of takes its grid however many frames the document played."""
    text = _song("{loop:0}")
    parent = os.getpid()
    good = _grid(text, 40)

    def reference(n):
        assert os.getpid() != parent
        return good[:n]

    job = s.Reference(reference, 40)
    assert not s.cells_of(text, 40, reference=job).size
    bad = good.copy()
    bad[5, 0] ^= 4
    job = s.Reference(lambda n: bad[:n], 40)
    assert [tuple(int(x) for x in c[:2]) for c in s.cells_of(text, 40, reference=job)] == [(5, 0)]


def test_a_started_reference_ends_when_the_document_cannot_be_played(monkeypatch):
    closed = []
    close = s.Reference.close
    monkeypatch.setattr(s.Reference, "close", lambda self: (closed.append(self), close(self)))
    monkeypatch.setattr(s, "play", lambda *_: (_ for _ in ()).throw(RuntimeError("no play")))
    job = s.Reference(lambda n: np.zeros((n, 25), dtype=np.uint8), 4)
    with pytest.raises(RuntimeError, match="no play"):
        s.cells_of(_song("end"), 4, reference=job)
    assert closed == [job]
