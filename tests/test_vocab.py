"""Songs from every player use one vocabulary: register targets, spec parameters,
timing and note keys, and the non-accumulator command names."""

# pylint: disable=redefined-outer-name
import re

import pytest
import yaml
from pygoattracker import decompile_sid

from unitracker import defmon, songio, vocab
from unitracker.atoms import parse_atom
from unitracker.goattracker import lift

TOP = {"unitracker", "playroutine", "meta", "hints", "clock", "voices", "registers", "song", "loop"}
DROPPED = {"pw+", "cut+", "freq+", "pw~", "slide", "porta", "vib"}


class Seen:
    """Every key, parameter and command a document uses."""

    def __init__(self):
        self.rows, self.params, self.commands = set(), set(), set()

    def spec(self, spec):
        assert isinstance(spec, dict)
        self.params |= set(spec)
        assert set(spec) <= vocab.SPEC_PARAMS, set(spec) - vocab.SPEC_PARAMS
        for key in ("at_lo", "at_hi"):
            if key in spec:
                assert set(spec[key]) <= vocab.AT_KEYS
        for key in ("up", "down"):
            if isinstance(spec.get(key), dict):
                assert set(spec[key]) <= vocab.PHASE_KEYS
                assert spec[key].get("carry_from", "freq") in vocab.TARGETS
        if "carry_from" in spec:
            assert spec["carry_from"] in vocab.TARGETS

    def row(self, row):
        assert isinstance(row, dict)
        self.rows |= set(row)
        assert set(row) <= vocab.ROW_KEYS, set(row) - vocab.ROW_KEYS
        for value in row.values():
            if isinstance(value, dict):
                self.spec(value)
            elif isinstance(value, list):
                self.program(value)

    def program(self, rows):
        for row in rows:
            if row == "end" or (isinstance(row, dict) and set(row) == {"loop"}):
                continue
            self.row(row)

    def atom(self, atom):
        if isinstance(atom, str):
            assert not re.search(r"!\d", atom)
        parsed = parse_atom(atom)
        if parsed["cmd"] is not None:
            self.commands.add(parsed["cmd"])
            assert parsed["cmd"] in vocab.COMMANDS
            if isinstance(parsed["arg"], dict):
                self.spec(parsed["arg"])

    def document(self, text):
        doc = yaml.safe_load(text)
        assert set(doc) <= TOP
        for item in doc["song"]:
            assert set(item) <= {"def", "play"}
            for label, entries in (item.get("def") or {}).items():
                assert label in vocab.DEFINITIONS, label
                for value in entries.values():
                    if label == "phrase":
                        for atom in value:
                            self.atom(atom)
                    elif label == "spec":
                        self.spec(value)
                    elif isinstance(value, list):
                        self.program(value)
                    else:
                        self.row(value)
        return self


@pytest.fixture(scope="module")
def documents(metal_warrior, crosseyed):
    dec = decompile_sid(metal_warrior.read_bytes(), subtune=0)
    raw = crosseyed.read_bytes()
    return {
        "goattracker2": [songio.dumps(lift(dec)), songio.dumps(lift(dec), embed=True)],
        "defmon": [songio.dumps(defmon.lift(raw)), songio.dumps(defmon.lift(raw), embed=True)],
    }


def test_songs_use_only_the_shared_vocabulary(documents):
    seen = {}
    for name, texts in documents.items():
        for text in texts:
            seen[name] = Seen().document(text)
    gt, dm = seen["goattracker2"], seen["defmon"]
    used = gt.rows | dm.rows | gt.params | dm.params | gt.commands | dm.commands
    assert not used & DROPPED
    assert {"ctrl", "pw", "note"} <= gt.rows & dm.rows
    assert "step" in gt.params & dm.params


@pytest.mark.parametrize("name", ["goattracker2", "defmon"])
def test_library_playroutines_declare_only_shared_names(name):
    pr = songio.library(name)
    for rtype, spec in pr["record_types"].items():
        assert rtype in vocab.DEFINITIONS
        assert set(spec.get("keys") or {}) <= vocab.ROW_KEYS, rtype
    assert set(pr.get("commands") or {}) <= vocab.COMMANDS
    for lane, params in (pr.get("specs") or {}).items():
        assert lane in vocab.TARGETS and set(params) <= vocab.SPEC_PARAMS
