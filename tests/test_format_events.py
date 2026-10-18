"""Event folds the writer folds and the reader unfolds (unitracker.format.events): defMON's
instrument and restart, SID-Wizard's envelope nibbles and one-command FX column, and the program
lifetime commands of each player."""

import numpy as np
import pytest

from unitracker.format import songio
from unitracker.format.events import instrument, lifetime, restart
from unitracker.format.records import record_types
from unitracker.player.vm import SongPlayer, compile_phrase

DM = songio.library("defmon")
SPEC = instrument.spec(DM)
LAYER_B = next(int(code) for code in SPEC["commands"])
ONCE = "    play: [p1, p1, p1]\n"


def song(pr, defs, phrases, rows=ONCE, loop="~"):
    phrase = "".join(f"        {name}: [{atoms}]\n" for name, atoms in phrases.items())
    return (
        f"unitracker: {songio.VERSION}\nplayroutine: {{ref: {pr}}}\nsong:\n  - def:\n{defs}"
        f"      phrase:\n{phrase}{rows}  - {'end' if loop == '~' else f'{{loop: {loop}}}'}\n"
    )


def grid(ir, ticks=150):
    return SongPlayer(ir).render_grid(ticks)


def commands(ir, phrase="p1"):
    return [e.get("commands") or e.get("command") for e in ir["phrases"][phrase]]


# -- instrument: one ins record naming the sidTAB programs of layers A and B --

DM_DEFS = """      ctrl:
        c1: [{ctrl: 65, gate: 1, hold: 4}, {gate: 0}, end]
        c2: [{res: 15, route: 7, mode: 16}, end]
      ins:
        i1: {ctrl: c1, filter: c2}
        i2: {filter: c2}
"""
DM_ROWS = "    play: [p1, p2, p2]\n"


def dm_song(p1, defs=DM_DEFS):
    return song("defmon", defs, {"p1": p1, "p2": "-*8"}, DM_ROWS)


def test_an_instrument_reads_as_both_layers_and_the_writer_names_native_layers():
    assert instrument.document(DM) == ("ins", 0)
    assert instrument.document(songio.library("goattracker2")) == ("ins", 0)
    assert instrument.written_as(DM, LAYER_B) == "ins.filter"
    assert instrument.written_as(songio.library("sidwizard"), 112) is None
    ir = songio.loads(dm_song("C4.i1*4, -.i2*4"))
    assert "ins" not in ir["records"]
    steps = compile_phrase(ir["phrases"]["p1"])
    assert steps[0, :5].tolist() == [1, 72, 1, LAYER_B, 2]
    assert steps[4, :5].tolist() == [0, 0, 0, LAYER_B, 2]
    text = songio.dumps(ir)
    assert "p1: [C4.i1*4, -.i2*4]" in text
    assert "i1: {ctrl: [{hold: 4, ctrl: 65, gate: 1}, {gate: 0}, end], filter: c1}" in text
    assert songio.dumps(songio.loads(text)) == text
    out = grid(ir, 8)
    assert out[1:5, 4].tolist() == [65] * 4 and out[2, 0x17] == 0xF7
    ir["phrases"]["p1"] = [
        {"note": 72, "instrument": 1, "steps": 4},
        {"command": [LAYER_B, 2]},
        {"note": 72, "instrument": 1, "command": [LAYER_B, 2], "steps": 3},
    ]
    text = songio.dumps(ir)
    assert "p1: [C4.i1*4, -.i2, C4.i3*3]" in text
    assert "i1: {ctrl: c1}" in text and "i2: {filter: c2}" in text
    assert "i3: {ctrl: c1, filter: c2}" in text


def test_unfold_puts_the_instrument_commands_before_the_event_commands():
    both = {"instrument": 1, "commands": [[9, 1]]}
    rtype = record_types(DM)["ins"]
    assert instrument.unfold(both, {"filter": 7}, SPEC, rtype, 1) == {
        "commands": [[LAYER_B, 7], [9, 1]]
    }


@pytest.mark.parametrize(
    "p1,defs,match",
    [
        ("C4.i3*8", DM_DEFS + "        i3: {}\n", "phrase p1: an instrument arms no program"),
        ("C4.c1*8", DM_DEFS, "'c1' is a ctrl, not a ins"),
    ],
)
def test_the_reader_refuses_an_instrument_the_layers_cannot_hold(p1, defs, match):
    with pytest.raises(songio.SongFormatError, match=match):
        songio.loads(dm_song(p1, defs))


def test_the_writer_refuses_an_event_that_arms_layer_b_twice():
    ir = songio.loads(dm_song("C4.i1*8"))
    ir["phrases"]["p1"] = [{"note": 72, "commands": [[LAYER_B, 2], [LAYER_B, 2]], "steps": 8}]
    with pytest.raises(songio.SongFormatError, match="defmon: an event arms the instrument's"):
        songio.dumps(ir)


# -- restart: a note's restart program, run on layer B on the steps before it --

RS_INS = "i1: {ctrl: [{ctrl: 65, gate: 1}, end], restart: r1}"
RS_PROGRAM = "[{hold: 3, ctrl: ^8, gate: 0}, {}, end]"
GT_RS_INS = "i1: {ctrl: [{ctrl: 65}, end], restart: r1}"


def rs_song(p1, program=RS_PROGRAM, ins=RS_INS, pr="defmon"):
    defs = f"      ins:\n        {ins}\n      restart:\n        r1: {program}\n"
    return song(pr, defs, {"p1": p1, "p2": "-*12"}, DM_ROWS)


def native_steps(*steps):
    return np.array([list(r) + [0] * (7 - len(r)) for r in steps], dtype=np.int64)


def test_fold_moves_a_lone_command_onto_the_note_it_precedes():
    # Each pair is a candidate command step and the step it precedes; entry 9 has no restart.
    phrase = native_steps(
        (0, 0, 0, LAYER_B, 4),
        (0,),
        (1, 60, 2),
        (0, 0, 0, LAYER_B, 4),
        (1, 62, 2, LAYER_B, 5),
        (0, 0, 3, LAYER_B, 4),
        (1, 60),
        (0, 0, 0, LAYER_B, 4, LAYER_B, 5),
        (1, 60),
        (0, 0, 0, LAYER_B, 4),
        (2,),
        (0, 0, 0, LAYER_B, 4),
        (1, 60, 0, LAYER_B, 1, LAYER_B, 2),
        (0, 0, 0, 2, 4),
        (1, 60),
        (0, 0, 0, LAYER_B, 9),
        (1, 60),
    )
    seen = []

    def restart_of(entry, gap):
        seen.append((entry, gap))
        return None if entry == 9 else 7

    out = restart.fold(phrase, LAYER_B, restart_of)
    assert seen == [(4, 2), (4, 1), (9, 1)]
    assert out[2].tolist() == [1, 60, 2, restart.CODE, 7, 0, 0]
    assert out[4].tolist() == [1, 62, 2, LAYER_B, 5, restart.CODE, 7]
    assert not out[[0, 3]].any() and (out[5:] == phrase[5:]).all()


def test_unfold_splits_the_steps_before_the_note():
    events = [
        {"note": 50, "steps": 5},
        {"note": 52, "restart": 1},
        {"steps": 3},
        {"note": 53, "restart": 1},
        {"steps": 2},
        {"note": 54, "restart": 2},
    ]
    gaps = {1: (9, 3), 2: (8, 1)}
    assert restart.unfold(events, LAYER_B, gaps.get) == [
        {"note": 50, "steps": 2},
        {"command": [LAYER_B, 9], "steps": 3},
        {"note": 52},
        {"command": [LAYER_B, 9], "steps": 3},
        {"note": 53},
        {},
        {"command": [LAYER_B, 8]},
        {"note": 54},
    ]


@pytest.mark.parametrize(
    "events,match",
    [
        ([{"note": 50, "steps": 3}, {"note": 52, "restart": 1}], "3 empty steps"),
        ([{"steps": 4}, {"note": "off", "restart": 1}], "needs a note"),
    ],
)
def test_unfold_refuses_a_restart_without_room(events, match):
    with pytest.raises(restart.RestartError, match=match):
        restart.unfold(events, LAYER_B, lambda index: (9, 3))


def test_a_restart_reads_as_layer_b_before_its_note_and_writes_back():
    text = songio.dumps(songio.loads(rs_song("-*4, C3.i1*8")))
    ir = songio.loads(text)
    assert songio.dumps(ir) == text
    assert f"restart: {RS_PROGRAM}}}" in text and "filter" not in text
    assert "restart" not in ir["records"]
    steps = compile_phrase(ir["phrases"]["p1"])
    assert steps[1, 3] == LAYER_B and steps[4, 0] == 1 and not steps[2:4].any()
    assert ir["records"]["sidtab"][steps[1, 4]] == {
        "dl": 0x80,
        "wgh": -1,
        "wgl": 8,
        "ad": -1,
        "sr": -1,
        "next": 0,
    }
    assert grid(ir, 8)[1:, 4].tolist() == [0, 8, 8, 8, 65, 65, 65]


def test_the_writer_folds_a_lone_layer_b_start_into_the_notes_instrument():
    plain = RS_INS.replace(", restart: r1", "")
    native = f"{plain}\n        i2: {{filter: [{{ctrl: ^8, gate: 0}}, end]}}"
    native += "\n        i3: {ctrl: [{ctrl: 17, gate: 1}], end]}".replace("}]", "}")
    folded = songio.dumps(
        songio.loads(rs_song("-, -.i2*3, C3.i1*8, -, -.i2*3, C3.i3*8, C3.i1*8", ins=native))
    )
    assert "[-*4, C3.i1*12, C3.i2*8, C3.i3*8]" in folded
    assert f"r1: {RS_PROGRAM}" in folded and folded.count("restart: r1}") == 2
    assert songio.dumps(songio.loads(folded)) == folded


RS_P1 = "-*4, C3.i1*8"


@pytest.mark.parametrize(
    "p1,program,pr,ins,match",
    [
        ("-*2, C3.i1*8", RS_PROGRAM, "defmon", RS_INS, "3 empty steps"),
        (RS_P1, "[{}, end]", "defmon", RS_INS, "a row before its note"),
        (RS_P1, "[{hold: 3}, {ctrl: 9}, end]", "defmon", RS_INS, "can't express row"),
        (RS_P1, "[{hold: 3, ctrl: ^8, gate: 0}, {}, {loop: 0}]", "defmon", RS_INS, "can't loop"),
        (RS_P1, RS_PROGRAM, "goattracker2", GT_RS_INS, "restart: .*can't express row"),
        ("C3.i1*8", RS_PROGRAM, "goattracker2", GT_RS_INS, "ctrl holds no mask here"),
    ],
)
def test_the_reader_refuses_a_restart_it_cannot_play(p1, program, pr, ins, match):
    with pytest.raises(songio.SongFormatError, match=f"{pr}: .*{match}"):
        songio.loads(rs_song(p1, program, ins, pr))


# -- envelope: !ad:n and !sr:n as the byte SID-Wizard's nibble small FX write --

SW_ENV = """      ins:
        i1: {ad: 18, sr: 240, ctrl: [{ctrl: 65, note: +0}, end]}
        i2: {ad: 52, sr: 248, ctrl: [{ctrl: 65, note: +0}, end]}
"""
AD, SR = 5, 6
SPLIT = "    play: [p1, p1, p1]\n  - play: [p2, p2, p2]\n  - play: [p3, p3, p3]\n"
SPLIT_PHRASES = {"p1": "C4.i1*4", "p2": "C4!ad:50*4", "p3": "C4.i2*4"}


def env_song(phrases, rows=ONCE, loop="~", defs=SW_ENV):
    return song("sidwizard", defs, phrases, rows, loop)


def test_a_byte_reads_as_the_nibble_and_column_the_player_writes_it_with():
    atoms = (
        "C4.i1!sr:192*4, D4!sr:208*4, -!ad:66*4, -!ad:21*4, -!sr:247*4, -!ad:18*4, C4*4,"
        " E4!legato!sr:241*4"
    )
    ir = songio.loads(env_song({"p1": atoms}))
    assert commands(ir) == [
        [80, 12],
        [336, 13],
        [288, 4],
        [304, 5],
        [352, 7],
        [288, 1],
        None,
        [[63, 0], [96, 1]],
    ]
    text = songio.dumps(ir)
    assert f"p1: [{atoms}]" in " ".join(text.split())
    assert songio.dumps(songio.loads(text)) == text
    voice = grid(ir, 180)[:, [AD, SR]]
    changes = voice[np.r_[True, (voice[1:] != voice[:-1]).any(1)]]
    assert [tuple(int(x) for x in r) for r in changes if r.any()][:7] == [
        (18, 192),
        (18, 208),
        (66, 208),
        (21, 208),
        (21, 247),
        (18, 247),
        (18, 240),
    ]


def test_a_phrase_whose_nibble_writes_different_bytes_splits_and_merges_back():
    plays = SPLIT + "  - play: [p2, p2, p2]\n"
    ir = songio.loads(env_song(SPLIT_PHRASES, plays))
    assert commands(ir, "p2") == [[288, 3]] and commands(ir, "p2/1") == [[304, 2]]
    assert [p["phrase"] for p in ir["tracks"][0]["plays"]] == ["p1", "p2", "p3", "p2/1"]
    out = songio.dumps(ir)
    assert out.count("C4!ad:50*4") == 1 and "p2/1" not in out
    assert songio.dumps(songio.loads(out)) == out
    for trk in ir["tracks"]:
        for play in trk["plays"]:
            play["phrase"] = play["phrase"].split("/")[0]
    text = songio.dumps(ir)
    assert "C4!ad:50*4" in text and "C4!ad:52*4" in text
    back = songio.loads(text)
    assert songio.dumps(back) == text
    assert np.array_equal(grid(back, 200), grid(ir, 200))


def test_a_lone_fx_column_command_off_a_note_start_is_written_in_the_first_slot():
    ir = songio.loads(env_song({"p1": "C4.i1*4, off!sr:192*4"}))
    native = songio.loads(env_song({"p1": "C4.i1*4, off!sr:192*4"}))
    native["phrases"]["p1"][1]["command"] = [80, 12]
    text = songio.dumps(native)
    assert "off!sr:192*4" in text and commands(songio.loads(text)) == commands(ir)
    assert np.array_equal(grid(native), grid(ir))


@pytest.mark.parametrize(
    "phrases,plays,loop,match",
    [
        (
            {"p1": "C4.i1*4, -!ad:255*4"},
            ONCE,
            "~",
            "can't express !ad:255 over the instrument's ad 18",
        ),
        ({"p1": "C4.i1*4, -!sr:none*4"}, ONCE, "~", "can't express !sr:none"),
        ({"p1": "-!ad:18*4, C4.i1*4"}, ONCE, "~", "!ad on a voice that has no instrument"),
        ({"p1": "C4.i1*4, -.i2*4, -!ad:50*4"}, ONCE, "~", "!ad also writes sr 248 over 240"),
        (SPLIT_PHRASES, SPLIT, 1, "phrase p2 plays its envelope commands differently"),
    ],
)
def test_the_reader_refuses_an_envelope_byte_no_small_fx_writes(phrases, plays, loop, match):
    with pytest.raises(songio.SongFormatError, match=f"sidwizard: .*{match}"):
        songio.loads(env_song(phrases, plays, loop))


@pytest.mark.parametrize(
    "event,column",
    [
        ({"note": 40, "command": [80, 12]}, "FX"),
        ({"note": 40, "instrument": 1, "command": [336, 12]}, "instrument"),
    ],
)
def test_a_column_the_slots_cannot_say_has_no_written_form(event, column):
    ir = songio.loads(env_song({"p1": "C4.i1*4, D4*4"}))
    ir["phrases"]["p1"][1] = event
    with pytest.raises(songio.SongFormatError, match=f"!sr in the {column} column has no written"):
        songio.dumps(ir)


# -- column: SID-Wizard's FX column holds one command --

COL_DEFS = SW_ENV + "      ctrl:\n        c1: [{ctrl: 33, note: +0}, end]\n"


def test_commands_in_different_columns_share_an_event():
    atoms = "C4.i1*4, D4!sr:192!tempo:4*4, E4!run:c1!tempo:4*4, F4!legato!tempo:4*4"
    text = songio.dumps(songio.loads(env_song({"p1": atoms}, defs=COL_DEFS)))
    ir = songio.loads(text)
    assert commands(ir)[1:] == [[[336, 12], [18, 4]], [[112, 2], [18, 4]], [[63, 0], [18, 4]]]
    assert songio.dumps(ir) == text


@pytest.mark.parametrize(
    "atoms,shown",
    [
        ("C4.i1!sr:192!tempo:4*4", "!sr and !tempo"),
        ("C4.i1!run:c1!tempo:4/5*4", "!run and !tempo"),
        ("C4.i1*4, D4!sr:192!run:c1*4", "!sr and !run"),
    ],
)
def test_two_commands_in_one_column_are_refused_naming_both(atoms, shown):
    with pytest.raises(songio.SongFormatError, match=f"^sidwizard: the player can't play {shown}"):
        songio.loads(env_song({"p1": atoms}, defs=COL_DEFS))


def test_the_writer_refuses_two_fx_column_commands():
    ir = songio.loads(env_song({"p1": "C4.i1!tempo:4*4"}, defs=COL_DEFS))
    ir["phrases"]["p1"][0].pop("command")
    ir["phrases"]["p1"][0]["commands"] = [[18, 4], [16, 0]]
    with pytest.raises(songio.SongFormatError, match="can't play !tempo and !freq on one event"):
        songio.dumps(ir)


# -- lifetime: !pw and !filter, a note that does not restart its instrument's program --

SW_INS = """      ins:
        i1: {sr: 240, ctrl: [{ctrl: 65, note: +0}, end], pw: '2048 + 16*min(64, φ)'}
        i2: {sr: 240, ctrl: [{ctrl: 65, note: +0}, end], pw: '1024 + 16*min(64, φ)'}
"""
GT_INS = """      x:
        x1: '1024 + 16*min(3, φ)'
      pw:
        pw1: '2048 + 16*min(3, φ)'
      ins:
        i1: {sr: 240, ctrl: [{ctrl: 65, note: +0}, end], pw: '1024 + 16*min(3, φ)',
          restart: [{hold: 2, gate: 0, ad: 15, sr: 0}, {ctrl: 9}, end]}
        i2: {sr: 240, ctrl: [{ctrl: 65, note: +0}, end]}
        i3: {sr: 240, ctrl: [{ctrl: 65, note: +0}, {run: pw1}, end]}
"""
GT_PHRASE = "C4.i1*2, D4.i2*2, E4*2, F4.i3*2, C4.i2*2"
LOOPED = song(
    "sidwizard",
    SW_INS,
    {"p1": "C4.i1*4", "p2": "C4.i2*4", "p3": "D4*4"},
    "    play: [p1, p1, p1]\n  - play: [p3, p3, p3]\n  - play: [p2, p2, p2]\n"
    "  - play: [p3, p3, p3]\n",
    loop=1,
)


def test_sidwizard_writes_the_flag_bits_on_the_notes_they_keep():
    base = song("sidwizard", SW_INS, {"p1": "C4.i1*4, D4*4, E4*4, C4.i2*4, D4*4"})
    plain, flagged = songio.loads(base), songio.loads(base)
    flagged["records"]["ins"][1]["flags"] = 0xD0
    text = songio.dumps(flagged)
    written = "D4!pw!filter*4, E4!pw!filter*4"
    assert f"p1: [C4.i1*4, {written}, C4.i2*4, D4*4]" in text and "flags" not in text
    back = songio.loads(text)
    assert back["records"]["ins"][1]["flags"] == 0xD0 and "flags" not in back["records"]["ins"][2]
    assert songio.dumps(back) == text
    assert np.array_equal(grid(back), grid(flagged))
    assert not np.array_equal(grid(back), grid(plain))


def test_lifetime_commands_take_no_command_slot_and_legato_notes_start_nothing():
    defs = SW_INS + "      x:\n        x1: (v - 7) % 65536\n"
    ir = songio.loads(
        song("sidwizard", defs, {"p1": "C4.i1*4, D4!ad:16!freq:x1!pw*4, E4!legato!pw*4"})
    )
    assert ir["records"]["ins"][1]["flags"] == 0x50
    assert [code for code, _ in ir["phrases"]["p1"][1]["commands"]] == [288, 16]
    out = songio.dumps(ir)
    assert "D4!ad:16!freq:x1!pw*4, E4!legato*4" in out
    assert songio.dumps(songio.loads(out)) == out


def test_a_note_under_instruments_that_keep_and_restart_has_no_written_form():
    ir = songio.loads(LOOPED)
    plays = list(lifetime.notes(ir["tracks"][:1], ir["phrases"], 0, 63))
    assert [(name, ins, sel) for name, _, ins, sel in plays] == [
        ("p1", 1, True),
        ("p3", 1, False),
        ("p2", 2, True),
        ("p3", 2, False),
        ("p3", 2, False),
        ("p2", 2, True),
        ("p3", 2, False),
    ]
    assert "!pw" not in songio.dumps(ir)
    ir["records"]["ins"][1]["flags"] = 0x50
    with pytest.raises(songio.SongFormatError, match="keep and restart the pw program"):
        songio.dumps(ir)
    ir["records"]["ins"][2]["flags"] = 0x50
    assert ": [D4!pw*4]" in songio.dumps(ir)


def test_goattracker_accepts_the_command_where_no_program_starts_and_writes_it_implicitly():
    plain = songio.loads(song("goattracker2", GT_INS, {"p1": GT_PHRASE}))
    kept = GT_PHRASE.replace("D4.i2", "D4.i2!pw!filter").replace("E4", "E4!pw")
    ir = songio.loads(song("goattracker2", GT_INS, {"p1": kept}))
    assert ir == plain and "!pw" not in songio.dumps(ir)


@pytest.mark.parametrize(
    "pr,defs,atoms,match",
    [
        ("sidwizard", SW_INS, "C4.i1*4, D4!pw*4, E4*4", "is on some notes that start"),
        ("sidwizard", SW_INS, "C4.i1!pw*4, D4*4", "selects an instrument, which restarts"),
        ("sidwizard", SW_INS, "C4.i1*4, -!filter*4", "!filter needs a note"),
        ("sidwizard", SW_INS, "C4.i1*4, D4!pw:1*4", "the player can't express !pw:1"),
        ("goattracker2", GT_INS, "C4.i1!pw*2", "whose instrument i1 starts the pw"),
        ("goattracker2", GT_INS, "F4.i3!pw*2", "whose instrument i3 starts the pw"),
        ("goattracker2", GT_INS, "D4.i2!run:pw1!pw*2", "on a note that starts the program"),
        (
            "defmon",
            "      ins:\n        i1: {ctrl: [{ctrl: 65}, end]}\n",
            "C4.i1!filter",
            "can't express !filter",
        ),
    ],
)
def test_the_reader_refuses_a_lifetime_command_the_player_cannot_play(pr, defs, atoms, match):
    with pytest.raises(songio.SongFormatError, match=f"{pr}: .*{match}"):
        songio.loads(song(pr, defs, {"p1": atoms}))


def test_a_stop_command_names_its_slot():
    text = song(
        "goattracker2",
        GT_INS,
        {"p1": "C4.i1*2, -!pw:none*2, -!filter:none*2, -!run:none*2, D4.i3!run:pw1*2"},
    )
    ir = songio.loads(text)
    assert commands(ir)[1:4] == [[9, 0], [10, 0], [8, 0]]
    assert songio.dumps(ir) == songio.dumps(songio.loads(songio.dumps(ir)))
    assert "-!pw:none*2, -!filter:none*2, -!run:none*2" in songio.dumps(ir)
    for atoms, pointer in ((text, 0), (text.replace("-!pw:none", "-"), 2)):
        player = SongPlayer(songio.loads(atoms))
        player.render_grid(24)
        assert player.cell("pptr", 0) == pointer
    for wrong in ("-!run:pw", "-!pw:pw1", "-!pw:1"):
        with pytest.raises(songio.SongFormatError):
            songio.loads(text.replace("-!pw:none", wrong))
