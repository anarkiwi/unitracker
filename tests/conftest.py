"""Shared fixtures: HVSC tunes by path, and synthetic GoatTracker 2 tunes."""

from pathlib import Path

import pytest
from pygoattracker import (
    Build,
    Instrument,
    Orderlist,
    Pattern,
    PlayPattern,
    Row,
    Song,
    Subtune,
    Table,
    TypedSong,
)
from pygoattracker import constants as GT
from pysidtracker.testing import resolve_tune

from unitracker.lift import goattracker2


def hvsc_tune(name: str, tune: str, doc: str):
    """A session fixture ``name``: the path to HVSC ``tune`` (local $HVSC, cache, or mirror)."""

    def fixture() -> Path:
        path = resolve_tune(tune)
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

penumbra = hvsc_tune(
    "penumbra",
    "DEMOS/M-R/Penumbra.sid",
    "Path to Penumbra, a full-ghosting altplayer build with fixed parameters.",
)
orbyte = hvsc_tune(
    "orbyte",
    "DEMOS/0-9/10_Orbyte.sid",
    "Path to 10 Orbyte, a fixed-parameter, simple-pulse build without funk tempo.",
)
heavy_industrial = hvsc_tune(
    "heavy_industrial",
    "MUSICIANS/J/Joeli_Jani/Heavy_Industrial_Machinery.sid",
    "Path to Heavy Industrial Machinery, a build with legato instruments.",
)
lester = hvsc_tune(
    "lester",
    "GAMES/G-L/Lester.sid",
    "Path to Lester, a buffered-writes build.",
)


def table(*rows) -> Table:
    """A GoatTracker 2 table of ``(left, right)`` ``rows``."""
    return Table(left=[r[0] for r in rows], right=[r[1] for r in rows])


def gt_tune(wave, instruments=None, rows=None, speed=(), pulse=(), filt=(), voice2=None, **opts):
    """A one-pattern GoatTracker 2 tune on voice 1 as a lift core.

    ``wave``, ``speed``, ``pulse`` and ``filt`` are the tables' ``(left, right)`` rows;
    ``instruments`` keyword dicts of :class:`pygoattracker.Instrument` (default one instrument
    on the wavetable's first row, gate on); ``rows`` the pattern's ``(note, instrument, command,
    data)`` rows, a note as an offset from ``FIRSTNOTE`` or None for a rest; ``voice2`` voice 2's,
    which otherwise keys off; ``opts`` the build's ``state`` (a
    ``pygoattracker.packed.PlayerState``) and :class:`pygoattracker.Build` fields
    (``pulse_step``, ``optimize_realtime``, ``voices``, ``idle``, ``calls`` and the like).
    """
    # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    base = {"wave_ptr": 1, "gateoff_timer": 2, "sustain_release": 0xF0, "first_wave": 0xFF}
    instruments = [base | i for i in instruments or [{}]]
    rows = rows or [(24, 1, 0, 0)] + [(None, 0, 0, 0)] * 15

    def pattern(rows):
        return Pattern(
            rows=[
                Row(
                    note=GT.REST if n is None else GT.FIRSTNOTE + n, instrument=i, command=c, data=d
                )
                for n, i, c, d in rows
            ]
        )

    patterns = [pattern(rows), Pattern(rows=[Row(note=GT.KEYOFF)] + [Row()] * 15)]
    patterns += [pattern(voice2)] if voice2 else []
    song = Song(
        name="synthetic",
        subtunes=[Subtune([Orderlist([PlayPattern(k)]) for k in (0, 2 if voice2 else 1, 1)])],
        instruments=[Instrument(**ins) for ins in instruments],
        patterns=patterns,
        wavetable=table(*wave),
        pulsetable=table(*pulse),
        filtertable=table(*filt),
        speedtable=table(*speed),
    )
    state = opts.pop("state", None)
    return goattracker2.core_of_song(TypedSong.from_song(song), Build(**opts), state)
