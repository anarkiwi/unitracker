"""Shared vocabulary: a playroutine's own row fields and command codes as the format's keys.

A record type's ``codec`` shapes map raw fields to shared keys and back; a written row is
accepted only if encoding its inverse gives the row again. A key may hold an expression,
a reference ``("x", index)`` to the format's expression records, or a reference
``(type, index)`` to a record a raw field holds (``{ref: type, field: f}``).
"""

import keyword
import re

from unitracker.format.atoms import AtomError, note_index, note_name
from unitracker.format.expr import ExprError, Resolver
from unitracker.format.records import ABSENT

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
RESTART = "restart"
ENVELOPE = -2
COMMAND_NAMES = (
    "run",
    "pw",
    "filter",
    "ad",
    "sr",
    "ctrl",
    "resfilt",
    "vol",
    "cut",
    "tempo",
    "vtempo",
    "freq",
    "legato",
)
ARGLESS = ("legato",)
BYTE_SHIFT = {"cut": 3}
KEY_DEFAULTS = {"hold": 1}
NONE = "none"
_RELATIVE = re.compile(r"^[+-]\d+$")
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
    """``(absolute, number)`` for a written ``note``: an int or ``+n``/``-n`` is relative, a note
    name absolute."""
    if isinstance(value, bool):
        raise CodecError(f"bad note {value!r}")
    if isinstance(value, int):
        return False, int(value)
    if _RELATIVE.match(str(value)):
        return False, int(value)
    try:
        return True, note_index(str(value))
    except AtomError as exc:
        raise CodecError(f"bad note {value!r}") from exc


def note_text(absolute: bool, number: int) -> str:
    """The written form of a note value: a note name, or ``+n``/``-n``."""
    return note_name(number) if absolute else f"{number:+d}"


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


_ANYWHERE = {"first": 1, "last": 0}


def row_place(k: int, count: int, ends: bool) -> dict:
    """The position of row ``k`` of a program of ``count`` rows that ends or loops."""
    return {"first": int(k == 0), "last": int(ends and k == count - 1)}


def written_value(key: str, value):
    """A shared key's value as written: notes, routes and masked controls in their text forms."""
    return _FORMS[key][1](value) if key in _FORMS else value


def byte_form(name: str, spec: dict, field: str) -> dict:
    """``spec`` with its ``byte`` form, the top byte of shared key or command ``name``, as the
    ``field`` expression of the value it gives (``byte << 3`` for ``cut``)."""
    if "byte" not in spec:
        return spec
    if name not in BYTE_SHIFT:
        raise CodecError(f"{name} has no byte form")
    if field in spec:
        raise CodecError(f"{name}: a byte form has no {field}")
    return {**spec, field: f"({spec['byte']}) << {BYTE_SHIFT[name]}"}


def note_byte(spec: dict) -> tuple[dict, dict]:
    """``(spec, raw)`` of a ``note`` key given as ``{byte: f, none: n, abs: [lo, hi]}``: raw
    field ``f`` is the note byte, ``n`` no note, ``lo``-``hi`` (default $80-$FF) an absolute note
    in its low 7 bits, and any other byte a relative one, two's complement in 7 bits when the
    absolute range reaches $FF, else in 8; ``raw`` is ``f`` from the written note."""
    if "byte" not in spec:
        return spec, {}
    field, none = spec["byte"], spec.get(NONE)
    lo, hi = spec.get("abs", (0x80, 0xFF))
    if none is None or set(spec) - {"byte", NONE, "abs"}:
        raise CodecError("a note byte is {byte, none, abs}")
    mask = 0x7F if hi == 0xFF else 0xFF
    absolute = f"{field} >= {lo} and {field} <= {hi} and {field} != {none}"
    sign = (mask + 1) >> 1
    return {
        "value": f"({field} & 0x7F) if {absolute} else (({field} & {mask}) ^ {sign}) - {sign}",
        "abs": absolute,
        "omit": f"{field} == {none}",
    }, {field: f"((note & 0x7F) | 0x80) if note_abs else ((note & {mask}) if has_note else {none})"}


def key_default(key: str, kspec: dict) -> dict:
    """``kspec`` with the ``omit`` a key with a vocabulary default gets when it gives none: left
    out when its value is the default."""
    if key not in KEY_DEFAULTS or "omit" in kspec or "value" not in kspec:
        return kspec
    return {**kspec, "omit": f"({kspec['value']}) == {KEY_DEFAULTS[key]}"}


def optional_codec(optional) -> tuple[dict, dict]:
    """``(keys, raw)`` a shape gets for its record type's optional fields that are shared keys:
    the key is the field, left out when it is :data:`ABSENT`, and reads back as it."""
    fields = [f for f in optional if f in ROW_KEYS]
    keys = {f: {"value": f, "omit": f"{f} < 0"} for f in fields}
    return keys, {f: f"{f} if has_{f} else {ABSENT}" for f in fields}


class _Shape:
    """One way to write a row: forward keys and inverse raw fields.

    A ``route`` key's ``rel`` gives its relation to the program's voice (see
    :func:`route_value`). A key's ``expr`` names the raw field of its expression, a key with
    ``ref`` is the reference to that record type its raw ``field`` holds, and a key with
    ``none`` is written ``none``. The record type's ``optional`` fields give the keys and raw
    fields of :func:`optional_codec`; a key the shape gives replaces both, a raw field the raw. A
    key of :data:`KEY_DEFAULTS` is left out at its default unless it gives ``omit`` (see
    :func:`key_default`), and reads as the default in ``raw`` when it is left out. A ``note``
    key's ``byte`` form gives its key and raw field (see :func:`note_byte`).
    """

    # pylint: disable=too-few-public-methods

    def __init__(self, spec: dict, optional=()):
        for key in sorted(set(spec) - {"when", "keys", "raw"}):
            raise CodecError(f"unknown key {key} in a codec shape")
        self.when = spec.get("when")
        own = spec.get("keys") or {}
        gen_keys, gen_raw = optional_codec(f for f in optional if f not in own)
        self.keys = {}
        for key, kspec in {**gen_keys, **own}.items():
            if key not in ROW_KEYS:
                raise CodecError(f"{key!r} is not a shared row key")
            kspec = kspec if isinstance(kspec, dict) else {"value": kspec}
            if key == "note":
                kspec, raw = note_byte(kspec)
                gen_raw.update(raw)
            self.keys[key] = key_default(key, byte_form(key, kspec, "value"))
            if ("ref" in self.keys[key]) != ("field" in self.keys[key]):
                raise CodecError(f"{key}: a reference key has both ref and field")
        self.refs = {k for k, s in self.keys.items() if "ref" in s}
        self.raw = {**gen_raw, **(spec.get("raw") or {})}
        bad = sorted(f for f, expr in self.raw.items() if isinstance(expr, dict))
        if bad:
            raise CodecError(f"raw {bad}: a raw field is an expression")


def is_values(value) -> bool:
    """True for a command argument of several values, ``(a, b)`` for ``!tempo:a/b``."""
    return isinstance(value, tuple) and all(
        isinstance(v, int) and not isinstance(v, bool) for v in value
    )


def arg_text(value) -> str:
    """A command argument as written: several values joined by ``/``."""
    return "/".join(map(str, value)) if is_values(value) else str(value)


def command_text(name: str, value) -> str:
    """A command as written: ``!name``, or ``!name:arg``."""
    return f"!{name}" if value is None else f"!{name}:{arg_text(value)}"


def is_expr(value) -> bool:
    """True for a written key's expression reference ``("x", index)``."""
    return isinstance(value, tuple) and len(value) == 2 and value[0] == EXPR


def first_tick(rtype, place) -> bool:
    """Whether a row of ``rtype`` at ``place`` is a restart's first-tick row (its last), where a
    ``ctrl`` sets the gate on."""
    return rtype.name == RESTART and bool(place and place.get("last"))


class RowCodec:
    """A record type's shapes, with the playroutine's note offset.

    On a restart's first-tick row a ``ctrl`` implies ``gate: 1``: the row is written without it,
    read with it, and refused with a ``gate`` (see :func:`first_tick`).
    """

    def __init__(self, rtype, spec: list, offset: int, evaluator=None):
        self.rtype = rtype
        self.shapes = [_Shape(s, rtype.optional) for s in spec]
        self.keys = frozenset(k for s in self.shapes for k in s.keys)
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
        return out

    def encode(self, row: dict, place: dict | None = None) -> dict | None:
        """Shared keys for a raw row, notes as ``(absolute, number)``; None if no shape fits.

        ``place`` gives a program row's position to the shapes' ``when``: ``first`` and
        ``last`` (the row before ``end``) are 1 or 0; a row of no known position is a first row.
        """
        full = self._full(row)
        for shape in self.shapes:
            if self._fits(shape, full, place):
                out = self._encode(shape, full)
                if first_tick(self.rtype, place) and "ctrl" in out and out.get("gate") == 1:
                    del out["gate"]
                return out
        return None

    def _fits(self, shape, full: dict, place) -> bool:
        place = _ANYWHERE if place is None else place
        return shape.when is None or bool(self.ev(shape.when, {**full, **place}))

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
                shift = BYTE_SHIFT[key] if "byte" in shape.keys[key] else 0
                env[env_name(key)] = norm[key] >> shift
            elif key not in norm and key in KEY_DEFAULTS:
                env[env_name(key)] = KEY_DEFAULTS[key]
        return env

    @staticmethod
    def _unfit(shape, norm) -> str | None:
        """Why a written row's keys can't take ``shape``, or None."""
        extra = set(norm) - set(shape.keys)
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
        exprs = {shape.keys[k]["expr"]: v[1] for k, v in norm.items() if is_expr(v)}
        env = {**exprs, **self._env(shape, {k: v for k, v in norm.items() if k not in shape.refs})}
        raw = {f: self.ev(expr, env) for f, expr in shape.raw.items()}
        raw.update(exprs)
        raw.update({shape.keys[k]["field"]: v[1] for k, v in norm.items() if k in shape.refs})
        full = self._full(raw)
        if not self._fits(shape, full, place):
            return None, "no shape"
        try:
            back = self._encode(shape, full)
        except CodecError as exc:
            return None, str(exc)
        if back == norm:
            return raw, ""
        bad = sorted(k for k in set(back) | set(norm) if back.get(k) != norm.get(k))
        return None, f"values of {bad}"

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
        if first_tick(self.rtype, place) and "ctrl" in written:
            if "gate" in written:
                raise CodecError(
                    f"{self.rtype.written}: the player can't express row {written!r}"
                    " (a ctrl on the note's first tick sets the gate on; the row has no gate)"
                )
            written = {**written, "gate": 1}
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


def plain_constants(codecs: dict, rtypes: dict, records: dict, exprs: dict) -> None:
    """Rewrite in ``records`` each row whose key holds a constant expression (of ``exprs``) where
    the key also takes a plain value, as the row of that value when it reads back."""
    for name, codec in codecs.items():
        number = {k for k, plain in codec.expr_keys().items() if plain}
        chain = rtypes[name].chain
        for index, row in list(records.get(name, {}).items()):
            shared = codec.encode(row) or {}
            consts = {
                k: int(exprs[v[1]])
                for k, v in shared.items()
                if k in number and is_expr(v) and str(exprs.get(v[1], "")).lstrip("-").isdigit()
            }
            if not consts:
                continue
            try:
                raw = codec.decode(
                    {k: written_value(k, v) for k, v in {**shared, **consts}.items()}
                )
            except CodecError:
                continue
            records[name][index] = {**raw, **({chain: row[chain]} if chain in row else {})}


def command_variants(spec: dict):
    """``(code, variant)`` of each variant of a playroutine's ``commands`` map."""
    for code, vs in (spec or {}).items():
        yield from ((int(code), v) for v in (vs if isinstance(vs, list) else [vs]))


def command_code(spec: dict, name: str, ref: str | None = None) -> int | None:
    """The first code of a playroutine's ``commands`` map named ``name``, and referencing ``ref``
    when given; None if there is none."""
    return next(
        (
            c
            for c, v in command_variants(spec)
            if v.get("name") == name and (ref is None or v.get("ref") == ref)
        ),
        None,
    )


def envelope_names(spec: dict) -> list:
    """The names of a playroutine's envelope commands: the codes with ``nibble``, whose byte
    depends on the voice (see ``envelope``); internal code ``ENVELOPE - k`` writes the k-th."""
    return sorted({str(v["name"]) for _, v in command_variants(spec) if "nibble" in v})


def inline_text(rtype, row: dict) -> str:
    """The written argument of an inline reference to ``row`` of ``rtype``: its fields in
    declaration order joined by ``/``."""
    return "/".join(str(int(row.get(f, rtype.default(f)))) for f in rtype.own)


def inline_row(rtype, values) -> dict:
    """The row of ``rtype`` an inline argument's values write; raises CodecError naming what is
    wrong."""
    values = values if isinstance(values, tuple) else (values,)
    if not is_values(values) or len(values) != len(rtype.own):
        raise CodecError(f"the argument is {len(rtype.own)} values, {'/'.join(rtype.own)}")
    if not all(0 <= v <= 0xFF for v in values):
        raise CodecError("each value is a byte (0-255)")
    return dict(zip(rtype.own, values))


class CommandCodec:
    """A playroutine's command codes as shared names; an envelope command's internal code (see
    :func:`envelope_names`) as its name and byte. A code with no ``name`` has no written form of
    its own: a document writes it through another record (see ``format.events.instrument``). An
    entry with ``inline`` writes the row its reference names in place (see :func:`inline_text`).
    """

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
                self.entries.append((code, name, ref, byte_form(name, var, "arg")))
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

    def check_inline(self, name: str, row: dict) -> None:
        """Raise unless the inline command ``name``'s ``when`` holds for the row it writes."""
        for _, ename, ref, var in self.entries:
            if ename == name and ref is not None and var.get("inline"):
                if "when" in var and not evaluate(var["when"], row):
                    text = "/".join(str(v) for v in row.values())
                    raise CodecError(f"the player can't express !{name}:{text}")
                return

    def also(self, code: int, arg: int) -> str | None:
        """The command (``name:arg``) an event writes beside command ``code`` with ``arg``, whose
        native effect it also has; None for none."""
        for ecode, _, _, var in self.entries:
            if ecode == code and "also" in var and evaluate(var.get("when", 1), {"arg": arg}):
                return str(var["also"])
        return None

    def values(self, name: str) -> bool:
        """Whether command ``name`` takes several values that are no record's row."""
        return any(n == name and "args" in var for _, n, _, var in self.entries)

    def encode(self, code: int, arg: int) -> tuple[str, object]:
        """``(name, value)``: value is an int, a tuple of ints for an entry with ``args``,
        ``(type, index)`` for a reference, ``none``, or None for a command of :data:`ARGLESS`."""
        for ecode, name, ref, var in self.entries:
            if ecode != code or (
                "when" in var and not var.get("inline") and not evaluate(var["when"], {"arg": arg})
            ):
                continue
            if name in ARGLESS:
                return name, None
            if "args" in var:
                return name, tuple(evaluate(e, {"arg": arg}) for e in var["args"].values())
            if ref is None:
                return name, evaluate(var["arg"], {"arg": arg}) if "arg" in var else arg
            if arg != self.rtypes[ref].none:
                return name, (ref, arg)
            if var.get("inline"):
                break
            return self.spelled(name, ref, NONE), NONE
        raise CodecError(f"command {code} with argument {arg} has no shared name")

    def spelled(self, name: str, ref: str | None, value) -> str:
        """The name command ``name`` of reference ``ref`` is written with for ``value``: ``none``
        of a program stops its slot, ``!run:none`` the control program's and ``!pw:none`` and
        ``!filter:none`` the others (the type's written name)."""
        if value != NONE or ref is None or self.rtypes[ref].chain is None:
            return name
        written = self.rtypes[ref].written
        return name if written == "ctrl" else written

    def _arg(self, name, ref, var, value):
        """The raw argument entry ``var`` (of command ``name``, reference ``ref``) reads a written
        value as, or None when it takes no such value."""
        if (name in ARGLESS) != (value is None):
            return None
        if value is None or value == NONE:
            none = None if ref is None else self.rtypes[ref].none
            return evaluate(var.get("raw", 0), {}) if value is None else none
        if "args" in var:
            fits = is_values(value) and len(value) == len(var["args"])
            return evaluate(var["raw"], dict(zip(var["args"], value))) if fits else None
        if ref is not None:
            return value[1] if isinstance(value, tuple) and value[0] == ref else None
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        arg = value >> BYTE_SHIFT[name] if "byte" in var else value
        return evaluate(var["raw"], {"arg": arg}) if "raw" in var else arg

    def decode(self, name: str, value) -> tuple[int, int]:
        """``(code, arg)`` for a written command; ``value`` as :meth:`encode` returns it."""
        if name not in COMMAND_NAMES:
            raise CodecError(f"unknown command !{name}")
        for code, ename, ref, var in self.entries:
            spelled = self.spelled(ename, ref, value)
            arg = self._arg(ename, ref, var, value) if spelled == name else None
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
        raise CodecError(f"the player can't express {command_text(name, value)}")
