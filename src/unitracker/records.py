"""Record types: the shared keys, value kinds, rules and templates a playroutine declares."""

META_KEYS = {
    "keys",
    "prefix",
    "chain",
    "none",
    "first",
    "max",
    "pin",
    "fixed",
    "rules",
    "defaults",
}
FORMS = ("set", "spec", "off", "live")
SCALAR_KINDS = ("int", "note", "abs", "flag")


class RecordTypeError(ValueError):
    """A record type declaration is malformed."""


class Kind:
    """What one key holds: a scalar, a reference, or a lane value in some forms."""

    # pylint: disable=too-few-public-methods

    def __init__(self, text: str, owner: str):
        parts = str(text).split()
        self.scalar = self.ref = None
        self.forms = ()
        if len(parts) == 1 and parts[0] in SCALAR_KINDS:
            self.scalar = parts[0]
        elif len(parts) == 2 and parts[0] == "ref":
            self.ref = parts[1]
        elif len(parts) == 2 and parts[0] == "value":
            self.forms = tuple(parts[1].split(","))
            if not self.forms or set(self.forms) - set(FORMS):
                raise RecordTypeError(f"{owner}: value forms are {', '.join(FORMS)}")
        else:
            raise RecordTypeError(f"{owner}: bad kind {text!r}")


class RecordType:
    """One ``record_types`` entry of a playroutine (see docs/format.md).

    Keys other than :data:`META_KEYS` are templates: action lists run for a row,
    which they read as ``row.<key>``.
    """

    # pylint: disable=too-many-instance-attributes,too-few-public-methods

    def __init__(self, name: str, spec: dict):
        self.name = name
        self.keys = {str(k): Kind(v, f"{name}.{k}") for k, v in (spec.get("keys") or {}).items()}
        self.defaults = {str(k): int(v) for k, v in (spec.get("defaults") or {}).items()}
        self.prefix = str(spec.get("prefix", name[0]))
        self.chain = spec.get("chain")
        self.none = spec.get("none")
        self.first = int(spec.get("first", 0 if self.none is None else int(self.none) + 1))
        self.pin = [int(i) for i in spec.get("pin") or []]
        self.max = int(spec.get("max", 255))
        self.fixed = bool(spec.get("fixed", False))
        self.rules = list(spec.get("rules") or [])
        self.templates = {k: v for k, v in spec.items() if k not in META_KEYS}
        for key, actions in self.templates.items():
            if not isinstance(actions, list):
                raise RecordTypeError(f"{name}.{key}: a template must be an action list")
        for rule in self.rules:
            if not isinstance(rule, dict) or set(rule) != {"check", "error"}:
                raise RecordTypeError(f"{name}: a rule is {{check, error}}")

    def default(self, field: str) -> int:
        """The int a missing ``field`` reads as."""
        if field == self.chain:
            return 0 if self.none is None else int(self.none)
        return self.defaults.get(field, 0)

    def refs(self) -> dict:
        """``{key: record type}`` for the keys holding references."""
        return {k: kind.ref for k, kind in self.keys.items() if kind.ref is not None}


def record_types(playroutine: dict) -> dict:
    """``{name: RecordType}`` for a playroutine's ``record_types``."""
    return {
        name: RecordType(name, spec or {})
        for name, spec in (playroutine.get("record_types") or {}).items()
    }
