"""The v2 document: canonical writing, reading back, and the errors a reader reports."""

import copy

import numpy as np
import pytest
import yaml
from pygoattracker import decompile_sid

from unitracker import songio
from unitracker.goattracker import lift
from unitracker.songio import SongFormatError
from unitracker.vm import SongPlayer

PLAYROUTINE = {
    "name": "mini",
    "note_offset": -12,
    "defaults": {
        "clock": {"cycles_per_tick": 19656, "clock_hz": 985248},
        "voices": 2,
        "registers": [0] * 25,
    },
    "instrument": {"type": "ins", "base": 0},
    "lanes": {"voice": {"pw": {}}},
    "specs": {"pw": ["step", "for", "from"], "freq": ["step", "toward"]},
    "spec_rules": [{"check": "not has(for_) or for_ <= 9", "error": "for is at most 9"}],
    "commands": {
        "ctrlprog": {"code": 8, "arg": "ref ctrlprog"},
        "pw": {"code": 2, "arg": "spec"},
        "funk": {"code": 3, "arg": "pair?"},
        "vol": {"code": 4, "arg": "int"},
        "hold": {"code": 5},
    },
    "record_types": {
        "ins": {
            "prefix": "i",
            "none": 0,
            "pin": [1],
            "keys": {
                "ad": "int",
                "ctrlprog": "ref ctrlprog",
                "keepgate": "flag",
                "pw": "value spec",
            },
            "rules": [{"check": "ad < 100", "error": "ad is below 100"}],
        },
        "ctrlprog": {
            "prefix": "c",
            "none": 0,
            "chain": "next",
            "keys": {
                "ctrl": "int",
                "note": "note",
                "pwprog": "ref ctrlprog",
                "pw": "value set,spec,off,live",
            },
            "do": [{"set": {"out": "row.ctrl"}}, {"apply": {"pw": "row.pw"}}],
        },
        "init": {"fixed": True, "keys": {"notebase": "abs"}},
    },
    "pitch": {"note": "n", "semi": {"args": ["k"], "expr": "k"}},
    "voice": {"ptr": {"width": 8}, "out": {"width": 8}, "pw": {"width": 12}, "n": {}},
    "procs": {
        "step": [
            {"if": "ev_instr", "then": [{"set": {"ptr": "ins[ev_instr].ctrlprog"}}]},
            {"if": "ev_cmd == 8", "then": [{"set": {"ptr": "ev_arg"}}]},
            {"if": "ev_cmd == 2", "then": [{"start": {"pw": "ev_arg"}}]},
            {
                "if": "ptr",
                "then": [
                    {"run": {"record": "ctrlprog", "index": "ptr", "field": "do"}},
                    {"set": {"ptr": "ctrlprog[ptr].next"}},
                ],
            },
            {"advance": "pw"},
        ]
    },
    "tick": [{"for_voices": [{"fetch": True}, {"call": "step"}]}],
    "ghost": {"voice": {0: "out", 1: "ev_note", 2: "pw & 0xFF", 3: "ins[1].ad + init[0].notebase"}},
}

SLIDE = {"step": 3}


def song() -> dict:
    """A two-voice song exercising every part of the writer."""
    phrase_a = [{"note": 24, "instrument": 1}, {}, {"note": 26, "instrument": 2, "steps": 2}]
    phrase_b = [{"note": 27, "instrument": 1}, {}, {"note": 29, "instrument": 2, "steps": 2}]
    return {
        "unitracker": "ir",
        "meta": {"title": "Mini", "author": "Test", "released": "2026", "source": "x"},
        "hints": {"ticks_per_step": 1},
        "clock": {"cycles_per_tick": 19656, "clock_hz": 985248},
        "voices": 2,
        "registers": [0] * 24 + [15],
        "tables": {"t": [1, 2, 3]},
        "playroutine": copy.deepcopy(PLAYROUTINE),
        "records": {
            "ins": {
                1: {"ad": 1, "ctrlprog": 1, "pw": dict(SLIDE)},
                2: {"ad": 2, "ctrlprog": 2, "keepgate": 1},
                3: {"ctrlprog": 4},
                9: {"ad": 7},
            },
            "ctrlprog": {
                1: {"ctrl": 10, "note": -3, "next": 2},
                2: {"ctrl": 20, "pw": dict(SLIDE), "next": 3},
                3: {"ctrl": 30, "noteabs": 40, "pw": "off", "next": 1},
                4: {"ctrl": 99, "pw": {"live": "semi(2)"}, "next": 5},
                5: {"ctrl": 50, "pwprog": 6, "pw": 7, "next": 0},
                6: {"ctrl": 60, "next": 0},
                7: {"ctrl": 1, "next": 0},
            },
            "init": {0: {"notebase": 5}},
        },
        "tracks": [
            {"plays": [{"phrase": "a"}, {"phrase": "b"}], "loop": 0},
            {"plays": [{"phrase": "c", "repeat": 2}], "loop": 0},
        ],
        "phrases": {
            "a": phrase_a,
            "b": phrase_b,
            "c": [
                {"command": ["ctrlprog", 1]},
                {"note": 30, "instrument": 3, "steps": 3, "command": ["pw", {"step": 1, "for": 2}]},
                {"command": ["funk", [8, 5]]},
                {"command": ["funk", None]},
                {"command": ["vol", 3]},
                {"command": ["hold", None]},
                {"command": ["ctrlprog", 0]},
            ],
            "unused": [{"note": 1}],
        },
    }


def grid(ir, n=64):
    return SongPlayer(ir).render_grid(n)


def test_roundtrip_plays_identically_and_is_canonical():
    ir = song()
    text = songio.dumps(ir, embed=True)
    back = songio.loads(text)
    np.testing.assert_array_equal(grid(back), grid(ir))
    assert songio.dumps(back, embed=True) == text
    doc = yaml.safe_load(text)
    assert list(doc) == ["unitracker", "playroutine", "meta", "hints", "registers", "song", "loop"]
    assert doc["meta"] == {"title": "Mini", "author": "Test", "released": "2026"}
    assert doc["registers"] == {24: 15}


def test_document_shape():
    doc = yaml.safe_load(songio.dumps(song(), embed=True))
    first = doc["song"][0]["def"]
    assert list(first)[:2] == ["spec", "init"]
    assert first["spec"] == {"v1": {"step": 3}}
    assert first["init"] == {0: {"notebase": "=-7"}}
    ins = first["ins"]
    assert ins["i1"]["pw"] == "@v1" and ins["i1"]["ctrlprog"] == "@c1"
    assert ins["i2"]["keepgate"] is True
    c1 = first["ctrlprog"]["c1"]
    assert c1[0] == {"ctrl": 10, "note": -3}
    assert c1[1] == {"ctrl": 20, "pw": "@v1"}
    assert c1[2] == {"ctrl": 30, "note": "=28", "pw": False}
    assert c1[3] == {"loop": 0}
    phrases = [p for row in doc["song"] for p in (row.get("def") or {}).get("phrase", {}).values()]
    flat = [a for p in phrases for a in p]
    assert "C1.i1" in flat
    assert "!funk:8,5" in " ".join(str(a) for a in flat)
    assert any("!pw:{step: 1, for: 2}" in str(a) for a in flat)
    assert any(str(a).endswith("!ctrlprog:off") for a in flat)


def test_reader_parses_live_notes_and_flags():
    ir = songio.loads(songio.dumps(song(), embed=True))
    progs = ir["records"]["ctrlprog"]
    assert any(row.get("pw") == {"live": "semi(2)"} for row in progs.values())
    assert any(row.get("noteabs") == 40 for row in progs.values())
    assert ir["records"]["init"] == {0: {"notebase": 5}}
    assert any(row.get("keepgate") == 1 for row in ir["records"]["ins"].values())


def test_named_spec_and_ref_mismatch_errors():
    text = songio.dumps(song(), embed=True)
    with pytest.raises(SongFormatError, match="unknown spec"):
        songio.loads(text.replace("'@v1'", "'@v9'", 1))


def test_metal_warrior_roundtrip(metal_warrior):
    dec = decompile_sid(metal_warrior.read_bytes(), subtune=0)
    text = songio.dumps(lift(dec))
    doc = yaml.safe_load(text)
    assert doc["playroutine"] == {"ref": "goattracker2", "constants": {"deftempo": 11}}
    assert songio.dumps(songio.loads(text)) == text


def test_library_playroutine_mismatch_needs_embed():
    ir = song()
    ir["playroutine"] = copy.deepcopy(songio.library("goattracker2"))
    ir["playroutine"]["tick"] = []
    with pytest.raises(SongFormatError, match="embed"):
        songio.dumps(ir)


def doc_with(mutate):
    doc = yaml.safe_load(songio.dumps(song(), embed=True))
    mutate(doc)
    return yaml.dump(doc, sort_keys=False)


def first_def(doc):
    return doc["song"][0]["def"]


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda d: d.update(unitracker=1), "not a unitracker v2"),
        (lambda d: d.update(song=[]), "list of rows"),
        (lambda d: d["song"][0].pop("play"), "play list"),
        (lambda d: d["song"][0]["play"].append("p9"), "entries"),
        (lambda d: d["song"][0]["play"].__setitem__(0, "%%"), "bad play entry"),
        (lambda d: first_def(d).update(nope={"x": {}}), "no 'nope' definitions"),
        (lambda d: first_def(d)["ins"].update(i9={"zz": 1}), "no key 'zz'"),
        (lambda d: first_def(d)["ins"].update(i9={"ad": "x"}), "an integer"),
        (lambda d: first_def(d)["ins"].update(i9={"ad": 200}), "below 100"),
        (lambda d: first_def(d)["ins"].update(i9={"keepgate": 1}), "on or off"),
        (lambda d: first_def(d)["ins"].update(i9={"ctrlprog": 3}), "@name or a program"),
        (lambda d: first_def(d)["ins"].update(i9={"ctrlprog": "@i1"}), "not a ctrlprog"),
        (lambda d: first_def(d)["ins"].update(i9={"ctrlprog": "@zz"}), "unknown name"),
        (lambda d: first_def(d)["ins"].update(i9={"ctrlprog": [{"ctrl": 1}]}), "rows then end"),
        (lambda d: first_def(d)["ins"].update(i9={"ctrlprog": [{}, 7]}), "bad program end"),
        (lambda d: first_def(d)["ins"].update(i9={"pw": 5}), "cannot set pw"),
        (lambda d: first_def(d)["ins"].update(i9={"pw": False}), "cannot turn pw off"),
        (lambda d: first_def(d)["ins"].update(i9={"pw": {"every": 2}}), "cannot express every"),
        (lambda d: first_def(d)["ins"].update(i9={"pw": {"step": 1, "for": 20}}), "at most 9"),
        (lambda d: first_def(d)["ins"].update(i9={"pw": {"stp": 1}}), "unknown parameters"),
        (lambda d: first_def(d)["ins"].update(i9={"pw": [1]}), "takes no spec|bad value"),
        (lambda d: first_def(d)["ctrlprog"]["c1"][0].update(note="x"), "a note is"),
        (lambda d: first_def(d)["ctrlprog"]["c1"][0].update(pw=[1]), "bad value"),
        (lambda d: d["song"][1].update({"def": {"ins": {"i1": {"ad": 1}}}}), "defined twice"),
        (lambda d: first_def(d)["ins"].update({"1x": {}}), "bad name"),
        (lambda d: first_def(d)["init"].update({0: []}), "a row is a mapping"),
        (lambda d: first_def(d)["init"][0].update(notebase=3), "a note is"),
        (lambda d: d["song"][1].update({"def": {"spec": {"v1": {"step": 1}}}}), "defined twice"),
        (lambda d: first_def(d)["spec"].update(v9={"nope": 1}), "unknown parameters"),
        (lambda d: d.update(playroutine="x"), "must be a mapping"),
        (lambda d: d.update(playroutine={"ref": "nope"}), "no playroutine"),
    ],
)
def test_reader_errors(mutate, match):
    with pytest.raises(SongFormatError, match=match):
        songio.loads(doc_with(mutate))


def phrase_doc(atom):
    def mutate(doc):
        for row in doc["song"]:
            phrases = (row.get("def") or {}).get("phrase")
            if phrases:
                name = next(iter(phrases))
                phrases[name] = [atom] + phrases[name][1:]
                return

    return mutate


@pytest.mark.parametrize(
    "atom,match",
    [
        ("C4!nope:1", "no command"),
        ("C4!pw", "takes a spec"),
        ("C4!pw:{every: 2}", "cannot express"),
        ("C4!ctrlprog:3", "@name or off"),
        ("C4!funk:3", "a,b"),
        ("C4!vol:@x", "an integer"),
        ("C4!hold:3", "no argument"),
        ("C4.zz", "unknown name"),
        ("X9", "bad atom"),
    ],
)
def test_atom_errors(atom, match):
    with pytest.raises(SongFormatError, match=match):
        songio.loads(doc_with(phrase_doc(atom)))


def test_writer_errors():
    ir = song()
    ir["records"]["ins"][1]["ctrlprog"] = 77
    with pytest.raises(SongFormatError, match="referenced but not defined"):
        songio.dumps(ir, embed=True)
    ir = song()
    ir["records"]["ctrlprog"][1]["zz"] = 1
    with pytest.raises(SongFormatError, match="not declared"):
        songio.dumps(ir, embed=True)


def test_rule_language_is_restricted():
    ir = song()
    ir["playroutine"]["record_types"]["ins"]["rules"] = [{"check": "[1]", "error": "x"}]
    text = songio.dumps(ir, embed=True)
    with pytest.raises(SongFormatError, match="not allowed"):
        songio.loads(text)
    ir["playroutine"]["record_types"]["ins"]["rules"] = [{"check": "len(ad)", "error": "x"}]
    with pytest.raises(SongFormatError, match="only has"):
        songio.loads(songio.dumps(ir, embed=True))
    ir["playroutine"]["record_types"]["ins"]["rules"] = [{"check": "ad[3]", "error": "x"}]
    with pytest.raises(SongFormatError, match="cannot apply"):
        songio.loads(songio.dumps(ir, embed=True))


def test_reader_table_patch_needs_a_default():
    text = songio.dumps(lift_like(), embed=False)
    doc = yaml.safe_load(text)
    doc["playroutine"]["tables"] = {"nope": {0: 1}}
    with pytest.raises(SongFormatError, match="no library default"):
        songio.loads(yaml.dump(doc))
    doc["playroutine"]["tables"] = {"ftab": [1, 2]}
    assert songio.loads(yaml.dump(doc))["tables"]["ftab"] == [1, 2]


def lift_like():
    """A tiny song on the library goattracker2 playroutine."""
    pr = songio.library("goattracker2")
    tables = pr.pop("tables")
    return {
        "unitracker": "ir",
        "meta": {},
        "clock": {"cycles_per_tick": 19656, "clock_hz": 985248},
        "voices": 3,
        "registers": [0] * 24 + [15],
        "tables": tables,
        "playroutine": pr,
        "records": {"ins": {1: {"ad": 1}}},
        "tracks": [{"plays": [{"phrase": "a"}], "loop": 0}] * 3,
        "phrases": {"a": [{"note": 30, "instrument": 1}]},
    }


def test_dump_writes_atomically(tmp_path):
    path = tmp_path / "song.yaml"
    songio.dump(song(), path, embed=True)
    assert songio.load(path)["meta"]["title"] == "Mini"
    assert not list(tmp_path.glob(".unitracker-*"))

    class Boom(dict):
        """A song whose every read fails."""

        def get(self, *a, **k):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        songio.dump(Boom(song()), tmp_path / "x.yaml")
    assert not list(tmp_path.glob(".unitracker-*"))
