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

CORPUS = {
    "penumbra": ("DEMOS/M-R/Penumbra.sid", "a full-ghosting altplayer build with fixed parameters"),
    "orbyte": (
        "DEMOS/0-9/10_Orbyte.sid",
        "a fixed-parameter, simple-pulse build without funk tempo",
    ),
    "heavy_industrial": (
        "MUSICIANS/J/Joeli_Jani/Heavy_Industrial_Machinery.sid",
        "a build with legato instruments",
    ),
    "lester": ("GAMES/G-L/Lester.sid", "a buffered-writes build"),
    "captain_future": ("GAMES/A-F/Captain_Future_Preview.sid", "a noeffects build"),
    "marble_boy": (
        "GAMES/M-R/Marble_Boy.sid",
        "a noeffects build whose play routine calls the player twice a frame",
    ),
    "squarish": (
        "MUSICIANS/P/Psych858o/Squarish.sid",
        "a nonormalspeed build with calculated speeds",
    ),
    "blast_em_all": ("MUSICIANS/J/Jammer/Blast_em_All.sid", "calculated portamento and vibrato"),
    "robots_can_dance": (
        "DEMOS/M-R/Robots_Can_Dance.sid",
        "wavetable portamento in a build with vibrato",
    ),
    "darkness": (
        "MUSICIANS/L/Linus/Darkness.sid",
        "instrument vibrato across wavetable commands, with calculated speeds",
    ),
    "joelbitar": ("DEMOS/UNKNOWN/Joelbitar.sid", "a loop re-entered with another instrument"),
    "opin_joskus": ("DEMOS/M-R/Opin_Joskus.sid", "a filter table modulating across a jump"),
    "z303r": ("MUSICIANS/X/Xiny6581/Z303R.sid", "filter pointers set beside cutoff commands"),
    "soiled_iron": (
        "MUSICIANS/C/Crisps/Soiled_Iron.sid",
        "a play wrapper and loaded channel state",
    ),
    "hz_jazz": (
        "MUSICIANS/B/Booker/Hz_Jazz.sid",
        "restarts that fetch on the previous note's tick",
    ),
    "an_ufo_attack": (
        "MUSICIANS/M/Mutetus/An_UFO_Attack_from_Space.sid",
        "legato instruments that keep a gate left off by a key off",
    ),
    "lily_lander": ("GAMES/G-L/Lily_Lander.sid", "a play routine flushing a ghost block"),
    "lazy_day": (
        "MUSICIANS/M/Mueller_Manuel/Lazy_Day.sid",
        "gate timers read from the live instrument",
    ),
    "commando": (
        "MUSICIANS/L/Linus/Commando_Take_Me_to_the_Bridge_short.sid",
        "a full ghost whose song start clears the control registers, and instrument vibrato "
        "reading the channel variables the start keeps",
    ),
    "petal_panic": (
        "MUSICIANS/C/Chiummo_Aldo/Petal_Panic_preview.sid",
        "instrument vibrato reading the vibrato delay the song start keeps",
    ),
    "cruiser_x_79": (
        "MUSICIANS/B/Bayliss_Richard/Cruiser-X_79.sid",
        "a speedtable the spec binds elsewhere than the player's other code",
    ),
    "deathflood": (
        "GAMES/A-F/Deathflood.sid",
        "pulse table jumps onto jump rows, which play as pulse sets",
    ),
    "yeti_mountain": (
        "MUSICIANS/R/Roly/Yeti_Mountain.sid",
        "a pulse table jump onto a jump row",
    ),
    "datatanten": (
        "DEMOS/A-F/Datatanten_Theme.sid",
        "a funk row of table bytes 0 that never ends",
    ),
    "dirty_cheater": (
        "MUSICIANS/S/Spider_Jerusalem/Dirty_Cheater.sid",
        "the tune's own init writes",
    ),
}
for _name, (_tune, _doc) in CORPUS.items():
    globals()[_name] = hvsc_tune(_name, _tune, f"Path to {_tune}, {_doc}.")

REFUSED = {
    "a_crack_in_the_facade": (
        "DEMOS/A-F/A_Crack_in_the_Facade.sid",
        "SID address",
        "a player writing a SID at $D420",
    ),
    "first_night_together": (
        "MUSICIANS/O/Onebitman/Zerozillion/First_Night_Together.sid",
        "noeffects vibrato",
        "instrument vibrato in a noeffects build",
    ),
    "i_dont_know": (
        "MUSICIANS/G/Groepaz/I_Dont_Know_What_I_Am_Doing.sid",
        "wrapper",
        "a play routine adding a cutoff sweep and a volume xor of its own",
    ),
    "two_hours": (
        "DEMOS/0-9/2_Hours_NOT_Enough.sid",
        "pointer onto a jump row",
        "a filter table jump onto a jump row, which plays as a filter set of mode $F0",
    ),
}
for _name, (_tune, _kind, _doc) in REFUSED.items():
    globals()[_name] = hvsc_tune(_name, _tune, f"Path to {_tune}, {_doc}.")


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
