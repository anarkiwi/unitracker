"""tools/reference_check.py: each reference tune's verdict and its differing registers."""

import importlib.util
import json
from pathlib import Path

from unitracker.lift import verify

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "reference_check", ROOT / "tools" / "reference_check.py"
)
reference_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reference_check)

VERDICT = verify.Verdict(
    ticks=8,
    played=8,
    starts=(2, 3, None),
    onset=(2, 6),
    whole=(1, 0),
    cells=((1, 0, 5, 0), (2, 6, 9, 0), (5, 6, 9, 2)),
)


def install_fake_lift(monkeypatch, verdict=VERDICT):
    """A lift that already holds its generic document and verdict."""

    class FakeLift:  # pylint: disable=too-few-public-methods
        """A lift that already holds its generic document and verdict."""

        document = "unitracker: 1\nmeta: {title: T}\nsong: []\n"
        gap = None

        def __init__(self):
            self.verdict = verdict

        def run(self):
            return {}

    monkeypatch.setattr(reference_check.backends, "lifter", lambda raw: FakeLift())


def test_the_check_line_names_the_differing_registers_and_their_values(monkeypatch, tmp_path):
    install_fake_lift(monkeypatch)
    sid = tmp_path / "t.sid"
    sid.write_bytes(b"PSID\x01")
    out = tmp_path / "t.yaml"
    line, verdict = reference_check.check(sid, out, [])
    assert verdict is VERDICT
    assert line == (
        "mismatch: tick 2, register $06 (from tick 0: tick 1, $00)"
        " | $00 v1 freq lo: 1 tick 1 (doc 05, native 00); "
        "$06 v1 sr: 2 ticks 2-5 (doc 09, native 00)"
        "; forbidden: none"
    )


def test_a_verified_lift_and_a_gap_keep_their_lines(monkeypatch, tmp_path):
    install_fake_lift(monkeypatch, verify.Verdict(8, 8, (0, 0, 0), None, None, ()))
    sid = tmp_path / "t.sid"
    sid.write_bytes(b"PSID\x01")
    line, verdict = reference_check.check(sid, tmp_path / "t.yaml", [])
    assert verdict.ok and line == "verified: 8 ticks play the native grid; forbidden: none"

    class Gap:  # pylint: disable=too-few-public-methods
        """A lift that ends with a lifting gap instead of a document."""

        document = None
        gap = "ins i1: no generic form"
        verdict = None

        def run(self):
            return {}

    monkeypatch.setattr(reference_check.backends, "lifter", lambda raw: Gap())
    line, verdict = reference_check.check(sid, tmp_path / "t.yaml", [])
    assert verdict is None and line == "no generic document: ins i1: no generic form"


def test_main_writes_the_machine_readable_verdicts(monkeypatch, tmp_path, capsys):
    install_fake_lift(monkeypatch)
    sid = tmp_path / "t.sid"
    sid.write_bytes(b"PSID\x01")
    docs = tmp_path / "docs"
    js = tmp_path / "verdicts.json"
    assert reference_check.main([str(docs), "--tunes", str(sid), "--json", str(js)]) == 0
    assert (docs / "t.yaml").is_file()
    verdicts = json.loads(js.read_text(encoding="utf-8"))
    assert verdicts["t"]["ok"] is False and verdicts["t"]["first"] == [2, 6]
    assert [e["reg"] for e in verdicts["t"]["registers"]] == [0, 6]
    assert "mismatch: tick 2, register $06" in capsys.readouterr().out
