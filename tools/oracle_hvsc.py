"""unitracker's renderer for the ``pysidtracker.survey`` whole-song HVSC oracle survey.

    PYTHONPATH=tools python -m pysidtracker.survey run $HVSC tunes.csv --work out/survey \
        --render oracle_hvsc:render
    python -m pysidtracker.survey file tools/oracle_hvsc.py:native TUNE.sid --frames N

``render`` lifts each subtune, converts it in-process (the survey parallelises over tunes),
writes its canonical document, reads it back and plays it; ``native`` plays the backend's player.
"""

import numpy as np

from unitracker.format import songio
from unitracker.lift import backends
from unitracker.modfit.convert import convert
from unitracker.player.vm import SongPlayer

PLAYER = "goattracker|sid-?wizard|defmon"


def render(data, song, nframes):
    """Render 1-based ``song`` of a ``.sid`` through its lifted document, and its backend."""
    text = songio.dumps(convert(backends.lift(data, song - 1, expressions=False), workers=1))
    grid = SongPlayer(songio.loads(text)).render_grid(nframes)
    return np.asarray(grid, np.int16), {"backend": backends.detect(data), "chars": len(text)}


def native(data, song, nframes):
    """Render 1-based ``song`` of a ``.sid`` with its backend's native player."""
    grid = backends.native_player(data, song - 1).render_grid(nframes)
    return np.asarray(grid, np.int16), {"backend": backends.detect(data)}
