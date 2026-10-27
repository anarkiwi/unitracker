"""tools/triggers.py: what starts each program, starts replaced, bindings and phase resets."""

import json

import optimiser_docs as D
import triggers

PHRASE = "      phrase:\n        p1:[{}]\n"


def measured(tmp_path, defs: str, phrase: str, **kw) -> dict:
    path = tmp_path / "t.yaml"
    path.write_text(D.doc(defs + PHRASE.format(phrase), mods=kw.pop("mods", ""), **kw))
    return triggers.measure(path, 200)


INS = "      ins:\n        i1:{ad:9,ctrl:c1}\n"
CTRL = "      ctrl:\n        c1:[{ctrl:65},end]\n        c2:[{ctrl:33},end]\n"
REL0 = INS + "      ctrl:\n        c1:[{ctrl:65,note:+0},end]\n"


def test_an_event_run_replaces_its_note_instrument_control_program(tmp_path):
    every = measured(tmp_path, INS + CTRL, "C4.i1 run:c2,-*3,D run:c2,-*3")
    assert every["replaced"] == {"ctrl/note/event": 2}
    assert every["instruments"]["… every start replaced"] == 1
    some = measured(tmp_path, INS + CTRL, "C4.i1 run:c2,-*3,D,-*3")
    assert some["replaced"] == {"ctrl/note/event": 1}
    assert some["instruments"]["… some starts replaced"] == 1
    assert some["instruments"]["… every start replaced"] == 0


def test_a_note_rel0_control_program_started_only_at_notes(tmp_path):
    notes = measured(tmp_path, REL0, "C4.i1,-*3,D,-*3")
    assert notes["static"]["ctrl first row note:+0"] == 1
    assert notes["rel0"]["only at notes (note:+0 does nothing)"] == 1
    assert notes["rel0"]["starts note at note"] == 2
    run = measured(tmp_path, REL0, "C4.i1,-*3,- run:c1,-*3")
    assert run["rel0"]["only at notes (note:+0 does nothing)"] == 0
    assert run["rel0"]["starts event off note"] == 1


def test_empty_programs_and_trailing_empty_rows_are_counted(tmp_path):
    defs = INS + "      ctrl:\n        c1:[{ctrl:65},{},end]\n        c2:[end]\n"
    rec = measured(tmp_path, defs, "C4.i1,-*3,- run:c2,-*3")
    assert rec["static"] == {"ctrl programs": 2, "ctrl empty": 1, "ctrl trailing empty row": 1}


def test_a_hard_restart_running_an_empty_control_program(tmp_path):
    restart = "restart:[{hold:2,gate:0,ad:15,sr:0},{ad:9,run:c2},end]"
    defs = (
        "      ins:\n"
        f"        i1:{{ad:9,ctrl:c1,{restart}}}\n"
        f"        i2:{{ad:9,{restart}}}\n"
        "      ctrl:\n        c1:[{ctrl:65},end]\n        c2:[end]\n"
    )
    rec = measured(tmp_path, defs, "-*4,C4.i1,-*3,D.i2,-*3")
    assert rec["restart_ctrl"] == {
        "empty/instrument has ctrl/at note": 1,
        "empty/no ctrl/at note": 1,
    }
    assert rec["replaced"] == {"ctrl/restart/note": 1}
    assert rec["restart_movable"] == {"instrument and program pairs": 1}


def test_a_fresh_binding_on_a_slot_reset_note_and_a_row_rebinding_it(tmp_path):
    defs = (
        "      ins:\n        i1:{ad:9,ctrl:c1,freq:vib(2)}\n"
        "      ctrl:\n        c1:[{ctrl:65},{wait:2,freq:vib(2)},end]\n"
    )
    rec = measured(
        tmp_path,
        defs,
        "C4.i1,-*7,D,-*7",
        head="voices:1\nslots:{freq:{freq:{reset:[note]}}}\n",
        mods="mod:\n vib(d):v + d*(phi % 4)\n",
    )
    assert rec["zeroed"] == {"freq/bind note fresh + slot reset event": 2}
    assert rec["binds"] == {"freq/note/fresh": 2, "freq/program/same modulator, phase kept": 2}


def test_reset_commands_zero_the_phase_they_reset(tmp_path):
    defs = "      ins:\n        i1:{ad:9,ctrl:c1}\n      ctrl:\n        c1:[{ctrl:65},end]\n"
    rec = measured(tmp_path, defs, "C4.i1 freq:vib(2),-*3,- freq:reset,-*3", mods=D.MODS)
    assert rec["binds"]["freq/event/reset command"] == 1
    assert rec["zeroed"]["freq/reset event"] == 1


def test_the_command_line_reads_lists_and_records_errors(tmp_path, capsys):
    good, bad = tmp_path / "good.yaml", tmp_path / "bad.yaml"
    good.write_text(D.doc(INS + CTRL + PHRASE.format("C4.i1 run:c2,-*3"), mods=""))
    bad.write_text("unitracker:2.3\nnot a document\n")
    listed = tmp_path / "docs.txt"
    listed.write_text(f"{good}\n{bad}\n")
    out = tmp_path / "triggers.json"
    assert triggers.main([str(listed), "--jobs", "1", "--out", str(out)]) == 0
    recs = {r["doc"]: r for r in json.loads(out.read_text())}
    assert recs["good"]["replaced"] == {"ctrl/note/event": 1} and "error" in recs["bad"]
    printed = capsys.readouterr().out
    assert "1 documents, 1 failed" in printed and "ctrl/note/event" in printed
