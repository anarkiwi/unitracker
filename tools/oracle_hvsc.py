"""unitracker's renderer for the ``pysidtracker.survey`` whole-song HVSC oracle survey.

    PYTHONPATH=tools python -m pysidtracker.survey run $HVSC tunes.csv --work out/survey \
        --render oracle_hvsc:render
    python -m pysidtracker.survey file tools/oracle_hvsc.py:native TUNE.sid --frames N

``render`` lifts each subtune in-process (the survey parallelises over tunes) and plays its
generic document on unitracker-player; ``native`` plays the backend's player.
"""

import os

import numpy as np

from unitracker.lift import backends
from unitracker.lift.base import LiftError
from unitracker.player import oracle

PLAYER = "goattracker|sid-?wizard|defmon"


def render(data, song, nframes):
    """Render 1-based ``song`` of a ``.sid`` through its generic document, and its backend."""
    os.environ.setdefault("UNITRACKER_WORKERS", "1")
    lift = backends.lifter(data, song - 1)
    lift.run()
    if lift.document is None:
        raise LiftError(f"no generic document: {lift.gap}")
    grid = oracle.play(lift.document, nframes)[0]
    info = {"backend": backends.detect(data), "chars": len(lift.document)}
    return np.asarray(grid, np.int16), info


def native(data, song, nframes):
    """Render 1-based ``song`` of a ``.sid`` with its backend's native player."""
    grid = backends.native_player(data, song - 1).render_grid(nframes)
    return np.asarray(grid, np.int16), {"backend": backends.detect(data)}
