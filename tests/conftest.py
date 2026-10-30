"""Shared fixtures: HVSC tunes by path, and synthetic GoatTracker 2 tunes."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from pygoattracker import (
    Build,
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
from pysidtracker.testing import resolve_tune


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


def table(*rows) -> Table:
    """A GoatTracker 2 table of ``(left, right)`` ``rows``."""
    return Table(left=[r[0] for r in rows], right=[r[1] for r in rows])


def gt_tune(wave, instruments=None, rows=None, speed=(), pulse=(), filt=(), voice2=None, **opts):
    """A one-pattern GoatTracker 2 tune on voice 1 as ``(decompiled tune, native player)``.

    ``wave``, ``speed``, ``pulse`` and ``filt`` are the tables' ``(left, right)`` rows;
    ``instruments`` keyword dicts of :class:`pygoattracker.Instrument` (default one instrument
    on the wavetable's first row); ``rows`` the pattern's ``(note, instrument, command, data)``
    rows, a note as an offset from ``FIRSTNOTE`` or None for a rest; ``voice2`` voice 2's, which
    otherwise keys off; ``opts`` the build's ``state`` (a ``pygoattracker.packed.PlayerState``)
    and :class:`pygoattracker.Build` fields (``pulse_step``, ``optimize_realtime``, ``voices``,
    ``idle``, ``calls`` and the like).
    """
    # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    instruments = instruments or [{"wave_ptr": 1, "gateoff_timer": 2, "sustain_release": 0xF0}]
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
    build = Build(**opts)
    info = SimpleNamespace(playback=build, state=state, note_read=GT.FREQ_TABLE)
    header = SimpleNamespace(name="synthetic", author="", released="", flags=0)

    def player():
        return Player(song, build=build, state=state, freq_table=GT.FREQ_TABLE)

    dec = SimpleNamespace(
        info=info, typed=TypedSong.from_song(song), subtune=0, header=header, player=player
    )
    return dec, player()
