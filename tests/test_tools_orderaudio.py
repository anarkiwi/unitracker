"""tools/orderaudio.py: the cases an order changes, rendered from the stream's own chip."""

from types import SimpleNamespace

import numpy as np
import orderaudio as A
from vagrant_moses import sidorder as S


def arguments(tmp_path, **kw):
    """The arguments of :func:`orderaudio.measure` for a short capture."""
    return SimpleNamespace(
        backend="auto",
        frames=150,
        cache=tmp_path / "cache",
        horizon=5,
        order="ctrl:2,ctrl,ad,sr:2,sr",
        wav=None,
        **kw,
    )


def test_measure_lists_the_changed_cases(metal_warrior, tmp_path):
    cases = A.measure({"path": str(metal_warrior)}, None, arguments(tmp_path))["cases"]
    assert cases and all("wav" not in c and c["ms"] >= 0 for c in cases)


def test_replay_state_is_the_chip_at_the_frame_start(metal_warrior, tmp_path):
    args = arguments(tmp_path)
    tl = S.Timeline(*A.W.capture({"path": str(metal_warrior)}, None, args), A.mm.PAL.clock_hz)
    replay = A.Replay(tl)
    state = replay.state(3)
    assert replay.state(3) == state and replay.state(5) != state
    ref = A.SID(clock_hz=tl.clock, sampling_hz=A.RATE, method="resample")
    ref.run(tl.init, tl.starts[0])
    for f in range(3):
        ref.run(tl.frames[f][1], tl.starts[f + 1])
    assert ref.save_state() == state


def test_wav_is_written_for_a_differing_pair(metal_warrior, tmp_path):
    args = arguments(tmp_path, jobs=1)
    args.wav = tmp_path / "wav"
    cases = A.measure({"path": str(metal_warrior)}, None, args)["cases"]
    names = [c["wav"] for c in cases if "wav" in c]
    assert names and all((args.wav / n).stat().st_size > 44 for n in names)
    assert np.asarray([c["ms"] for c in cases if "wav" in c]).all()
