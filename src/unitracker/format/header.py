"""A document's header: its playroutine entry, environment and initial tempo."""

import copy
import functools
from importlib import resources

import yaml

from unitracker.format import tempo
from unitracker.format.schema import expand
from unitracker.format.tablepatch import table_diff, unread_dropped
from unitracker.format.yamlstyle import Flow, FlowMap

REF_KEYS = ("ref", "constants", "tables")
META_KEYS = ("title", "author", "released")
DEFAULTS = {"clock": {"cycles_per_tick": 19656, "clock_hz": 985248}, "voices": 3}


class SongFormatError(ValueError):
    """A document does not follow the unitracker v1.20 format."""


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


def defaults(pr: dict) -> dict:
    """The ``clock`` and ``voices`` a song of playroutine ``pr`` has unless it says otherwise."""
    return {**DEFAULTS, **(pr.get("defaults") or {})}


def _refuse(problem) -> None:
    if problem is not None:
        raise SongFormatError(problem)


def read_playroutine(spec) -> tuple[dict, dict]:
    """``(playroutine, tables)`` of a document's ``playroutine`` entry."""
    if not isinstance(spec, dict):
        raise SongFormatError("playroutine must be a mapping")
    if "ref" not in spec:
        if not isinstance(spec.get("name"), str):
            raise SongFormatError("an embedded playroutine has a name")
        pr = expand(spec)
        return pr, {k: [int(x) for x in v] for k, v in (pr.pop("tables", None) or {}).items()}
    for key in sorted(set(spec) - set(REF_KEYS)):
        raise SongFormatError(f"unknown key {key} in playroutine")
    if not isinstance(spec["ref"], str):
        raise SongFormatError(f"no playroutine {spec['ref']!r} in the library")
    pr = library(spec["ref"])
    constants = _mapping(spec.get("constants"), "playroutine.constants")
    for key in sorted(set(constants) - set(pr.get("constants") or {})):
        raise SongFormatError(f"unknown key {key} in playroutine.constants")
    pr["constants"] = {**(pr.get("constants") or {}), **_ints(constants, "a constant")}
    tables = {k: [int(x) for x in v] for k, v in (pr.pop("tables", None) or {}).items()}
    for name, diff in _mapping(spec.get("tables"), "playroutine.tables").items():
        if name not in tables:
            raise SongFormatError(f"unknown table {name!r} in playroutine.tables")
        if isinstance(diff, list):
            tables[name] = list(_ints(dict(enumerate(diff)), f"an entry of {name!r}").values())
            continue
        for i, v in _ints(_mapping(diff, f"table {name!r}"), f"an entry of {name!r}").items():
            if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(tables[name]):
                raise SongFormatError(f"table {name!r} has no entry {i!r}")
            tables[name][i] = v
    return pr, tables


def _mapping(value, what: str) -> dict:
    """``value``, a mapping where ``what`` is one (none: empty)."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SongFormatError(f"{what} is a mapping")
    return value


def _ints(values: dict, what: str) -> dict:
    """``values``, each an integer of ``what``."""
    for v in values.values():
        if isinstance(v, bool) or not isinstance(v, int):
            raise SongFormatError(f"{what} is an integer, not {v!r}")
    return {k: int(v) for k, v in values.items()}


def meta(doc: dict) -> dict:
    """A document's ``meta``: its title, author and release."""
    value = doc.get("meta") or {}
    if not isinstance(value, dict):
        raise SongFormatError("meta must be a mapping")
    for key in sorted(set(value) - set(META_KEYS)):
        raise SongFormatError(f"unknown key {key} in meta")
    return dict(value)


def initial_tempo(pr: dict, doc: dict) -> dict:
    """``{tempo: value}``: the document's initial tempo, else the playroutine's default; empty
    for a playroutine without a tempo and a document without one."""
    if not (pr.get(tempo.NAME) or tempo.NAME in doc):
        return {}
    value = doc.get(tempo.NAME, tempo.default(pr))
    _refuse(None if value is None else tempo.problem(pr, value))
    return {tempo.NAME: value}


def playroutine_entry(pr: dict, ir: dict, embed: bool):
    """The ``playroutine`` entry of song ``ir`` of playroutine ``pr``: a library reference with
    differences, or embedded."""
    name = pr["name"]
    tables = {k: [int(x) for x in v] for k, v in (ir.get("tables") or {}).items()}
    if embed:
        full = expand(pr)
        out = {"name": name, "constants": FlowMap(pr.get("constants") or {})}
        out["tables"] = {k: Flow(v) for k, v in tables.items()}
        out.update((k, v) for k, v in full.items() if k not in ("name", "constants", "tables"))
        return out
    lib = library(name)
    own = ("constants", "tables")
    if {k: v for k, v in pr.items() if k not in own} != {
        k: v for k, v in lib.items() if k not in own
    }:
        raise SongFormatError(
            f"the song's playroutine differs from the library {name!r}; write with embed"
        )
    out = FlowMap(ref=name)
    libc = lib.get("constants") or {}
    consts = {k: v for k, v in (pr.get("constants") or {}).items() if libc.get(k) != v}
    if consts:
        out["constants"] = FlowMap(consts)
    diff = unread_dropped(table_diff(tables, lib.get("tables") or {}), ir)
    return {**out, "tables": diff} if diff else out


def environment(pr: dict, ir: dict) -> dict:
    """``clock``, ``voices`` and ``tempo`` of song ``ir`` where they differ from the defaults."""
    base = defaults(pr)
    env = {}
    for key in ("clock", "voices"):
        if key in ir and ir[key] != base[key]:
            env[key] = FlowMap(ir[key]) if isinstance(ir[key], dict) else ir[key]
    value = ir.get(tempo.NAME)
    if value is not None:
        _refuse(tempo.problem(pr, value))
        value = tempo.written(*tempo.pair(value))
        if value != tempo.default(pr):
            env[tempo.NAME] = Flow(value) if isinstance(value, list) else value
    return env
