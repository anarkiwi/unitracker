"""Record types: the field layout, references and templates a playroutine declares."""

META_KEYS = {
    "fields",
    "prefix",
    "chain",
    "none",
    "stop",
    "first",
    "max",
    "pin",
    "fixed",
    "form",
    "refs",
    "codec",
    "end",
    "expr",
}
EXPR = "x"


class RecordTypeError(ValueError):
    """A record type declaration is malformed."""


class RecordType:
    """One ``record_types`` entry of a playroutine (see docs/format.md).

    Keys other than :data:`META_KEYS` are templates: action lists run for a row,
    which they read as ``row.<field>``.
    """

    # pylint: disable=too-many-instance-attributes,too-few-public-methods

    def __init__(self, name: str, spec: dict):
        self.name = name
        fields = spec.get("fields") or {}
        if isinstance(fields, list):
            fields = {f: 0 for f in fields}
        self.fields = {str(k): int(v) for k, v in fields.items()}
        self.prefix = str(spec.get("prefix", name[0]))
        self.chain = spec.get("chain")
        self.none = spec.get("none")
        self.stop = spec.get("stop")
        self.first = int(spec.get("first", 0 if self.none is None else int(self.none) + 1))
        self.pin = [int(i) for i in spec.get("pin") or []]
        self.max = int(spec.get("max", 255))
        self.fixed = bool(spec.get("fixed", False))
        self.form = str(spec.get("form", "list"))
        if self.form not in ("list", "map"):
            raise RecordTypeError(f"{name}: form must be list or map")
        self.refs = {}
        for field, refs in (spec.get("refs") or {}).items():
            out = []
            for ref in refs if isinstance(refs, list) else [refs]:
                if isinstance(ref, str):
                    ref = {"type": ref}
                out.append((str(ref["type"]), ref.get("when")))
            self.refs[str(field)] = out
        self.codec = list(spec.get("codec") or [])
        self.end = dict(spec.get("end") or {})
        self.expr = spec.get("expr")
        self.templates = {k: v for k, v in spec.items() if k not in META_KEYS}
        for key, actions in self.templates.items():
            if not isinstance(actions, list):
                raise RecordTypeError(f"{name}.{key}: a template must be an action list")
        if self.chain is not None and self.chain not in self.fields:
            raise RecordTypeError(f"{name}: chain field {self.chain!r} is not a field")
        if self.expr is not None and self.refs.get(self.expr) != [(EXPR, None)]:
            raise RecordTypeError(f"{name}: expr field {self.expr!r} must reference {EXPR}")

    def default(self, field: str) -> int:
        """The value a missing ``field`` reads as."""
        return self.fields.get(field, 0)


def record_types(playroutine: dict) -> dict:
    """``{name: RecordType}`` for a playroutine's ``record_types``, and the format's expressions.

    The expression type ``x`` holds modulator expressions (see docs/format.md).
    """
    specs = playroutine.get("record_types") or {}
    if EXPR in specs:
        raise RecordTypeError(f"{EXPR!r} is the format's expression type")
    out = {name: RecordType(name, spec or {}) for name, spec in specs.items()}
    out[EXPR] = RecordType(EXPR, {"prefix": EXPR, "none": 0, "max": 0xFFFF})
    return out
