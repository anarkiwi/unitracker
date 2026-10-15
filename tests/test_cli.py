"""The two command-line tools."""

from unitracker import cli, songio


def test_lift_check_and_render(metal_warrior, tmp_path, capsys):
    out = tmp_path / "mw.yaml"
    assert cli.gt_main([str(metal_warrior), "-o", str(out)]) == 0
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
    cli.gt_main([str(metal_warrior), "-o", str(out)])
    song = songio.load(out)
    song["records"]["ins"][1]["sr"] = 0x77
    songio.dump(song, out)
    assert cli.play_main([str(out), "--seconds", "1", "--check", str(metal_warrior)]) == 1
    assert "mismatch: tick" in capsys.readouterr().out


def test_lift_to_stdout(metal_warrior, capsys):
    assert cli.gt_main([str(metal_warrior)]) == 0
    assert songio.loads(capsys.readouterr().out)["unitracker"] == 0
