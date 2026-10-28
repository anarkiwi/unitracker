"""Shared fixtures: HVSC tunes by path, and synthetic GoatTracker 2 tunes."""

import hashlib
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
from pydefmon import DefmonSong, PatternEvent, SidtabRow
from pysidtracker import native_decrunch
from pysidtracker.testing import TuneFetchError, default_cache, fetch_prgs, resolve_tune

DEFMON_RELEASE = "https://csdb.dk/getinternalfile.php/204680/defmon-20201008.zip"
DEFMON_EDITOR_SHA256 = "bc78644c5597a91b86df54aabcc6fbc76ccedf5bf0f3ddc63ac475dc6527b329"
DEFMON_GATES = (0x715E, 0x715F, 0x7162)


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


@pytest.fixture(scope="session", name="defmon_editor")
def fixture_defmon_editor() -> bytes:
    """The defMON V20201008 editor's decrunched 64K image, whose packer turns a worktune into
    a replay: fetched from csdb once and cached under ``.tunecache``."""
    dst = default_cache("disks") / "defmon-static.bin"
    if dst.exists() and hashlib.sha256(dst.read_bytes()).hexdigest() == DEFMON_EDITOR_SHA256:
        return dst.read_bytes()
    try:
        prgs = fetch_prgs(DEFMON_RELEASE, member="defmon-20201008.d64")
    except TuneFetchError:
        pytest.skip("defMON release unavailable")
    for prg in (f.prg for f in prgs if f.name.lstrip(".").upper().startswith("DEFMON")):
        image = native_decrunch(prg)
        flat = bytes(image.mem) if image is not None else b""
        if hashlib.sha256(flat).hexdigest() == DEFMON_EDITOR_SHA256:
            dst.write_bytes(flat)
            return flat
    raise ValueError("no defMON editor in the release decrunches to its pinned image")


def pack_worktune(editor: bytes, rows: dict, patterns: list, arranger: list) -> bytes:
    """The replay defMON packs of a worktune: sidTAB ``rows`` ``{row: (dl, columns)}`` or
    ``{row: target}`` for a jump row, ``patterns`` of events and ``arranger`` steps of three
    patterns or ``(target, count)`` jumps."""
    song = DefmonSong()
    for row, value in rows.items():
        if isinstance(value, int):
            song.set_jp(row, value)
            continue
        song.sidtab_region[row * 15 : row * 15 + 15] = SidtabRow.pack(value[1])
        song.set_jp(row)
        song.set_dl(row, value[0])
    for k, events in enumerate(patterns):
        song.set_pattern_events(k, events + [PatternEvent.delay(0)] * (32 - len(events)))
    for y, step in enumerate(arranger):
        if len(step) == 2:
            song.set_jump(y, target=step[0], count=step[1])
        else:
            song.set_step(y, v1=step[0], v2=step[1], v3=step[2])
    for gate in DEFMON_GATES:
        song.snapshot[gate - 0x1800] = 1
    song.set_cia2_timer(0x4CC7)
    song.set_sub_frame_count(1)
    return DefmonSong.from_bytes(song.to_bytes()).to_replay(editor)


DEFMON_ROWS = {
    9: (2, {"WGh": 0x40, "WGl": 0x01, "AD": 0x09, "SR": 0xF0, "PW": 0x08, "PS": 0x83}),
    10: (0x80, {"WGl": 0x00}),
    12: (1, {"WGh": 0x20, "WGl": 0x01, "TR": 12, "AF": 0x81}),
    13: (0, {"TR": 0, "AF": 0x05}),
    14: 12,
    16: (0x80, {"ACID": 0x1000, "CP": 0x10, "RE": 0xF1, "FV": 0x10}),
    17: (0x80, {"ACID": 0xC120, "CP": 0xE0, "RE": 0x08}),
}
DEFMON_PATTERNS = [
    [
        PatternEvent.note_on(0x30, slot_a=9, duration=3),
        PatternEvent(0x22, 0, 16, 0),
        PatternEvent.note_on(0x34, duration=1),
    ]
    + [PatternEvent.alt_end(1)],
    [
        PatternEvent.note_on(0x3C, slot_a=12, duration=5),
        PatternEvent.delay(3),
        PatternEvent.alt_end(1),
    ],
    [PatternEvent.delay(7), PatternEvent(0x70, 9, 17, 0x40), PatternEvent.alt_end(1)],
]


@pytest.fixture(scope="session")
def defmon_song(defmon_editor) -> bytes:
    """Two steps and a loop over both layers, a slide and portamento loop and the filter."""
    return pack_worktune(
        defmon_editor, DEFMON_ROWS, DEFMON_PATTERNS, [(0, 1, 2), (1, 2, 0), (1, 0)]
    )


@pytest.fixture(scope="session")
def defmon_gap(defmon_editor) -> bytes:
    """A gate-off row whose waveform nibble is the previous program's."""
    rows = {9: (0x80, {"WGh": 0x40, "WGl": 0x01}), 18: (0x80, {"WGl": 0x00})}
    events = [
        PatternEvent.note_on(0x30, slot_a=9, duration=3),
        PatternEvent(0x43, 18, 0, 0),
        PatternEvent.alt_end(1),
    ]
    return pack_worktune(defmon_editor, rows, [events], [(0, 0, 0), (0, 0)])


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
