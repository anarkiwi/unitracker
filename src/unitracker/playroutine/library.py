"""The playroutine library: each player build's decoding declaration, expanded."""

import copy
import functools
from importlib import resources

import yaml

from unitracker.playroutine.schema import expand

META_KEYS = ("title", "author", "released")
DEFAULTS = {"clock": {"cycles_per_tick": 19656, "clock_hz": 985248}, "voices": 3}


class SongFormatError(ValueError):
    """A song the generic writer cannot write, or a playroutine the library does not hold."""


@functools.cache
def _library_text(name: str) -> str:
    path = resources.files("unitracker").joinpath(f"playroutines/{name}.yaml")
    if not path.is_file():
        raise SongFormatError(f"no playroutine {name!r} in the library")
    return path.read_text()


@functools.cache
def _library(name: str) -> dict:
    return expand({"name": name, **yaml.safe_load(_library_text(name))})


def library(name: str) -> dict:
    """The library definition of playroutine ``name``, named by its file."""
    return copy.deepcopy(_library(name))
