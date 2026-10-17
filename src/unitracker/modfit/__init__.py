"""Rewrite playroutine modulations as proved bounded accumulators."""

import functools
from importlib import resources

import yaml

from unitracker.format.songio import library


@functools.lru_cache(maxsize=None)
def playroutine(name: str) -> dict:
    """The library playroutine ``name``, loaded once (callers must not mutate it)."""
    return library(name)


@functools.lru_cache(maxsize=None)
def specs() -> dict:
    """The packaged modulation and orbit specs."""
    return yaml.safe_load(resources.files(__name__).joinpath("specs.yaml").read_text())


def resolve(doc, path: str):
    """The node at dotted ``path``; integer-looking keys also match int keys."""
    for part in path.split("."):
        doc = doc[int(part)] if part.isdigit() and int(part) in doc else doc[part]
    return doc
