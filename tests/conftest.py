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

DEFMON = {
    "glow_worm": (
        "MUSICIANS/I/Ilkke/Glow_Worm.sid",
        "a defMON tune of the absolute-note build with counted arranger jumps",
    ),
    "tetrapapak": (
        "MUSICIANS/I/Ilkke/Tetrapapak.sid",
        "a defMON tune of the relative-note build with counted arranger jumps",
    ),
    "higher_scores": (
        "MUSICIANS/F/Frantic/Higher_Scores.sid",
        "a defMON tune of the relative-note build with uncounted jumps",
    ),
    "ponky": (
        "MUSICIANS/G/Goto80/Ponky.sid",
        "a defMON tune whose control byte combines its two wave columns with OR",
    ),
    "fading_lights": (
        "DEMOS/A-F/Fading_Lights.sid",
        "a defMON tune that alternates full and sub-tick calls",
    ),
    "automatas": (
        "MUSICIANS/G/Goto80/Automatas.sid",
        "a defMON tune with one full call in eight",
    ),
    "syrapaog": ("DEMOS/S-Z/Syrapaog.sid", "a defMON tune whose four calls a frame are all full"),
    "timewarp": ("MUSICIANS/F/F7sus4/Timewarp_Spheres_2SID.sid", "a stereo defMON tune"),
    "rent_a_cop": (
        "MUSICIANS/G/Goto80/Rent-A-Cop.sid",
        "a defMON tune whose pattern streams run past 32 steps",
    ),
    "in_memory_of": (
        "MUSICIANS/G/Goto80/In_Memory_of.sid",
        "a defMON tune whose arranger plays patterns past $7f and a row from JP rows",
    ),
    "techno_aha": (
        "MUSICIANS/G/Goto80/Techno_Aha.sid",
        "a defMON tune whose patterns end only in the memory after them",
    ),
    "triangle": (
        "MUSICIANS/F/F7sus4/Triangle_My_Amelie.sid",
        "a defMON tune whose first seven calls are sub-tick calls",
    ),
    "svenska": (
        "MUSICIANS/G/Goto80/Svenska_Narkotika_3.sid",
        "a defMON tune of three full calls in four, the first a sub-tick call",
    ),
    "anticitizen": (
        "MUSICIANS/N/NecroPolo/Anticitizen_64.sid",
        "a defMON tune that alternates a sub-tick call and a full call",
    ),
}
for _name, (_tune, _doc) in DEFMON.items():
    globals()[_name] = hvsc_tune(_name, _tune, f"Path to {_doc}.")


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
