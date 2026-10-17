"""The two command-line tools."""

from unitracker import cli
from unitracker.format import songio


def test_lift_check_and_render(metal_warrior, tmp_path, capsys):
    out = tmp_path / "mw.yaml"
    assert cli.lift_main([str(metal_warrior), "-o", str(out)]) == 0
    wav = tmp_path / "mw.wav"
    assert (
        cli.play_main(
            [str(out), "--seconds", "2", "--check", str(metal_warrior), "--wav", str(wav)]
        )
        == 0
    )
    text = capsys.readouterr().out
    assert "match: 100 ticks" in text and wav.stat().st_size > 10000


def test_check_reports_mismatch(metal_warrior, tmp_path, capsys):
    out = tmp_path / "mw.yaml"
    cli.lift_main([str(metal_warrior), "-o", str(out)])
    song = songio.load(out)
    song["records"]["ins"][1]["sr"] = 0x77
    songio.dump(song, out)
    assert cli.play_main([str(out), "--seconds", "1", "--check", str(metal_warrior)]) == 1
    assert "mismatch: tick" in capsys.readouterr().out


def test_lift_to_stdout(metal_warrior, capsys):
    assert cli.lift_main([str(metal_warrior)]) == 0
    assert songio.loads(capsys.readouterr().out)["unitracker"] == "ir"


def test_lift_and_check_defmon(crosseyed, tmp_path, capsys):
    out = tmp_path / "ce.yaml"
    assert cli.lift_main([str(crosseyed), "-o", str(out)]) == 0
    assert cli.play_main([str(out), "--seconds", "3", "--check", str(crosseyed)]) == 0
    assert "match: 150 ticks" in capsys.readouterr().out


def test_lift_and_check_sidwizard(csillag, tmp_path, capsys):
    out = tmp_path / "cs.yaml"
    assert cli.lift_main([str(csillag), "-o", str(out)]) == 0
    assert cli.play_main([str(out), "--seconds", "3", "--check", str(csillag)]) == 0
    assert "match: 150 ticks" in capsys.readouterr().out
