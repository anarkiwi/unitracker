"""Read a unitracker v2 document into the internal representation the player runs."""

import copy
import re

from unitracker import spec as specs
from unitracker import timeline
from unitracker.atoms import AtomError, parse_atom
from unitracker.docbase import SongFormatError, View, check_rules, library
from unitracker.expr import parse as parse_expr
from unitracker.vm import IR_VERSION, OFF, phrase_steps

PHRASE = "phrase"
SPEC = "spec"
_ENTRY = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<t>[+-]\d+)?$")
_ONE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ABS = re.compile(r"^=(-?\d+)$")


class _Reader:
    """Expands a v2 document to the internal representation."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, doc: dict):
        self.doc = doc
        self.pr = self._playroutine(doc.get("playroutine"))
        view = View(self.pr)
        self.rtypes, self.commands, self.offset = view.rtypes, view.commands, view.offset
        self.ins_type, self.ins_base = view.ins_type, view.ins_base
        self.lane_params = {k: set(v) for k, v in (self.pr.get("specs") or {}).items()}
        self.spec_rules = list(self.pr.get("spec_rules") or [])
        self.player = self.pr.get("name", "the playroutine")
        self.next_index = {name: rt.first for name, rt in self.rtypes.items()}
        self.names = {}
        self.spec_names = {}
        self.pending = []
        self.records = {name: {} for name in self.rtypes}

    def _playroutine(self, spec) -> dict:
        if not isinstance(spec, dict):
            raise SongFormatError("playroutine must be a mapping")
        if "ref" not in spec:
            pr = copy.deepcopy(spec)
            self.tables = {
                k: [int(x) for x in v] for k, v in (pr.pop("tables", None) or {}).items()
            }
            self.defaults = pr.get("defaults") or {}
            return pr
        pr = library(spec["ref"])
        pr["constants"] = {**(pr.get("constants") or {}), **(spec.get("constants") or {})}
        tables = {k: [int(x) for x in v] for k, v in (pr.pop("tables", None) or {}).items()}
        for name, diff in (spec.get("tables") or {}).items():
            if isinstance(diff, list):
                tables[name] = [int(x) for x in diff]
            else:
                if name not in tables:
                    raise SongFormatError(f"table {name!r} has no library default to patch")
                for i, v in diff.items():
                    tables[name][int(i)] = int(v)
        self.tables = tables
        self.defaults = pr.get("defaults") or {}
        return pr

    def _alloc(self, rtype, count) -> int:
        start = self.next_index[rtype.name]
        if start + count - 1 > rtype.max:
            raise SongFormatError(f"{rtype.name}: more than {rtype.max} rows")
        self.next_index[rtype.name] = start + count
        return start

    # -- values --

    def spec(self, value, lane: str, where: str) -> dict:
        """A spec for ``lane``: an inline mapping or an ``@`` name; checked against the player."""
        if isinstance(value, str) and value.startswith("@"):
            if value[1:] not in self.spec_names:
                raise SongFormatError(f"{where}: unknown spec {value!r}")
            parsed = self.spec_names[value[1:]]
        else:
            try:
                parsed = specs.parse(value, where)
            except specs.SpecError as exc:
                raise SongFormatError(str(exc)) from exc
        allowed = self.lane_params.get(lane)
        if allowed is None:
            raise SongFormatError(f"{where}: {self.player} runs no specs on {lane}")
        extra = set(parsed) - allowed
        if extra:
            raise SongFormatError(
                f"{where}: {self.player} cannot express {', '.join(sorted(extra))} on {lane}"
            )
        rules = [r for r in self.spec_rules if r.get("lane", lane) == lane]
        check_rules(rules, parsed, lambda name: -1, f"{where} ({self.player})")
        return parsed

    def _value(self, rtype, key, value, where):
        """The internal value of one shared row value; ``(key, value)``."""
        # pylint: disable=too-many-return-statements,too-many-branches
        kind = rtype.keys.get(key)
        if kind is None:
            raise SongFormatError(f"{where}: {self.player} has no key {key!r} here")
        if kind.ref is not None:
            if value is False or value == OFF:
                ttype = self.rtypes[kind.ref]
                return key, int(ttype.none if ttype.none is not None else 0)
            if isinstance(value, str) and value.startswith("@"):
                return key, ("name", value[1:])
            if isinstance(value, list):
                return key, self._program(self.rtypes[kind.ref], value, where)
            raise SongFormatError(f"{where}.{key}: a reference is @name or a program")
        if kind.scalar == "flag":
            if not isinstance(value, bool):
                raise SongFormatError(f"{where}.{key}: on or off")
            return key, int(value)
        if kind.scalar in ("abs", "note"):
            if isinstance(value, str) and _ABS.match(value):
                index = int(_ABS.match(value)[1]) - self.offset
                return ("noteabs" if kind.scalar == "note" else key), index
            if kind.scalar == "note" and isinstance(value, int) and not isinstance(value, bool):
                return key, value
            raise SongFormatError(f"{where}.{key}: a note is +n, -n or =n")
        if kind.scalar == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise SongFormatError(f"{where}.{key}: an integer")
            return key, value
        forms = kind.forms
        if value is False or value == OFF:
            if "off" not in forms:
                raise SongFormatError(f"{where}.{key}: {self.player} cannot turn {key} off here")
            return key, OFF
        if isinstance(value, dict) or (isinstance(value, str) and value.startswith("@")):
            if "spec" not in forms:
                raise SongFormatError(f"{where}.{key}: {self.player} takes no spec here")
            return key, self.spec(value, key, f"{where}.{key}")
        try:
            number = specs.number(value, key)
        except specs.SpecError:
            number = None
        if number is not None:
            if "set" not in forms:
                raise SongFormatError(f"{where}.{key}: {self.player} cannot set {key} here")
            return key, number
        if isinstance(value, str) and "live" in forms:
            parse_expr(value)
            return key, {"live": value}
        raise SongFormatError(f"{where}.{key}: bad value {value!r}")

    def _proto(self, rtype, value, where) -> dict:
        """A row's internal fields; references stay ``("name", str)`` until resolved."""
        if not isinstance(value, dict):
            raise SongFormatError(f"{where}: a row is a mapping")
        row = {}
        for key, val in value.items():
            name, internal = self._value(rtype, str(key), val, where)
            row[name] = internal
        plain = {k: (v if not isinstance(v, tuple) else 0) for k, v in row.items()}
        check_rules(rtype.rules, plain, rtype.default, f"{where} ({self.player})")
        return row

    def _program(self, rtype, value, where) -> int:
        """Allocate a program's rows; its entry index."""
        if not isinstance(value, list) or len(value) < 2:
            raise SongFormatError(f"{where}: a program is rows then end or loop")
        rows, term = value[:-1], value[-1]
        start = self._alloc(rtype, len(rows))
        for k, raw in enumerate(rows):
            row = self._proto(rtype, raw, f"{where}[{k}]")
            row[rtype.chain] = start + k + 1
            self.pending.append((rtype, start + k, row))
        last = self.pending[-1][2]
        if term == timeline.END:
            last[rtype.chain] = int(rtype.none) if rtype.none is not None else 0
        elif isinstance(term, dict) and set(term) == {"loop"} and 0 <= term["loop"] < len(rows):
            last[rtype.chain] = start + int(term["loop"])
        else:
            raise SongFormatError(f"{where}: bad program end {term!r}")
        return start

    def _define(self, tname, name, value):
        if tname == SPEC:
            if name in self.spec_names:
                raise SongFormatError(f"spec {name!r} defined twice")
            try:
                self.spec_names[name] = specs.parse(value, f"spec {name}")
            except specs.SpecError as exc:
                raise SongFormatError(str(exc)) from exc
            return
        rtype = self.rtypes.get(tname)
        if rtype is None:
            raise SongFormatError(f"{self.player} has no {tname!r} definitions")
        if rtype.fixed:
            self.pending.append((rtype, int(name), self._proto(rtype, value, f"{tname} {name}")))
            return
        if not isinstance(name, str) or not _ONE.match(name):
            raise SongFormatError(f"{tname}: bad name {name!r}")
        if name in self.names:
            raise SongFormatError(f"name {name!r} defined twice")
        if rtype.chain is not None:
            index = self._program(rtype, value, f"{tname} {name}")
        else:
            index = self._alloc(rtype, 1)
            self.pending.append((rtype, index, self._proto(rtype, value, f"{tname} {name}")))
        self.names[name] = (tname, index)

    def lookup(self, name, tname) -> int:
        """The index of object ``name``, which must be a ``tname``."""
        if name not in self.names:
            raise SongFormatError(f"unknown name {name!r}")
        kind, index = self.names[name]
        if kind != tname:
            raise SongFormatError(f"{name!r} is a {kind}, not a {tname}")
        return index

    def _resolve(self):
        for rtype, index, row in self.pending:
            out = {}
            for key, val in row.items():
                if isinstance(val, tuple):
                    val = self.lookup(val[1], rtype.keys[key].ref)
                out[key] = val
            if index in self.records[rtype.name]:
                raise SongFormatError(f"{rtype.name} row {index} defined twice")
            self.records[rtype.name][index] = out

    def command(self, name, arg, where):
        """An internal ``[name, arg]`` for a parsed command."""
        # pylint: disable=too-many-return-statements
        if name not in self.commands:
            raise SongFormatError(f"{where}: {self.player} has no command {name!r}")
        kind = str(self.commands[name].get("arg", "none"))
        if kind == "spec":
            if arg is None:
                raise SongFormatError(f"{where}: !{name} takes a spec")
            return [name, self.spec(arg, name, f"{where} !{name}")]
        if kind.startswith("ref "):
            target = kind.split()[1]
            if arg == "off":
                return [name, int(self.rtypes[target].none or 0)]
            if not (isinstance(arg, str) and arg.startswith("@")):
                raise SongFormatError(f"{where}: !{name} takes @name or off")
            return [name, ("name", arg[1:], target)]
        if kind in ("pair", "pair?"):
            if arg is None and kind == "pair?":
                return [name, None]
            if not isinstance(arg, list):
                raise SongFormatError(f"{where}: !{name} takes a,b")
            return [name, arg]
        if kind == "int":
            if not isinstance(arg, int):
                raise SongFormatError(f"{where}: !{name} takes an integer")
            return [name, arg]
        if arg is not None:
            raise SongFormatError(f"{where}: !{name} takes no argument")
        return [name, None]

    def event(self, atom, where) -> dict:
        """An internal event for an atom."""
        try:
            parsed = parse_atom(atom)
        except AtomError as exc:
            raise SongFormatError(f"{where}: {exc}") from exc
        event = {}
        if parsed["kind"] == 1:
            event["note"] = parsed["note"] - self.offset
        elif parsed["kind"]:
            event["note"] = "off" if parsed["kind"] == 2 else "on"
        if parsed["ins"] is not None:
            event["instrument"] = self.lookup(parsed["ins"], self.ins_type) + self.ins_base
        if parsed["cmd"] is not None:
            event["command"] = self.command(parsed["cmd"], parsed["arg"], where)
        if parsed["steps"] > 1:
            event["steps"] = parsed["steps"]
        return event

    def _collect(self, song):
        """Define every record, spec and phrase; ``(phrase atoms, play rows)``."""
        if not isinstance(song, list) or not song:
            raise SongFormatError("song must be a list of rows")
        phrases, rows = {}, []
        for r, item in enumerate(song):
            if not isinstance(item, dict) or "play" not in item:
                raise SongFormatError(f"song row {r} needs a play list")
            defs = item.get("def") or {}
            for tname in sorted(defs, key=lambda t: t != SPEC):
                for name, value in (defs[tname] or {}).items():
                    if tname == PHRASE:
                        if name in phrases or name in self.names:
                            raise SongFormatError(f"name {name!r} defined twice")
                        phrases[name] = value
                    else:
                        self._define(tname, name, value)
            rows.append([self._entry(e) for e in item["play"]])
        self._resolve()
        return phrases, rows

    def _events(self, phrases):
        out = {}
        for name, atoms in phrases.items():
            events = [self.event(a, f"phrase {name}") for a in atoms]
            for event in events:
                cmd = event.get("command")
                if cmd and isinstance(cmd[1], tuple):
                    cmd[1] = self.lookup(cmd[1][1], cmd[1][2])
            out[name] = events
        return out

    def read(self) -> dict:
        """The internal representation."""
        doc = self.doc
        phrases, rows = self._collect(doc.get("song"))
        events = self._events(phrases)
        lengths = {name: len(phrase_steps(ev)) for name, ev in events.items()}
        voices = int(doc.get("voices", self.defaults.get("voices", 3)))
        try:
            tracks = timeline.from_rows(rows, doc.get("loop"), lengths, voices)
        except timeline.TimelineError as exc:
            raise SongFormatError(str(exc)) from exc
        regs = list(self.defaults.get("registers") or [0] * 25)
        for i, v in (doc.get("registers") or {}).items():
            regs[int(i)] = int(v)
        return {
            "unitracker": IR_VERSION,
            "meta": dict(doc.get("meta") or {}),
            "hints": dict(doc.get("hints") or {}),
            "clock": dict(doc.get("clock") or self.defaults.get("clock") or {}),
            "voices": voices,
            "registers": regs,
            "tables": self.tables,
            "playroutine": self.pr,
            "records": {k: v for k, v in self.records.items() if v},
            "tracks": tracks,
            "phrases": events,
        }

    @staticmethod
    def _entry(entry):
        if entry is None:
            return None
        if entry == timeline.END:
            return timeline.END
        match = _ENTRY.match(str(entry))
        if match is None:
            raise SongFormatError(f"bad play entry {entry!r}")
        return match["name"], int(match["t"] or 0)


def read(doc: dict) -> dict:
    """The internal representation of a parsed v2 document."""
    return _Reader(doc).read()
