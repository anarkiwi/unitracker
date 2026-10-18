"""Codec shape primitives the playroutines share: vocabulary key defaults."""

import pytest

from unitracker.format.codec import CodecError, RowCodec
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
