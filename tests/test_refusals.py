"""The driver remembers the edits the guarantee refused, by the parts each touches and reads."""

from unitracker_grammar.document import Instrument, Program, Section, printed, read, replace

import optimiser_docs as D

from unitracker.optimiser import Guarantee, optimise
from unitracker.optimiser.passes import Edit
from unitracker.optimiser.refusals import Refusals, parts, references

SPARE = D.doc(
    "      ins:\n"
    "        i1:{ad:9,sr:240,ctrl:c1}\n"
    "        i5:{ad:1,sr:2,ctrl:c2}\n"
    "        i6:{ad:3,sr:4,ctrl:c2}\n"
    "        i7:{ad:5,sr:6,ctrl:c2}\n"
    "      ctrl:\n"
    "        c1:[{ctrl:65},end]\n"
    "        c2:[{ctrl:33},end]\n"
    "      phrase:\n"
    "        p1:[C4.i1,-,D,-*3]\n",
    mods="",
)


def _record(doc, kind: str, name: str, value):
    defs = tuple(
        (
            replace(s, records=tuple((k, value if k == name else v) for k, v in s.records))
            if s.kind == kind
            else s
        )
        for s in doc.defs
    )
    return replace(doc, defs=defs)


def _drop(doc, kind: str, name: str):
    defs = tuple(
        (
            replace(s, records=tuple((k, v) for k, v in s.records if k != name))
            if s.kind == kind
            else s
        )
        for s in doc.defs
    )
    return replace(doc, defs=tuple(s for s in defs if s.records))


def _silence(doc):
    """i1 without its control program: audible, and smaller."""
    return _record(doc, "ins", "i1", Instrument({"ad": 9, "sr": 240}))


SILENCE = Edit("i1 silenced", _silence)


def test_a_document_s_parts_are_its_lines_templates_and_records():
    found = parts(read(D.MERGE))
    assert {("header",), ("tables",), ("init",), ("rows",)} < found.keys()
    assert {("mod", "vib"), ("ins", "i1"), ("ctrl", "c2"), ("phrase", "p2")} < found.keys()
    assert references(found[("phrase", "p1")][1]) == {"i1", "i2", "vib", "wob"}
    assert references(found[("ins", "i2")][1]) == {"c2"}


def test_a_refusal_s_key_holds_while_what_the_edit_touches_and_reads_is_unchanged():
    doc, refusals = read(SPARE), Refusals()
    key = refusals.key("p", SILENCE, doc, SILENCE.apply(doc))
    unrelated = _drop(doc, "ins", "i6")
    assert refusals.key("p", SILENCE, unrelated, SILENCE.apply(unrelated)) == key
    for changed in (
        _record(doc, "ctrl", "c1", Program([{"ctrl": 17}])),
        _record(doc, "ins", "i1", Instrument({"ad": 8, "sr": 240, "ctrl": "c1"})),
        replace(doc, rows=doc.rows * 2),
        replace(doc, header=replace(doc.header, tempo=3)),
    ):
        assert refusals.key("p", SILENCE, changed, SILENCE.apply(changed)) != key, changed
    added = replace(doc, defs=doc.defs + (Section("filter", [("f1", 5)]),))
    assert refusals.key("p", SILENCE, added, SILENCE.apply(added)) == key
    assert refusals.key("q", SILENCE, doc, SILENCE.apply(doc)) != key


def test_a_refused_edit_is_proved_once_while_other_rounds_keep_edits(monkeypatch):
    """Each round keeps one unused instrument's drop and is offered the audible edit again: the
    guarantee sees it once, and each round still reports it refused."""
    proved, holds = [], Guarantee.holds

    def counted(self, text):
        proved.append("i1:{ad:9,sr:240}" in text)
        return holds(self, text)

    def spare(doc):
        unused = [n for n in ("i5", "i6", "i7") if n in doc.records("ins")]
        return [Edit(f"drop {n}", lambda d, n=n: _drop(d, "ins", n)) for n in unused[:1]]

    monkeypatch.setattr(Guarantee, "holds", counted)
    order = (("spare", spare), ("silence", lambda doc: [SILENCE]))
    result = optimise(SPARE, order=order, rename=False)
    assert [label for _, label in result.kept] == ["drop i5", "drop i6", "drop i7"]
    assert result.refused == ["i1 silenced"] * 4 and sum(proved) == 1
    assert printed(read(result.text)) == result.text and "c2" in result.text
