"""SID-Wizard player behaviours the lifter models: each a change to Csillag's window whose lifted
song plays on the VM as the native player does."""

import numpy as np
import pytest
from conftest import assert_plays
from pysidtracker import SidImage
from pysidwizard import SWMPlayer, parse_sid_song
from test_lift_sidwizard import EXTRA, TICKS, WINDOW_TICKS, as_built, window

from unitracker.lift import sidwizard


def test_the_build_is_the_player_codes_over_a_stale_driver_byte(request, csillag):
    raw = window(csillag.read_bytes())
    swm = parse_sid_song(raw, 1)
    swm.driver_type = 3
    lift = sidwizard.SidWizardLift(
        sidwizard.Decoded(swm, SidImage.from_bytes(raw).header, 0), expressions=False
    )
    assert lift.features == swm.features != EXTRA
    grid = SWMPlayer(swm).render_grid(TICKS)
    assert_plays(request, lift.run(), grid, lambda: SWMPlayer(swm))


def instrument_set(number: int, **fields):
    """A change to a module setting instrument ``number``'s ``fields``."""

    def change(swm):
        for name, value in fields.items():
            setattr(swm.instruments[number - 1], name, value)

    return change


def kept_filter(swm):
    """Instrument 6, which voice 1 plays on notes that name no instrument, takes instrument 8's
    filter program and keeps it across them."""
    instrument_set(6, filter_table=swm.instruments[7].filter_table, control=0x9A)(swm)


def legato_envelope(swm):
    """The first note that names no instrument and has no FX becomes a legato note with an attack
    FX."""
    row = next(
        r
        for p in swm.patterns
        for r in p.rows
        if r.note and r.note < 0x60 and not r.instrument and r.fx is None
    )
    row.instrument, row.fx = 0x3F, 0x25


def retuned(swm):
    """FREQTBH from note index 40 one higher, which the vibrato amplitudes read too."""
    hi = swm.freq_hi_image
    swm.freq_hi_image = hi[:40] + bytes((b + 1) & 0xFF for b in hi[40:96]) + hi[96:]


def glides(swm):
    """Each empty row of pattern 3 before a note plays a note-column glide ($78)."""
    rows = swm.patterns[2].rows
    for row, after in zip(rows, rows[1:]):
        empty = row.note is None and row.instrument is None and row.fx is None
        if empty and after.note and after.note < 0x60:
            row.note = 0x78


def portamentos(swm):
    """Each note that names no instrument and has no FX slides there (big FX $03)."""
    for pattern in swm.patterns:
        for row in pattern.rows:
            if row.note and row.note < 0x60 and not row.instrument and row.fx is None:
                row.fx, row.fx_value = 0x03, 0


CHANGES = {
    "vibrato type $20": (instrument_set(9, control=0x2A), 0, 1),
    "vibrato type $30": (instrument_set(9, control=0x3A), 0, 1),
    "type $00 without vibrato types": (instrument_set(9, control=0x0A), 1, 1),
    "a vibrato delay with bit 7": (instrument_set(9, vibrato_delay=0x88), 0, 1),
    "an INCVIBR delay with bit 7": (instrument_set(1, control=0x0A, vibrato_delay=0x85), 0, 1),
    "a kept filter program": (kept_filter, 0, 1),
    "a multispeed pulse program on arp speed bit 7": (instrument_set(1, arp_speed=0x81), 0, 2),
    "a sweep count past a restarted sweep's": (
        instrument_set(1, pw_table=bytes([0x08, 0x10, 0, 0x40, 0xF8, 0])),
        0,
        1,
    ),
    "a first WF row without a pitch": (
        instrument_set(6, wf_table=bytes([0x81, 0x80, 0, 0x51, 0, 0, 0x10, 0, 0])),
        0,
        1,
    ),
    "a legato note's envelope FX": (legato_envelope, 0, 1),
    "a retuned FREQTBH": (retuned, 0, 1),
    "a WF jump past $7F": (
        instrument_set(5, wf_table=bytes([0x41, 0xB0, 0, 0xFE, 0xD0, 0, 0x80, 0xDF, 0])),
        0,
        1,
    ),
    "a pending glide under PORTAVIBRA": (glides, 3, 1),
    "portamentos reaching their notes under PORTAVIBRA": (portamentos, 3, 1),
    "an instrument 0 other than instrument 1": (
        lambda swm: setattr(swm, "dummy_instrument", swm.instruments[2]),
        0,
        1,
    ),
}


@pytest.mark.parametrize("change,driver,speed", CHANGES.values(), ids=CHANGES)
def test_a_changed_module_plays_its_native_player(request, csillag, change, driver, speed):
    raw = window(csillag.read_bytes())

    def built():
        swm = as_built(raw, driver, speed)
        change(swm)
        return swm

    grid = np.asarray(SWMPlayer(built()).render_grid(WINDOW_TICKS), np.uint8)
    lift = sidwizard.SidWizardLift(
        sidwizard.Decoded(built(), SidImage.from_bytes(raw).header, 0), expressions=False
    )
    assert_plays(request, lift.run(), grid, lambda: SWMPlayer(built()))
