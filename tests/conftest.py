"""Shared fixtures: the HVSC tunes the conformance tests lift."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from pygoattracker import constants as GT
from pygoattracker import Instrument, Orderlist, Pattern, Player, PlayPattern, Row, Song
from pygoattracker import Subtune, Table, TypedSong
from pysidtracker.testing import resolve_tune

from unitracker import backends, optimise

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


def gt_tune(wave, instruments=None, rows=None, speed=(), pulse=(), filt=()):
    """A one-pattern GoatTracker 2 tune on voice 1 as ``(decompiled tune, native player)``.

    ``wave``, ``speed``, ``pulse`` and ``filt`` are the tables' ``(left, right)`` rows;
    ``instruments`` keyword dicts of :class:`pygoattracker.Instrument` (default one instrument
    on the wavetable's first row); ``rows`` the pattern's ``(note, instrument, command, data)``
    rows, a note as an offset from ``FIRSTNOTE`` or None for a rest.
    """
    # pylint: disable=too-many-arguments,too-many-positional-arguments

    def table(pairs):
        return Table(left=[p[0] for p in pairs], right=[p[1] for p in pairs])

    instruments = instruments or [{"wave_ptr": 1, "gateoff_timer": 2, "sustain_release": 0xF0}]
    rows = rows or [(24, 1, 0, 0)] + [(None, 0, 0, 0)] * 15
    pattern = Pattern(
        rows=[
            Row(note=GT.REST if n is None else GT.FIRSTNOTE + n, instrument=i, command=c, data=d)
            for n, i, c, d in rows
        ]
    )
    song = Song(
        name="synthetic",
        subtunes=[Subtune([Orderlist([PlayPattern(k)]) for k in (0, 1, 1)])],
        instruments=[Instrument(**ins) for ins in instruments],
        patterns=[pattern, Pattern(rows=[Row(note=GT.KEYOFF)] + [Row()] * 15)],
        wavetable=table(wave),
        pulsetable=table(pulse),
        filtertable=table(filt),
        speedtable=table(speed),
    )
    build = SimpleNamespace(adparam=None, srparam=None, deftempo=None)
    info = SimpleNamespace(
        simplepulse=False,
        state=None,
        init_regs=(),
        live_vibrato=False,
        build=build,
        note_read=GT.FREQ_TABLE,
    )
    header = SimpleNamespace(name="synthetic", author="", released="", flags=0)
    dec = SimpleNamespace(info=info, typed=TypedSong.from_song(song), subtune=0, header=header)
    return dec, Player(song, freq_table=GT.FREQ_TABLE)


OPTIMISED = {
    "metal_warrior": ["hoist"],
    "crosseyed": ["instruments", "programs"],
    "csillag": ["instruments"],
}


class _Optimised(dict):
    """Each reference tune lifted and optimised on first use."""

    def __init__(self, request):
        super().__init__()
        self.request = request

    def __missing__(self, name):
        sid = self.request.getfixturevalue(name)
        ir = backends.lift(sid.read_bytes())
        self[name] = (sid, optimise.optimise(ir, OPTIMISED[name], progress=False))
        return self[name]


@pytest.fixture(scope="session")
def optimised_tunes(request) -> dict:
    """``{tune: (path, optimiser Result)}``: a reference tune lifted and optimised by the passes
    of :data:`OPTIMISED` (the full runs are measured in docs/optimise.md)."""
    return _Optimised(request)
