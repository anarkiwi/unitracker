"""``unitracker-lift``: dispatch by format to the lifter, optionally optimised."""

import pytest

from unitracker import cli


def test_a_tune_is_lifted_by_its_format_and_verified(metal_warrior, tmp_path, capsys):
    raw = metal_warrior.read_bytes()
    assert cli.lifter(raw)[0] == "goattracker2"
    assert cli.verify(raw, 0, 200).size == 0
    out = tmp_path / "out.yaml"
    assert cli.lift_main([str(metal_warrior), "-o", str(out)]) == 0
    assert out.read_text() == cli.lift(raw)
    assert cli.lift_main([str(metal_warrior)]) == 0
    assert capsys.readouterr().out == out.read_text()


def test_a_tune_is_lifted_and_optimised(metal_warrior, tmp_path, capsys):
    out = tmp_path / "out.yaml"
    argv = [str(metal_warrior), "-o", str(out), "--optimise", "--ticks", "64"]
    assert cli.lift_main(argv) == 0
    assert out.read_text().startswith("unitracker:")
    assert " tokens" in capsys.readouterr().err


def test_a_tune_without_a_lifter_is_refused(tmp_path, metal_warrior, capsys, monkeypatch):
    junk = tmp_path / "junk.sid"
    junk.write_bytes(b"not a sid")
    assert cli.lift_main([str(junk)]) == 1
    assert "no tracker format recognised" in capsys.readouterr().err
    monkeypatch.delitem(cli.LIFTERS, "goattracker2")
    with pytest.raises(cli.FormatError, match="no lifter for goattracker2"):
        cli.lift(metal_warrior.read_bytes())
