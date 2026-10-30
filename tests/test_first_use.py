"""A lifted document with its definitions at their first use plays as with them in their
sections."""

import numpy as np
from unitracker_grammar.document import first_use, printed, read
from unitracker_player.player import play

from unitracker import cli


def test_a_lifted_document_plays_the_same_with_its_definitions_at_first_use(metal_warrior):
    text = cli.lift(metal_warrior.read_bytes())
    moved = printed(first_use(read(text)))
    assert "=" in moved.split("song:")[1] and read(moved) == first_use(read(text))
    assert np.array_equal(play(moved, ticks=1500), play(text, ticks=1500))
