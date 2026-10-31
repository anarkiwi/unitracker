"""Song bytes of every GoatTracker 2 fixture against the committed baseline: a lift that grows a
fixture's document is a change to justify in the commit that updates ``lift_sizes.json``
(``python tests/test_lift_sizes.py`` rewrites it)."""

import json
import sys
from pathlib import Path

import pytest
from pysidtracker.testing import resolve_tune

from conftest import CORPUS, gt_tune
from test_lift_goattracker2 import CASES
from unitracker.lift import goattracker2 as g
from unitracker.lift import invariants

BASELINE = Path(__file__).with_name("lift_sizes.json")


def song_bytes(name: str):
    """The song bytes of fixture ``name`` (``case:...`` a synthetic tune, ``corpus:...`` an HVSC
    one), None where it is unavailable or refused."""
    kind, key = name.split(":", 1)
    try:
        if kind == "case":
            core = gt_tune(**CASES[key])
        else:
            path = resolve_tune(CORPUS[key][0])
            if path is None:
                return None
            core = g.core_of_sid(Path(path).read_bytes())
        return invariants.sizes(g.document_text(core))["song"]
    except g.Refusal:
        return None


def names() -> list:
    """Every fixture's name."""
    return [f"case:{c}" for c in CASES] + [f"corpus:{c}" for c in CORPUS]


def test_no_fixture_grows_past_its_baseline():
    have = {n: got for n in names() if (got := song_bytes(n)) is not None}
    if not have:
        pytest.skip("no fixture available")
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert invariants.grown(have, baseline) == []


def test_the_baseline_names_only_fixtures():
    assert set(json.loads(BASELINE.read_text(encoding="utf-8"))) <= set(names())


def main() -> None:
    """Rewrite the baseline from the fixtures as lifted now."""
    out = {n: got for n in names() if (got := song_bytes(n)) is not None}
    BASELINE.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(len(out), "fixtures", file=sys.stderr)


if __name__ == "__main__":
    main()
