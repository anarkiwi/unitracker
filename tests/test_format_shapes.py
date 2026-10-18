"""Codec shape primitives the playroutines share: vocabulary key defaults and the note byte."""

import pytest

from unitracker.format.codec import CodecError, RowCodec, note_text
from unitracker.format.records import RecordType


def codec(fields, *shapes) -> RowCodec:
    return RowCodec(RecordType("t", {"fields": fields}), list(shapes), 0)


@pytest.mark.parametrize(
    "shape,raw,written",
    [
        ({"keys": {"hold": "w + 1"}, "raw": {"w": "hold - 1"}}, {"w": 0}, {}),
        ({"keys": {"hold": "w + 1"}, "raw": {"w": "hold - 1"}}, {"w": 3}, {"hold": 4}),
        (
            {"keys": {"hold": {"value": "w + 1", "omit": "w > 8"}}, "raw": {"w": "hold - 1"}},
            {"w": 0},
            {"hold": 1},
        ),
    ],
)
def test_hold_is_left_out_at_its_default_and_reads_back_as_it(shape, raw, written):
    rows = codec(["w"], shape)
    assert rows.encode(raw) == written
    assert rows.decode(written) == raw


def test_a_hold_its_omit_leaves_out_does_not_read_back():
    rows = codec(["w"], {"keys": {"hold": {"value": "w + 1", "omit": "w > 8"}}, "raw": {"w": "w"}})
    with pytest.raises(CodecError, match="values of"):
        rows.decode({})


SEVEN = {"byte": "n", "none": 0x80}
EIGHT = {"byte": "n", "none": 0x80, "abs": [0x81, 0xDF]}


@pytest.mark.parametrize(
    "note,n,written",
    [
        (SEVEN, 0x80, {}),
        (SEVEN, 0x05, {"note": (False, 5)}),
        (SEVEN, 0x45, {"note": (False, -59)}),
        (SEVEN, 0x90, {"note": (True, 16)}),
        (EIGHT, 0x45, {"note": (False, 69)}),
        (EIGHT, 0xE5, {"note": (False, -27)}),
        (EIGHT, 0x90, {"note": (True, 16)}),
        ({"byte": "n", "none": -1}, -1, {}),
        ({"byte": "n", "none": -1}, 0x80, {"note": (True, 0)}),
    ],
)
def test_a_note_byte_reads_absolute_relative_or_none(note, n, written):
    rows = codec({"n": 0x80}, {"keys": {"note": note}})
    assert rows.encode({"n": n}) == written
    assert rows.decode({k: note_text(*v) for k, v in written.items()}) == {"n": n}


@pytest.mark.parametrize("note", [{"byte": "n"}, {"byte": "n", "none": 0x80, "omit": "n == 0"}])
def test_a_note_byte_gives_its_none_and_nothing_else(note):
    with pytest.raises(CodecError, match="a note byte is"):
        codec(["n"], {"keys": {"note": note}})


def test_a_relative_note_past_the_byte_does_not_read_back():
    with pytest.raises(CodecError, match="can't express"):
        codec({"n": 0x80}, {"keys": {"note": SEVEN}}).decode({"note": 70})
