"""The lift as a ``pysidtracker.pathdiff`` side, and the native-vs-lift divergences pathdiff
localised, each a strict xfail explained by whydiff until its fix lands."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from conftest import gt_tune
from pysidtracker.pathdiff import check_case, xfail_params

from unitracker.lift import backends, verify
from unitracker.lift.base import LiftError
from unitracker.lift.goattracker import GoatTrackerLift
from unitracker.player import oracle
from unitracker.player.canonical import REG_COUNT

MANIFEST = Path(__file__).with_name("pathdiff.json")


def test_pathdiff_side_plays_the_document_with_its_voices_onsets():
    lift = GoatTrackerLift(gt_tune([(0x41, 0), (0xFF, 0)])[0])
    lift.run()
    grid, starts = oracle.play(lift.document, 120)
    subject = verify.pathdiff_side(lift.document)
    player = subject.make()
    played = []
    for _ in grid:
        player.play_frame()
        played.append([c[i] for c, i in (subject.cell(player, r) for r in range(REG_COUNT))])
    assert subject.label == "lift"
    np.testing.assert_array_equal(np.array(played, dtype=np.uint8), grid)
    assert subject.starts(player) == starts and starts[0] is not None


def test_pathdiff_lift_refuses_a_lift_without_a_document(monkeypatch):
    refused = SimpleNamespace(run=lambda: None, document=None, gap="no recurrence")
    monkeypatch.setattr(backends, "lifter", lambda raw, subtune: refused)
    with pytest.raises(LiftError, match="no recurrence"):
        backends.pathdiff_lift(b"", 1)


@pytest.mark.parametrize("case", xfail_params(MANIFEST))
def test_pathdiff(case):
    check_case(case)
