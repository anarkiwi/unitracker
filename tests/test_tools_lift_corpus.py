"""The corpus lift tool and the defMON state trace."""

import dmtrace
import lift_corpus
import numpy as np
import pytest
from pysidtracker.testing import resolve_tune

GLOW = "MUSICIANS/I/Ilkke/Glow_Worm.sid"
CAR = "MUSICIANS/G/Goto80/Rent-A-Cop.sid"
RENT = "MUSICIANS/F/F7sus4/Timewarp_Spheres_2SID.sid"
METAL = "MUSICIANS/C/Cadaver/Metal_Warrior_Title_Remix.sid"


def test_a_tune_is_exact_refused_or_another_format(glow_worm, timewarp, metal_warrior):
    assert glow_worm and timewarp and metal_warrior
    exact = lift_corpus.classify(GLOW, 300, 120, "defmon")
    assert (exact["verdict"], exact["detail"]) == ("exact", "")
    assert exact["sizes"]["native"]["instruments"] == 3
    refused = lift_corpus.classify(RENT, 300, 120, "defmon")
    assert (refused["verdict"], refused["detail"]) == ("refused", "multi-SID")
    other = lift_corpus.classify(METAL, 300, 120, "defmon")
    assert (other["verdict"], other["detail"]) == ("other", "goattracker2")


def test_a_missing_tune_and_a_failing_lift_are_named(glow_worm, monkeypatch):
    assert glow_worm
    monkeypatch.setattr(lift_corpus, "resolve_tune", lambda _: None)
    assert lift_corpus.classify("x.sid", 10, 10)["verdict"] == "missing"
    monkeypatch.setattr(lift_corpus, "resolve_tune", resolve_tune)
    monkeypatch.setattr(lift_corpus, "find_format", lambda _: type("F", (), {"name": "nope"})())
    got = lift_corpus.classify(GLOW, 10, 10)
    assert got["verdict"] == "error" and "ModuleNotFoundError" in got["detail"]


def test_a_divergent_document_is_counted(glow_worm, monkeypatch):
    assert glow_worm
    from unitracker.lift import defmon  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(defmon, "verify", lambda *_: (np.array([[4, 2, 0, 1], [5, 2, 0, 1]]), ""))
    got = lift_corpus.classify(GLOW, 10, 10, "defmon")
    assert got["verdict"] == "diverged" and got["detail"] == "2 cells, first (4, 2)"


def test_the_corpus_prints_its_counts(glow_worm, timewarp, tmp_path, capsys):
    assert glow_worm and timewarp
    names = tmp_path / "names.txt"
    names.write_text(f'"{GLOW}"\n{RENT}\n')
    out = tmp_path / "out.json"
    argv = [str(names), "--format", "defmon", "--frames", "200", "--jobs", "2", "--out", str(out)]
    argv += ["--subtunes"]
    assert lift_corpus.main(argv) == 0
    text = capsys.readouterr().out
    assert "'exact': 1" in text and "'refused': 1" in text and "multi-SID" in text
    assert "instruments 3/3" in text and "bytes " in text
    assert out.read_text().count('"verdict"') == 2


def test_the_trace_follows_the_cells_of_the_spec_player(glow_worm, capsys):
    rows = list(dmtrace.trace(glow_worm.read_bytes(), 4, ["trigger", "cursor", "detune@2"]))
    assert [r[0] for r in rows] == [0, 1, 2, 3]
    assert rows[0][1:] == (0x80, 0, 2) and rows[2][1] == 0
    assert dmtrace.main([GLOW, "2", "cursor"]) == 0
    assert capsys.readouterr().out.split()[:2] == ["frame", "cursor"]


@pytest.mark.parametrize("frames", [3])
def test_the_trace_names_a_cell_per_voice(glow_worm, frames):
    rows = list(dmtrace.trace(glow_worm.read_bytes(), frames, ["vbit@0", "vbit@1", "vbit@2"]))
    assert all(r[1:] == (1, 2, 4) for r in rows)


def test_every_subtune_is_checked_and_an_oversized_document_is_named(rent_a_cop, monkeypatch):
    assert rent_a_cop
    from unitracker.lift import defmon  # pylint: disable=import-outside-toplevel
    from unitracker.lift.invariants import ModelError  # pylint: disable=import-outside-toplevel

    seen = []

    def verify(raw, subtune, frames):
        seen.append(subtune)
        if subtune == 1:
            raise ModelError("9 lifted phrases > 3 native")
        return np.array([]), ""

    monkeypatch.setattr(defmon, "verify", verify)
    got = lift_corpus.classify(CAR, 10, 10, "defmon", True)
    assert seen == [0, 1] and got["verdict"] == "oversized" and got["detail"].endswith("@1")
