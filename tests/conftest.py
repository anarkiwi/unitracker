"""Shared fixtures: the HVSC tunes the conformance tests lift."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from pygoattracker import (
    Instrument,
    Orderlist,
    Pattern,
    Player,
    PlayPattern,
    Row,
    Song,
    Subtune,
    Table,
    TypedSong,
)
from pygoattracker import constants as GT
from pysidtracker.testing import oracle_grid, resolve_tune
from pysidwizard import parse_sid_song
from pysidwizard.player import INST_WF_TABLE_POS

from unitracker import optimise
from unitracker.lift import backends

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


def sw_fx(raw: bytes, rows: dict):
    """SID-Wizard tune ``raw``'s first subtune with each ``{(pattern, row): (fx, value)}`` set."""
    swm = parse_sid_song(raw, 1)
    for (pattern, k), (fx, value) in rows.items():
        row = swm.patterns[pattern - 1].rows[k]
        row.fx, row.fx_value = fx, value
    return swm


def sw_table_byte(raw: bytes, instrument: int, table: str, offset: int, value: int) -> bytes:
    """SID-Wizard tune ``raw`` with byte ``offset`` of instrument ``instrument``'s ``pw`` or
    ``filter`` table set to ``value``; the instruments lie in order in the file."""
    pos, instruments = -1, parse_sid_song(raw, 1).instruments[:instrument]
    for one in instruments:
        pos = raw.index(one.memory_image(), pos + 1)
    ins = instruments[-1]
    start = pos + INST_WF_TABLE_POS + len(ins.wf_table) + 1
    if table == "filter":
        start += len(ins.pw_table) + 1
    out = bytearray(raw)
    out[start + offset] = value
    return bytes(out)


def oracle(raw: bytes, name: str, frames: int):
    """The sidtrace 6502 oracle's register grid of tune ``raw`` (Docker), cached by content."""
    path = CACHE / "oracle" / f"{name}-{hashlib.sha256(raw).hexdigest()[:16]}.sid"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    grid = oracle_grid(path, oracle_cache=CACHE / "oracle", seconds=frames // 50 + 2, frames=frames)
    return np.asarray(grid, dtype=np.uint8)[:, :25]


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
