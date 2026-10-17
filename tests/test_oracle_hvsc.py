"""tools/oracle_hvsc.py: the survey renderer plays a tune through its written document."""

from pathlib import Path

from pysidtracker.survey import load_render

from unitracker.format import songio
from unitracker.lift import backends
from unitracker.player.vm import SongPlayer, first_mismatch

TOOL = str(Path(__file__).resolve().parent.parent / "tools" / "oracle_hvsc.py")


def test_the_renderer_plays_the_lifted_document_as_the_native_player(metal_warrior):
    raw = metal_warrior.read_bytes()
    grid, info = load_render(TOOL)(raw, 1, 300)
    assert grid.shape == (300, 25) and grid.dtype.name == "int16"
    assert info["backend"] == "goattracker2" and info["chars"] > 0
    song = SongPlayer(songio.loads(songio.dumps(backends.lift(raw, 0))))
    assert (grid == song.render_grid(300)).all()
    native = backends.native_player(raw, 0).render_grid(300)
    assert first_mismatch(grid, native, song.starts) is None
