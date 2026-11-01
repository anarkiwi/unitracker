"""Song bytes of every SID-Wizard fixture against the committed baseline: a lift that grows a
fixture's document is a change to justify in the commit that updates ``lift_sizes_sidwizard.json``
(``python tests/test_lift_sizes_sidwizard.py`` rewrites it)."""

import json
import sys
from pathlib import Path

import pytest
from pysidtracker.testing import resolve_tune

from conftest import SIDWIZARD
from unitracker.lift import invariants
from unitracker.lift import sidwizard as sw

BASELINE = Path(__file__).with_name("lift_sizes_sidwizard.json")
EXACT = [n for n in SIDWIZARD if n != "friendly_fire"]


def song_bytes(name: str):
    """The song bytes of fixture ``name``, None where it is unavailable or refused."""
    path = resolve_tune(SIDWIZARD[name][0])
    if path is None:
        return None
    try:
        text, _ = sw.document_of(sw.core_of_sid(Path(path).read_bytes()))
    except sw.Refusal:
        return None
    return invariants.sizes(text)["song"]


def test_no_fixture_grows_past_its_baseline():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    have = {n: got for n in EXACT if (got := song_bytes(n)) is not None}
    if have:
        assert invariants.grown(have, baseline) == []
    else:
        pytest.skip("no fixture available")


def test_the_baseline_is_every_fixture_that_lifts_exactly():
    assert set(json.loads(BASELINE.read_text(encoding="utf-8"))) == set(EXACT)


def main() -> None:
    """Rewrite the baseline from the fixtures as lifted now."""
    out = {n: got for n in EXACT if (got := song_bytes(n))}
    BASELINE.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(len(out), "fixtures", file=sys.stderr)


if __name__ == "__main__":
    main()
