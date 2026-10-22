"""Smaller songs with the same output: passes over the lifter's song.

Each edit is a pure function of a song and its arguments. The driver keeps an edit when its
generic document has fewer tokens, the lifter's VM replays it as the input (see
:func:`mismatch`) and its document plays on unitracker-player as the input's, and applies the
passes to a fixpoint (see docs/optimise.md).
"""

from unitracker.optimise.driver import Finding, Optimiser, Result, canonical, optimise
from unitracker.optimise.edits import (
    EDITS,
    alias,
    clear,
    drop_command,
    drop_instrument,
    drop_keep,
    factor,
    hoist,
    hold,
    hoist_value,
    merge,
    reloop,
    share,
    staircase,
    stitch,
    truncate,
    unhoist,
)
from unitracker.optimise.model import programs_of, shape
from unitracker.optimise.passes import (
    CANONICAL,
    LARGER_UNPROVED,
    PASSES,
    commands,
    expressions,
    fields,
    hoists,
    instruments,
    phrases,
    programs,
    shared_runs,
)
from unitracker.modfit.grammar import ENCODING, tokens
from unitracker.optimise.proof import Trace, mismatch
from unitracker.player import reach

__all__ = [
    "alias",
    "CANONICAL",
    "canonical",
    "clear",
    "commands",
    "drop_command",
    "drop_instrument",
    "drop_keep",
    "EDITS",
    "ENCODING",
    "expressions",
    "factor",
    "fields",
    "Finding",
    "hoist",
    "hoist_value",
    "hold",
    "hoists",
    "instruments",
    "LARGER_UNPROVED",
    "merge",
    "mismatch",
    "optimise",
    "Optimiser",
    "PASSES",
    "phrases",
    "programs",
    "programs_of",
    "reach",
    "reloop",
    "Result",
    "shape",
    "share",
    "shared_runs",
    "staircase",
    "stitch",
    "tokens",
    "Trace",
    "truncate",
    "unhoist",
]
