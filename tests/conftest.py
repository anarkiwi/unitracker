"""Shared fixtures: HVSC tunes by path, and synthetic GoatTracker 2 tunes."""

from pathlib import Path
from types import SimpleNamespace

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
crosseyed = hvsc_tune(
    "crosseyed",
    "MUSICIANS/I/Ilkke/Crosseyed.sid",
    "Path to iLKke's Crosseyed .sid, a defMON replay.",
)
rent_a_cop = hvsc_tune(
    "rent_a_cop",
    "MUSICIANS/G/Goto80/Rent-A-Cop_Reloaded_title.sid",
    "Path to Goto80's Rent-A-Cop Reloaded title, a defMON replay at $9000 of the plain-arranger,"
    " relative-note build.",
)
tractorbeam = hvsc_tune(
    "tractorbeam",
    "MUSICIANS/V/Vincenzo/Broken_Tractorbeam.sid",
    "Path to Vincenzo's Broken Tractorbeam, a defMON replay whose ANC is encoded $2B.",
)
persei = hvsc_tune(
    "persei",
    "MUSICIANS/F/F7sus4/Persei.sid",
    "Path to F7sus4's Persei, a defMON replay whose play routine calls it twice a frame.",
)
ngc1277 = hvsc_tune(
    "ngc1277",
    "MUSICIANS/F/F7sus4/NGC1277_tune_1.sid",
    "Path to F7sus4's NGC1277 tune 1, a defMON replay at $0400 with its pointer at $02.",
)
csillag = hvsc_tune(
    "csillag",
    "MUSICIANS/H/Hermit/Csillag_szazketto_kettoskereszt.sid",
    "Path to Hermit's Csillag szazketto kettoskereszt, a SID-Wizard export.",
)
lykia_boss = hvsc_tune(
    "lykia_boss",
    "MUSICIANS/C/C0zmo/Lykia_Boss.sid",
    "Path to C0zmo's Lykia Boss, a SID-Wizard bare-build export.",
)
mega_run = hvsc_tune(
    "mega_run",
    "MUSICIANS/A/Arturo_Dente/Mega-Run.sid",
    "Path to Arturo Dente's Mega-Run, a SID-Wizard light-build export.",
)

space_invadarz = hvsc_tune(
    "space_invadarz",
    "MUSICIANS/B/Bayliss_Richard/Space_Invadarz_on_Vacation.sid",
    "Path to Richard Bayliss's Space Invadarz on Vacation, a Music Assembler tune with inner"
    " programs, an arpeggio and pulse and filter sweeps.",
)
eastern_promise = hvsc_tune(
    "eastern_promise",
    "MUSICIANS/M/Merman/Eastern_Promise.sid",
    "Path to Merman's Eastern Promise, a Music Assembler native song with transposes.",
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
    otherwise keys off; ``opts`` the build's ``state`` (a ``pygoattracker.packed.PlayerState``),
    ``pulse_step`` and other :class:`pygoattracker.player.Player` options (``optimize_realtime``
    and the like).
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
    build = SimpleNamespace(adparam=None, srparam=None, deftempo=None)
    state, step = opts.pop("state", None), opts.pop("pulse_step", 1)
    info = SimpleNamespace(
        simplepulse=False,
        state=state,
        init_regs=(),
        live_vibrato=False,
        build=build,
        note_read=GT.FREQ_TABLE,
    )
    header = SimpleNamespace(name="synthetic", author="", released="", flags=0)
    dec = SimpleNamespace(
        info=info,
        typed=TypedSong.from_song(song),
        subtune=0,
        header=header,
        player=lambda: Player(
            song,
            freq_table=GT.FREQ_TABLE,
            default_tempo=build.deftempo,
            state=state,
            pulse_step=step,
            **opts,
        ),
    )
    return dec, Player(song, freq_table=GT.FREQ_TABLE, state=state, pulse_step=step, **opts)
