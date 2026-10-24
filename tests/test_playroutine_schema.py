"""The playroutine schema's conventions (unitracker.playroutine.schema)."""

import pytest

from unitracker.playroutine.library import library
from unitracker.playroutine.schema import SchemaError, expand

LIBRARY = ("goattracker2", "defmon", "sidwizard")


@pytest.mark.parametrize("name", LIBRARY)
def test_expanding_is_idempotent(name):
    lib = library(name)
    assert expand(lib) == lib


def test_library_record_types_take_the_defaults():
    gt, dm = library("goattracker2"), library("defmon")
    types = dict(gt["record_types"])
    for fixed in (types.pop("vinit"), types.pop("ginit")):
        assert fixed["fixed"] and "none" not in fixed
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


def test_units_take_x_label_program_and_scope_from_site_and_cell():
    units = {u["name"]: u for u in library("defmon")["modulators"]["units"]}
    assert units["acid B"]["x"] == "sidtab[rowB].xc" and units["acid B"]["label"] == ["rowB"]
    assert units["acid B"]["program"] == {
        "fields": ["cp", "acid", "xc"],
        "record": "sidtab",
        "pointer": "rowB",
    }
    assert units["acid B"]["scope"] == "global" and "scope" not in units["pulse A"]
    gt = {u["name"]: u for u in library("goattracker2")["modulators"]["units"]}
    assert gt["command"]["x"].startswith("cmddata if") and gt["command"]["label"] == [
        "command",
        "cmddata",
    ]
    assert gt["filter"]["scope"] == "global" and gt["wave"]["label"] == ["wcur"]


def _wrap(cells: dict, widths: dict) -> dict:
    return {c: v & ((1 << widths[c]) - 1) for c, v in cells.items()}


@pytest.mark.parametrize(
    "cell,widths,chip,old",
    [
        ("c", {"c": 8}, {"width": 11}, "c << 3"),
        ("c", {"c": 16}, {"width": 12}, "c & 0xFFF"),
        ("c", {"c": 16}, {"width": 16, "offset": "t"}, "(c - t) & 0xFFFF"),
        (
            "h:l",
            {"h": 8, "l": 8},
            {"width": 12, "offset": "-(t << 8)"},
            "(((h + t) & 0x0F) << 8) | l",
        ),
        (
            "h:l:3",
            {"h": 8, "l": 8},
            {"width": 11, "offset": "-(t << 3)"},
            "(((h + t) & 0xFF) << 3) | (l & 7)",
        ),
    ],
)
def test_a_chip_value_and_store_follow_from_width_and_offset(cell, widths, chip, old):
    # pylint: disable=eval-used
    unit = {"name": "u", "cell": cell, "chip": dict(chip)}
    out = expand(
        {"voice": {c: {"width": w} for c, w in widths.items()}, "modulators": {"units": [unit]}}
    )
    got = out["modulators"]["units"][0]["chip"]
    mask = (1 << chip["width"]) - 1
    for t in (0, 3, 200):
        for raw in range(0, 1 << 16, 97):
            cells = _wrap({c: raw >> (8 * i) for i, c in enumerate(widths)}, widths)
            value = eval(got["value"], {}, {**cells, "t": t}) & mask
            assert value == eval(old, {}, {**cells, "t": t})
            back = {c: eval(e, {}, {"value": value, "t": t}) for c, e in got["store"].items()}
            assert eval(got["value"], {}, {**_wrap(back, widths), "t": t}) & mask == value


def test_explicit_unit_keys_and_chip_maps_stay():
    unit = {
        "cell": "g",
        "scope": "voice",
        "x": "0",
        "label": [],
        "site": {"record": "r", "index": "i", "field": "f"},
        "program": {"record": "q", "pointer": "p", "fields": []},
        "chip": {"width": 8, "value": "g", "store": {"g": "value"}},
        "hooks": [],
    }
    out = expand({"global": {"g": {"width": 8}}, "modulators": {"units": [unit]}})
    assert out["modulators"]["units"][0] == unit


def _row(record, index, cell, kind="written"):
    return {"at": {"record": record, "field": "do", "index": index}, kind: [cell]}


DECLARED_HOOKS = {
    ("goattracker2", "wave"): [{"at": {"record": "wavecmd", "field": "do", "index": [1, 2, 3, 4]}}],
    ("goattracker2", "pulse"): [
        {"at": {"proc": "new_note_init"}, "written": ["pptr"]},
        _row("cmd", 9, "pptr"),
    ],
    ("goattracker2", "filter"): [
        {"at": {"proc": "new_note_init"}, "written": ["fptr"]},
        _row("cmd", 10, "fptr"),
        _row("cmd", 11, "fptr"),
    ],
    ("defmon", "pulse A"): [{"at": {"proc": "pattern"}, "pointer": ["rowA"]}],
    ("defmon", "acid B"): [{"at": {"proc": "pattern"}, "pointer": ["rowB"]}],
    ("sidwizard", "pulse"): [{"at": {"proc": "start_note"}, "written": ["ppos"]}],
    ("sidwizard", "filter"): [{"at": {"proc": "set_filter"}, "written": ["fpos"]}],
}


def test_inferred_hooks_are_the_ones_the_playroutines_declared():
    found = {}
    for name in LIBRARY:
        for unit in library(name)["modulators"]["units"]:
            found[(name, unit["name"])] = unit["hooks"]
    assert {k: found[k] for k in DECLARED_HOOKS} == DECLARED_HOOKS
    assert sum(map(len, DECLARED_HOOKS.values())) == 10
    assert found[("goattracker2", "command")][0]["changed"] == ["command", "cmddata"]
    assert found[("defmon", "pitch A")] == [
        {"at": {"proc": "pattern"}, "written": ["sacc"], "pointer": ["rowA"]}
    ]


def test_named_tables_and_freq_come_from_functions():
    dm, sw = library("defmon")["modulators"], library("sidwizard")["modulators"]
    assert dm["freq"] == {"hi": "note_hi", "lo": "note_lo"}
    assert sw["freq"] == {"hi": "freqhi", "lo": "freqlo"}
    assert dm["tables"]["S"]["entry"] == (
        "((por_lo[i + 1] | (por_hi[i + 1] << 8)) - (por_lo[i] | (por_hi[i] << 8))) & 0xFFFF"
    )
    assert dm["functions"] == {"por": "S[y]"}
    assert list(sw["functions"]) == ["fmlo", "fmhi", "ex"] and sw["functions"]["ex"] == "E[i]"


def test_a_freq_function_reads_a_word_table_or_a_pair():
    functions = {
        "w": {"args": ["k"], "expr": "ft[k]"},
        "p": {"args": ["k"], "expr": "lo[k & 0xFF] + (hi[k & 0xFF] << 8)"},
        "bad": {"args": ["k"], "expr": "ft[k + 1]"},
        "t": {"args": ["y"], "expr": "a[y] + y"},
    }

    def mods(freq, tables=None):
        pr = {"functions": functions, "modulators": {"freq": freq, "tables": tables or {}}}
        return expand(pr)["modulators"]

    assert mods("w")["freq"] == {"word": "ft"}
    assert mods("p")["freq"] == {"hi": "hi", "lo": "lo"}
    with pytest.raises(SchemaError, match="bad reads no word table"):
        mods("bad")
    got = mods("w", {"T": {"function": "t", "size": 4}, "U": {"function": "t", "entry": "0"}})
    assert got["tables"]["T"]["entry"] == "a[i] + i" and got["tables"]["U"]["entry"] == "0"
    assert got["functions"] == {"t": "T[y]"}


def test_a_unit_with_no_pointer_has_no_inferred_hooks():
    out = expand({"modulators": {"units": [{"cell": "g", "fire": ["p"]}]}, "procs": {"p": []}})
    assert "hooks" not in out["modulators"]["units"][0]


def test_a_pointer_write_inside_the_firing_is_no_hook():
    def write(cell):
        return [{"if": "1", "then": [{"set": {cell: 1}}]}]

    pr = {
        "procs": {
            "step": [{"run": {"record": "cmd", "index": "c", "field": "do"}}],
            "go": write("p"),
        },
        "records": {"cmd": {1: {"do": write("p")}}, "arm": {2: {"do": write("p")}, 3: {"do": []}}},
        "modulators": {
            "units": [
                {"cell": "a", "fire": ["step"], "site": {"record": "t", "index": "p", "field": "x"}}
            ]
        },
    }
    assert expand(pr)["modulators"]["units"][0]["hooks"] == [
        {"at": {"proc": "go"}, "written": ["p"]},
        {"at": {"record": "arm", "field": "do", "index": 2}, "written": ["p"]},
    ]


def test_a_command_shape_stands_for_each_command_with_a_row_key():
    commands = {
        3: {"name": "ad"},
        4: [{"name": "vol", "when": "arg < 0x10"}, {"name": "tempo", "when": "arg >= 0x10"}],
        5: {"name": "run", "ref": "t"},
        6: {"name": "cut", "byte": "arg"},
        7: {"name": "sr", "arg": "arg ^ 1", "raw": "arg ^ 1"},
        8: {"name": "freq", "ref": "x"},
    }
    shape = {"when": "c > 0", "command": "c & 7", "arg": "a", "skip": [3]}
    shape["raw"] = {"c": "8 | command", "a": "arg"}
    pr = {"commands": commands, "record_types": {"t": {"fields": ["c", "a"], "codec": [shape]}}}
    assert expand(pr)["record_types"]["t"]["codec"] == [
        {
            "when": "(c > 0) and ((c & 7) == 4) and (a < 0x10)",
            "keys": {"vol": {"value": "a"}},
            "raw": {"c": "8 | 4", "a": "vol"},
        },
        {
            "when": "(c > 0) and ((c & 7) == 5) and (a != 0)",
            "keys": {"run": {"ref": "t", "field": "a"}},
            "raw": {"c": "8 | 5"},
        },
        {
            "when": "(c > 0) and ((c & 7) == 6)",
            "keys": {"cut": {"byte": "a"}},
            "raw": {"c": "8 | 6", "a": "cut"},
        },
        {
            "when": "(c > 0) and ((c & 7) == 7)",
            "keys": {"sr": {"value": "a ^ 1"}},
            "raw": {"c": "8 | 7", "a": "(sr ^ 1)"},
        },
    ]
