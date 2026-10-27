"""The shared builder spells a trace's programs without what changes nothing, and the defMON
tracer's documents play the replay."""

import pytest
from unitracker_grammar import document as d
from unitracker_player.player import play

from unitracker.direct import builder as b
from unitracker.direct import cli
from unitracker.direct import defmon as dm
from unitracker.direct import defmon_trace as dt
from unitracker.direct.trace import Command, Effect, Note, Start, Step

TICKS = 3000
SLOTS = {"freq": ("n", "p"), "pw": ("sw",), "cut": ("k1", "k2")}
PITCH = d.Call("pitch", ())


class Fake:
    """A tracer of one voice whose positions count down to a stop, or cycle mod ``cycle``."""

    def __init__(self, events, cycle=None):
        self.stream, self.cycle = events, cycle

    @staticmethod
    def head():
        return {
            "meta": (("title", "t"),),
            "clock": (19656, 985248),
            "slots": (("freq", (d.Slot("p"),)),),
            "state": None,
            "tables": (),
            "code": {"lift": "lda #m\nsta v\nrts\n"},
            "instruments": {"i1": d.Instrument({"p.freq": d.Call("lift", (1,))})},
        }

    @staticmethod
    def init():
        return (d.Event("-", "i1", (("run", d.Run("ctrl", d.Program([{"ad": 1}]))),)),)

    def events(self):
        yield from self.stream

    def step(self, pos):
        if self.cycle:
            return (pos + 1) % self.cycle, [(("ad", pos),)]
        return (pos - 1 if pos else None), [(Effect("lift", (("m", pos),)),)] if pos % 2 else []

    @staticmethod
    def bind(effect, voice):
        return (("p.freq", d.Call("lift", (dict(effect.params)["m"] + voice,))),)


def test_a_walk_holds_each_row_to_the_next_fetch_and_loops_where_a_position_recurs():
    rows, loop = b.walk(Fake([]), 5, 0)
    assert [(h, k) for h, k, _ in rows] == [
        (2, (("p.freq", d.Call("lift", (5,))),)),
        (2, (("p.freq", d.Call("lift", (3,))),)),
        (1, (("p.freq", d.Call("lift", (1,))),)),
    ]
    assert loop is None
    rows, loop = b.walk(Fake([], cycle=3), 1, 0)
    assert [k for _, k, _ in rows] == [(("ad", 1),), (("ad", 2),), (("ad", 0),)] and loop == 0


def test_tidy_drops_empty_rows_but_keeps_their_ticks():
    a, e = (("ad", 1),), ()
    assert b.tidy([[1, a, 0], [2, e, 1], [3, e, 2]], None) == ([[1, a, 0]], None)
    assert b.tidy([[1, a, 0], [2, e, 1], [3, a, 2]], 2) == ([[3, a, 0], [3, a, 2]], 1)
    assert b.tidy([[1, a, 0], [2, e, 1]], 1) == ([[1, a, 0]], None)
    rows, loop = b.tidy([[1, a, 0], [2, e, 1], [3, a, 2], [4, a, 3]], 1)
    assert [h for h, _, _ in rows] == [3, 3, 6] and loop == 1


def test_a_key_every_execution_already_holds_goes():
    pitch = (("p.freq", PITCH),)
    rows = [[1, pitch + (("note", d.Signed(0)),), 0], [1, pitch + (("ad", 2),), 1]]
    at_note = b.Context(entry=b.noted({}, ()))
    kept = b.plain(rows, None, at_note, SLOTS, set())
    assert [k for _, k, _ in kept] == [pitch, (("ad", 2),)]
    noted = b.Context(entry={}, notes={(("p.freq", (d.Call("vib", ()), False)),)})
    assert [k for _, k, _ in b.plain(rows, None, noted, SLOTS, set())][1] == pitch + (("ad", 2),)
    looped = b.plain(
        [[1, (("ad", 2),), 0], [1, (("ad", 3),), 1]], 0, b.Context(entry={}), SLOTS, set()
    )
    assert [k for _, k, _ in looped] == [(("ad", 2),), (("ad", 3),)]


def test_a_key_a_slot_listens_for_stays():
    state = b.apply({}, [("p.freq", (PITCH, False)), ("ctrl", 65), ("gate", 1)])
    keys = (("ctrl", 65), ("gate", 1), ("p.freq", PITCH))
    assert not b.kept(keys, state, SLOTS, set())
    assert b.kept(keys, state, SLOTS, {"row.freq", "row.ctrl"}) == (("ctrl", 65), ("p.freq", PITCH))
    assert b.kept(keys, state, SLOTS, {"row"}) == (("p.freq", PITCH),)
    assert b.facts("pw", 3, SLOTS) == (("sw.pw", (3, False)),)
    assert b.facts("note", "C4", SLOTS) == (("abs", "C4"),)
    assert b.facts("mask", 9, SLOTS) == (("mask", 8),) and not b.facts("res", 1, SLOTS)
    assert b.compiled("route_on") == "route" and b.compiled("k1.cut") == "cut"


def test_shared_slots_two_voices_bind_and_pulse_programs_are_unknown_between_rows():
    sub = d.Run("pw", d.Program([{"ad": 4}]))
    one = (((1, (("k1.cut", 1), ("run", sub))),), None)
    two = (((1, (("k1.cut", 2),)),), None)
    ctx = {one: b.Context(voices={0}), two: b.Context(voices={1})}
    b.interfere(ctx, SLOTS)
    assert ctx[one].tops == {"k1.cut", "ad"} and ctx[two].tops == {"k1.cut"}


def test_the_built_document_names_by_first_use_and_loops():
    stream = [
        Step(1, "a"),
        Command(1, 0, "vol", 15),
        Note(1, 0, "C4"),
        Start(1, 0, 5),
        Note(4, 0, "D4"),
        Step(6, "b"),
        Note(6, 0, "E4"),
        Start(6, 0, 1),
        Step(9, "a"),
    ]
    built = b.build(Fake(stream))
    progs = built.doc.records("ctrl")
    assert list(progs) == ["c1", "c2", "c3"] and built.doc.loop == 1
    assert len(progs["c2"].rows) == 3 and not progs["c3"].rows
    assert "lift(m)" in built.text.split("ins:")[0]
    assert len(play(built.text, ticks=12)) == 12
    stated = b.build(Fake(stream), {"stated"}).doc.records("phrase")
    assert [e.ins for e in stated["p2"]] == ["i1", None, None, None]
    with pytest.raises(b.BuildError, match="without a loop"):
        b.build(Fake(stream[:-1]))


@pytest.mark.parametrize("name", ["crosseyed", "tractorbeam", "rent_a_cop", "persei", "ngc1277"])
def test_the_traced_document_plays_the_replay(request, name):
    cells, text, _ = dt.verify(request.getfixturevalue(name).read_bytes(), frames=TICKS)
    assert not cells.size, cli.summary(cells, 3)
    old = dm.transcribe(request.getfixturevalue(name).read_bytes())
    assert len(text) < len(old)


def test_defmon_effects_bind_its_templates():
    """Each shared effect kind binds the defMON template of its pitch mode or column."""
    assert dt.mode(0) == Effect("pitch")
    assert dt.Tracer.bind(dt.mode(0xD8), 0)[0] == ("p.freq", dm.pitch(0xD8))
    assert dt.Tracer.bind(dt.mode(3), 1)[1] == ("pm.pw", dm.call("pmode", m=3))
    cut = Effect("cutoff", (("layer", "b"), ("set", 1), ("offset", 4), ("value", 0), ("step", 0)))
    assert dt.Tracer.bind(cut, 2) == (("k3b.cut", dm.call("acid", g=1, c=4, j=0, k=0)),)


def test_a_mismatch_is_explained_through_the_player(crosseyed):
    """whydiff traces the document through unitracker-player to the first differing cell."""
    raw = crosseyed.read_bytes()
    text = b.build(dt.Tracer(raw)).text
    ref, _ = dm.reference(raw, 0, 40)
    assert b.explain(text, raw, ref, frames=40) is None
    bad = ref.copy()
    bad[20, 24] ^= 1
    why = b.explain(text, raw, bad, frames=40)
    assert why.frame == 20 and why.register == 24
