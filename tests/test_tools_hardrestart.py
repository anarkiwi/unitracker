"""tools/hardrestart.py: hard restarts on synthetic and native register grids."""

import json

import numpy as np
import pytest

import hardrestart as H

PLAIN = (0x41, 0x09, 0xA8)


def grid(*voices, frames: int = 12) -> np.ndarray:
    """A register grid whose voice i plays ``voices[i]``: ``(ctrl, ad, sr)`` per frame, the
    last held to ``frames``."""
    out = np.zeros((frames, 25), np.int16)
    for i, rows in enumerate(voices):
        rows = [*rows, *[rows[-1]] * (frames - len(rows))]
        out[:, 7 * i + 4 : 7 * i + 7] = rows
    return out


def note(lead: list, onset=PLAIN, pad: int = 6) -> list:
    """A voice holding ``onset`` for ``pad`` frames, then ``lead``'s, then ``onset`` again."""
    return [onset] * pad + lead + [onset]


def one(rows, k: int = 4, clock=None) -> dict:
    """:func:`hardrestart.analyse` of a one-voice grid."""
    return H.analyse(grid(rows, frames=len(rows) + 2), k, clock)


def only(rec: dict) -> str:
    """The single class ``rec``'s onsets fell in."""
    (cls,) = [c for c, n in rec["classes"].items() if n]
    assert rec["classes"][cls] == rec["onsets"] == 1
    return cls


def test_onsets_are_gate_rises_and_test_clears_under_a_held_gate():
    gt2 = [(0x40, 0, 0), (0x40, 0, 0), (0x09, 0, 0), (0x41, 0, 0)]
    regate = [(0x41, 0, 0), (0x49, 0, 0), (0x49, 0, 0), (0x41, 0, 0)]
    tested = [(0x48, 0, 0), (0x49, 0, 0), (0x41, 0, 0), (0x41, 0, 0)]
    t, v = H.onsets(H.voices(grid(gt2, regate, tested, frames=6))[0])
    assert sorted(zip(t.tolist(), v.tolist())) == [(1, 2), (2, 0), (3, 1)]


def test_classic_restart_zeroes_ad_and_sr_with_the_gate_off():
    rec = one(note([(0x40, 0, 0), (0x40, 0, 0)]))
    assert only(rec) == "classic" and rec["leads"]["classic"] == [0, 0, 1, 0, 0]
    assert rec["values"] == {"0000": 2} and rec["dominant_lead"] == 2 and rec["consistent"]
    assert rec["features"]["ad_zero"] == [1, 1, 0, 0]
    assert rec["features"]["gate_off"] == [1, 1, 0, 0] and rec["gate_off"] == [0, 0, 1, 0, 0]
    assert rec["safe"]["classic"] == 1 and rec["wrap"] == 2


def test_lowering_only_sr_is_sr_only_and_safe_while_the_gate_is_off():
    rec = one(note([(0x40, 0x0F, 0), (0x40, 0x0F, 0)]))
    assert only(rec) == "sr-only" and rec["values"] == {"0F00": 2}
    assert rec["rate_safe"] == [0, 0, 1, 0, 0] and rec["safe"]["sr-only"] == 1


def test_one_frame_restart_is_below_the_prescaler_wrap():
    rec = one(note([(0x40, 0x00, 0x80)]))
    assert only(rec) == "classic" and rec["dominant_lead"] == 1
    assert rec["rate_safe"][1] == 1 and rec["safe"]["classic"] == 0


def test_lowering_only_ad_is_ad_only():
    assert only(one(note([(0x40, 0x00, 0xA8)] * 2))) == "ad-only"


def test_test_bit_regate_without_envelope_change():
    rec = one(note([(0x49, 0x09, 0xA8)]))
    assert only(rec) == "test-bit" and rec["gate_off"][0] == 1
    assert rec["features"]["test"] == [1, 0, 0, 0]


def test_gate_off_alone_keeps_a_slow_release_unsafe():
    rec = one(note([(0x40, 0x09, 0xA8)] * 2))
    assert only(rec) == "gate-off" and rec["leads"]["gate-off"][2] == 1
    assert rec["restarts"] == 0 and rec["dominant_lead"] is None and not rec["consistent"]
    assert rec["rate_safe"][0] == 1


def test_a_state_filling_the_window_is_held_not_a_restart():
    assert only(one(note([(0x40, 0x09, 0xA8)] * 4))) == "none"
    assert only(one(note([(0x40, 0, 0)] * 6))) == "none"
    assert only(one(note([(0x40, 0, 0)] * 6), k=8)) == "classic"


def test_a_lead_reaches_back_only_to_the_voices_onset_before():
    rows = [(0x40, 0, 0)] * 2 + [(0x41, 9, 0xA8)] + [(0x40, 0, 0)] * 2 + [(0x41, 9, 0xA8)]
    rec = one(rows, k=8)
    assert rec["onsets"] == 2 and rec["classes"]["classic"] == rec["classes"]["none"] == 1
    assert rec["leads"]["classic"][2] == 1 and rec["features"]["gate_off"][:4] == [2, 2, 0, 0]
    assert H.previous(np.array([5, 2, 9, 4]), np.array([0, 0, 0, 1])).tolist() == [2, 0, 5, 0]


def test_the_note_befores_lower_envelope_is_not_lowered():
    rows = [(0x41, 0, 0x44)] * 6 + [(0x40, 0, 0x44)] * 2 + [(0x41, 0, 0xF3)]
    assert only(one(rows)) == "gate-off"


def test_voices_are_measured_apart():
    rec = H.analyse(grid(note([(0x40, 0, 0)] * 2), note([(0x40, 9, 0xA8)] * 2)))
    assert rec["voices"] == [1, 1, 0] and rec["classes"]["classic"] == 1
    assert rec["classes"]["gate-off"] == 1 and rec["test_at_onset"] == 0


@pytest.mark.parametrize("clock,frames", [("PAL", 2), ("NTSC", 2), (None, 2), ("weird", 2)])
def test_wrap_frames(clock, frames):
    assert H.wrap_frames(clock) == frames
    assert H.WRAP / H.CYCLES["PAL"] > 1


def test_sample_caps_each_player_reproducibly():
    rows = [{"path": f"{p}{i}", "player": p} for p in "ab" for i in range(5)] + [
        {"path": "c0", "player": "c"}
    ]
    picked = H.sample(rows, 2, seed=1)
    assert picked == H.sample(rows, 2, seed=1)
    assert [r["player"] for r in picked] == ["a", "a", "b", "b", "c"]
    assert H.sample(rows, 0, seed=1) == rows


def test_summary_pools_each_player():
    good = {"path": "x", "player": "P"} | one(note([(0x40, 0, 0)] * 2))
    text = H.summary([good, good, {"path": "y", "player": "P", "error": "boom"}])
    assert text.startswith("P: 2 tunes with onsets, 1 failed or silent")
    assert "hard restart on most onsets: 100%" in text and "classic 100%" in text
    assert "0000:4" in text and H.summary([{"player": "Q", "error": "e"}]).count("\n") == 0


def test_main_measures_a_native_tune(crosseyed, tmp_path, capsys):
    out = tmp_path / "hr.json"
    assert H.main([str(crosseyed), "--frames", "400", "--jobs", "1", "--out", str(out)]) == 0
    (rec,) = json.loads(out.read_text())
    assert rec["path"] == str(crosseyed) and rec["onsets"] == sum(rec["classes"].values())
    assert sum(rec["rate_safe"]) == rec["onsets"] > 0
    assert "tunes with onsets" in capsys.readouterr().out
