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
border_odyssey = hvsc_tune(
    "border_odyssey",
    "MUSICIANS/H/Hermit/Border_Odyssey.sid",
    "Path to Hermit's Border Odyssey, a SID-Wizard normal-build export with octave shifts.",
)
samwise = hvsc_tune(
    "samwise",
    "MUSICIANS/A/Ant1/Samwise.sid",
    "Path to Ant1's Samwise, a SID-Wizard extra-build export of a pre-1.8 driver.",
)
flowing_slowly_extended = hvsc_tune(
    "flowing_slowly_extended",
    "MUSICIANS/T/Toggle/Flowing_Slowly_extended.sid",
    "Path to Toggle's Flowing Slowly (extended), a SID-Wizard extra build with staccato.",
)
whammer = hvsc_tune(
    "whammer",
    "MUSICIANS/V/Vincenzo/Whammer.sid",
    "Path to Vincenzo's Whammer, a SID-Wizard medium-build export with portamentos.",
)

turn_me_on = hvsc_tune(
    "turn_me_on",
    "MUSICIANS/E/Emarti/Turn_Me_On.sid",
    "Path to Emarti's Turn Me On, a SID-Wizard tune that selects an instrument before any note.",
)
reflex = hvsc_tune(
    "reflex",
    "MUSICIANS/G/Garvalf/Reflex.sid",
    "Path to Garvalf's Reflex, whose instrument selected before any note walks its header.",
)
axelf = hvsc_tune(
    "axelf",
    "MUSICIANS/G/Gregfeel/AxelF-Shots_out_of_Nowhere.sid",
    "Path to Gregfeel's AxelF, a first note's HARDRST carry on a walk started before it.",
)
crapman = hvsc_tune(
    "crapman",
    "MUSICIANS/B/Batsman/Crapman.sid",
    "Path to Batsman's Crapman, a SID-Wizard tune whose init leaves a tracked cutoff.",
)
twenty_first = hvsc_tune(
    "twenty_first",
    "MUSICIANS/V/Vincenzo/21st.sid",
    "Path to Vincenzo's 21st, WF rows held over ticks with an untracked pulse table.",
)
jt_style = hvsc_tune(
    "jt_style",
    "MUSICIANS/H/Hermit/JT_Style.sid",
    "Path to Hermit's JT Style, a multispeed SID-Wizard tune with orderlist transposes.",
)
lenore = hvsc_tune(
    "lenore",
    "MUSICIANS/H/Hermit/Lenore.sid",
    "Path to Hermit's Lenore, multispeed, with big FX $08 and MULCNTP filter walks.",
)
stompbox = hvsc_tune(
    "stompbox",
    "MUSICIANS/V/Vincenzo/Stompbox.sid",
    "Path to Vincenzo's Stompbox, multispeed, with orderlist raw bytes.",
)
csango_boogie = hvsc_tune(
    "csango_boogie",
    "MUSICIANS/V/Vincenzo/Csango_Boogie.sid",
    "Path to Vincenzo's Csango Boogie, multispeed, with big FX $05 and a wrapped DPITCH.",
)
samar_is_20 = hvsc_tune(
    "samar_is_20",
    "MUSICIANS/H/Hermit/Samar_Is_20.sid",
    "Path to Hermit's Samar Is 20, with big FX $09 and chord offset $FB.",
)
pantheon = hvsc_tune(
    "pantheon",
    "MUSICIANS/T/Toggle/Pantheon.sid",
    "Path to Toggle's Pantheon, a first frame keeping the waveform with no hard restart.",
)
grumps = hvsc_tune(
    "grumps",
    "MUSICIANS/G/Goerp/Grumps.sid",
    "Path to Goerp's Grumps, multispeed, with orderlist MainVolume.",
)
x_copy = hvsc_tune(
    "x_copy",
    "MUSICIANS/M/Moellpauk/X-Copy-The_Intro.sid",
    "Path to Moellpauk's X-Copy intro, whose instrument 0 walks its pulse on MULPLY calls.",
)
emomyst = hvsc_tune(
    "emomyst",
    "MUSICIANS/H/Hermit/Emomyst.sid",
    "Path to Hermit's Emomyst, a build whose STRTSND keeps ARPSCNT.",
)
pimp_my_commodore = hvsc_tune(
    "pimp_my_commodore",
    "MUSICIANS/H/Hermit/Pimp_My_Commodore.sid",
    "Path to Hermit's Pimp My Commodore, a SID-Wizard export whose init routine writes the"
    " voices.",
)

space_invadarz = hvsc_tune(
    "space_invadarz",
    "MUSICIANS/B/Bayliss_Richard/Space_Invadarz_on_Vacation.sid",
    "Path to Richard Bayliss's Space Invadarz on Vacation, a Music Assembler tune with inner"
    " programs, an arpeggio and pulse and filter sweeps.",
)
cannon_craze = hvsc_tune(
    "cannon_craze",
    "MUSICIANS/M/Mateus/Cannon_Craze.sid",
    "Path to Mateus's Cannon Craze, a Music Assembler tune whose init sets each voice's test bit.",
)
eastern_promise = hvsc_tune(
    "eastern_promise",
    "MUSICIANS/M/Merman/Eastern_Promise.sid",
    "Path to Merman's Eastern Promise, a Music Assembler native song with transposes.",
)
chodel = hvsc_tune(
    "chodel",
    "MUSICIANS/J/Joan/Chodel.sid",
    "Path to Joan's Chodel, a Music Assembler tune whose first voice starts on a rest with no"
    " trigger yet played.",
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
