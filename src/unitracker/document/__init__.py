"""The generic document: unitracker-grammar's language, which unitracker-player plays, written
from a lifter's song."""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from unitracker.document.writer import (
        GenericError,
        Native,
        native_from,
        native_over,
        native_state,
        play_to,
        write,
    )

__all__ = [
    "GenericError",
    "Native",
    "native_from",
    "native_over",
    "native_state",
    "play_to",
    "write",
]


def __getattr__(name: str):
    """The writer's API, imported on first use so the document's submodules load alone."""
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f"{__name__}.writer"), name)
    globals()[name] = value
    return value
