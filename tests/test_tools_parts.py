"""tools/parts.py: sounds percussive by how they are played, and each part's repetition."""

import json

import parts

import optimiser_docs as D

DRUMS = ",".join(["C4.i1", "D3.i2", "C4.i1", "G3.i2", "C4.i1", "A3.i2", "C4.i1", "E3.i2"] * 2)
DOC = D.doc(
    "      ins:\n"
    "        i1:{ad:9,sr:0,ctrl:c1}\n"
    "        i2:{ad:9,sr:240,ctrl:c2}\n"
    "      ctrl:\n"
    "        c1:[{wait:0,ctrl:129,note:+0},end]\n"
    "        c2:[{wait:0,ctrl:65,note:+0},end]\n"
    "      phrase:\n"
    f"        p1:[{DRUMS}]\n",
    head="voices:1\ntempo:6\n",
    mods="",
)


def test_a_sound_on_one_pitch_is_percussive_and_its_voice_interleaves(tmp_path):
    segs = parts.segments(DOC, 400)
    drums = parts.percussive(segs)
    assert len(drums) == 1 and "129" in next(iter(drums))
    path = tmp_path / "form" / "t.yaml"
    path.parent.mkdir()
    path.write_text(DOC)
    rec = parts.parts(path, 400)
    assert len(rec["voices"]) == 1
    voice = rec["voices"][0]
    assert rec["sounds"] == 2 and rec["percussive"] == 1 and voice["mixed"]
    assert voice["distinct_percussive"] == 1 and voice["distinct_tonal"] == 1


def test_the_command_line_summarises_and_writes_records(tmp_path, capsys):
    path = tmp_path / "t.yaml"
    path.write_text(DOC)
    out = tmp_path / "parts.json"
    assert parts.main([str(path), "--jobs", "1", "--ticks", "400", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "1 documents (0 failed), 1 with percussive sounds" in printed
    assert json.loads(out.read_text())[0]["percussive"] == 1
