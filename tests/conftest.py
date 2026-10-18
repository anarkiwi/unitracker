"""Shared fixtures: the HVSC reference tunes, each lifted once per session, and synthetic tunes."""

import os
import pickle
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from filelock import FileLock
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
from pysidtracker.survey import file_oracle
from pysidtracker.testing import resolve_tune
from pysidwizard import parse_sid_song
from pysidwizard.player import INST_WF_TABLE_POS

from unitracker import optimise
from unitracker.format import songio
from unitracker.lift import backends

CACHE = Path(__file__).resolve().parent.parent / ".tunecache"


def hvsc_tune(name: str, tune: str, doc: str):
    """A session fixture ``name``: the path to HVSC ``tune`` (local $HVSC, cache, or mirror)."""

    def fixture() -> Path:
        path = resolve_tune(tune, cache_dir=CACHE)
        if path is None:
            pytest.skip("HVSC tune unavailable")
        return Path(path)

    fixture.__doc__ = doc
    return pytest.fixture(scope="session", name=name)(fixture)


metal_warrior = hvsc_tune(
    "metal_warrior",
    "MUSICIANS/C/Cadaver/Metal_Warrior_Title_Remix.sid",
    "Path to the Metal Warrior Title Remix .sid.",
)
crosseyed = hvsc_tune(
    "crosseyed",
    "MUSICIANS/I/Ilkke/Crosseyed.sid",
    "Path to iLKke's Crosseyed .sid, a defMON replay.",
)
csillag = hvsc_tune(
    "csillag",
    "MUSICIANS/H/Hermit/Csillag_szazketto_kettoskereszt.sid",
    "Path to Hermit's Csillag szazketto kettoskereszt, a SID-Wizard export.",
)


def table(*rows) -> Table:
    """A GoatTracker 2 table of ``(left, right)`` ``rows``."""
    return Table(left=[r[0] for r in rows], right=[r[1] for r in rows])


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


def sw_fx(raw: bytes, rows: dict):
    """SID-Wizard tune ``raw``'s first subtune with each ``{(pattern, row): (fx, value)}`` set."""
    swm = parse_sid_song(raw, 1)
    for (pattern, k), (fx, value) in rows.items():
        row = swm.patterns[pattern - 1].rows[k]
        row.fx, row.fx_value = fx, value
    return swm


def sw_table_byte(raw: bytes, instrument: int, which: str, offset: int, value: int) -> bytes:
    """SID-Wizard tune ``raw`` with byte ``offset`` of instrument ``instrument``'s ``pw`` or
    ``filter`` table set to ``value``; the instruments lie in order in the file."""
    pos, instruments = -1, parse_sid_song(raw, 1).instruments[:instrument]
    for one in instruments:
        pos = raw.index(one.memory_image(), pos + 1)
    ins = instruments[-1]
    start = pos + INST_WF_TABLE_POS + len(ins.wf_table) + 1
    if which == "filter":
        start += len(ins.pw_table) + 1
    out = bytearray(raw)
    out[start + offset] = value
    return bytes(out)


def oracle(raw: bytes, frames: int):
    """The 6502 oracle's register grid of tune ``raw``, cached by content under the tune cache."""
    return np.asarray(file_oracle(raw, 1, frames, work=CACHE / "oracle")[1], dtype=np.uint8)


def gt_tune(wave, instruments=None, rows=None, speed=(), pulse=(), filt=()):
    """A one-pattern GoatTracker 2 tune on voice 1 as ``(decompiled tune, native player)``.

    ``wave``, ``speed``, ``pulse`` and ``filt`` are the tables' ``(left, right)`` rows;
    ``instruments`` keyword dicts of :class:`pygoattracker.Instrument` (default one instrument
    on the wavetable's first row); ``rows`` the pattern's ``(note, instrument, command, data)``
    rows, a note as an offset from ``FIRSTNOTE`` or None for a rest.
    """
    # pylint: disable=too-many-arguments,too-many-positional-arguments
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
        wavetable=table(*wave),
        pulsetable=table(*pulse),
        filtertable=table(*filt),
        speedtable=table(*speed),
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
REFERENCE_TICKS = {
    "metal_warrior": 2 * 3840,
    "crosseyed": 95 + 2 * 1152 + 100,
    "csillag": 6 * 2304 + 600,
}


def once(factory, key: str, make):
    """``make()``, computed once per test session and shared by every xdist worker."""
    base = factory.getbasetemp()
    root = base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base
    path = root / f"once-{key}.pkl"
    with FileLock(f"{path}.lock"):
        if path.is_file():
            return pickle.loads(path.read_bytes())
        value = make()
        path.write_bytes(pickle.dumps(value))
    return value


@dataclass(frozen=True)
class Reference:
    """A reference tune lifted once: its path, bytes, canonical document and native grid."""

    sid: Path
    raw: bytes
    text: str
    native: np.ndarray

    def ir(self) -> dict:
        """A fresh copy of the lifted song."""
        return songio.loads(self.text)


class _Shared(dict):
    """Each reference tune's ``make(name, sid, *needs(name))`` on first use, shared across
    workers; ``needs`` runs before the lock, so no lock is taken while another is held."""

    def __init__(self, request, kind, make, needs=lambda name: ()):
        super().__init__()
        self.request, self.kind, self.make, self.needs = request, kind, make, needs

    def __missing__(self, name):
        sid, args = self.request.getfixturevalue(name), self.needs(name)
        factory = self.request.getfixturevalue("tmp_path_factory")
        self[name] = once(factory, f"{self.kind}-{name}", lambda: self.make(name, sid, *args))
        return self[name]


def _reference(name, sid):
    raw = sid.read_bytes()
    grid = backends.native_player(raw).render_grid(REFERENCE_TICKS[name])
    return Reference(sid, raw, songio.dumps(backends.lift(raw)), np.asarray(grid, np.uint8))


@pytest.fixture(scope="session")
def references(request) -> dict:
    """``{tune: Reference}``: each reference tune lifted once per session."""
    return _Shared(request, "reference", _reference)


@pytest.fixture(scope="session")
def optimised_tunes(request) -> dict:
    """``{tune: (path, optimiser Result)}``: a reference tune's lift optimised by the passes of
    :data:`OPTIMISED`, a subset that keeps the suite's run time bounded."""

    def make(name, sid, reference):
        return sid, optimise.optimise(reference.ir(), OPTIMISED[name], progress=False)

    refs = request.getfixturevalue("references")
    return _Shared(request, "optimised", make, lambda name: (refs[name],))
