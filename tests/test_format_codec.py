"""Shared vocabulary: each library playroutine's row codecs and command codes, the written forms of
their values, and the command and `ctrl` row key tables of docs/playroutines."""

import contextlib
import functools
import itertools
import math
import re
from importlib import resources
from pathlib import Path

import numpy as np
import pytest
import yaml

from unitracker.format import songio
from unitracker.format.codec import (
    COMMAND_NAMES,
    NONE,
    ROW_KEYS,
    CodecError,
    CommandCodec,
    Evaluator,
    RowCodec,
    ctrl_text,
    ctrl_value,
    envelope_names,
    evaluate,
    note_text,
    note_value,
    route_text,
    route_value,
    row_place,
    written_value,
)
from unitracker.format.events import lifetime, restart
from unitracker.format.records import record_types
from unitracker.player.vm import SongPlayer

PLAYERS = sorted(
    p.name.removesuffix(".yaml")
    for p in resources.files("unitracker").joinpath("playroutines").iterdir()
    if p.name.endswith(".yaml")
)
LIBS = {p: songio.library(p) for p in PLAYERS}
TYPES = {p: record_types(pr) for p, pr in LIBS.items()}
CODECS = {p: songio.codecs(pr, pr.get("tables")) for p, pr in LIBS.items()}
COMMANDS = {p: CommandCodec(pr.get("commands"), TYPES[p]) for p, pr in LIBS.items()}
ROW_CODECS = [(p, n) for p in PLAYERS for n in CODECS[p]]
FIRST, MIDDLE, LAST, ONLY = PLACES = tuple(
    row_place(*a) for a in ((0, 2, True), (1, 3, True), (1, 2, True), (0, 1, True))
)
HR = {"gate": 0, "ad": 0x0F, "sr": 0}
EDGES = (0, 1, 2, 3, 0x0F, 0x10, 0x3F, 0x40, 0x7F, 0x80, 0x81, 0x90, 0xDF, 0xE0, 0xFE, 0xFF)
EDGES += tuple(range(0xF0, 0xFE))

# (raw row, written row or None for no shape, raw row it reads back as when not the same)
ROWS = {
    ("goattracker2", "ctrltab"): [
        ({"w": 0xE9, "n": 0}, {"ctrl": 9, "note": "+0"}),
        ({"w": 129, "n": 196}, {"ctrl": 129, "note": "=68"}),
        ({"w": 1, "n": 5}, {"wait": 1, "note": "+5"}),
        ({"w": 65, "n": 0x80}, {"ctrl": 65}),
        ({"w": 0, "n": 12}, {"note": "+12"}),
        ({"w": 0, "n": 0x7F}, {"note": "-1"}),
        ({"w": 0xF1, "n": 0, "x": 2}, {"freq": ("x", 2)}),
        ({"w": 0xF5, "n": 0x19}, {"ad": 0x19}),
        ({"w": 0xF6, "n": 0xC8}, {"sr": 0xC8}),
        ({"w": 0xF9, "n": 3}, {"run": ("pulsetab", 3)}),
        ({"w": 0xFA, "n": 2}, {"run": ("filttab", 2)}),
        ({"w": 0xFB, "n": 0xC1}, {"res": 12, "route": 1}),
        ({"w": 0xFC, "n": 0x80}, {"cut": 1024}),
        ({"w": 0xFD, "n": 9}, {"vol": 9}),
        ({"w": 0xF9, "n": 0}, {"pw": NONE}),
        ({"w": 0xFA, "n": 0}, {"filter": NONE}),
        ({"w": 0xFB, "n": 0}, {"res": 0, "route": 0, "filter": NONE}),
        *(({"w": 0xF0 + c, "n": 1}, None) for c in (0, 1, 2, 3, 4, 7, 8, 14)),
        ({"w": 0xF1, "n": 0}, None),
        ({"w": 0xF2, "n": 0, "x": 1}, None),
        ({"w": 0xFD, "n": 0x10}, None),
    ],
    ("goattracker2", "pulsetab"): [
        ({"l": 0x84, "r": 0}, {"pw": 1024}),
        ({"l": 0, "r": 16}, None),
        ({"l": 32, "r": 16}, None),
    ],
    ("goattracker2", "filttab"): [
        ({"l": 0x90, "r": 193, "cut": 128}, {"mode": 16, "res": 12, "route": 1, "cut": 1024}),
        ({"l": 0x90, "r": 193}, {"mode": 16, "res": 12, "route": 1}),
        ({"l": 0x90, "r": 193, "x": 3}, {"mode": 16, "res": 12, "route": 1, "cut": ("x", 3)}),
        ({"l": 0x90, "r": 1, "cut": 128}, {"mode": 16, "res": 0, "route": 1, "cut": 1024}),
        ({"l": 0, "r": 240}, {"cut": 1920}),
        ({"l": 5, "r": 1, "cut": 3}, None),
        ({"l": 0, "r": 5, "x": 3}, None),
    ],
    ("sidwizard", "wftab"): [
        ({"w": 17, "a": 0, "h": 1}, {"hold": 2, "ctrl": 17, "note": "+0"}),
        ({"w": 80, "a": 0xFC, "h": 0x3F}, {"hold": 64, "ctrl": 80, "note": "-4"}),
        ({"w": 65, "a": 0x80}, {"ctrl": 65}),
        (
            {"w": 0x41, "a": 0x0C, "d": 3, "h": 2},
            {"hold": 3, "ctrl": 0x41, "note": "+12", "freq": 3},
        ),
        ({"w": 0x21, "a": 0xF4, "d": 0xF0}, {"ctrl": 0x21, "note": "-12", "freq": -16}),
        ({"w": 0, "a": 5}, {"note": "+5"}),
        ({"w": 3, "a": 0x80, "d": 0x80, "h": 3}, {"hold": 4, "freq": -128}),
        ({"w": 0x0F, "a": 0x99, "h": 0x0F}, {"hold": 16, "note": "=24"}),
        ({"w": 0x50, "a": 0x7F}, None),
        ({"w": 0x50, "a": 0, "h": 0x40}, None),
        ({"w": 5, "a": 3, "h": 2}, None),
    ],
    ("sidwizard", "pwtab"): [
        ({"a": 0x81, "b": 2}, {"pw": 258}),
        ({"a": 64, "b": 248}, None),
        ({"a": 0x81, "b": 2, "k": 1}, None),
    ],
    ("sidwizard", "flttab"): [
        ({"a": 0x90, "res": 3, "x": 4}, {"mode": 16, "cut": ("x", 4), "res": 3}),
        ({"a": 0x90, "b": 8, "res": 3}, {"mode": 16, "cut": 64, "res": 3}),
        ({"a": 0x90, "b": 8, "x": 4}, None),
        ({"a": 0x90, "b": 8, "k": 1}, None),
    ],
    ("defmon", "sidtab"): [
        (
            {"dl": 1, "wgh": 80, "wgl": 5, "ad": 0, "sr": 68, "tr": 12, "xp": 2},
            {"hold": 2, "ctrl": "80^5", "note": "+12", "ad": 0, "sr": 68, "pw": ("x", 2)},
        ),
        (
            {"dl": 1, "wgh": 64, "wgl": 1, "af": 0x80, "xf": 2, "xc": 5},
            {"hold": 2, "ctrl": "64^1", "freq": ("x", 2), "cut": ("x", 5)},
        ),
        ({"dl": 242, "tr": 180}, {"note": "=28"}, {"tr": 180}),
        ({"wgl": 8}, {"ctrl": "^8"}),
        ({"wgh": 16}, {"ctrl": 16}),
        ({"re": 0}, {"route": "-"}),
        ({"re": 0xF8}, {"res": 15, "route": "+ 8"}),
        ({"re": 0x9F}, {"res": 9, "route": "+ 15"}),
        ({"re": 0xF7}, {"res": 15, "route": 7}),
        ({"re": 0x10}, {"res": 1, "route": 0}),
        ({"fv": 0x20}, {"mode": 0x20}),
        ({"fv": 0x2F}, {"mode": 0x20}, {"fv": 0x20}),
        *(({"af": b}, None) for b in (0, 0x10, 0x7F, 0x90)),
        *(({c: 1}, None) for c in ("pw", "ps", "acid", "cp")),
        ({"af": 0x80}, None),
        ({"af": 0x10, "xf": 1}, None),
        ({"af": 0x80, "xf": 1, "pw": 1}, None),
        ({"af": 0x80, "xf": 1, "acid": 0x8001}, None),
    ],
}

# (place, written restart row, whether the player plays it)
RESTARTS = {
    "goattracker2": [
        (FIRST, {"hold": 4, **HR}, True),
        (FIRST, {"hold": 63, "gate": 0}, True),
        (FIRST, {}, True),
        (LAST, {"ctrl": 9, "gate": 1}, True),
        (LAST, {"gate": 0}, True),
        (LAST, {}, True),
        (FIRST, {"hold": 64, "gate": 0}, False),
        (FIRST, {**HR, "ad": 0x1F}, False),
        (FIRST, {"ad": 0x0F, "sr": 0}, False),
        (FIRST, {"ctrl": 9}, False),
        (MIDDLE, {"hold": 2}, False),
        (LAST, {"hold": 2}, False),
        (LAST, {"ctrl": 9}, False),
        (LAST, {"ctrl": 0xFE, "gate": 1}, False),
        (ONLY, {"ctrl": 9, "gate": 1}, False),
        (LAST, {"ctrl": "^8", "gate": 1}, False),
    ],
    "sidwizard": [
        (FIRST, {"hold": 2, "ctrl": 0x18, **HR}, True),
        (FIRST, {**HR, "ad": 0x2F}, True),
        (LAST, {"ctrl": 9}, True),
        (ONLY, {}, True),
        (FIRST, {"hold": 3, **HR}, False),
        (FIRST, {"hold": 2, "ctrl": 0x20, **HR}, False),
        (FIRST, {"hold": 2, "ad": 0x0F, "sr": 0}, False),
        (MIDDLE, {"hold": 1, **HR}, False),
        (LAST, {"gate": 0}, False),
    ],
    "defmon": [
        (FIRST, {"hold": 3, "ctrl": "65^1", "ad": 9}, True),
        (LAST, {}, True),
        (LAST, {"ctrl": 65}, False),
    ],
}

# written rows the player can't play that no generated rule below finds
REFUSALS = {
    ("goattracker2", "ctrltab"): [
        ({"ctrl": 0xE5}, r"values of \['ctrl'\]"),
        ({"ctrl": 9, "wait": 1}, "values of"),
        ({"note": "=0"}, r"values of \['note'\]"),
        ({"vol": 16}, "no shape"),
        ({"res": 0, "route": 0}, "no shape"),
        ({"res": 1, "route": 0, "filter": NONE}, "values of"),
    ],
    ("goattracker2", "pulsetab"): [({"pw": 4096}, r"values of \['pw'\]")],
    ("goattracker2", "filttab"): [
        ({"mode": 16, "res": 12}, r"values of \['route'\]"),
        ({"mode": 16, "res": 16, "route": 1}, r"values of \['res'\]"),
        ({"mode": 16, "res": 1, "route": "+"}, r"values of \['route'\]"),
    ],
    ("sidwizard", "flttab"): [({"mode": 16, "res": 16, "cut": 64}, r"values of \['res'\]")],
    ("sidwizard", "wftab"): [
        *(({"hold": h, "ctrl": 17}, "can't express") for h in (0, 1, 65)),
        *(({"hold": h}, "can't express") for h in (0, 1, 17)),
        ({"ctrl": 5}, "can't express"),
        ({"freq": -1}, "can't express"),
        ({"freq": 200}, r"values of \['freq'\]"),
    ],
    ("defmon", "sidtab"): [
        ({"route": "+"}, r"values of \['route'\]"),
        ({"route": "- 1"}, r"values of \['route'\]"),
        ({"res": 3}, r"values of \['res'\]"),
        ({"res": 3, "route": 8}, r"values of \['route'\]"),
        ({"res": 1, "route": "-"}, r"values of \['res'\]"),
        ({"pw": 256}, "values of"),
        ({"cut": 1024}, "values of"),
    ],
}

# (code, arg, written command or None for no shared name, read-back (code, arg) if not the same)
COMMAND_ROWS = {
    "goattracker2": [
        (15, 16, ("tempo", 16)),
        (15, 3, ("tempo", 3)),
        (15, 2, ("tempo", 3), (15, 3)),
        (15, 0x90, ("vtempo", 16)),
        (12, 128, ("cut", 1024)),
        (13, 15, ("vol", 15)),
        (13, 0x20, ("mark", 0x20)),
        (8, 3, ("run", ("ctrltab", 3))),
        (10, 2, ("run", ("filttab", 2))),
        (16, 3, ("freq", ("x", 3))),
        (16, 0, ("freq", NONE)),
        (17, 0, ("legato", 0)),
        (14, 0, ("funk", NONE)),
        (14, 0x10609, ("funk", (6, 9))),
        (14, 0x10000, ("funk", (0, 0))),
        (14, 6, None),
        (15, 0, None),
        (17, 1, None),
        (4, 1, None),
    ],
    "sidwizard": [
        (16, 3, ("freq", ("x", 3))),
        (16, 0, ("freq", NONE)),
        (63, 0, ("legato", 0)),
        (112, 5, ("run", ("wftab", 5))),
        (112, 0, ("run", NONE)),
        (17, 0x57, ("funk", (5, 7))),
        (17, 0x0F, ("funk", (6, 15)), (17, 0x6F)),
        (18, 0x84, ("tempo", 4), (18, 4)),
        (18, 0, ("tempo", 6), (18, 6)),
        (19, 127, ("vtempo", 127)),
        (17, 0x26, None),
    ],
    "defmon": [(1, 4, None)],
}

# written commands the player can't play
COMMAND_REFUSALS = {
    "goattracker2": [
        ("cut", 1025),
        ("tempo", 2),
        ("run", 5),
        ("funk", ("ctrltab", 1)),
        *(("funk", v) for v in [(6, 256), (6,), (6, 6, 6), 6]),
        ("legato", 1),
        ("freq", 5),
    ],
    "sidwizard": [
        ("freq", 5),
        ("legato", 1),
        ("tempo", 2),
        *(("funk", v) for v in [(2, 6), (6, 16), (16, 6), (6,), (6, 6, 6), 6, NONE, ("x", 1)]),
    ],
    "defmon": [("vol", 15)],
}


def full(rtype, row: dict) -> dict:
    return {f: int(row.get(f, d)) for f, d in rtype.fields.items()}


def as_written(shared: dict | None) -> dict | None:
    return None if shared is None else {k: written_value(k, v) for k, v in shared.items()}


def written(c: RowCodec, raw: dict, place=None) -> dict | None:
    return as_written(c.encode(raw, place))


def mutants(rtype, seeds: list, rng) -> list:
    """``seeds`` and rows around them: each seed with one or two fields changed, and rows of
    fields drawn from the field's default, an edge byte or any value it holds."""
    fields = [f for f in rtype.fields if f != rtype.chain]

    def pick(f):
        low = min(rtype.default(f), 0)
        return int(rng.choice((rtype.default(f), *EDGES, int(rng.integers(low, 256)))))

    out = [full(rtype, s) for s in seeds]
    for seed in out[: len(seeds)] * 64:
        out.append({**seed, **{f: pick(f) for f in rng.choice(fields, rng.integers(1, 3))}})
    return out + [{f: pick(f) for f in fields} for _ in range(1024)]


@functools.cache
def sample(player: str, name: str) -> list:
    """``(place, shape index, raw, shared)`` of the rows around the type's documented ones that
    some shape writes."""
    c, rng = CODECS[player][name], np.random.default_rng(sorted(CODECS[player]).index(name))
    seeds = [r[0] for r in ROWS.get((player, name), ())]
    seeds += [c.decode(w, p) for p, w, ok in RESTARTS[player] if ok and name == "restart"]
    out = []
    for raw, place in itertools.product(mutants(c.rtype, seeds, rng), PLACES):
        env = {**raw, **place}
        hit = [s.when is None or bool(c.ev(s.when, env)) for s in c.shapes]
        if any(hit):
            out.append((place, hit.index(True), raw, c.encode(raw, place)))
    return out


@pytest.mark.parametrize("player,name", ROW_CODECS)
def test_every_shape_reads_back_what_it_writes(player, name):
    c = CODECS[player][name]
    rows = sample(player, name)
    assert {k for _, k, _, _ in rows} == set(range(len(c.shapes)))
    for place, _, raw, shared in rows:
        back = c.decode(as_written(shared), place)
        assert c.encode(back, place) == shared, (raw, shared, back)


@pytest.mark.parametrize("player,name", list(ROWS))
def test_rows_mean_what_the_player_plays(player, name):
    c = CODECS[player][name]
    for raw, shared, *back in ROWS[player, name]:
        assert written(c, raw) == shared, raw
        if shared is not None:
            assert full(c.rtype, c.decode(shared)) == full(c.rtype, back[0] if back else raw)


@pytest.mark.parametrize(
    "field,values",
    [("re", range(-1, 256)), ("tr", range(256)), ("fv", [-1, *range(0, 256, 16)])],
)
def test_defmon_columns_read_back_every_byte(field, values):
    c = CODECS["defmon"]["sidtab"]
    for byte in values:
        assert c.decode(written(c, {field: byte}))[field] == byte


@pytest.mark.parametrize("player", PLAYERS)
def test_restart_rows_hold_only_what_the_player_plays_at_their_place(player):
    c = CODECS[player]["restart"]
    for place, shared, ok in RESTARTS[player]:
        if ok:
            assert written(c, c.decode(shared, place), place) == shared
            continue
        with pytest.raises(CodecError, match="^restart: the player can't express row"):
            c.decode(shared, place)


@pytest.mark.parametrize("player,name", list(REFUSALS))
def test_rows_the_player_cannot_play_are_refused(player, name):
    for shared, match in REFUSALS[player, name]:
        with pytest.raises(CodecError, match=match):
            CODECS[player][name].decode(shared)


def shaped(c: RowCodec, key: str, attr: str) -> list:
    """The shapes whose ``key`` has ``attr``."""
    return [s for s in c.shapes if attr in s.keys.get(key, {})]


def int_values(player: str, name: str) -> dict:
    """``{key: [(place, shared row)]}`` of the integer shared keys the sampled rows write."""
    out = {}
    for place, _, _, shared in sample(player, name):
        for k, v in shared.items():
            if k in ROW_KEYS and isinstance(v, int) and not isinstance(v, bool):
                out.setdefault(k, []).append((place, shared))
    return out


@pytest.mark.parametrize("player,name", ROW_CODECS)
def test_keys_outside_every_shape_or_that_no_shape_combines_are_refused(player, name):
    c = CODECS[player][name]
    key = next(k for k in ROW_KEYS if k not in c.keys)
    with pytest.raises(CodecError, match=f"^unknown key {key} in {c.rtype.written}$"):
        c.decode({key: 0})
    shapes = [{*s.keys, *s.keep} for s in c.shapes]
    values = {k: v for _, _, _, shared in sample(player, name) for k, v in shared.items()}
    for pair in itertools.combinations(sorted(values), 2):
        if not any(set(pair) <= s for s in shapes):
            with pytest.raises(CodecError, match=r"\(keys \["):
                c.decode(as_written({k: values[k] for k in pair}))


@pytest.mark.parametrize("player,name", ROW_CODECS)
def test_values_off_the_grid_a_key_takes_are_refused(player, name):
    c = CODECS[player][name]
    for key, found in int_values(player, name).items():
        place, shared = found[0]
        if math.gcd(*(row[key] for _, row in found)) > 1:
            with pytest.raises(CodecError, match=rf"values of \['{key}'\]|no shape"):
                c.decode(as_written({**shared, key: shared[key] + 1}), place)


@pytest.mark.parametrize("player,name", ROW_CODECS)
def test_expressions_none_and_references_go_only_where_a_shape_holds_them(player, name):
    c, rtypes = CODECS[player][name], TYPES[player]
    for key in sorted(c.keys & set(ROW_KEYS)):
        exprs = shaped(c, key, "expr")
        if not exprs and key not in c.ref_keys():
            with pytest.raises(
                CodecError, match=f"{key} (holds no expression|is only none) here|bad {key}"
            ):
                c.decode({key: ("x", 1)})
        for place, k, _, shared in (r for r in sample(player, name) if c.shapes[r[1]] in exprs):
            row = {**as_written(shared), key: ("x", 1)}
            assert c.decode(row, place)[c.shapes[k].keys[key]["expr"]] == 1
        if exprs and not any("value" in s.keys[key] for s in exprs):
            with pytest.raises(CodecError, match="the player can't express row"):
                c.decode({key: 5})
        if shaped(c, key, "none"):
            with pytest.raises(CodecError, match=f"{key} is only none here"):
                c.decode({key: 5})
        else:
            with pytest.raises(CodecError, match=f"{key} can't be none here|bad {key}"):
                c.decode({key: NONE})
        if key == "ctrl" and not shaped(c, key, "eor"):
            with pytest.raises(CodecError, match="ctrl holds no mask here"):
                c.decode({"ctrl": "^8"})
    for key, (field, types) in c.ref_keys().items():
        for tname in types:
            assert c.decode({key: (tname, 3)})[field] == 3
        other = next(t for t in sorted(rtypes) if t not in types)
        with pytest.raises(CodecError, match=f"{key} names no "):
            c.decode({key: (other, 3)})


def test_expression_and_reference_keys():
    keys = {name: (c.expr_keys(), c.ref_keys()) for p in PLAYERS for name, c in CODECS[p].items()}
    assert keys == {
        "restart": ({}, {}),
        "ctrltab": ({"freq": False}, {"run": ("n", {"pulsetab", "filttab"})}),
        "pulsetab": ({}, {}),
        "filttab": ({"cut": True}, {}),
        "wftab": ({}, {}),
        "pwtab": ({}, {}),
        "flttab": ({"cut": True}, {}),
        "sidtab": ({"pw": False, "cut": False, "freq": False}, {}),
        "vinit": ({}, {}),
    }


def test_defmon_programs_end_on_a_freeze():
    c = CODECS["defmon"]["sidtab"]
    end = {"dl": 0x80, "tr": 180, "af": 0x80, "xf": 1}
    c.check_end({"dl": 0x80, "tr": 180}, {"note": "=28"})
    c.check_end(end, {"note": "=28", "freq": "v + 1"})
    bad = [
        ({"dl": 0x80}, {"hold": 3}),
        (end, {"note": "=28"}),
        (end, {"note": "=29", "freq": "v"}),
        ({"dl": 0x80, "tr": 180}, {"note": "=28", "freq": "v"}),
    ]
    for raw, row in bad:
        with pytest.raises(CodecError, match="can't end"):
            c.check_end(raw, row)
    doc = {
        "unitracker": songio.VERSION,
        "playroutine": {"ref": "defmon"},
        "song": [{"def": {"ctrl": {"c1": [{"ctrl": 65}, {"hold": 3}, "end"]}}, "play": ["~"] * 3}],
    }
    with pytest.raises(songio.SongFormatError, match="can't end a program"):
        songio.loads(yaml.safe_dump(doc))


@pytest.mark.parametrize("player", PLAYERS)
def test_every_command_reads_back_what_it_writes(player):
    cmds = COMMANDS[player]
    codes = sorted({code for code, *_ in cmds.entries} | set(cmds.unnamed))
    named = set()
    for code, arg in itertools.product(codes, range(256)):
        try:
            name, value = cmds.encode(code, arg)
        except CodecError as exc:
            assert str(exc) == f"command {code} with argument {arg} has no shared name" or (
                arg == TYPES[player][cmds.ref(code)].none and "can't say which" in str(exc)
            )
            continue
        named.add(code)
        assert cmds.encode(*cmds.decode(name, value)) == (name, value)
    assert named == {code for code, *_ in cmds.entries}
    for code in set(range(max(codes) + 2)) - set(codes):
        with pytest.raises(CodecError, match="no shared name"):
            cmds.encode(code, 1)


@pytest.mark.parametrize("player", PLAYERS)
def test_commands_mean_what_the_player_plays(player):
    cmds = COMMANDS[player]
    for code, arg, shared, *back in COMMAND_ROWS[player]:
        if shared is None:
            with pytest.raises(CodecError, match="has no shared name"):
                cmds.encode(code, arg)
            continue
        assert cmds.encode(code, arg) == shared
        assert cmds.decode(*shared) == (back[0] if back else (code, arg))
    for name, value in COMMAND_REFUSALS[player]:
        with pytest.raises(CodecError, match=f"can't express !{name}:"):
            cmds.decode(name, value)


@pytest.mark.parametrize("player", PLAYERS)
def test_command_names_the_player_cannot_say_are_refused(player):
    cmds = COMMANDS[player]
    names = {name for _, name, _, _ in cmds.entries}
    for name in set(COMMAND_NAMES) - names:
        with pytest.raises(CodecError, match=f"can't express !{name}:1$"):
            cmds.decode(name, 1)
    with pytest.raises(CodecError, match="unknown command !noteglide"):
        cmds.decode("noteglide", 1)
    for code, name, ref, _ in cmds.entries:
        if ref is None:
            continue
        with pytest.raises(CodecError, match="a byte"):
            cmds.decode(name, (ref, 256))
        codes = {c for c, n, r, _ in cmds.entries if n == name and r is not None}
        if len(codes) > 1:
            with pytest.raises(
                CodecError, match=re.escape(f"can't say which of commands {sorted(codes)}")
            ):
                cmds.encode(code, TYPES[player][ref].none)
    assert all(cmds.ref(code) == ref for code, ref in cmds.unnamed.items())


@pytest.mark.parametrize(
    "spec,match",
    [
        ({"keys": {"wave": 1}}, "not a shared row key"),
        ({"keep": ["ctrl"]}, "collide with shared keys"),
        ({"keys": {"run": {"ref": "pulsetab"}}}, "both ref and field"),
        ({"raw": {"w": {"range": [0, 3]}}}, "a raw field is an expression"),
        ({1: {"name": "mod"}}, "not a shared command name"),
        ({1: {"name": "run", "ref": "nope"}}, "unknown type 'nope'"),
    ],
)
def test_codec_declarations_are_checked(spec, match):
    types = TYPES["goattracker2"]
    with pytest.raises(CodecError, match=match):
        if 1 in spec:
            CommandCodec(spec, types)
        else:
            RowCodec(types["ctrltab"], [spec], 0)


def test_codec_expressions_read_only_declared_tables():
    assert Evaluator(None, {"t": [7]})("t[0] + t[5]", {}) == 7
    for call in (lambda: Evaluator()("t[0]", {}), lambda: evaluate("nope(", {})):
        with pytest.raises(CodecError):
            call()


@pytest.mark.parametrize(
    "parse,text,forms,bad",
    [
        (
            note_value,
            lambda v: note_text(*v),
            [("=68", (True, 68)), ("-3", (False, -3)), ("=-24", (True, -24)), ("+5", (False, 5))],
            [True, "=+3", "x", 1.5],
        ),
        (
            route_value,
            lambda v: route_text(*v),
            [(7, (0, 7)), ("+", (1, 0)), ("-", (-1, 0)), ("+ 8", (1, 8)), ("- 3", (-1, 3))],
            [True, "+8", "x", "+ x"],
        ),
        (
            ctrl_value,
            ctrl_text,
            [(65, 65), ("^8", (None, 8)), ("64^1", (64, 1)), ("16^0", (16, 0))],
            [True, "8^", "^-1", "x"],
        ),
    ],
)
def test_written_forms_read_and_write_as_themselves(parse, text, forms, bad):
    for written_form, value in forms:
        assert parse(written_form) == value
        assert text(value) == written_form
    for value in bad:
        with pytest.raises(CodecError, match="bad"):
            parse(value)
    assert note_value(5) == (False, 5)


def midi(freq_word):
    return round(69 + 12 * math.log2(freq_word * 985248 / 16777216 / 440))


@pytest.mark.parametrize(
    "player,words,notes",
    [
        ("goattracker2", lambda t, i: t["ftab"][i], range(24, 96)),
        ("defmon", lambda t, i: t["note_lo"][i] | t["note_hi"][i] << 8, range(24, 110)),
        ("sidwizard", lambda t, i: t["freqlo"][i] | t["freqhi"][i] << 8, range(1, 96)),
    ],
)
def test_note_offsets_follow_the_frequency_tables(player, words, notes):
    pr = LIBS[player]
    for index in notes:
        assert midi(words(pr["tables"], index)) == index - pr.get("note_offset", 0) + 12


def defmon_doc(vinit, ginit=None):
    song = {
        "def": {"vinit": {0: vinit}, "ginit": {0: ginit or {}}, "phrase": {"p1": ["-*4"]}},
        "play": ["p1"] * 3,
    }
    doc = {"unitracker": songio.VERSION, "playroutine": {"ref": "defmon"}, "song": [song]}
    return songio.loads(yaml.safe_dump({**doc, "loop": 0}))


def nfreq(note):
    tables = LIBS["defmon"]["tables"]
    return tables["note_lo"][note] | tables["note_hi"][note] << 8


@pytest.mark.parametrize("freq", [3, -5, 0])
def test_defmon_init_keys_mean_what_they_mean_in_rows(freq):
    ir = defmon_doc({"pw": 0x0567, "note": "=36", **({"freq": freq} if freq else {})}, {"sub": 1})
    voice, word = ir["records"]["vinit"][0], nfreq(60) + freq
    assert (voice["pw"], voice["tbase"], voice["flo"] | voice["fhi"] << 8) == (0x0567, 60, word)
    assert ir["records"]["ginit"][0] == {"sub": 1}
    grid = SongPlayer(ir).render_grid(2)
    assert list(grid[0][0:4]) == [word & 0xFF, word >> 8, 0x67, 0x05]
    text = songio.dumps(ir)
    assert f"0: {{note: =36, pw: 1383{f', freq: {freq}' if freq else ''}}}" in text
    assert songio.dumps(songio.loads(text)) == text


def test_defmon_init_frequency_is_an_offset_that_wraps():
    ir = defmon_doc({})
    voice = ir["records"]["vinit"][0]
    assert voice["flo"] | voice["fhi"] << 8 == nfreq(0)
    voice.update(flo=0xFF, fhi=0xFF, tbase=0)
    assert f"freq: {0xFFFF - nfreq(0) - 0x10000}" in songio.dumps(ir)
    voice.update(flo=0, fhi=0, tbase=100)
    text = songio.dumps(ir)
    assert f"freq: -{nfreq(100)}" in text and songio.dumps(songio.loads(text)) == text


def test_defmon_init_cells_without_a_written_form_are_refused():
    for vinit, key in [({"pw": 0x1100}, "pw"), ({"note": "+3"}, "note")]:
        with pytest.raises(songio.SongFormatError, match=rf"values of \['{key}'\]"):
            defmon_doc(vinit)
    ir = defmon_doc({})
    ir["records"]["vinit"][0]["pw"] = 0x1100
    with pytest.raises(songio.SongFormatError, match=r"vinit.*values of \['pw'\]"):
        songio.dumps(ir)
    ir = defmon_doc({"pw": 5})
    ir["playroutine"]["record_types"]["vinit"]["codec"][0]["when"] = "0"
    with pytest.raises(songio.SongFormatError, match="no shared-vocabulary form"):
        songio.dumps(ir, embed=True)


DM_ROUTES = """
unitracker: 1.19
playroutine: {ref: defmon}
song:
  - def:
      ins:
        i1: {ctrl: [{res: 2, route: 3}, end]}
        i2: {ctrl: [{res: 15, route: + 8}, end]}
        i3: {ctrl: [{route: '-'}, end]}
      phrase:
        p1: [-*8]
        p2: [-.i1*2, -.i2*4, -.i3*2]
    play: [p1, p2, p1]
loop: 0
"""


def test_defmon_routes_relative_to_the_programs_voice():
    ir = songio.loads(DM_ROUTES)
    text = songio.dumps(ir)
    assert "route: + 8" in text and "route: '-'" in text
    assert songio.dumps(songio.loads(text)) == text
    d417 = [int(r[0x17]) for r in SongPlayer(ir).render_grid(10)]
    assert list(dict.fromkeys(d417)) == [0, 0x23, 0xF8 | 0x03 | 2, 0xF8 | 0x01]


SW_CHORD = """
unitracker: 1.19
playroutine: {ref: sidwizard}
song:
  - def:
      ins:
        i1: {ad: 1, sr: 240, ctrl: [{hold: 2, ctrl: 17, note: +0}, {hold: 2, ctrl: 17, note: +3}, {
            loop: 0}]}
      ctrl:
        c1: [{hold: 3, ctrl: 17, note: +4}, {hold: 3, ctrl: 17, note: +7}, {loop: 0}]
      phrase:
        p1: [C4.i1*4, C4!run:@c1*4]
    play: [p1, p1, p1]
loop: 0
"""


def test_sidwizard_chords_are_ctrl_programs_started_by_run():
    ir = songio.loads(SW_CHORD)
    assert songio.dumps(songio.loads(songio.dumps(ir))) == songio.dumps(ir)
    assert ir["phrases"][next(iter(ir["phrases"]))][1]["command"][0] == 112
    freq = [int(r[0]) | int(r[1]) << 8 for r in SongPlayer(ir).render_grid(48)]
    for steps, hold in ((freq[6:24], 2), (freq[30:48], 3)):
        runs = [len(list(g)) for _, g in itertools.groupby(steps)]
        assert set(runs[1:-1]) == {hold} and len(set(steps)) == 2
    assert not set(freq[6:24]) & set(freq[30:48])


def atom_doc(player: str, atom: str) -> str:
    return (
        f"unitracker: {songio.VERSION}\nplayroutine: {{ref: {player}}}\nsong:\n  - def:\n"
        f"      phrase:\n        p1: ['{atom}']\n    play: [p1, p1, p1]\nloop: 0\n"
    )


@pytest.mark.parametrize(
    "player,played,refused",
    [
        ("goattracker2", [], ["tempo:2"]),
        (
            "sidwizard",
            ["tempo:3", "vtempo:127", "funk:3/15", "funk:6/7"],
            ["tempo:2", "vtempo:128", "funk:2/6", "funk:6/16", "funk:6", "funk:none"],
        ),
        ("defmon", [], ["tempo:6"]),
    ],
)
def test_documents_take_the_timing_commands_the_player_plays(player, played, refused):
    for command in played:
        assert f"-!{command}" in songio.dumps(songio.loads(atom_doc(player, f"-!{command}")))
    for command in refused:
        with pytest.raises(
            songio.SongFormatError,
            match=f"^{player}: the player can't express !{re.escape(command)} in ",
        ):
            songio.loads(atom_doc(player, f"-!{command}"))


DOCS = Path(__file__).resolve().parents[1] / "docs" / "playroutines"
HEADER = "| command | argument |"
KEYS_HEADER = "| `ctrl` row key | forms |"


def spans(values) -> str:
    """Sorted integers as runs of one step: ``a``, ``a-b`` or ``a-b step s``."""
    values, out, k = sorted(values), [], 0
    while k < len(values):
        j, step = k, values[k + 1] - values[k] if k + 1 < len(values) else 1
        while j + 1 < len(values) and values[j + 1] - values[j] == step:
            j += 1
        if j - k < (1 if step == 1 else 2):
            out.append(f"{values[k]}")
            k += 1
            continue
        out.append(f"{values[k]}-{values[j]}" + ("" if step == 1 else f" step {step}"))
        k = j + 1
    return ", ".join(out)


def cell(values: set) -> str:
    """The written values a command takes: for an argument of several values, each position's
    runs joined by ``/``, which hold only when every combination is taken."""
    if not all(isinstance(v, tuple) for v in values):
        return spans(values)
    columns = [{v[k] for v in values} for k in range(len(next(iter(values))))]
    assert len(values) == math.prod(map(len, columns)), sorted(values)
    return "/".join(spans(c) for c in columns)


def readable(cmds, code: int, name: str, var: dict):
    """The written values of command ``name`` that variant ``var`` of ``code`` reads: each tuple of
    bytes for one with ``args``, else what encoding each byte argument gives."""
    if "args" in var:
        tried = itertools.product(range(256), repeat=len(var["args"]))
    else:
        tried = []
        for arg in range(256):
            with contextlib.suppress(CodecError):
                ename, value = cmds.encode(code, arg)
                tried += [value] if ename == name else []
    for value in tried:
        with contextlib.suppress(CodecError):
            cmds.decode(name, value)
            yield value


def accepted(player: str) -> dict:
    """``{name: cell}``: what each shared command takes in the playroutine."""
    pr, rtypes, cmds = LIBS[player], TYPES[player], COMMANDS[player]
    values, refs = {}, {}
    for code, name, ref, var in cmds.entries:
        if ref is not None:
            refs.setdefault(name, []).append(f"`@{rtypes[ref].written}`")
            continue
        if "none" in var:
            continue
        values.setdefault(name, set()).update(readable(cmds, code, name, var))
    cells = {n: cell(v) for n, v in values.items()}
    for name in {n for _, n, _, var in cmds.entries if "none" in var}:
        cells[name] += f", `{NONE}`"
    for name in envelope_names(pr.get("commands")):
        cells[name] += ", at most one nibble from the instrument's"
    for name, names in refs.items():
        try:
            names += [f"`{NONE}`"] if cmds.decode(name, NONE) else []
        except CodecError:
            pass
        cells[name] = ", ".join(names)
    if pr.get(restart.NAME):
        cells[restart.NAME] = f"`@{restart.NAME}`"
    cells.update({n: "0" for n in pr.get(lifetime.NAME) or {}})
    return cells


def key_forms(player: str) -> dict:
    """``{key: text}``: the forms each row key takes in the playroutine's ``ctrl`` programs."""
    rtypes = TYPES[player]
    rtype = next(t for t in rtypes.values() if t.written == "ctrl")
    forms = {}
    for shape in rtype.codec:
        for key, spec in (shape.get("keys") or {}).items():
            spec = spec if isinstance(spec, dict) else {"value": spec}
            found = forms.setdefault(key, {})
            found.update(dict.fromkeys(["value"] if "value" in spec else []))
            found.update(dict.fromkeys(["`^n`"] if "eor" in spec else []))
            found.update(dict.fromkeys(["`+ m`, `-`"] if "rel" in spec else []))
            found.update(dict.fromkeys(["expression"] if "expr" in spec else []))
            found.update(dict.fromkeys([f"`{NONE}`"] if spec.get("none") else []))
            found.update(
                dict.fromkeys([f"`{rtypes[spec['ref']].written}` name"] if "ref" in spec else [])
            )
    return {k: ", ".join(v) for k, v in forms.items()}


def documented(player: str, header: str) -> list[str]:
    """The lines of the table under ``header`` as docs/playroutines/<player>.md has them."""
    lines = (DOCS / f"{player}.md").read_text().splitlines()
    start = lines.index(header) if header in lines else len(lines)
    end = next((k for k in range(start, len(lines)) if not lines[k].startswith("|")), len(lines))
    return lines[start:end]


@pytest.mark.parametrize("player", PLAYERS)
def test_the_documented_tables_are_the_playroutines(player):
    cells, keys = accepted(player), key_forms(player)
    names = (n for n in (*COMMAND_NAMES, *lifetime.NAMES) if n in cells)
    commands = [HEADER, "|---|---|"] + [f"| `!{n}` | {cells[n]} |" for n in names]
    row_keys = [KEYS_HEADER, "|---|---|"] + [
        f"| `{k}` | {keys[k]} |" for k in ROW_KEYS if k in keys
    ]
    assert documented(player, HEADER) == commands, "\n".join(commands)
    assert documented(player, KEYS_HEADER) == row_keys, "\n".join(row_keys)


def test_spans():
    cases = [
        ([0], "0"),
        (range(256), "0-255"),
        (range(0, 2041, 8), "0-2040 step 8"),
        ([1, 2, 3, 7], "1-3, 7"),
        ([1, 5, 6, 7], "1, 5-7"),
        ([2, 4, 9], "2, 4, 9"),
        ([2, 4, 6, 9], "2-6 step 2, 9"),
    ]
    assert [spans(values) for values, _ in cases] == [text for _, text in cases]


SCHEMA = (DOCS / "schema.md").read_text()


def schema_section(heading: str) -> str:
    """The text of docs/playroutines/schema.md under ``## heading``, up to the next one."""
    return SCHEMA.split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]


def table_rows(text: str) -> dict[str, str]:
    """Each backticked name of a table's first column, mapped to its row's last cell."""
    rows = (line.split("|")[1:-1] for line in text.splitlines() if line.startswith("| `"))
    return {n: cells[-1] for cells in rows for n in re.findall(r"`(\w+)", cells[0])}


def code_words(text: str) -> set[str]:
    """The identifiers in the backticked spans and fenced blocks of ``text``."""
    code = re.findall(r"```.*?```|`[^`]+`", text, flags=re.S)
    return {w for s in code for w in re.findall(r"[A-Za-z_]\w*", s)}


def brace_keys(text: str) -> set[str]:
    """The names a ``{a, b: ...}`` form in ``text`` lists."""
    return set(re.findall(r"[{,]\s*(\w+)", text))


def schema_keys() -> dict[str, set[str]]:
    """Per schema level, the keys docs/playroutines/schema.md documents."""
    fence = re.search(r"```yaml\n(.*?)```", SCHEMA, flags=re.S).group(1)
    units = table_rows(schema_section("Modulator declarations").split("| unit key |", 1)[1])
    out = {
        "top": set(re.findall(r"(?m)^(\w+):", fence)),
        "block": code_words(f"`{fence}`"),
        "record type": set(table_rows(schema_section("Record types"))),
        "codec": code_words(schema_section("Codecs")),
        "action": set(table_rows(schema_section("Actions"))),
        "modulators": set(table_rows(schema_section("Modulator declarations"))),
        "unit": set(units),
    }
    out.update({f"unit.{k}": brace_keys(units[k]) for k in ("site", "retire", "hooks", "program")})
    out["unit.chip"] = brace_keys(units["chip"])
    return out


def used_keys(pr: dict):
    """(level, key) for each key a playroutine definition uses, at the levels the schema lists."""
    yield from (("top", k) for k in pr)
    nested = [pr.get("instrument") or {}, (pr.get("instrument") or {}).get("writes_as") or {}]
    nested += [pr.get("restart") or {}, pr.get("defaults") or {}]
    nested += [*(pr.get("lifetime") or {}).values(), *pr["voice"].values(), *pr["global"].values()]
    yield from (("block", k) for d in nested if isinstance(d, dict) for k in d)
    for spec in pr["record_types"].values():
        for k, v in spec.items():
            if k != "codec" and isinstance(v, list) and all(isinstance(a, dict) for a in v):
                yield from (("action", [*a][0]) for a in v)
            else:
                yield "record type", k
        yield from (("codec", k) for shape in spec.get("codec") or [] for k in shape)
    for entry in pr["commands"].values():
        yield from (
            ("codec", k) for e in (entry if isinstance(entry, list) else [entry]) for k in e
        )
    yield from (("modulators", k) for k in pr["modulators"])
    for unit in pr["modulators"]["units"]:
        yield from (("unit", k) for k in unit)
        for k in ("site", "retire", "hooks", "program", "chip"):
            for d in unit.get(k) if isinstance(unit.get(k), list) else [unit.get(k) or {}]:
                yield from ((f"unit.{k}", f) for f in d)


@pytest.mark.parametrize("player", PLAYERS)
def test_every_key_a_library_playroutine_uses_is_documented(player):
    known = schema_keys()
    assert {(lv, k) for lv, k in used_keys(LIBS[player]) if k not in known[lv]} == set()


def test_an_undocumented_playroutine_key_is_found():
    pr = {"started": 1, "voice": {}, "global": {}}
    pr["record_types"] = {"t": {"speed": 1, "do": [{"set": {}}, {"jump": 1}]}}
    pr.update(commands={1: {"name": "x", "scale": 2}}, modulators={"units": [{"scale": 1}]})
    known = schema_keys()
    found = {(lv, k) for lv, k in used_keys(pr) if k not in known[lv]}
    assert found == {
        ("top", "started"),
        ("record type", "speed"),
        ("action", "jump"),
        ("codec", "scale"),
        ("unit", "scale"),
    }
