"""tools/vocab_census.py: the written vocabulary of documents, tallied per playroutine."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("vocab_census", ROOT / "tools" / "vocab_census.py")
vocab_census = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vocab_census)

DOC = """unitracker: 1.20
playroutine: {ref: goattracker2}
meta: {title: T, author: A}
tempo: [6, 7]
song:
  - def:
      restart:
        r1: [{hold: 2, gate: 0, ad: 15, sr: 0}, {ctrl: 9}, end]
      x:
        x1: v + 20*((φ - 3) % 12 >= 6) - 10
      ins:
        i1: {ad: 1, sr: 2, ctrl: [{ctrl: 65, note: +0}, {ctrl: 17, note: F3}, {loop: 1}],
          pw: 16*abs((φ - 32) % 128 - 64) + 512, filter: f1, restart: r1}
      filter:
        f1: [{cut: 2*φ + 100, res: 15, route: +, mode: 16}, end]
      phrase:
        p1: [C4.i1!freq:x1!legato*2, -!cut:80, off, on, D4!tempo:6/7, -*4]
    play: [p1, p1+5, ~]
"""


def test_the_census_counts_forms_by_section(tmp_path):
    """Events, commands, row keys, program endings and expression operators are tallied."""
    path = tmp_path / "t.yaml"
    path.write_text(DOC, encoding="utf-8")
    census = vocab_census.Census()
    census.document(path.name, vocab_census.parse(DOC))
    count = census.count
    assert count[("event note", "pitch")] == 2
    assert count[("event note", "off")] == count[("event note", "on")] == 1
    assert count[("event note", "-")] == 2
    assert count[("event", "*steps")] == 2
    assert count[("event", "2 commands")] == 1
    assert count[("command pair", "!freq+!legato")] == 1
    assert count[("command", "!freq:name:x")] == 1
    assert count[("command", "!legato:bare")] == 1
    assert count[("command", "!cut:int")] == 1
    assert count[("command", "!tempo:a/b")] == 1
    assert count[("ctrl note", "relative")] == count[("ctrl note", "absolute")] == 1
    assert count[("ctrl program", "loop")] == 1
    assert count[("filter route", "edit")] == 1
    assert count[("ins pw", "expr")] == 1 and count[("ins pw ops", "abs(")] == 1
    assert count[("x ops", "%")] == 1 and count[("x", "expression")] == 1
    assert count[("play entry", "transposed")] == count[("play entry", "~")] == 1
    assert count[("tempo", "swing")] == 1
    assert count[("record type", "ins")] == 1
    assert census.songs[("command", "!cut:int")] == {"t.yaml"}


def test_main_reports_per_playroutine_and_writes_json(tmp_path, capsys):
    """The report names the playroutine and its song count; the JSON holds the counts."""
    path = tmp_path / "t.yaml"
    path.write_text(DOC, encoding="utf-8")
    out = tmp_path / "v.json"
    assert vocab_census.main([str(path), "--json", str(out)]) == 0
    text = capsys.readouterr().out
    assert "## goattracker2: 1 songs" in text and "!freq:name:x" in text
    assert json.loads(out.read_text(encoding="utf-8"))["goattracker2"]["command|!cut:int"] == [1, 1]


def test_argument_and_value_shapes():
    """Command arguments and program-valued fields are classified by their written form."""
    assert [vocab_census.arg_shape(a) for a in (None, "none", "x3", "12", "6/7", "v+1")] == [
        "bare",
        "none",
        "name:x",
        "int",
        "a/b",
        "expr",
    ]
    assert [vocab_census.value_shape(v) for v in ([], 3, "none", "f1", "v + 1")] == [
        "inline",
        "int",
        "none",
        "ref",
        "expr",
    ]
