"""tools/solvertime.py: the solver's time against the music's."""

from types import SimpleNamespace

import solvertime as T
import writeorder as W


def test_solve_reports_the_music_length_and_the_multiple_of_real_time(metal_warrior, tmp_path):
    args = SimpleNamespace(backend="auto", frames=120, cache=tmp_path / "cache")
    init, frames = W.capture({"path": str(metal_warrior)}, None, args)
    rec = T.solve(init, frames, 5)
    assert rec["frames"] == len(frames) and rec["cases"] > 0
    assert abs(rec["music_s"] - len(frames) * 19656 / 985248) < 0.1
    assert rec["x_real"] == rec["music_s"] / rec["solver_s"] > 1
