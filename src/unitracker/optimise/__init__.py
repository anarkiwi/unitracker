"""Smaller documents with the same output: passes over the internal representation.

Each edit is a pure function of a song and its arguments. The driver keeps an edit when its
written text has fewer tokens and plays the input's register grid (see :func:`mismatch`), and
applies the passes to a fixpoint (see docs/optimise.md).
"""

from unitracker.optimise.describe import describe, first_difference, one_line, writer
from unitracker.optimise.driver import Finding, Optimiser, Result, optimise
from unitracker.optimise.edits import (
    EDITS,
    alias,
    clear,
    drop_command,
    drop_instrument,
    drop_keep,
    factor,
    hoist,
    hoist_value,
    merge,
    reloop,
    share,
    truncate,
    unhoist,
)
from unitracker.optimise.model import entries, program, programs_of, references, shape, view
from unitracker.optimise.passes import (
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
from unitracker.optimise.proof import ENCODING, Trace, mismatch, tokens
from unitracker.player import reach

__all__ = [
    "alias",
    "clear",
    "commands",
    "describe",
    "drop_command",
    "drop_instrument",
    "drop_keep",
    "EDITS",
    "ENCODING",
    "entries",
    "expressions",
    "factor",
    "fields",
    "Finding",
    "first_difference",
    "hoist",
    "hoist_value",
    "hoists",
    "instruments",
    "LARGER_UNPROVED",
    "merge",
    "mismatch",
    "one_line",
    "optimise",
    "Optimiser",
    "PASSES",
    "phrases",
    "program",
    "programs",
    "programs_of",
    "reach",
    "references",
    "reloop",
    "Result",
    "shape",
    "share",
    "shared_runs",
    "tokens",
    "Trace",
    "truncate",
    "unhoist",
    "view",
    "writer",
]
