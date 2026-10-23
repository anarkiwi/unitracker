"""Playroutine codec declarations: their checks, the tables their expressions read, and the
library note tables."""

import math

import pytest

from unitracker.playroutine.codec import CodecError, CommandCodec, Evaluator, RowCodec, evaluate
from unitracker.playroutine.library import library
from unitracker.playroutine.records import record_types

LIBS = {p: library(p) for p in ("goattracker2", "defmon", "sidwizard")}
TYPES = {"goattracker2": record_types(LIBS["goattracker2"])}


@pytest.mark.parametrize(
    "spec,match",
    [
        ({"keys": {"wave": 1}}, "not a shared row key"),
        ({"keep": ["ctrl"]}, "unknown key keep in a codec shape"),
        ({"keys": {"run": {"ref": "pulsetab"}}}, "both ref and field"),
        ({"raw": {"w": {"range": [0, 3]}}}, "a raw field is an expression"),
        ({1: {"name": "mod"}}, "not a shared command name"),
        ({1: {"name": "run", "ref": "nope"}}, "unknown type 'nope'"),
        ({"keys": {"ad": {"byte": "n"}}}, "ad has no byte form"),
    ],
)
def test_codec_declarations_are_checked(spec, match):
    types = TYPES["goattracker2"]
    with pytest.raises(CodecError, match=match):
        if 1 in spec:
            CommandCodec(spec, types)
        else:
            RowCodec(types["ctrltab"], [spec], 0)


@pytest.mark.parametrize("none, other", [("none", "hold"), ("hold", "none")])
def test_a_freq_entry_spells_its_none_argument(none, other):
    """``none: hold`` writes the none argument as ``!freq:hold``, which alone decodes to it;
    :meth:`CommandCodec.stop` finds it either way, and a command without one has none."""
    var = {"name": "freq", "ref": "x"} | ({"none": "hold"} if none == "hold" else {})
    cmds = CommandCodec({16: var, 5: {"name": "ad"}}, record_types(LIBS["goattracker2"]))
    assert cmds.encode(16, 0) == ("freq", none)
    assert cmds.decode("freq", none) == cmds.stop("freq") == (16, 0)
    with pytest.raises(CodecError):
        cmds.decode("freq", other)
    with pytest.raises(CodecError, match="can't express !ad:none"):
        cmds.stop("ad")


def test_codec_expressions_read_only_declared_tables():
    assert Evaluator(None, {"t": [7]})("t[0] + t[5]", {}) == 7
    for call in (lambda: Evaluator()("t[0]", {}), lambda: evaluate("nope(", {})):
        with pytest.raises(CodecError):
            call()


def midi(freq_word):
    return round(69 + 12 * math.log2(freq_word * 985248 / 16777216 / 440))


@pytest.mark.parametrize(
    "player,words,notes",
    [
        ("goattracker2", lambda t, i: t["ftab"][i], range(24, 96)),
        ("defmon", lambda t, i: t["note_lo"][i] | t["note_hi"][i] << 8, range(24, 110)),
        ("sidwizard", lambda t, i: t["freqlo"][i] | t["freqhi"][i] << 8, range(1, 96)),
    ],
)
def test_note_offsets_follow_the_frequency_tables(player, words, notes):
    pr = LIBS[player]
    for index in notes:
        assert midi(words(pr["tables"], index)) == index - pr.get("note_offset", 0) + 12
