"""Program-row keys that replaced list rows: GoatTracker wavetable commands and SID-Wizard WF
hold and detune rows."""

import functools

import pytest

from unitracker import songio
from unitracker.codec import CodecError, RowCodec, written_value
from unitracker.records import record_types


@functools.cache
def row_codec(pr_name: str, name: str) -> RowCodec:
    """The library playroutine's codec of record type ``name``."""
    return songio.codecs(songio.library(pr_name), None)[name]


def round_trip(pr_name: str, name: str, raw: dict) -> dict:
    """``raw``'s shared keys as written, asserting they read back as ``raw``."""
    c = row_codec(pr_name, name)
    shared = c.encode(raw)
    assert c.decode({k: written_value(k, v) for k, v in shared.items()}) == raw
    return {k: written_value(k, v) for k, v in shared.items()}


@pytest.mark.parametrize(
    "raw,shared",
    [
        ({"w": 0xF5, "n": 0x19}, {"ad": 0x19}),
        ({"w": 0xF6, "n": 0xC8}, {"sr": 0xC8}),
        ({"w": 0xF9, "n": 3}, {"run": ("pulsetab", 3)}),
        ({"w": 0xFA, "n": 2}, {"run": ("filttab", 2)}),
        ({"w": 0xFB, "n": 0xC1}, {"res": 12, "route": 1}),
        ({"w": 0xFC, "n": 0x80}, {"cut": 1024}),
        ({"w": 0xFD, "n": 9}, {"vol": 9}),
    ],
)
def test_goattracker_wave_commands_are_shared_row_keys(raw, shared):
    assert round_trip("goattracker2", "ctrltab", raw) == shared
    keys = row_codec("goattracker2", "ctrltab").ref_keys()
    assert keys == {"run": ("n", {"pulsetab", "filttab"})}


@pytest.mark.parametrize(
    "raw",
    [{"w": 0xF0 + c, "n": 1} for c in (0, 1, 2, 3, 4, 7, 8, 14)]
    + [{"w": 0xF9, "n": 0}, {"w": 0xFA, "n": 0}, {"w": 0xFB, "n": 0}, {"w": 0xFD, "n": 0x10}],
)
def test_goattracker_wave_commands_without_a_shared_form(raw):
    assert row_codec("goattracker2", "ctrltab").encode(raw) is None


@pytest.mark.parametrize(
    "shared,match",
    [
        ({"run": ("speedtab", 1)}, "names no"),
        ({"ad": 256}, "no shape"),
        ({"vol": 16}, "no shape"),
        ({"res": 0, "route": 0}, "no shape"),
        ({"cut": 1025}, "values of"),
        ({"ad": 1, "sr": 2}, "keys"),
    ],
)
def test_goattracker_wave_commands_refuse_what_they_cannot_set(shared, match):
    with pytest.raises(CodecError, match=match):
        row_codec("goattracker2", "ctrltab").decode(shared)


def test_a_reference_key_needs_ref_and_field():
    ctrltab = record_types(songio.library("goattracker2"))["ctrltab"]
    with pytest.raises(CodecError, match="both ref and field"):
        RowCodec(ctrltab, [{"keys": {"run": {"ref": "pulsetab"}}}], 0)


@pytest.mark.parametrize(
    "raw,shared",
    [
        ({"w": 0x41, "a": 0x80, "d": 0, "h": 0}, {"ctrl": 0x41}),
        (
            {"w": 0x41, "a": 0x0C, "d": 3, "h": 2},
            {"hold": 3, "ctrl": 0x41, "note": "+12", "freq": 3},
        ),
        ({"w": 0x21, "a": 0xF4, "d": 0xF0, "h": 0}, {"ctrl": 0x21, "note": "-12", "freq": -16}),
        ({"w": 0, "a": 5, "d": 0, "h": 0}, {"note": "+5"}),
        ({"w": 3, "a": 0x80, "d": 0x80, "h": 3}, {"hold": 4, "freq": -128}),
        ({"w": 0x0F, "a": 0x99, "d": 0, "h": 0x0F}, {"hold": 16, "note": "=24"}),
    ],
)
def test_sidwizard_wf_rows_hold_and_detune(raw, shared):
    assert round_trip("sidwizard", "wftab", raw) == shared


@pytest.mark.parametrize(
    "shared",
    [{"hold": 1}, {"hold": 0}, {"hold": 17}, {"ctrl": 5}, {"freq": -1}, {"freq": 200}],
)
def test_sidwizard_wf_rows_refuse_what_the_player_cannot_hold(shared):
    with pytest.raises(CodecError):
        row_codec("sidwizard", "wftab").decode(shared)
