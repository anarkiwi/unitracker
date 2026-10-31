"""Song bytes of every defMON fixture against the committed baseline: a lift that grows a
fixture's document is a change to justify in the commit that updates ``lift_sizes_defmon.json``
(``python tests/test_lift_sizes_defmon.py`` rewrites it)."""

import json
import sys
from pathlib import Path

import pytest
from pysidtracker.testing import resolve_tune

from conftest import DEFMON
from unitracker.lift import defmon as d
from unitracker.lift import invariants

BASELINE = Path(__file__).with_name("lift_sizes_defmon.json")


def song_bytes(name: str):
    """The song bytes of fixture ``name``, None where it is unavailable or refused."""
    path = resolve_tune(DEFMON[name][0])
    if path is None:
        return None
    try:
        text = d.document_text(d.core_of_sid(Path(path).read_bytes()))
    except d.Refusal:
        return None
    return invariants.sizes(text)["song"]


def test_no_fixture_grows_past_its_baseline():
    have = {n: got for n in DEFMON if (got := song_bytes(n)) is not None}
    if not have:
        pytest.skip("no fixture available")
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert invariants.grown(have, baseline) == []


def test_the_baseline_names_only_fixtures_that_lift():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert set(baseline) <= set(DEFMON) and "timewarp" not in baseline


def main() -> None:
    """Rewrite the baseline from the fixtures as lifted now."""
    out = {n: got for n in DEFMON if (got := song_bytes(n)) is not None}
    BASELINE.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(len(out), "fixtures", file=sys.stderr)


if __name__ == "__main__":
    main()
