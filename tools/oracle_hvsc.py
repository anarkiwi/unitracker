"""unitracker's renderer for the ``pysidtracker.survey`` whole-song HVSC oracle survey.

    PYTHONPATH=tools python -m pysidtracker.survey run $HVSC tunes.csv --work out/survey \
        --render oracle_hvsc:render

Each subtune is lifted, converted in-process (the survey parallelises over tunes), written as
its canonical document, read back and played by the generic player.
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
