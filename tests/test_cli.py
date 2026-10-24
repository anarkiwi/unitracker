"""``unitracker-lift``: dispatch by format to the direct lifters, optionally optimised."""

import pytest

from unitracker import cli


def test_a_tune_is_lifted_by_its_format_and_verified(mega_run, tmp_path, capsys):
    raw = mega_run.read_bytes()
    assert cli.lifter(raw)[0] == "sidwizard"
    assert set(cli.verify(raw, 0, 200)[:, 1].tolist()) <= {0, 7, 14}
    out = tmp_path / "out.yaml"
    assert cli.lift_main([str(mega_run), "-o", str(out)]) == 0
    assert out.read_text() == cli.lift(raw)
    assert cli.lift_main([str(mega_run)]) == 0
    assert capsys.readouterr().out == out.read_text()


def test_a_goattracker_tune_is_lifted_and_optimised(metal_warrior, tmp_path, capsys):
    out = tmp_path / "out.yaml"
    argv = [str(metal_warrior), "-o", str(out), "--optimise", "--ticks", "64"]
    assert cli.lift_main(argv) == 0
    assert out.read_text().startswith("unitracker: ")
    assert " tokens" in capsys.readouterr().err


def test_a_defmon_tune_is_lifted_exactly(crosseyed):
    raw = crosseyed.read_bytes()
    assert cli.lifter(raw)[0] == "defmon" and cli.verify(raw, 0, 200).size == 0


def test_a_tune_without_a_direct_lifter_is_refused(tmp_path, mega_run, capsys, monkeypatch):
    junk = tmp_path / "junk.sid"
    junk.write_bytes(b"not a sid")
    assert cli.lift_main([str(junk)]) == 1
    assert "no tracker format recognised" in capsys.readouterr().err
    monkeypatch.delitem(cli.LIFTERS, "sidwizard")
    with pytest.raises(cli.FormatError, match="no direct lifter for sidwizard"):
        cli.lift(mega_run.read_bytes())
