"""Shared vocabulary: a playroutine's own row fields and command codes as the format's keys.

A record type's ``codec`` shapes map raw fields to shared keys and back; a written row is
accepted only if encoding its inverse gives the row again. A key may hold an expression,
a reference ``("x", index)`` to the format's expression records.
"""

import keyword
import re

from unitracker.expr import ExprError, Resolver

ROW_KEYS = ("wait", "hold", "ctrl", "note", "ad", "sr", "pw", "cut", "resfilt", "mode", "freq")
EXPR = "x"
RETIRED = {
    "mod": "retired in v1.5: write the modulation as an expression (`!freq:@x`, or a program"
    " row's `pw`, `cut` or `freq`)"
}
RETIRED_ROW_KEYS = {"fv": "retired in v1.7: write the $D418 filter-mode bits as `mode`"}
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
    "freq",
    "vib",
    "mark",
    # Player-specific: SID-Wizard.
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
)
NONE = "none"
_NOTE = re.compile(r"^(?P<abs>=)?(?P<n>[+-]?\d+)$")


class CodecError(ValueError):
    """A row or command can't be written in, or read from, the shared vocabulary."""


class _Env(Resolver):
    """Expressions over one mapping of names (a missing name reads as 0) and the
    playroutine's tables and functions."""

    def __init__(self, functions=None, tables=()):
        super().__init__(functions)
        self.tables = set(tables)

    def name(self, name):
        return f"E.get({name!r}, 0)"

    def table(self, name, index):
        if name not in self.tables:
            raise ExprError(f"unknown table {name!r}")
        return f"_tg(T[{name!r}], {index})"


def _tg(table, index):
    return table[index] if 0 <= index < len(table) else 0


class Evaluator:
    """Evaluates codec expressions with a playroutine's functions and a song's tables."""

    # pylint: disable=too-few-public-methods

    def __init__(self, functions=None, tables=None):
        self.tables = {k: [int(x) for x in v] for k, v in (tables or {}).items()}
        self.resolver = _Env(functions, self.tables)
        self.codes = {}

    def __call__(self, text, env: dict) -> int:
        text = str(text)
        code = self.codes.get(text)
        if code is None:
            try:
                code = compile(self.resolver.compile(text), "<codec>", "eval")
            except ExprError as exc:
                raise CodecError(str(exc)) from exc
            self.codes[text] = code
        glob = {"E": env, "T": self.tables, "_tg": _tg}
        return int(eval(code, glob))  # pylint: disable=eval-used


_PLAIN = Evaluator()


def evaluate(text, env: dict) -> int:
    """Value of expression ``text`` with names from ``env``."""
    return _PLAIN(text, env)


def env_name(key: str) -> str:
    """The expression name of written key ``key`` (``for`` reads as ``for_``)."""
    return f"{key}_" if keyword.iskeyword(key) else key


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
    """One way to write a row: forward keys, inverse raw fields, fields kept as they are.

    A raw field given as ``{search: [lo, hi]}`` is the first value in that range whose row
    writes back as the written one. A key's ``expr`` names the raw field of its expression.
    """

    # pylint: disable=too-few-public-methods

    def __init__(self, spec: dict):
        self.when = spec.get("when")
        self.keys = {}
        for key, kspec in (spec.get("keys") or {}).items():
            if key not in ROW_KEYS:
                raise CodecError(f"{key!r} is not a shared row key")
            self.keys[key] = kspec if isinstance(kspec, dict) else {"value": kspec}
        self.raw, self.search = {}, None
        for field, expr in (spec.get("raw") or {}).items():
            if not isinstance(expr, dict):
                self.raw[field] = expr
            elif self.search is not None or set(expr) != {"search"}:
                raise CodecError(f"raw {field}: one field per shape may be {{search: [lo, hi]}}")
            else:
                low, high = expr["search"]
                self.search = (field, range(int(low), int(high) + 1))
        self.keep = list(spec.get("keep") or [])
        clash = set(self.keep) & set(ROW_KEYS)
        if clash:
            raise CodecError(f"kept fields {sorted(clash)} collide with shared keys")


def is_expr(value) -> bool:
    """True for a written key's expression reference ``("x", index)``."""
    return isinstance(value, tuple) and len(value) == 2 and value[0] == EXPR


class RowCodec:
    """A record type's shapes, with the playroutine's note offset."""

    def __init__(self, rtype, spec: list, offset: int, evaluator=None):
        self.rtype = rtype
        self.shapes = [_Shape(s) for s in spec]
        self.offset = offset
        self.ev = evaluator or _PLAIN

    def expr_keys(self) -> dict:
        """``{key: number}`` of the keys that hold an expression; ``number`` when a key may also
        hold a plain value."""
        out = {}
        for shape in self.shapes:
            for key, kspec in shape.keys.items():
                if "expr" in kspec:
                    out[key] = out.get(key, False) or "value" in kspec
        return out

    def _full(self, row: dict) -> dict:
        return {f: int(row.get(f, d)) for f, d in self.rtype.fields.items()}

    def _encode(self, shape, row) -> dict:
        out = {}
        for key in ROW_KEYS:
            kspec = shape.keys.get(key)
            if kspec is None:
                continue
            field = kspec.get("expr")
            if field is not None and row[field] != self.rtype.default(field):
                out[key] = (EXPR, row[field])
                continue
            if "value" not in kspec or ("omit" in kspec and self.ev(kspec["omit"], row)):
                continue
            value = self.ev(kspec["value"], row)
            if key == "note":
                absolute = "abs" in kspec and bool(self.ev(kspec["abs"], row))
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
            if shape.when is None or self.ev(shape.when, full):
                return self._encode(shape, full)
        return None

    def _env(self, shape, norm) -> dict:
        env = {}
        for key in shape.keys:
            plain = key in norm and not is_expr(norm[key])
            env[f"has_{key}"] = int(plain)
            if key == "note" and plain:
                absolute, number = norm[key]
                env["note_abs"] = int(absolute)
                env["note"] = number + self.offset if absolute else number
            elif plain:
                env[env_name(key)] = norm[key]
        return env

    def _try(self, shape, norm) -> tuple[dict | None, str]:
        extra = set(norm) - set(shape.keys) - set(shape.keep)
        if extra:
            return None, f"keys {sorted(extra)}"
        for key, value in norm.items():
            if is_expr(value) and "expr" not in shape.keys.get(key, {}):
                return None, f"{key} holds no expression here"
        env = self._env(shape, norm)
        raw = {f: int(norm[f]) if f in norm else self.rtype.default(f) for f in shape.keep}
        raw.update({f: self.ev(expr, env) for f, expr in shape.raw.items()})
        raw.update({shape.keys[k]["expr"]: v[1] for k, v in norm.items() if is_expr(v)})
        field, values = shape.search or (None, [None])
        problem = "no shape"
        for value in values:
            if field is not None:
                raw[field] = value
            full = self._full(raw)
            if shape.when is not None and not self.ev(shape.when, full):
                continue
            try:
                back = self._encode(shape, full)
            except CodecError as exc:
                problem = str(exc)
                continue
            if back == norm:
                return dict(raw), ""
            bad = sorted(k for k in set(back) | set(norm) if back.get(k) != norm.get(k))
            problem = f"values of {bad}"
        return None, problem

    @staticmethod
    def normalize(written: dict) -> dict:
        """A written row with notes as ``(absolute, number)``."""
        return {k: note_value(v) if k == "note" else v for k, v in written.items()}

    def decode(self, written: dict) -> dict:
        """Raw fields for a written row; raises CodecError naming what the player can't express."""
        for key in written:
            if key in RETIRED_ROW_KEYS and not any(key in s.keep for s in self.shapes):
                raise CodecError(f"{self.rtype.name}: {key} is {RETIRED_ROW_KEYS[key]}")
        norm = self.normalize(written)
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
        """Raise unless ``raw`` (a program's last row after ``end``) still writes as ``written``.

        An expression key compares by presence: ``raw`` holds its reference, ``written`` its text.
        """
        enc, norm = self.encode(raw), self.normalize(written)
        exprs = {k for k, v in (enc or {}).items() if is_expr(v)}
        if (
            enc is None
            or not exprs <= set(norm)
            or {k: v for k, v in enc.items() if k not in exprs}
            != {k: v for k, v in norm.items() if k not in exprs}
        ):
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
        if name in RETIRED:
            raise CodecError(f"!{name} is {RETIRED[name]}")
        for code, ename, ref, var in self.entries:
            arg = self._arg(ref, var, value) if ename == name else None
            if arg is None:
                continue
            if ref is not None and not 0 <= arg <= 0xFF:
                raise CodecError(
                    f"!{name} names {ref} {arg}: a command's argument is a byte (0-255)"
                )
            try:
                if self.encode(code, arg) == (name, value):
                    return code, arg
            except CodecError:
                continue
        raise CodecError(f"the player can't express !{name}:{value}")
