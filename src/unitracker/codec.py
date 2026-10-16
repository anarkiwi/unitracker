"""Shared vocabulary: a playroutine's own row fields and command codes as the format's keys.

A record type's ``codec`` shapes map raw fields to shared keys and back; a written row is
accepted only if encoding its inverse gives the row again. Commands map codes to names, or
to a ``mod`` record built from the command code and its argument.
"""

import keyword
import re

from unitracker.expr import ExprError, Resolver

MOD_KEYS = ("reg", "step", "rel", "to", "lo", "hi", "at", "every", "delay", "for")
ROW_KEYS = ("wait", "hold", "ctrl", "note", "ad", "sr", "pw", "cut", "resfilt", "mode") + MOD_KEYS
MOD = "mod"
NOTE_SYMBOL = "note"
SYMBOLS = {"reg": ("freq", "pw", "cut"), "at": ("hold", "wrap", "reflect", "reload")}
NOTE_KEYS = ("rel", "to", "for")
REG_WIDTH = {"freq": 16, "pw": 12, "cut": 11}
# Expression names for the symbolic values; `note` reads as -1.
SYMBOL_VALUES = {
    **{s.upper(): i for values in SYMBOLS.values() for i, s in enumerate(values)},
    "NOTE": -1,
}
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
    "mod",
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
    """Expressions over one mapping of names (a missing name reads as 0), the symbolic
    values, and the playroutine's tables and functions."""

    def __init__(self, functions=None, tables=()):
        super().__init__(functions)
        self.tables = set(tables)

    def name(self, name):
        if name in SYMBOL_VALUES:
            return str(SYMBOL_VALUES[name])
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


def _mod_value(key, value):
    """A written mod key's value, checked against the key's vocabulary."""
    if key in SYMBOLS:
        ok = value in SYMBOLS[key]
    elif key == "rel":
        ok = value == NOTE_SYMBOL
    else:
        ok = (isinstance(value, int) and not isinstance(value, bool)) or (
            key in NOTE_KEYS and value == NOTE_SYMBOL
        )
    if not ok:
        raise CodecError(f"bad {key} {value!r}")
    return value


def _mod_code(key, value) -> int:
    """The integer an expression reads for a written mod value."""
    if key in SYMBOLS:
        return SYMBOLS[key].index(value)
    return SYMBOL_VALUES["NOTE"] if value == NOTE_SYMBOL else value


def _mod_text(key, value):
    """The written form of the integer a shape gives mod key ``key``."""
    if key in SYMBOLS:
        if not 0 <= value < len(SYMBOLS[key]):
            raise CodecError(f"{key} value {value} has no name")
        return SYMBOLS[key][value]
    if key in NOTE_KEYS and value == SYMBOL_VALUES["NOTE"]:
        return NOTE_SYMBOL
    if key == "rel":
        raise CodecError(f"rel value {value} has no name")
    return value


_MISSING = object()


def mod_defaults(norm: dict, implies: dict) -> dict:
    """``norm`` without the mod keys that hold their default (see docs/format.md)."""
    reg = norm.get("reg", implies.get("reg"))
    defaults = {"every": 1, "delay": 0, "lo": 0, "at": "hold" if "to" in norm else "wrap"}
    if "to" not in norm:
        defaults["step"] = 0
    if reg in REG_WIDTH:
        defaults["hi"] = (1 << REG_WIDTH[reg]) - 1
    defaults.update(implies)
    return {k: v for k, v in norm.items() if k not in MOD_KEYS or defaults.get(k, _MISSING) != v}


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
    writes back as the written one; ``implies`` names mod keys the record type fixes.
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
        self.implies = {k: _mod_value(k, v) for k, v in (spec.get("implies") or {}).items()}
        if set(self.implies) - set(MOD_KEYS) or set(self.implies) & set(self.keys):
            raise CodecError(f"implies {sorted(self.implies)} must be unwritten mod keys")


class RowCodec:
    """A record type's shapes, with the playroutine's note offset."""

    def __init__(self, rtype, spec: list, offset: int, evaluator=None):
        self.rtype = rtype
        self.shapes = [_Shape(s) for s in spec]
        self.offset = offset
        self.ev = evaluator or _PLAIN

    def _full(self, row: dict) -> dict:
        return {f: int(row.get(f, d)) for f, d in self.rtype.fields.items()}

    def _encode(self, shape, row) -> dict:
        out = {}
        for key in ROW_KEYS:
            kspec = shape.keys.get(key)
            if kspec is None or ("omit" in kspec and self.ev(kspec["omit"], row)):
                continue
            value = self.ev(kspec["value"], row)
            if key == "note":
                absolute = "abs" in kspec and bool(self.ev(kspec["abs"], row))
                out[key] = (absolute, value - self.offset if absolute else value)
            elif key in MOD_KEYS:
                out[key] = _mod_text(key, value)
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
            env[f"has_{key}"] = int(key in norm)
            if key == "note" and key in norm:
                absolute, number = norm[key]
                env["note_abs"] = int(absolute)
                env["note"] = number + self.offset if absolute else number
            elif key in norm:
                env[env_name(key)] = _mod_code(key, norm[key]) if key in MOD_KEYS else norm[key]
        return env

    def _try(self, shape, norm) -> tuple[dict | None, str]:
        norm = mod_defaults(norm, shape.implies)
        extra = set(norm) - set(shape.keys) - set(shape.keep)
        if extra:
            return None, f"keys {sorted(extra)}"
        env = self._env(shape, norm)
        raw = {f: int(norm[f]) if f in norm else self.rtype.default(f) for f in shape.keep}
        raw.update({f: self.ev(expr, env) for f, expr in shape.raw.items()})
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
        """A written row with notes as ``(absolute, number)`` and mod values checked."""
        out = {}
        for key, value in written.items():
            if key == "note":
                out[key] = note_value(value)
            elif key in MOD_KEYS:
                out[key] = _mod_value(key, value)
            else:
                out[key] = value
        return out

    def decode(self, written: dict) -> dict:
        """Raw fields for a written row; raises CodecError naming what the player can't express."""
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
        """Raise unless ``raw`` (a program's last row after ``end``) still writes as ``written``."""
        if self.encode(raw) != mod_defaults(self.normalize(written), {}):
            raise CodecError(f"{self.rtype.name}: row {written!r} can't end a program")


class CommandCodec:
    """A playroutine's command codes as shared names.

    A ``mod`` variant writes its command as a ``mod`` record whose raw fields are the
    variant's ``mod`` constants and the argument: ``arg`` for a plain one, or for one that
    indexes record type ``from``, ``arg`` 1 and the indexed row's fields (``arg`` 0 for none).
    """

    def __init__(self, spec: dict, rtypes: dict):
        self.entries, self.mods = [], {}
        for code, variants in (spec or {}).items():
            for var in variants if isinstance(variants, list) else [variants]:
                name = str(var["name"])
                if name not in COMMAND_NAMES:
                    raise CodecError(f"{name!r} is not a shared command name")
                if "mod" in var:
                    self._add_mod(int(code), var, rtypes)
                    continue
                ref = var.get("ref")
                if ref is not None and ref not in rtypes:
                    raise CodecError(f"command {name} references unknown type {ref!r}")
                self.entries.append((int(code), name, ref, var))
        self.rtypes = rtypes

    def _add_mod(self, code, var, rtypes):
        src = var.get("from")
        if var["name"] != MOD or MOD not in rtypes or code in self.mods:
            raise CodecError(f"command {code}: a mod variant is the one {MOD!r} for its code")
        if src is not None and (src not in rtypes or rtypes[src].none is None):
            raise CodecError(f"command {code}: mod argument type {src!r} needs a none index")
        self.mods[code] = ({str(k): int(v) for k, v in var["mod"].items()}, src)

    def is_mod(self, code: int) -> bool:
        """Whether command ``code`` is written as a ``mod`` record."""
        return code in self.mods

    def mod_row(self, code: int, arg: int, row_of) -> dict:
        """The raw ``mod`` row for command ``code`` with ``arg``; ``row_of(type, index)``."""
        fields, src = self.mods[code]
        row = dict(fields)
        if src is None:
            row["arg"] = arg
            return row
        rtype = self.rtypes[src]
        row["arg"] = int(arg != rtype.none)
        if arg != rtype.none:
            source = row_of(src, arg)
            row.update(
                (f, int(source.get(f, d))) for f, d in rtype.fields.items() if f != rtype.chain
            )
        return row

    def mod_command(self, row: dict) -> tuple[int, str | None]:
        """``(code, from type)`` of the command a raw ``mod`` row is."""
        for code, (fields, src) in self.mods.items():
            if all(row.get(k) == v for k, v in fields.items()):
                return code, src
        raise CodecError(f"no command writes mod row {row}")

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
