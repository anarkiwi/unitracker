"""The lift's invariants: a document holds no more objects of a kind than its native tune."""

import pytest

import optimiser_docs as D

from unitracker import cli
from unitracker.direct import invariants as inv

DOC = (
    D.HEAD + "voices:2\ntempo:6\n"
    "ins:\n i1:{ad:9,sr:0,ctrl:c1,pw:pw1}\n i2:{ad:9,sr:240,ctrl:c1}\n i3:{ad:1,sr:2,ctrl:c2}\n"
    " i4:{ad:1,sr:2,ctrl:c3}\n"
    "ctrl:\n c1:[{ctrl:65},end]\n c2:[{ctrl:17,run:f1},end]\n c3:[{ctrl:33},end]\n"
    " c4:[{ctrl:129},end]\n"
    "pw:\n pw1:[{pw:2048},{pw:1024},end]\n"
    "filter:\n f1:[{cut:100},{cut:50},end]\n"
    "phrase:\n p1:[C4.i1,D4.i2]\n p2:[E4 run:c3,-]\n p3:[G4.i3,-]\n p4:[A4.i4,-]\n"
    "song:\n - play:[p1,p2]\n - play:[p1+2,~]\n - play:[p1-1,p3]\n - end\n"
)
COUNTS = {"instruments": 3, "ctrl": 3, "pw": 1, "filter": 1, "phrases": 3, "song": (3, 2)}


def test_the_played_objects_are_counted_once_each():
    assert inv.lifted(DOC) == COUNTS | {"sidtab": 6}


@pytest.mark.parametrize(
    "native,message",
    [
        (COUNTS, None),
        ({}, None),
        (COUNTS | {"pw": 0, "phrases": 2}, "1 lifted pw > 0 native"),
        ({"song": (3, 1), "sidtab": 6}, "2 lifted song on voice 2 > 1 native"),
        ({"song": (2, 2), "sidtab": 5}, "6 lifted sidtab > 5 native"),
    ],
)
def test_the_first_kind_exceeded_is_named_with_both_counts(native, message):
    assert inv.exceeded(inv.lifted(DOC), native) == message


def test_a_document_with_more_objects_than_its_tune_is_refused():
    inv.check(DOC, COUNTS)
    with pytest.raises(inv.ModelError, match="3 lifted instruments > 2 native"):
        inv.check(DOC, {"instruments": 2})


@pytest.mark.parametrize(
    "tune,kinds",
    [
        ("metal_warrior", {"instruments", "ctrl", "pw", "filter", "phrases", "song"}),
        ("space_invadarz", {"instruments", "ctrl", "pw", "phrases", "song"}),
        ("crosseyed", {"sidtab"}),
    ],
)
def test_each_lifter_counts_its_native_objects(tune, kinds, request, capsys):
    raw = request.getfixturevalue(tune).read_bytes()
    native = cli.lifter(raw)[1].native_counts(raw)
    assert set(native) == kinds and all(native.values())
    assert len(native.get("song", (1, 1, 1))) == 3
    message = inv.exceeded(inv.lifted(cli.lift(raw, check=False)), native)
    path = request.getfixturevalue(tune)
    if message is None:
        assert cli.lift(raw) == cli.lift(raw, check=False)
        return
    with pytest.raises(inv.ModelError, match=message):
        cli.lift(raw)
    assert cli.lift_main([str(path)]) == 1 and message in capsys.readouterr().err
