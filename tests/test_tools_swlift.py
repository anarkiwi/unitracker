"""The SID-Wizard transliteration's tools: the divergence report and the sample check."""

import csv

import pytest
from pysidwizard import Row
from test_direct_swlift import META, E, lead, tune

import swdiff
import swsample
from unitracker.direct import swlift as sw


@pytest.fixture(name="module")
def fixture_module(monkeypatch):
    """A module the raw entry points read in place of a SID: ``swm`` and its sid path."""

    def use(swm):
        monkeypatch.setattr(sw, "parse_sid_song", lambda raw, song: swm)
        monkeypatch.setattr(swdiff, "parse_sid_song", lambda raw, song: swm)
        monkeypatch.setattr(sw, "_meta", lambda raw: META)
        monkeypatch.setattr(swsample, "parse_sid", lambda raw: swm)
        return swm

    return use


def test_the_report_is_exact_or_names_each_register(module, tmp_path, capsys):
    """An exact tune reports so; a kept pulse walk's first differing tick, with the voices'."""
    sid = tmp_path / "t.sid"
    sid.write_bytes(b"")
    module(tune([Row(note=49, instrument=1), E, E, E], [lead()]))
    assert swdiff.report(b"", 0, 200, 1, []) == "exact"
    rows = [Row(note=49, instrument=1), E, Row(note=49), E, Row(note=49), E]
    module(tune(rows, [lead(control=0x5A, pw_table=bytes.fromhex("880000 041000 fe2300"))]))
    assert swdiff.main([str(sid), "--frames", "300", "--regs", "v1_pw_lo"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("v1_pw_lo: first ") and " * " in text
    assert swdiff.report(b"", 0, 300, 0, ["v1_ctrl"]) == ""


def test_the_sample_checks_counts_and_summarises(module, tmp_path, capsys):
    """Each tune's verdict, sizes and gaps, the verdict counts and the size quantiles."""
    module(tune([Row(note=49, instrument=1), E, Row(note=50), E], [lead(control=0x5A)]))
    for name in ("a.sid", "b.sid"):
        (tmp_path / name).write_bytes(b"")
    listing = tmp_path / "list.csv"
    with listing.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["path", "md5", "player", "songs", "clock", "lengths"])
        writer.writerow(["a.sid", "", "Hermit/SidWizard_V1.x", 1, "PAL", "4"])
        writer.writerow(["b.sid", "", "GoatTracker_V2.x", 1, "PAL", "4"])
        writer.writerow(["c.sid", "", "Hermit/SidWizard_V1.x", 1, "PAL", ""])
    out = tmp_path / "sample.csv"
    args = [str(listing), "--hvsc", str(tmp_path), "--jobs", "1", "--out", str(out)]
    assert swsample.main(args) == 0
    rows = list(csv.DictReader(out.open()))
    assert [r["verdict"] for r in rows] == ["differing", "failed"]
    assert rows[0]["frames"] == str(swsample.frames_of("4", 60000)) and int(rows[0]["tokens"]) > 0
    assert "a note keeping its pulse walk, which pauses:" in rows[0]["gaps"]
    printed = capsys.readouterr().out
    assert printed.startswith("exact 0 differing 1 failed 1\nbytes per native byte: p10 ")
    assert "1 failed: FileNotFoundError" in printed


def test_why_explains_the_first_differing_cell(module, monkeypatch, tmp_path, capsys):
    """The reference grid handed to pysidtracker is the document's after the reference's lead;
    an exact tune has nothing to explain."""
    seen = {}

    class Found:  # pylint: disable=too-few-public-methods
        """A divergence's account."""

        @staticmethod
        def text(width):
            """Its text at ``width``."""
            return f"explained at {width}"

    def explain(raw, player, reference, **kwargs):
        seen.update(reference=reference, player=player(raw), **kwargs)
        return Found()

    monkeypatch.setattr(swdiff, "explain_divergence", explain)
    module(tune([Row(note=49, instrument=1), E], [lead()]))
    sid = tmp_path / "t.sid"
    sid.write_bytes(b"")
    assert swdiff.main([str(sid), "--frames", "40", "--why"]) == 0
    assert capsys.readouterr().out == "explained at 100\n"
    assert seen["reference"].shape == (42, 25) and seen["label"] == "pysidwizard"
