"""tools/vocab_census.py: the vocabulary of generic documents, tallied over a corpus."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("vocab_census", ROOT / "tools" / "vocab_census.py")
vocab_census = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vocab_census)

DOC = """unitracker: 1.20
meta: {title: T, author: A}
tempo: [6, 7]
tables:
  G: [0, 744]
init: [-!vol:15, -, -]
song:
  - def:
      restart:
        r1: [{hold: 2, gate: 0, ad: 15, sr: 0}, {ctrl: 9}, end]
      ins:
        i1: {ad: 1, sr: 2, ctrl: [{ctrl: 65, note: +0}, {ctrl: 17, note: F3}, {loop: 1}], pw: 16*abs((φ - 32) % 128 - 64) + 512, filter: f1, freq: 'G[N]', restart: r1}
      filter:
        f1: [{cut: 2*φ + 100, res: 15, route: +, mode: 16}, {}, end]
      x:
        x1: v + 20*((φ - 3) % 12 >= 6) - 10
      phrase:
        p1: [C4.i1!freq:x1!legato*2, -!cut:80, off, on, D4!tempo:6/7, -*4]
    play: [p1, p1+5, ~]
  - play: [p1, p1, p1]
  - {loop: 0}
"""


def test_the_census_counts_forms_by_section():
    """Header lines, record types, row keys, events, commands, plays and operators are tallied."""
    census = vocab_census.Census()
    census.document("t.yaml", DOC)
    count = census.count
    assert count[("header", "tempo swing")] == count[("header", "init")] == 1
    assert count[("table", "G")] == 1 and count[("meta key", "author")] == 1
    assert count[("init note", "-")] == 3 and count[("init command !vol", "int")] == 1
    assert count[("phrase note", "pitch")] == count[("phrase note", "-")] == 2
    assert count[("phrase note", "off")] == count[("phrase note", "on")] == 1
    assert count[("phrase event", "*steps")] == 2
    assert count[("phrase event", "names instrument")] == 1
    assert count[("phrase command set", "!freq+!legato")] == 1
    assert count[("phrase command !freq", "x")] == 1
    assert count[("phrase command !tempo", "int/int")] == 1
    assert count[("phrase command !cut", "int")] == 1
    assert count[("ctrl row note", "signed")] == count[("ctrl row note", "note")] == 1
    assert count[("ctrl program", "loop")] == 1 and count[("filter program", "end")] == 1
    assert count[("filter rows", "row")] == 2 and count[("filter rows", "empty row")] == 1
    assert count[("filter row route", "route_add")] == 1
    assert count[("filter row cut", "expr")] == 1 and count[("filter row mode", "int")] == 1
    assert count[("ins ctrl", "program")] == 1 and count[("ins filter", "filter")] == 1
    assert count[("ins pw", "expr")] == 1 and count[("ins pw ops", "abs(")] == 1
    assert count[("ins pw ops", "-")] == 2 and count[("ins freq ops", "G[]")] == 1
    assert count[("x ops", ">=")] == count[("x ops", "v")] == 1
    assert count[("play entry", "transposed")] == count[("play entry", "~")] == 1
    assert count[("play entry", "phrase")] == 4
    assert count[("timeline", "play row")] == count[("timeline", "loop")] == 1
    assert count[("record type", "ins")] == 1
    assert census.docs[("phrase command !cut", "int")] == {"t.yaml"}


def test_a_lifted_document_is_tallied(references):
    census = vocab_census.Census()
    census.document("mw.yaml", references["metal_warrior"].generic)
    count = census.count
    assert count[("header", "init")] == 1 and count[("table", "G")] == 1
    assert count[("ins key", "restart")] > 0 and count[("phrase command key", "!tempo")] > 0
    assert count[("ins freq ops", "G[]")] > 0


def test_main_reports_the_corpus_and_writes_json(tmp_path, capsys):
    """The report counts the documents; the JSON holds each form's occurrences and documents."""
    paths = [tmp_path / "a.yaml", tmp_path / "b.yaml"]
    paths[0].write_text(DOC, encoding="utf-8")
    paths[1].write_text(DOC.replace("!cut:80", ""), encoding="utf-8")
    out = tmp_path / "v.json"
    assert vocab_census.main([*map(str, paths), "--json", str(out)]) == 0
    text = capsys.readouterr().out
    assert "## 2 documents" in text and "!freq+!legato" in text
    counts = json.loads(out.read_text(encoding="utf-8"))
    assert counts["phrase command !cut|int"] == [1, 1]
    assert counts["phrase command !freq|x"] == [2, 2]
