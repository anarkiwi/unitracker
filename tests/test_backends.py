"""Format detection and dispatch."""

import pytest
from pydefmon import DefmonPlayer

from unitracker import backends


def test_detect_and_dispatch(crosseyed, metal_warrior):
    raw_d, raw_g = crosseyed.read_bytes(), metal_warrior.read_bytes()
    assert backends.detect(raw_d) == "defmon"
    assert backends.detect(raw_g) == "goattracker2"
    assert backends.lift(raw_d)["playroutine"]["name"] == "defmon"
    assert backends.lift(raw_g, 0)["playroutine"]["name"] == "goattracker2"
    assert isinstance(backends.native_player(raw_d), DefmonPlayer)
    assert len(backends.native_player(raw_g).render_grid(2)) == 2


def test_unknown_format(metal_warrior):
    raw = bytearray(metal_warrior.read_bytes())
    raw[0x7C:] = bytes(len(raw) - 0x7C)
    with pytest.raises(backends.UnknownFormat):
        backends.detect(bytes(raw))
