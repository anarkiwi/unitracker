"""Shared fixtures: the HVSC tunes the conformance tests lift."""

from pathlib import Path

import pytest
from pysidtracker.testing import resolve_tune

METAL_WARRIOR = "MUSICIANS/C/Cadaver/Metal_Warrior_Title_Remix.sid"
CACHE = Path(__file__).resolve().parent.parent / ".tunecache"


@pytest.fixture(scope="session")
def metal_warrior() -> Path:
    """Path to the Metal Warrior Title Remix .sid (local $HVSC, cache, or mirror)."""
    path = resolve_tune(METAL_WARRIOR, cache_dir=CACHE)
    if path is None:
        pytest.skip("HVSC tune unavailable")
    return Path(path)


def mini_song(playroutine, tracks=None, phrases=None, **extra) -> dict:
    """A one-voice song around ``playroutine``."""
    song = {
        "unitracker": "ir",
        "clock": {"cycles_per_tick": 19656, "clock_hz": 985248},
        "voices": 1,
        "tracks": tracks or [{"plays": [{"phrase": "a"}], "loop": 0}],
        "phrases": phrases or {"a": [{}]},
        "playroutine": playroutine,
    }
    song.update(extra)
    return song


CROSSEYED = "MUSICIANS/I/Ilkke/Crosseyed.sid"


@pytest.fixture(scope="session")
def crosseyed() -> Path:
    """Path to iLKke's Crosseyed .sid, a defMON replay."""
    path = resolve_tune(CROSSEYED, cache_dir=CACHE)
    if path is None:
        pytest.skip("HVSC tune unavailable")
    return Path(path)


CSILLAG = "MUSICIANS/H/Hermit/Csillag_szazketto_kettoskereszt.sid"


@pytest.fixture(scope="session")
def csillag() -> Path:
    """Path to Hermit's Csillag szazketto kettoskereszt, a SID-Wizard export."""
    path = resolve_tune(CSILLAG, cache_dir=CACHE)
    if path is None:
        pytest.skip("HVSC tune unavailable")
    return Path(path)
