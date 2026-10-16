"""Shared vocabulary: a playroutine's own row fields and command codes as the format's keys.

A record type's ``codec`` shapes map raw fields to shared keys and back; a written row is
accepted only if encoding its inverse gives the row again. Commands map codes to names.
"""

import functools
import re

from unitracker.expr import ExprError, Resolver

ROW_KEYS = ("wait", "hold", "ctrl", "note", "ad", "sr", "pw", "cut", "resfilt", "mode")
COMMAND_NAMES = (
    "run",
    "run2",
    "ad",
    "sr",
    "ctrl",
    "resfilt",
    "vol",
    "cut",
    "tempo",
    "vtempo",
    "funk",
    "slideup",
    "slidedown",
    "porta",
    "vib",
    "mark",
    # Player-specific: SID-Wizard.
    "pitchup",
    "pitchdown",
    "glide",
    "attack",
    "decay",
    "sustain",
    "release",
    "chord",
    "legato",
    "iattack",
    "idecay",
    "isustain",
    "irelease",
    "ichord",
    "notevib",
    "noteglide",
)
NONE = "none"
_NOTE = re.compile(r"^(?P<abs>=)?(?P<n>[+-]?\d+)$")


class CodecError(ValueError):
    """A row or command can't be written in, or read from, the shared vocabulary."""


class _Env(Resolver):
    """Expressions over one mapping of names; a missing name reads as 0."""

    def name(self, name):
        return f"E.get({name!r}, 0)"


@functools.cache
def _code(text) -> object:
    try:
        return compile(_Env().compile(text), "<codec>", "eval")
    except ExprError as exc:
        raise CodecError(str(exc)) from exc


def evaluate(text, env: dict) -> int:
    """Value of expression ``text`` with names from ``env``."""
    return int(eval(_code(str(text)), {"E": env}))  # pylint: disable=eval-used


def note_value(value) -> tuple[bool, int]:
    """``(absolute, number)`` for a written ``note``: an int is relative, ``"=n"`` absolute."""
    if isinstance(value, bool):
        raise CodecError(f"bad note {value!r}")
    if isinstance(value, int):
        return False, value
    match = _NOTE.match(str(value))
    if match is None or (match["abs"] and match["n"][0] == "+"):
        raise CodecError(f"bad note {value!r}")
    return bool(match["abs"]), int(match["n"])


def note_text(absolute: bool, number: int) -> str:
    """The written form of a note value."""
    return f"={number}" if absolute else f"{number:+d}"


class _Shape:
    """One way to write a row: forward keys, inverse raw fields, fields kept as they are."""

    # pylint: disable=too-few-public-methods

    def __init__(self, spec: dict):
        self.when = spec.get("when")
        self.keys = {}
        for key, kspec in (spec.get("keys") or {}).items():
            if key not in ROW_KEYS:
                raise CodecError(f"{key!r} is not a shared row key")
            self.keys[key] = kspec if isinstance(kspec, dict) else {"value": kspec}
        self.raw = dict(spec.get("raw") or {})
        self.keep = list(spec.get("keep") or [])
        clash = set(self.keep) & set(ROW_KEYS)
        if clash:
            raise CodecError(f"kept fields {sorted(clash)} collide with shared keys")


class RowCodec:
    """A record type's shapes, with the playroutine's note offset."""

    def __init__(self, rtype, spec: list, offset: int):
        self.rtype = rtype
        self.shapes = [_Shape(s) for s in spec]
        self.offset = offset

    def _full(self, row: dict) -> dict:
        return {f: int(row.get(f, d)) for f, d in self.rtype.fields.items()}

    def _encode(self, shape, row) -> dict:
        out = {}
        for key in ROW_KEYS:
            kspec = shape.keys.get(key)
            if kspec is None or ("omit" in kspec and evaluate(kspec["omit"], row)):
                continue
            value = evaluate(kspec["value"], row)
            if key == "note":
                absolute = "abs" in kspec and bool(evaluate(kspec["abs"], row))
                out[key] = (absolute, value - self.offset if absolute else value)
            else:
                out[key] = value
        for field in shape.keep:
            if row[field] != self.rtype.default(field):
                out[field] = row[field]
        return out

    def encode(self, row: dict) -> dict | None:
        """Shared keys for a raw row, notes as ``(absolute, number)``; None if no shape fits."""
        full = self._full(row)
        for shape in self.shapes:
            if shape.when is None or evaluate(shape.when, full):
                return self._encode(shape, full)
        return None

    def _try(self, shape, norm) -> tuple[dict | None, str]:
        extra = set(norm) - set(shape.keys) - set(shape.keep)
        if extra:
            return None, f"keys {sorted(extra)}"
        env = {}
        for key in shape.keys:
            env[f"has_{key}"] = int(key in norm)
            if key == "note" and key in norm:
                absolute, number = norm[key]
                env["note_abs"] = int(absolute)
                env["note"] = number + self.offset if absolute else number
            elif key in norm:
                env[key] = norm[key]
        raw = {f: int(norm[f]) if f in norm else self.rtype.default(f) for f in shape.keep}
        raw.update({f: evaluate(expr, env) for f, expr in shape.raw.items()})
        full = self._full(raw)
        if shape.when is not None and not evaluate(shape.when, full):
            return None, "no shape"
        back = self._encode(shape, full)
        if back == norm:
            return raw, ""
        bad = sorted(k for k in set(back) | set(norm) if back.get(k) != norm.get(k))
        return None, f"values of {bad}"

    def decode(self, written: dict) -> dict:
        """Raw fields for a written row; raises CodecError naming what the player can't express."""
        norm = {k: note_value(v) if k == "note" else v for k, v in written.items()}
        problems = []
        for shape in self.shapes:
            raw, problem = self._try(shape, norm)
            if raw is not None:
                return raw
            problems.append(problem)
        raise CodecError(
            f"{self.rtype.name}: the player can't express row {written!r} ({'; '.join(problems)})"
        )

    def check_end(self, raw: dict, written: dict) -> None:
        """Raise unless ``raw`` (a program's last row after ``end``) still writes as ``written``."""
        norm = {k: note_value(v) if k == "note" else v for k, v in written.items()}
        if self.encode(raw) != norm:
            raise CodecError(f"{self.rtype.name}: row {written!r} can't end a program")


class CommandCodec:
    """A playroutine's command codes as shared names."""

    def __init__(self, spec: dict, rtypes: dict):
        self.entries = []
        for code, variants in (spec or {}).items():
            for var in variants if isinstance(variants, list) else [variants]:
                name = str(var["name"])
                if name not in COMMAND_NAMES:
                    raise CodecError(f"{name!r} is not a shared command name")
                ref = var.get("ref")
                if ref is not None and ref not in rtypes:
                    raise CodecError(f"command {name} references unknown type {ref!r}")
                self.entries.append((int(code), name, ref, var))
        self.rtypes = rtypes

    def ref(self, code: int) -> str | None:
        """The record type command ``code``'s argument references, if any."""
        for ecode, _, ref, _ in self.entries:
            if ecode == code and ref is not None:
                return ref
        return None

    def encode(self, code: int, arg: int) -> tuple[str, object]:
        """``(name, value)``: value is an int, ``(type, index)`` for a reference, or ``none``."""
        for ecode, name, ref, var in self.entries:
            if ecode != code or ("when" in var and not evaluate(var["when"], {"arg": arg})):
                continue
            if ref is None:
                return name, evaluate(var["arg"], {"arg": arg}) if "arg" in var else arg
            if arg != self.rtypes[ref].none:
                return name, (ref, arg)
            codes = {c for c, n, r, _ in self.entries if n == name and r is not None}
            if len(codes) > 1:
                raise CodecError(f"!{name}:{NONE} can't say which of commands {sorted(codes)}")
            return name, NONE
        raise CodecError(f"command {code} with argument {arg} has no shared name")

    def _arg(self, ref, var, value):
        if ref is not None:
            if value == NONE:
                return self.rtypes[ref].none
            if isinstance(value, tuple) and value[0] == ref:
                return value[1]
            return None
        if isinstance(value, int) and not isinstance(value, bool):
            return evaluate(var["raw"], {"arg": value}) if "raw" in var else value
        return None

    def decode(self, name: str, value) -> tuple[int, int]:
        """``(code, arg)`` for a written command; ``value`` as :meth:`encode` returns it."""
        for code, ename, ref, var in self.entries:
            arg = self._arg(ref, var, value) if ename == name else None
            if arg is None:
                continue
            try:
                if self.encode(code, arg) == (name, value):
                    return code, arg
            except CodecError:
                continue
        raise CodecError(f"the player can't express !{name}:{value}")
