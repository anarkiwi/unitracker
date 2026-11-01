"""The reference players of the defMON and GoatTracker 2 lifts play on the native core."""

# pylint: disable=protected-access

import pytest
from pysidtracker import find_format
from pysidtracker.playspec import native
from pysidtracker.playspec.player import SpecPlayer

from unitracker.cli import lift
from unitracker.lift.shared import spec_played

pytestmark = pytest.mark.skipif(not native.available(), reason="the native core is not built")


def players(monkeypatch, raw: bytes) -> list:
    """The spec players made while ``raw`` is lifted."""
    made = []
    init = SpecPlayer.__init__

    def record(self, *args, **kwargs):
        made.append(self)
        init(self, *args, **kwargs)

    monkeypatch.setattr(SpecPlayer, "__init__", record)
    lift(raw)
    return made


def test_the_defmon_lift_plays_its_references_natively(monkeypatch, glow_worm):
    made = players(monkeypatch, glow_worm.read_bytes())
    assert made
    for player in made:
        assert player._frames and player._bails == 0
        assert player._frames - player._played <= 2


@pytest.mark.parametrize("tune", ["glow_worm", "metal_warrior"])
def test_the_frame_by_frame_reference_plays_natively(request, tune):
    raw = request.getfixturevalue(tune).read_bytes()
    fmt = find_format(raw)
    player = fmt.spec_player(fmt.parser.parse(raw), notes=True)
    grid, edges = spec_played(player, 60)
    assert grid.shape == (60, 25) and len(edges)
    assert player._frames == 60 and player._frames - player._played <= 2
