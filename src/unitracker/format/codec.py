"""Shared vocabulary: a playroutine's own row fields and command codes as the format's keys.

A record type's ``codec`` shapes map raw fields to shared keys and back; a written row is
accepted only if encoding its inverse gives the row again. A key may hold an expression,
a reference ``("x", index)`` to the format's expression records, or a reference
``(type, index)`` to a record a raw field holds (``{ref: type, field: f}``).
"""

import keyword
import re

from unitracker.format.expr import ExprError, Resolver

ROW_KEYS = (
    "wait",
    "hold",
    "ctrl",
    "gate",
    "note",
    "ad",
    "sr",
    "pw",
    "cut",
    "res",
    "route",
    "mode",
    "freq",
    "run",
    "filter",
    "vol",
)
EXPR = "x"
ENVELOPE = -2
COMMAND_NAMES = (
    "run",
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
    "mark",
    "restart",
    "legato",
)
NONE = "none"
_NOTE = re.compile(r"^(?P<abs>=)?(?P<n>[+-]?\d+)$")
_ROUTE = re.compile(r"^(?P<sign>[+-])(?: (?P<mask>\d+))?$")
_CTRL = re.compile(r"^(?P<lit>\d+)?\^(?P<mask>\d+)$")


class CodecError(ValueError):
    """A row or command can't be written in, or read from, the shared vocabulary."""


class _Env(Resolver):
    """Expressions over one mapping of names (a missing name reads as the playroutine's
    constant of that name, else 0) and the playroutine's tables and functions."""

    def __init__(self, functions=None, tables=()):
        super().__init__(functions)
        self.tables = set(tables)

    def name(self, name):
        return f"E.get({name!r}, C.get({name!r}, 0))"

    def table(self, name, index):
        if name not in self.tables:
            raise ExprError(f"unknown table {name!r}")
        return f"_tg(T[{name!r}], {index})"


def _tg(table, index):
    return table[index] if 0 <= index < len(table) else 0


class Evaluator:
    """Evaluates codec expressions with a playroutine's functions and constants and a song's
    tables."""

    # pylint: disable=too-few-public-methods

    def __init__(self, functions=None, tables=None, constants=None):
        self.tables = {k: [int(x) for x in v] for k, v in (tables or {}).items()}
        self.constants = {k: int(v) for k, v in (constants or {}).items()}
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
        glob = {"E": env, "C": self.constants, "T": self.tables, "_tg": _tg}
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


def route_value(value) -> tuple[int, int]:
    """``(relation, mask)`` for a written ``route``: an int is the mask (relation 0); ``"+ m"``
    adds the program's voice and mask ``m`` (relation 1), ``"- m"`` removes them (relation -1),
    and ``m`` may be left out when it is 0."""
    if isinstance(value, bool):
        raise CodecError(f"bad route {value!r}")
    if isinstance(value, int):
        return 0, value
    match = _ROUTE.match(str(value))
    if match is None:
        raise CodecError(f"bad route {value!r}")
    return (1 if match["sign"] == "+" else -1), int(match["mask"] or 0)


def route_text(relation: int, mask: int):
    """The written form of a route value."""
    if not relation:
        return mask
    return ("+" if relation > 0 else "-") + (f" {mask}" if mask else "")


def ctrl_value(value):
    """A written ``ctrl``: an int, or ``(literal or None, mask)`` for ``^mask`` or ``n^mask``."""
    if isinstance(value, (int, tuple)) and not isinstance(value, bool):
        return value
    match = _CTRL.match(str(value))
    if match is None:
        raise CodecError(f"bad ctrl {value!r}")
    return int(match["lit"]) if match["lit"] else None, int(match["mask"])


def ctrl_text(value):
    """The written form of a ``ctrl``: an int, or the text of one with an exclusive-or mask."""
    if not isinstance(value, tuple):
        return value
    literal, mask = value
    return f"{'' if literal is None else literal}^{mask}"


_FORMS = {
    "note": (note_value, lambda v: note_text(*v)),
    "route": (route_value, lambda v: route_text(*v)),
    "ctrl": (ctrl_value, ctrl_text),
}


def row_place(k: int, count: int, ends: bool) -> dict:
    """The position of row ``k`` of a program of ``count`` rows that ends or loops."""
    return {"first": int(k == 0), "last": int(ends and k == count - 1)}


def written_value(key: str, value):
    """A shared key's value as written: notes, routes and masked controls in their text forms."""
    return _FORMS[key][1](value) if key in _FORMS else value


class _Shape:
    """One way to write a row: forward keys, inverse raw fields, fields kept as they are.

    A ``route`` key's ``rel`` gives its relation to the program's voice (see
    :func:`route_value`). A raw field given as ``{search: [lo, hi]}`` is the first value in
    that range whose row writes back as the written one. A key's ``expr`` names the raw field
    of its expression, a key with ``ref`` is the reference to that record type its raw
    ``field`` holds, and a key with ``none`` is written ``none``.
    """

    # pylint: disable=too-few-public-methods

    def __init__(self, spec: dict):
        self.when = spec.get("when")
        self.keys = {}
        for key, kspec in (spec.get("keys") or {}).items():
            if key not in ROW_KEYS:
                raise CodecError(f"{key!r} is not a shared row key")
            self.keys[key] = kspec if isinstance(kspec, dict) else {"value": kspec}
            if ("ref" in self.keys[key]) != ("field" in self.keys[key]):
                raise CodecError(f"{key}: a reference key has both ref and field")
        self.refs = {k for k, s in self.keys.items() if "ref" in s}
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


def is_values(value) -> bool:
    """True for a command argument of several values, ``(a, b)`` for ``!funk:a/b``."""
    return isinstance(value, tuple) and all(
        isinstance(v, int) and not isinstance(v, bool) for v in value
    )


def arg_text(value) -> str:
    """A command argument as written: several values joined by ``/``."""
    return "/".join(map(str, value)) if is_values(value) else str(value)


def is_expr(value) -> bool:
    """True for a written key's expression reference ``("x", index)``."""
    return isinstance(value, tuple) and len(value) == 2 and value[0] == EXPR


class RowCodec:
    """A record type's shapes, with the playroutine's note offset."""

    def __init__(self, rtype, spec: list, offset: int, evaluator=None):
        self.rtype = rtype
        self.shapes = [_Shape(s) for s in spec]
        self.keys = frozenset(k for s in self.shapes for k in (*s.keys, *s.keep))
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

    def ref_keys(self) -> dict:
        """``{key: (raw field, {record types})}`` of the keys that hold a record reference."""
        out = {}
        for shape in self.shapes:
            for key, kspec in shape.keys.items():
                if "ref" in kspec:
                    out.setdefault(key, (kspec["field"], set()))[1].add(kspec["ref"])
        return out

    def _full(self, row: dict) -> dict:
        return {f: int(row.get(f, d)) for f, d in self.rtype.fields.items()}

    def _part(self, kspec: dict, row: dict):
        """A key's value from the raw fields, or None when it is left out."""
        if "value" not in kspec or ("omit" in kspec and self.ev(kspec["omit"], row)):
            return None
        return self.ev(kspec["value"], row)

    def _encode(self, shape, row) -> dict:
        out = {}
        for key in ROW_KEYS:
            kspec = shape.keys.get(key)
            if kspec is None:
                continue
            if "ref" in kspec:
                out[key] = (kspec["ref"], row[kspec["field"]])
                continue
            if kspec.get("none"):
                out[key] = NONE
                continue
            field = kspec.get("expr")
            if field is not None and row[field] != self.rtype.default(field):
                out[key] = (EXPR, row[field])
                continue
            value = self._part(kspec, row)
            mask = self._part(kspec["eor"], row) if "eor" in kspec else None
            if mask is not None:
                out[key] = (value, mask)
                continue
            if value is None:
                continue
            if key == "note":
                absolute = "abs" in kspec and bool(self.ev(kspec["abs"], row))
                out[key] = (absolute, value - self.offset if absolute else value)
            elif key == "route":
                out[key] = (int(self.ev(kspec["rel"], row)) if "rel" in kspec else 0, value)
            else:
                out[key] = value
        for field in shape.keep:
            if row[field] != self.rtype.default(field):
                out[field] = row[field]
        return out

    def encode(self, row: dict, place: dict | None = None) -> dict | None:
        """Shared keys for a raw row, notes as ``(absolute, number)``; None if no shape fits.

        ``place`` gives a program row's position to the shapes' ``when``: ``first`` and
        ``last`` (the row before ``end``) are 1 or 0.
        """
        full = self._full(row)
        for shape in self.shapes:
            if self._fits(shape, full, place):
                return self._encode(shape, full)
        return None

    def _fits(self, shape, full: dict, place) -> bool:
        return shape.when is None or bool(self.ev(shape.when, {**full, **(place or {})}))

    def _env(self, shape, norm) -> dict:
        env = {}
        for key in shape.keys:
            plain = key in norm and not is_expr(norm[key]) and norm[key] != NONE
            env[f"has_{key}"] = int(key in norm and not is_expr(norm[key]))
            if key == "note" and plain:
                absolute, number = norm[key]
                env["note_abs"] = int(absolute)
                env["note"] = number + self.offset if absolute else number
            elif key == "route" and plain:
                env["route_rel"], env["route"] = norm[key]
            elif key == "ctrl" and plain and isinstance(norm[key], tuple):
                literal, env["eor"] = norm[key]
                env["has_eor"], env["has_ctrl"] = 1, int(literal is not None)
                env.update({"ctrl": literal} if literal is not None else {})
            elif plain:
                env[env_name(key)] = norm[key]
        return env

    @staticmethod
    def _unfit(shape, norm) -> str | None:
        """Why a written row's keys can't take ``shape``, or None."""
        extra = set(norm) - set(shape.keys) - set(shape.keep)
        if extra:
            return f"keys {sorted(extra)}"
        for key, value in norm.items():
            kspec = shape.keys.get(key, {})
            if bool(kspec.get("none")) != (value == NONE):
                return (
                    f"{key} is only {NONE} here"
                    if kspec.get("none")
                    else f"{key} can't be {NONE} here"
                )
            if "ref" in kspec:
                if not (isinstance(value, tuple) and value[0] == kspec["ref"]):
                    return f"{key} names no {kspec['ref']} here"
            elif is_expr(value) and "expr" not in kspec:
                return f"{key} holds no expression here"
            elif key == "ctrl" and isinstance(value, tuple) and "eor" not in kspec:
                return "ctrl holds no mask here"
        return None

    def _try(self, shape, norm, place) -> tuple[dict | None, str]:
        unfit = self._unfit(shape, norm)
        if unfit is not None:
            return None, unfit
        env = self._env(shape, {k: v for k, v in norm.items() if k not in shape.refs})
        raw = {f: int(norm[f]) if f in norm else self.rtype.default(f) for f in shape.keep}
        raw.update({f: self.ev(expr, env) for f, expr in shape.raw.items()})
        raw.update({shape.keys[k]["expr"]: v[1] for k, v in norm.items() if is_expr(v)})
        raw.update({shape.keys[k]["field"]: v[1] for k, v in norm.items() if k in shape.refs})
        field, values = shape.search or (None, [None])
        problem = "no shape"
        for value in values:
            if field is not None:
                raw[field] = value
            full = self._full(raw)
            if not self._fits(shape, full, place):
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
        """A written row with notes as ``(absolute, number)``, routes as ``(relation, mask)``
        and masked controls as ``(literal, mask)``."""
        return {k: _FORMS[k][0](v) if k in _FORMS else v for k, v in written.items()}

    def decode(self, written: dict, place: dict | None = None) -> dict:
        """Raw fields for a written row at ``place`` (see :meth:`encode`); raises CodecError
        naming what the player can't express."""
        for key in sorted(set(written) - self.keys):
            raise CodecError(f"unknown key {key} in {self.rtype.written}")
        norm = self.normalize(written)
        problems = []
        for shape in self.shapes:
            raw, problem = self._try(shape, norm, place)
            if raw is not None:
                return raw
            problems.append(problem)
        raise CodecError(
            f"{self.rtype.written}: the player can't express row {written!r}"
            f" ({'; '.join(problems)})"
        )

    def check_end(self, raw: dict, written: dict, place: dict | None = None) -> None:
        """Raise unless ``raw`` (a program's last row after ``end``) still writes as ``written``.

        An expression key compares by presence: ``raw`` holds its reference, ``written`` its text.
        """
        enc, norm = self.encode(raw, place), self.normalize(written)
        exprs = {k for k, v in (enc or {}).items() if is_expr(v)}
        if (
            enc is None
            or not exprs <= set(norm)
            or {k: v for k, v in enc.items() if k not in exprs}
            != {k: v for k, v in norm.items() if k not in exprs}
        ):
            raise CodecError(f"{self.rtype.written}: row {written!r} can't end a program")


def command_variants(spec: dict):
    """``(code, variant)`` of each variant of a playroutine's ``commands`` map."""
    for code, vs in (spec or {}).items():
        yield from ((int(code), v) for v in (vs if isinstance(vs, list) else [vs]))


def envelope_names(spec: dict) -> list:
    """The names of a playroutine's envelope commands: the codes with ``nibble``, whose byte
    depends on the voice (see ``envelope``); internal code ``ENVELOPE - k`` writes the k-th."""
    return sorted({str(v["name"]) for _, v in command_variants(spec) if "nibble" in v})


class CommandCodec:
    """A playroutine's command codes as shared names; an envelope command's internal code (see
    :func:`envelope_names`) as its name and byte. A code with no ``name`` has no written form of
    its own: a document writes it through another record (see ``format.events.instrument``)."""

    def __init__(self, spec: dict, rtypes: dict):
        self.entries, self.unnamed = [], {}
        for code, var in command_variants(spec):
            name = str(var.get("name", f"command {code}"))
            ref = var.get("ref")
            if ref is not None and ref not in rtypes:
                raise CodecError(f"{name} references unknown type {ref!r}")
            if "name" not in var:
                self.unnamed[code] = ref
                continue
            if name not in COMMAND_NAMES:
                raise CodecError(f"{name!r} is not a shared command name")
            if "nibble" not in var:
                self.entries.append((code, name, ref, var))
        self.entries += [(ENVELOPE - k, n, None, {}) for k, n in enumerate(envelope_names(spec))]
        self.rtypes = rtypes

    def ref(self, code: int) -> str | None:
        """The record type command ``code``'s argument references, if any."""
        for ecode, _, ref, _ in self.entries:
            if ecode == code and ref is not None:
                return ref
        return self.unnamed.get(code)

    def inline(self, key: int | str) -> str | None:
        """The record type whose row a command (a code or a name) writes as its argument."""
        for code, name, ref, var in self.entries:
            if key in (code, name) and ref is not None and var.get("inline"):
                return ref
        return None

    def values(self, name: str) -> bool:
        """Whether command ``name`` takes several values that are no record's row."""
        return any(n == name and "args" in var for _, n, _, var in self.entries)

    def encode(self, code: int, arg: int) -> tuple[str, object]:
        """``(name, value)``: value is an int, a tuple of ints for an entry with ``args``,
        ``(type, index)`` for a reference, or ``none``."""
        for ecode, name, ref, var in self.entries:
            if ecode != code or ("when" in var and not evaluate(var["when"], {"arg": arg})):
                continue
            if "args" in var:
                return name, tuple(evaluate(e, {"arg": arg}) for e in var["args"].values())
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
        if "args" in var:
            fits = is_values(value) and len(value) == len(var["args"])
            return evaluate(var["raw"], dict(zip(var["args"], value))) if fits else None
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
        if name not in COMMAND_NAMES:
            raise CodecError(f"unknown command !{name}")
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
        raise CodecError(f"the player can't express !{name}:{arg_text(value)}")
