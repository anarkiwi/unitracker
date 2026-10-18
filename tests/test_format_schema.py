"""The playroutine schema's conventions (format/schema.py)."""

import pytest

from unitracker.format import songio
from unitracker.format.schema import expand

LIBRARY = ("goattracker2", "defmon", "sidwizard")


@pytest.mark.parametrize("name", LIBRARY)
def test_expanding_is_idempotent(name):
    lib = songio.library(name)
    assert expand(lib) == lib


def test_library_record_types_take_the_defaults():
    gt, dm = songio.library("goattracker2"), songio.library("defmon")
    types = gt["record_types"]
    assert {n: (t.get("chain"), t["none"]) for n, t in types.items()} == {
        "ins": (None, 0),
        "restart": ("next", 0),
        "speedtab": (None, 0),
        "ctrltab": ("next", 0),
        "pulsetab": ("next", 0),
        "filttab": ("next", 0),
    }
    assert types["pulsetab"]["refs"] == {"x": "x"}
    assert dm["record_types"]["sidtab"]["none"] is None
    assert "none" not in dm["record_types"]["vinit"]
    assert gt["instrument"] == {"type": "ins", "base": 0} and gt["note_offset"] == 0
    assert dm["instrument"]["type"] == "sidtab"
    assert gt["ghost"]["voice"] == {
        0: "freq & 0xFF",
        1: "freq >> 8",
        2: "pulse & 0xFF",
        3: "pulse >> 8",
        4: "wave & gate",
        5: "ad",
        6: "sr",
    }
    assert dm["ghost"]["voice"][0] == "o_flo" and dm["ghost"]["voice"][1] == "o_fhi"


def test_an_explicit_key_overrides_each_default():
    pr = {
        "record_types": {
            "t": {
                "fields": ["v", "next"],
                "chain": None,
                "none": 3,
                "expr": "v",
                "refs": {"v": "u"},
            },
            "u": {"writes_as": "ins", "fields": ["next"]},
        },
        "instrument": {"type": "t", "base": 1},
        "note_offset": 2,
        "voice": {"w": {"width": 16}, "a": {"width": 8}, "b": {"width": 8}},
        "ghost": {"voice": {0: "w", 1: "a", 2: "a:b", 4: "a"}},
    }
    out = expand(pr)
    assert out["record_types"]["t"] == pr["record_types"]["t"]
    assert out["record_types"]["u"]["chain"] == "next"
    assert out["instrument"] == pr["instrument"] and out["note_offset"] == 2
    assert out["ghost"]["voice"] == {0: "w & 0xFF", 1: "a", 2: "b", 3: "a", 4: "a"}
    assert "instrument" not in expand({"record_types": {"t": {}}})
