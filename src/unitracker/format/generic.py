"""The generic document: the internal song in unitracker-grammar's language, which names no
player and which unitracker-player plays. A frequency expression's internal ``v`` is the offset
from the note's frequency in every playroutine; the generic one is the frequency word, and a
native note table that differs from ``F`` where the song reads it is the document table ``G``.
"""

import ast
import re
from dataclasses import dataclass, field

from unitracker_grammar import PAL_HZ, CanonicalError, frequency, parse

from unitracker.format import songio, tempo, xexpr
from unitracker.format.header import DEFAULTS
from unitracker.format.records import record_types
from unitracker.format.yamlstyle import Atom, Flow, FlowMap
from unitracker.player import oracle, reach
from unitracker.player.modulation import declared
from unitracker.player.vm import SongPlayer

NOTE_TABLE = "G"
FREQ_WIDTH = 1 << 16
SECTIONS = ("restart", "ins", "ctrl", "pw", "filter", "x", "phrase")
ROW_KEYS = tuple("wait hold ctrl gate note ad sr pw cut res route mode freq run filter vol".split())
INS_KEYS = ("ad", "sr", "ctrl", "pw", "filter", "freq", "restart")
INIT_TYPES = ("vinit", "ginit")
CMD_ORDER = tuple("run pw filter ad sr ctrl resfilt mode vol cut tempo vtempo freq legato".split())
PREFIX = {"ctrl": "c", "pw": "pw", "filter": "f", "restart": "r", "x": "x", "phrase": "p"}
# The ticks a note sounds after its onset cell is set (each playroutine's doc).
ONSET_LAG = {"defmon": 1}
GATE_CLEARS_MASK = ("defmon",)  # a row that sets the gate without a mask clears the mask
FIRST_TICK_BEFORE = ("goattracker2", "sidwizard")  # first-tick row a tick before the programs
NOTE_KEEPS_GATE = ("goattracker2",)  # a note keeps the gate its restart leaves
ROUTED_BY_INSTRUMENT = ("sidwizard",)  # the filter routes each voice with a filter program
_NAME = re.compile(r"(c|pw|f|r|x|p)(\d+)")
_XREF = re.compile(r"!(pw|cut|freq):(x\d+)")
_RUN = re.compile(r"!run:(c\d+)")
_FREQ_INT = re.compile(r"!freq:([+-]?\d+)(?=[!*]|$)")
_TABLE = re.compile(r"([A-EG-MO-Z][A-Za-z]*)\[")


class GenericError(ValueError):
    """A lifting gap: the internal song holds something the generic document cannot spell."""


@dataclass(frozen=True)
class Native:  # pylint: disable=too-many-instance-attributes
    """What a song plays natively that its generic document needs: the ticks to its state's
    recurrence and a period more, the format notes it reads the frequency of, its note table
    there, its modulator tables, each voice's onset tick and, where no voice starts on the
    first tick, the registers after it: the state after init."""

    ticks: int
    notes: frozenset
    table: dict
    tables: dict = field(default_factory=dict)
    clock_hz: int = PAL_HZ
    starts: tuple = ()
    init: tuple | None = None

    def note_table(self) -> list[int] | None:
        """The native note table from format note 0 when it differs from ``F`` at a note the
        song reads, else None; as a table patch does, it holds only what the song reads."""
        if all(self.table[k] == frequency(k, self.clock_hz) for k in self.notes):
            return None
        return [self.table.get(k, 0) for k in range(max(self.notes) + 1)]


def _replay(ir: dict, max_ticks: int, ticks: int | None) -> tuple:
    """``(player, ticks, registers after the first tick)`` of a replay of ``ir`` to its state's
    recurrence and a period more, or ``ticks`` where it does not recur within ``max_ticks``."""
    player = SongPlayer(ir, record_reads=True)
    first: list = []
    try:
        tick, period = reach.recurrence(
            player, max_ticks, each=lambda p: None if first else first.append(tuple(p.regs))
        )
    except reach.ReachabilityError:
        if ticks is None:
            raise
        tick, period = ticks, 0
    return player, tick + period, first[0] if first else None


def native_state(ir: dict, max_ticks: int = reach.MAX_TICKS, ticks: int | None = None) -> Native:
    """The :class:`Native` of song ``ir``: one replay to its state's recurrence, recording the
    frequency-table entries it reads and its registers after the first tick. ``ticks``, where
    given, stands for the recurrence and a period of a song whose state does not recur within
    ``max_ticks``."""
    player, ticks, first = _replay(ir, max_ticks, ticks)
    spec = declared(ir["playroutine"]) or {}
    ns, offset = player.namespace, int(ir["playroutine"].get("note_offset", 0))
    reads = (player.table_reads or {}).get
    notes = {i - offset for t in (spec.get("freq") or {}).values() for i in reads(t, ())}
    notes = {k for k in notes if k >= 0}
    clock = int((ir.get("clock") or DEFAULTS["clock"])["clock_hz"])
    word = ns.get("_F") or (lambda k: frequency(k, clock))
    table = {k: int(word(k)) for k in notes}
    tables = {
        name: [int(ns["_T"][name](i)) for i in range(int(t["size"]))]
        for name, t in (spec.get("tables") or {}).items()
    }
    init = first if 0 not in player.starts else None
    return Native(ticks, frozenset(notes), table, tables, clock, tuple(player.starts), init)


def with_command(atom: str, name: str, value: str) -> str:
    """Event ``atom`` with command ``name:value`` in the grammar's order, unless it has one."""
    match = re.fullmatch(r"([^!*]*)((?:![^!*]+)*)(\*\d+)?", atom)
    head, cmds, steps = match.group(1), match.group(2).split("!")[1:], match.group(3) or ""
    if any(c.split(":")[0] == name for c in cmds if ":" in c):
        return atom
    order = {k: i for i, k in enumerate(CMD_ORDER)}

    def rank(cmd: str) -> int:
        return order[cmd.split(":")[0]] if ":" in cmd or cmd == "legato" else len(order)

    return head + "".join(f"!{c}" for c in sorted([*cmds, f"{name}:{value}"], key=rank)) + steps


def _gate_left(rows: list) -> int | None:
    """The gate an internal restart leaves for its note: its first-tick row's ``ctrl`` sets it
    on; else the last ``gate`` its rows set, or None where they set none."""
    if "ctrl" in rows[-2]:
        return 1
    gates = [int(r["gate"]) for r in rows[:-1] if isinstance(r, dict) and "gate" in r]
    return gates[-1] if gates else None


def _ordered(row: dict, keys=ROW_KEYS) -> FlowMap:
    return FlowMap((k, row[k]) for k in keys if k in row)


def _dicts(node):
    """Every mapping inside ``node``."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _dicts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _dicts(value)


class _Swap(ast.NodeTransformer):
    """``F(k)`` read from the note table, and ``v`` replaced by ``offset`` when one is given."""

    def __init__(self, note, offset=None):
        self.note, self.offset = note, offset

    def visit_Call(self, node):  # pylint: disable=invalid-name
        """``F(k)`` as the note table's word."""
        self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id == "F":
            return self.note(node.args[0])
        return node

    def visit_Name(self, node):  # pylint: disable=invalid-name
        """``v`` as the offset."""
        return self.offset if self.offset is not None and node.id == "v" else node


class _Respell:  # pylint: disable=too-many-instance-attributes
    """The rewrite of one internal document's written structure into the generic one."""

    def __init__(self, ir: dict, native: Native, lead: int = 0):
        self.ir, self.native, self.lead = ir, native, lead
        self.pr, self.name = ir["playroutine"], ir["playroutine"]["name"]
        self.voices = int(ir.get("voices", 3))
        self.g = native.note_table()
        fields = getattr(record_types(self.pr).get("ins"), "fields", None) or {}
        self.envelope = {k: int(fields[k]) for k in ("ad", "sr") if k in fields}
        self.read: set[str] = set()
        self.defined: dict[str, tuple] = {}
        self.used: dict[str, int] = {}
        self.added: dict[str, dict] = {}
        self.aliases: dict[tuple, str] = {}
        self.runs: set[str] = set()
        self.stop: str | None = None
        self.init_tempo: str | None = None

    # -- names --

    def fresh(self, kind: str, value) -> Atom:
        """A new name of ``kind`` defined as ``value`` in the song's first row."""
        prefix = PREFIX[kind]
        self.used[prefix] = self.used.get(prefix, 0) + 1
        name = Atom(f"{prefix}{self.used[prefix]}")
        self.added.setdefault(kind, {})[name] = value
        return name

    # -- expressions --

    def _note(self, arg):
        if self.g is None:
            return ast.Call(ast.Name("F"), [arg], [])
        return ast.Subscript(ast.Name(NOTE_TABLE), arg)

    def expr(self, value, freq: bool = False, note: bool = False):
        """An expression value in the generic language; ``freq`` for a frequency's, whose
        internal ``v`` is the offset from the note's frequency, which ``note``, a frequency
        starting on its note's first tick, starts at 0."""
        if isinstance(value, (bool, Atom)) or not isinstance(value, (int, str)):
            return value
        if isinstance(value, int) and (not freq or self.g is None):
            return value
        try:
            tree = ast.parse(str(value).strip(), mode="eval").body
        except SyntaxError as exc:
            raise GenericError(f"expression {value!r} does not parse: {exc.msg}") from exc
        if freq:
            base = self._note(ast.Name("N"))
            off = ast.BinOp(
                ast.BinOp(ast.Name("v"), ast.Sub(), base), ast.Mod(), ast.Constant(FREQ_WIDTH)
            )
            if note:
                started = ast.Compare(ast.Name("φ"), [ast.GtE()], [ast.Constant(1)])
                off = ast.BinOp(started, ast.Mult(), off)
            tree = _Swap(self._note, off).visit(tree)
            if self.g is None and isinstance(tree, ast.Constant):
                return tree.value
            tree = ast.BinOp(base, ast.Add(), tree)
        else:
            tree = _Swap(self._note).visit(tree)
        text = xexpr._Printer().text(tree)  # pylint: disable=protected-access
        self.read.update(_TABLE.findall(text))
        written = xexpr.written(text)
        return written if isinstance(written, int) and not freq else text

    # -- records --

    def row(self, row, where: str, note: bool = False):
        """A program row with its expressions respelled; a row no shared key covers is a gap.
        ``note`` for a row on its note's first tick, where the note has reset the offset."""
        if isinstance(row, str) and row == "end":
            return row
        if not isinstance(row, dict):
            raise GenericError(f"{where}: row {list(row)} is in its type's native form")
        unknown = [k for k in row if k not in ROW_KEYS]
        if unknown and set(row) != {"loop"}:
            raise GenericError(f"{where}: row {dict(row)} has keys {unknown} with no generic form")
        out = FlowMap(row)
        for key in ("pw", "cut"):
            if key in out and out[key] != "none":
                out[key] = self.expr(out[key])
        if "freq" in out:
            out["freq"] = self.expr(out["freq"], freq=True, note=note)
        if self.name in GATE_CLEARS_MASK and "gate" in out and "^" not in str(out.get("ctrl")):
            out["ctrl"] = Atom(f"{out.get('ctrl', '')}^0")
            out = _ordered(out)
        return out

    def program(self, value, where: str, key: str | None = None, note: bool = False):
        """A program, a program name, or (for a pulse or filter program) one expression;
        ``note`` for a program only notes start, whose first row then applies on the note's
        first tick unless it waits."""
        if isinstance(value, Atom):
            return value
        if isinstance(value, list):
            return Flow(
                self.row(r, f"{where} row {i}", note and i == 0 and not r.get("wait"))
                for i, r in enumerate(value)
            )
        if key in ("pw", "filter"):
            return self.expr(value)
        raise GenericError(f"{where}: {value!r} is not a program")

    def instrument(self, value, where: str):
        """An instrument with its programs and ``freq`` respelled, ``freq: G[N]`` where it has
        none and the note table is the document's, and the envelope bytes its note sets."""
        if not isinstance(value, dict):
            raise GenericError(f"{where}: {value!r} is in its type's native form")
        unknown = [k for k in value if k not in INS_KEYS]
        if unknown:
            raise GenericError(f"{where}: fields {unknown} have no generic form")
        out = FlowMap()
        for key in INS_KEYS:
            if key in ("ad", "sr") and (key in value or key in self.envelope):
                out[key] = value.get(key, self.envelope.get(key))
            elif key == "freq" and (key in value or self.g is not None):
                out[key] = self.expr(value.get(key, 0), freq=True, note=True)
            elif key in value:
                named = self.named(key, value[key])
                out[key] = self.program(named, f"{where}.{key}", key, key == "ctrl")
        if self.name in ROUTED_BY_INSTRUMENT:
            out["filter"] = self.routed(out.get("filter"))
            out = _ordered(out, INS_KEYS)
        if "restart" in out and self.name in FIRST_TICK_BEFORE:
            rows = self.restart_rows(value["restart"], where)
            if self.name in NOTE_KEEPS_GATE and _gate_left(rows) == 0:
                out["ctrl"] = self.gated(out.get("ctrl"), where)
            out["restart"] = self.restart(rows, out, where)
        return out

    def gated(self, value, where: str) -> Flow:
        """An instrument's control program that first turns off the gate its note turns on."""
        if isinstance(value, Atom):
            value = self.program(self.defined[str(value)][2], f"{where}.ctrl", "ctrl", True)
        rows = list(value or [Atom("end")])
        loop = rows[-1]
        if isinstance(loop, dict):
            loop = FlowMap(loop=loop["loop"] + 1)
        return Flow([FlowMap(hold=0, gate=0), *rows[:-1], loop])

    def named(self, kind: str, value):
        """A program name an instrument's ``kind`` field holds, as one of that kind's names:
        a program of another kind is defined again under ``kind``."""
        match = _NAME.fullmatch(value) if isinstance(value, Atom) else None
        if match is None or match.group(1) == PREFIX[kind]:
            return value
        if (kind, value) not in self.aliases:
            _, okind, program = self.defined[str(value)]
            self.aliases[(kind, value)] = self.fresh(
                kind, self.program(program, f"{okind} {value}", kind)
            )
        return self.aliases[(kind, value)]

    def routed(self, value):
        """An instrument's filter program that routes the filter to its voice, or one that
        removes the voice where it has none."""
        if value is None:
            return Flow([FlowMap(route=Atom("'-'")), Atom("end")])
        if isinstance(value, Atom):
            value = self.program(self.defined[str(value)][2], f"filter {value}", "filter")
        if not isinstance(value, list):
            value = Flow([FlowMap(cut=value), Atom("end")])
        return Flow([_ordered({**value[0], "route": Atom("+")}), *value[1:]])

    def restart_rows(self, value, where: str) -> list:
        """The internal rows of an instrument's restart, its name resolved."""
        rows = list(self.defined[str(value)][2] if isinstance(value, Atom) else value)
        if len(rows) < 2 or rows[-1] != "end":
            raise GenericError(f"{where}.restart: {rows} is not a restart program")
        return rows

    def restart(self, rows: list, ins: dict, where: str) -> Flow:
        """An instrument's restart with its first-tick row, the gate it sets, the instrument's
        envelope and the voice's held frequency and stopped control program a tick before the
        note, whose own row is then empty."""
        first = FlowMap(rows[-2])
        if "ctrl" in first and "gate" not in first:
            first["gate"] = 1
        first.update((k, ins[k]) for k in ("ad", "sr") if k in ins)
        if self.stop is None:
            self.stop = self.fresh("ctrl", Flow([Atom("end")]))
        first.update(freq="v", run=self.stop)
        return self.program([*rows[:-2], _ordered(first), FlowMap(), rows[-1]], f"{where}.restart")

    # -- phrases, init and the lead-in --

    def phrase_uses(self, rows) -> dict:
        """``{x name: command}`` of the expressions phrases name."""
        uses: dict[str, set] = {}
        for atom in self.atoms(rows):
            for cmd, name in _XREF.findall(atom):
                uses.setdefault(name, set()).add(cmd)
        mixed = {n: sorted(c) for n, c in uses.items() if len(c) > 1 and "freq" in c}
        if mixed:
            raise GenericError(f"expressions {mixed} drive both a frequency and another cell")
        return {n: next(iter(c)) for n, c in uses.items()}

    @staticmethod
    def atoms(rows):
        """Every event atom of the song's phrases, as text."""
        for item in rows:
            for atoms in ((item.get("def") or {}).get("phrase") or {}).values():
                yield from (str(a) for a in atoms)

    def freq_offsets(self, rows) -> None:
        """Each ``!freq:n`` as an expression of the document's note table."""
        made: dict[int, Atom] = {}
        for item in rows:
            phrases = (item.get("def") or {}).get("phrase") or {}
            for name, atoms in phrases.items():
                out = []
                for atom in atoms:
                    match = _FREQ_INT.search(str(atom))
                    if match:
                        n = int(match.group(1))
                        if n not in made:
                            made[n] = self.fresh("x", self.expr(n, freq=True))
                        atom = Atom(str(atom).replace(match.group(0), f"!freq:{made[n]}"))
                    out.append(atom)
                phrases[name] = Flow(out)

    def init(self, defs: dict) -> Flow | None:
        """The ``init`` row, one event per voice: the global registers the native player's
        init sets, the lead-in's tempo, and the fixed records of the state after init, a
        legato note for the base note and the commands that set each key."""
        fixed = {t: defs.pop(t) for t in INIT_TYPES if t in defs}
        cmds: list[dict] = [{} for _ in range(self.voices)]
        notes = ["-"] * self.voices
        cmds[0].update(self.after_init())
        if self.init_tempo is not None:
            cmds[0]["tempo"] = self.init_tempo
        for kind in INIT_TYPES:
            for index, rec in (fixed.get(kind) or {}).items():
                voice = int(index) if kind == "vinit" else 0
                for key, value in rec.items():
                    cmds[voice].update(self.init_command(kind, index, key, value))
                    if kind == "vinit" and key == "note":
                        notes[voice] = str(value)
        if not any(cmds) and notes == ["-"] * self.voices:
            return None
        return Flow(
            Atom(
                notes[v]
                + "".join(
                    f"!{k}" if cmds[v][k] is None else f"!{k}:{cmds[v][k]}"
                    for k in CMD_ORDER
                    if k in cmds[v]
                )
            )
            for v in range(self.voices)
        )

    def init_command(self, kind: str, index, key: str, value) -> dict:
        """``{command: argument}`` of a fixed record's key; a note is a legato note."""
        if kind == "vinit" and key == "note":
            return {"legato": None}
        if key in ("pw", "cut", "freq"):
            freq = key == "freq"
            if isinstance(value, int) and not (freq and self.g is not None):
                return {key: value}
            return {key: self.fresh("x", self.expr(value, freq))}
        if key in ("mode", "vol"):
            return {key: value}
        raise GenericError(f"{kind} {index}: {key} has no init command")

    def after_init(self) -> dict:
        """The init commands of the global registers the native player sets in its init."""
        regs = self.native.init
        if regs is None:
            return {}
        res, route, mode, vol = regs[0x17] >> 4, regs[0x17] & 15, regs[0x18] & 0xF0, regs[0x18] & 15
        cut = regs[0x16] << 3 | regs[0x15] & 7
        out = {"resfilt": f"{res}/{route}"} if res or route else {}
        out.update((k, v) for k, v in (("mode", mode), ("vol", vol), ("cut", cut)) if v)
        return out

    def tempo_pair(self) -> tuple[int, int]:
        """The song's initial tempo ``(a, b)``; a playroutine without a tempo steps each tick."""
        value = self.ir.get(tempo.NAME, tempo.default(self.pr))
        if value is None:
            value = 6 if self.pr.get(tempo.NAME) else 1
        return tempo.pair(value)

    def lead_in(self, rows: list) -> dict:
        """The row before the song's first that delays it ``lead`` ticks, the native player's
        start: a step the init row times, then a step whose event restores the song's tempo,
        or that event on the song's first step where the lead is shorter than a step."""
        a, b = self.tempo_pair()
        back = f"{b}/{a}" if a != b else str(a)
        atoms = [Atom("-")]
        if self.lead > b:
            self.init_tempo = str(self.lead - b)
            atoms.append(Atom(f"-!tempo:{back}"))
        elif self.lead == b:
            self.init_tempo = back if a != b else None
        else:
            self.init_tempo = str(self.lead)
            self.restore(rows, f"{a}/{b}" if a != b else str(a))
        lead = self.fresh("phrase", Flow(atoms))
        defs = {"phrase": {lead: self.added["phrase"].pop(lead)}}
        return {"def": defs, "play": Flow([lead] * self.voices)}

    def restore(self, rows: list, value: str) -> None:
        """The song's first row playing a copy of its first phrase whose first event sets the
        song's tempo ``value``."""
        play = rows[0]["play"]
        voice, entry = next((v, e) for v, e in enumerate(play) if str(e or "").startswith("p"))
        base, shift = re.fullmatch(r"(p\d+)([+-]\d+)?", entry).groups()
        defs = rows[self.defined[base][0]]["def"]["phrase"]
        source = next(atoms for name, atoms in defs.items() if str(name) == base)
        copy = Flow([Atom(with_command(str(source[0]), "tempo", value)), *source[1:]])
        play[voice] = Atom(f"{self.fresh('phrase', copy)}{shift or ''}")

    # -- the document --

    def __call__(self, doc: dict) -> dict:
        rows = songio.song_rows(doc)
        self.defined = {
            str(name): (r, kind, value)
            for r, item in enumerate(rows)
            for kind, entries in (item.get("def") or {}).items()
            for name, value in entries.items()
        }
        for name in self.defined:
            if match := _NAME.fullmatch(name):
                prefix, number = match.groups()
                self.used[prefix] = max(self.used.get(prefix, 0), int(number))
        uses = self.phrase_uses(rows)
        self.runs = {n for a in self.atoms(rows) for n in _RUN.findall(a)} | {
            str(d["run"]) for d in _dicts(rows) if "run" in d
        }
        if self.g is not None:
            self.freq_offsets(rows)
        lead = self.lead_in(rows) if self.lead else None
        init = self.init(rows[0].get("def") or {}) if rows else None
        for item in rows:
            for kind, entries in (item.get("def") or {}).items():
                self.section(kind, entries, uses)
        for item in rows:
            if self.name in FIRST_TICK_BEFORE:
                (item.get("def") or {}).pop("restart", None)
            if "def" in item and not item["def"]:
                del item["def"]
        if any(self.added.values()):
            first = rows[0].setdefault("def", {})
            for kind, entries in self.added.items():
                if entries:
                    first.setdefault(kind, {}).update(entries)
            rows[0] = {"def": first, "play": rows[0]["play"]}
        if lead is not None:
            doc["song"].insert(0, lead)
            if isinstance(doc["song"][-1], dict):
                doc["song"][-1] = FlowMap(loop=doc["song"][-1]["loop"] + 1)
        return self.header(doc, init)

    def section(self, kind: str, entries: dict, uses: dict) -> None:
        """A row's definitions of ``kind`` respelled in place."""
        if kind not in SECTIONS:
            names = ", ".join(str(n) for n in entries)
            raise GenericError(f"record type {kind} ({names}) has no generic form")
        for name, value in list(entries.items()):
            where = f"{kind} {name}"
            if kind == "ins":
                entries[name] = self.instrument(value, where)
            elif kind == "x":
                entries[name] = self.expr(value, uses.get(str(name)) == "freq")
            elif kind != "phrase":
                started = kind == "ctrl" and str(name) not in self.runs
                entries[name] = self.program(value, where, kind, started)

    def header(self, doc: dict, init) -> dict:
        """The document's header lines in the grammar's order, then its song; the grammar's
        ``meta`` holds at least one key, an empty title where the song has none."""
        out = {"unitracker": doc["unitracker"], "meta": doc["meta"] or FlowMap(title="")}
        clock = self.ir.get("clock") or DEFAULTS["clock"]
        if dict(clock) != DEFAULTS["clock"]:
            out["clock"] = FlowMap(
                cycles_per_tick=int(clock["cycles_per_tick"]), clock_hz=int(clock["clock_hz"])
            )
        if self.voices != 3:
            out["voices"] = self.voices
        value = tempo.written(*self.tempo_pair())
        if value != 6:
            out["tempo"] = Flow(value) if isinstance(value, list) else value
        tables = {NOTE_TABLE: self.g} if self.g is not None and NOTE_TABLE in self.read else {}
        for name in sorted(self.read - {NOTE_TABLE}):
            if name not in self.native.tables:
                raise GenericError(f"table {name} is read but the playroutine declares none")
            tables[name] = self.native.tables[name]
        negative = sorted(name for name, values in tables.items() if min(values, default=0) < 0)
        if negative:
            raise GenericError(f"tables {negative} hold negative entries")
        if tables:
            out["tables"] = {Atom(k): Flow(v) for k, v in tables.items()}
        if init is not None:
            out["init"] = init
        out["song"] = doc["song"]
        return out


def _lead(text: str, starts: tuple, lag: int = 0) -> int:
    """The ticks the native player starts the song after the document does: each voice's onset
    tick less its first note's tick in ``text``, which every voice that plays shares, and the
    ``lag`` its notes sound after their onsets."""
    if all(s is None for s in starts):
        return 0
    _, onsets = oracle.play(text, max(s for s in starts if s is not None) + 1)
    leads = {s - o for s, o in zip(starts, onsets) if s is not None and o is not None}
    if len(leads) != 1 or min(leads) < 0:
        raise GenericError(
            f"the voices' onsets are at native ticks {list(starts)} and their first notes at"
            f" document ticks {list(onsets)}: no one lead-in plays them"
        )
    return leads.pop() + lag


def dumps(ir: dict, native: Native | None = None) -> str:
    """The generic document of internal song ``ir``, which parses with ``unitracker_grammar``.

    Raises :class:`GenericError` naming the record and row the document cannot spell.
    """
    native = native_state(ir) if native is None else native
    text = songio.dumps(ir, embed=True, respell=_Respell(ir, native))
    lead = _lead(text, native.starts, ONSET_LAG.get(ir["playroutine"]["name"], 0))
    if lead:
        text = songio.dumps(ir, embed=True, respell=_Respell(ir, native, lead))
    try:
        parse(text)
    except CanonicalError as exc:
        raise AssertionError(f"the generic writer wrote text outside the grammar: {exc}") from exc
    return text
