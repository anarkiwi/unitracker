"""The generic document: the internal song in unitracker-grammar's language, which names no
player and which unitracker-player plays. A frequency expression's internal ``v`` is the offset
from the note's frequency in every playroutine; the generic one is the frequency word, and a
native note table that differs from ``F`` where the song reads it is the document table ``G``.
"""

import ast
import itertools
import re
from dataclasses import dataclass, field

from unitracker_grammar import PAL_HZ, CanonicalError, frequency, parse

from unitracker.format import retime, songio, tempo, xexpr
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
PREFIX = {
    "ctrl": "c",
    "pw": "pw",
    "filter": "f",
    "restart": "r",
    "x": "x",
    "phrase": "p",
    "ins": "i",
}
FIELD = {"pw": "pw", "cut": "filter", "freq": "freq"}  # the instrument field of a cell key
# The ticks a note sounds after its onset cell is set (each playroutine's doc).
ONSET_LAG = {"defmon": 1}
GATE_CLEARS_MASK = ("defmon",)  # a row that sets the gate without a mask clears the mask
FIRST_TICK_BEFORE = ("goattracker2", "sidwizard")  # first-tick row a tick before the programs
NOTE_KEEPS_GATE = ("goattracker2",)  # a note keeps the gate its restart leaves
ROUTED_BY_INSTRUMENT = ("sidwizard",)  # the filter routes each voice with a filter program
NOTED = "note command"  # an origin (oracle.firings) whose note resets the frequency's offset
COPY = "instrument copy"  # an instrument origin that only a copy of the instrument plays
_NAME = re.compile(r"(c|pw|f|r|x|p|i)(\d+)")
_NAMES = re.compile(r"\b(?:c|pw|f|r|x|p|i)\d+\b")
_HEAD = re.compile(r"([^.!*]+)(?:\.(i\d+))?")
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
    first tick, the registers after it: the state after init; and each expression's firings,
    ``{cell: {tick: occurrence}}`` with ``{occurrence: expression text}``."""

    ticks: int
    notes: frozenset
    table: dict
    tables: dict = field(default_factory=dict)
    clock_hz: int = PAL_HZ
    starts: tuple = ()
    init: tuple | None = None
    fired: dict = field(default_factory=dict)
    texts: dict = field(default_factory=dict)

    def note_table(self) -> list[int] | None:
        """The native note table from format note 0 when it differs from ``F`` at a note the
        song reads, else None; as a table patch does, it holds only what the song reads."""
        if all(self.table[k] == frequency(k, self.clock_hz) for k in self.notes):
            return None
        return [self.table.get(k, 0) for k in range(max(self.notes) + 1)]


def _canonical(value) -> str:
    """The one text of an expression value (see :func:`xexpr.canonical`)."""
    return xexpr.canonical(str(value))


def _replay(ir: dict, max_ticks: int, ticks: int | None) -> tuple:
    """``(player, ticks, registers after the first tick, log)`` of a replay of ``ir`` to its
    state's recurrence and a period more, or ``ticks`` where it does not recur within
    ``max_ticks``: the player has played those ticks, logging its expressions' firings."""
    player = SongPlayer(ir, record_reads=True)
    log = _log(player)
    first: list = []
    try:
        tick, period = reach.recurrence(
            player, max_ticks, each=lambda p: None if first else first.append(tuple(p.regs))
        )
    except reach.ReachabilityError:
        if ticks is None:
            raise
        tick, period = ticks, 0
    while player.ticks < tick + period:
        player.play_frame()
    return player, tick + period, first[0] if first else None, log


def _log(player) -> list:
    """The list to which each firing of an expression the player's units play appends
    ``(tick, cell, occurrence, expression index)``: the cell ``(key, voice)`` (voice None for
    a global unit's), an occurrence numbered from 1 per start."""
    log: list = []
    modulation = player.modulation
    if modulation is None:
        return log
    cells = {u["name"]: (u["key"], u.get("scope") == "global") for u in modulation.units}
    last: dict = {}
    serials = itertools.count(1)

    def fired(unit: dict, occ) -> None:
        if occ.derived is None:
            return
        key, glob = cells[unit["name"]]
        name = (unit["name"], occ.voice)
        if last.get(name, (None,))[0] is not occ:
            last[name] = (occ, next(serials))
        log.append((modulation.tick, (key, None if glob else occ.voice), last[name][1], occ.key))

    modulation.fired = fired
    return log


def _firings(player, log: list, ticks: int) -> tuple:
    """``({cell: {tick: occurrence}}, {occurrence: expression text})`` of the firings logged
    before ``ticks``."""
    fired: dict = {}
    keys: dict = {}
    exprs = player.song.get("exprs") or {}
    for tick, cell, serial, key in log:
        if tick < ticks:
            fired.setdefault(cell, {})[tick] = serial
            keys[serial] = _canonical(exprs[key])
    return fired, keys


def native_state(ir: dict, max_ticks: int = reach.MAX_TICKS, ticks: int | None = None) -> Native:
    """The :class:`Native` of song ``ir``: one replay to its state's recurrence, recording the
    frequency-table entries it reads and its registers after the first tick. ``ticks``, where
    given, stands for the recurrence and a period of a song whose state does not recur within
    ``max_ticks``."""
    player, ticks, first, log = _replay(ir, max_ticks, ticks)
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
    fired, texts = _firings(player, log, ticks)
    return Native(
        ticks, frozenset(notes), table, tables, clock, tuple(player.starts), init, fired, texts
    )


@dataclass
class Timing:
    """The schedules (:mod:`unitracker.format.retime`) of the expressions a document plays:
    ``base``, ``{(cell key, expression text): Schedule}`` where its modulators agree, and
    ``events``, ``{(phrase, event index): {cell key: (text, Schedule, how)}}`` for an event
    whose modulators of a text agree with each other but not with the text's base: it plays
    its own through its command, a copy of the program it runs or (``COPY``) of its
    instrument, ``how`` the origin of :func:`oracle.firings`. A text's base is the consensus
    of its modulators (:func:`retime.consensus`)."""

    base: dict = field(default_factory=dict)
    events: dict = field(default_factory=dict)


def _alone(mods: list) -> list:
    """For each ``(cell, ticks, origin)`` of :func:`oracle.firings`, whether it fires on every
    tick to its last and the cell's next modulator is one an event starts on the next tick:
    an event's command plays it as the program its note starts does."""
    out, last = [], {}
    for k in range(len(mods) - 1, -1, -1):
        cell, ticks, _ = mods[k]
        nxt = last.get(cell)
        out.append(
            ticks[-1] - ticks[0] + 1 == len(ticks)
            and (nxt is None or nxt[2] is not None and nxt[1][0] == ticks[-1] + 1)
        )
        last[cell] = mods[k]
    return out[::-1]


def timing(native: Native, text: str) -> Timing:
    """The :class:`Timing` of document ``text``: each modulator plays the native occurrence
    that fires on the earliest of its ticks one fires, and holds on its others."""
    found: dict = {}
    mods = oracle.firings(text, native.ticks)
    for (cell, ticks, origin), alone in zip(mods, _alone(mods)):
        fired = native.fired.get(cell) or {}
        occ = next((fired[t] for t in ticks if t in fired), None)
        if occ is not None:
            skips = frozenset(phi for phi, t in enumerate(ticks) if fired.get(t) != occ)
            group = found.setdefault((cell[0], native.texts[occ]), {}).setdefault(origin, [])
            group.append((retime.Schedule(skips, len(ticks)), alone))
    out = Timing()
    for key, groups in found.items():
        base = retime.consensus(s for group in groups.values() for s, _ in group)
        for origin, group in groups.items():
            own = retime.merged(s for s, _ in group)
            if origin is None or own is None:
                continue
            how = origin[2]
            if how == "instrument" and not all(alone for _, alone in group):
                how = COPY
            if not base.covers(own) or key[0] == "freq" and how == NOTED and own.lead:
                out.events.setdefault(origin[:2], {})[key[0]] = (key[1], own, how)
        if base.skips:
            out.base[key] = base
    return out


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


def _values(node):
    """The text of every value inside ``node``, a mapping's keys left out."""
    if isinstance(node, dict):
        node = list(node.values())
    if isinstance(node, list):
        for value in node:
            yield from _values(value)
    elif node is not None:
        yield str(node)


def _is_note(atom: str) -> bool:
    """Whether event ``atom`` plays a note."""
    return _HEAD.match(atom).group(1) not in ("-", "off", "on")


def _instrument_at(atoms: list, i: int) -> str | None:
    """The instrument event ``i`` of a phrase's ``atoms`` plays where the phrase names it."""
    return next((_HEAD.match(a).group(2) for a in atoms[i::-1] if _HEAD.match(a).group(2)), None)


def _with_ins(atom: Atom, ins: str) -> Atom:
    """Event ``atom`` naming instrument ``ins``."""
    match = _HEAD.match(atom)
    return Atom(f"{match.group(1)}.{ins}{atom[match.end():]}")


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

    def __init__(self, ir: dict, native: Native, lead: int = 0, timing: Timing | None = None):
        self.ir, self.native, self.lead = ir, native, lead
        self.timing = timing or Timing()
        self.replaced: set[str] = set()
        self.forced: dict = {}
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

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def expr(self, value, freq=False, note=False, cell: str | None = None, schedule=None):
        """An expression value in the generic language, re-timed to ``schedule`` or the ticks
        of the modulators of ``cell`` that play it (:class:`Timing`); ``freq`` for a
        frequency's, whose internal ``v`` is the offset from the note's frequency, which
        ``note``, a frequency starting on its note's first tick, keeps at 0 to its first
        firing."""
        if isinstance(value, (bool, Atom)) or not isinstance(value, (int, str)):
            return value
        if schedule is None and cell:
            key = (cell, _canonical(value))
            schedule = self.forced.get(key) or self.timing.base.get(key)
        found = None if schedule is None else retime.retimed(str(value), schedule, note and freq)
        if found is not None:
            value = xexpr.written(found)
        lead = max(1, schedule.lead) if found is not None else 1
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
                started = ast.Compare(ast.Name("φ"), [ast.GtE()], [ast.Constant(lead)])
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
                out[key] = self.expr(out[key], cell=key)
        if "freq" in out:
            out["freq"] = self.expr(out["freq"], freq=True, note=note, cell="freq")
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
            return self.expr(value, cell="pw" if key == "pw" else "cut")
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
                cell = "freq" if key in value else None
                out[key] = self.expr(value.get(key, 0), freq=True, note=True, cell=cell)
            elif key in value:
                named = self._named(key, value[key])
                out[key] = self.program(named, f"{where}.{key}", key, key == "ctrl")
        if self.name in ROUTED_BY_INSTRUMENT:
            out["filter"] = self._routed(out.get("filter"))
            out = _ordered(out, INS_KEYS)
        if "restart" in out and self.name in FIRST_TICK_BEFORE:
            rows = self._restart_rows(value["restart"], where)
            if self.name in NOTE_KEEPS_GATE and _gate_left(rows) == 0:
                out["ctrl"] = self._gated(out.get("ctrl"), where)
            out["restart"] = self._restart(rows, out, where)
        return out

    def _gated(self, value, where: str) -> Flow:
        """An instrument's control program that first turns off the gate its note turns on."""
        if isinstance(value, Atom):
            value = self.program(self.defined[str(value)][2], f"{where}.ctrl", "ctrl", True)
        rows = list(value or [Atom("end")])
        loop = rows[-1]
        if isinstance(loop, dict):
            loop = FlowMap(loop=loop["loop"] + 1)
        return Flow([FlowMap(hold=0, gate=0), *rows[:-1], loop])

    def _named(self, kind: str, value):
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

    def _routed(self, value):
        """An instrument's filter program that routes the filter to its voice, or one that
        removes the voice where it has none."""
        if value is None:
            return Flow([FlowMap(route=Atom("'-'")), Atom("end")])
        if isinstance(value, Atom):
            value = self.program(self.defined[str(value)][2], f"filter {value}", "filter")
        if not isinstance(value, list):
            value = Flow([FlowMap(cut=value), Atom("end")])
        return Flow([_ordered({**value[0], "route": Atom("+")}), *value[1:]])

    def _restart_rows(self, value, where: str) -> list:
        """The internal rows of an instrument's restart, its name resolved."""
        rows = list(self.defined[str(value)][2] if isinstance(value, Atom) else value)
        if len(rows) < 2 or rows[-1] != "end":
            raise GenericError(f"{where}.restart: {rows} is not a restart program")
        return rows

    def _restart(self, rows: list, ins: dict, where: str) -> Flow:
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

    def _phrase_uses(self, rows) -> dict:
        """``{x name: command}`` of the expressions phrases name."""
        uses: dict[str, set] = {}
        for atom in self._atoms(rows):
            for cmd, name in _XREF.findall(atom):
                uses.setdefault(name, set()).add(cmd)
        mixed = {n: sorted(c) for n, c in uses.items() if len(c) > 1 and "freq" in c}
        if mixed:
            raise GenericError(f"expressions {mixed} drive both a frequency and another cell")
        return {n: next(iter(c)) for n, c in uses.items()}

    @staticmethod
    def _atoms(rows):
        """Every event atom of the song's phrases, as text."""
        for item in rows:
            for atoms in ((item.get("def") or {}).get("phrase") or {}).values():
                yield from (str(a) for a in atoms)

    def _freq_offsets(self, rows) -> None:
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
                            made[n] = self.fresh("x", self.expr(n, freq=True, cell="freq"))
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
        cmds[0].update(self._after_init())
        if self.init_tempo is not None:
            cmds[0]["tempo"] = self.init_tempo
        for kind in INIT_TYPES:
            for index, rec in (fixed.get(kind) or {}).items():
                voice = int(index) if kind == "vinit" else 0
                for key, value in rec.items():
                    cmds[voice].update(self._init_command(kind, index, key, value))
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

    def _init_command(self, kind: str, index, key: str, value) -> dict:
        """``{command: argument}`` of a fixed record's key; a note is a legato note."""
        if kind == "vinit" and key == "note":
            return {"legato": None}
        if key in ("pw", "cut", "freq"):
            freq = key == "freq"
            if isinstance(value, int) and not (freq and self.g is not None):
                return {key: value}
            return {key: self.fresh("x", self.expr(value, freq, cell=key))}
        if key in ("mode", "vol"):
            return {key: value}
        raise GenericError(f"{kind} {index}: {key} has no init command")

    def _after_init(self) -> dict:
        """The init commands of the global registers the native player sets in its init."""
        regs = self.native.init
        if regs is None:
            return {}
        res, route, mode, vol = regs[0x17] >> 4, regs[0x17] & 15, regs[0x18] & 0xF0, regs[0x18] & 15
        cut = regs[0x16] << 3 | regs[0x15] & 7
        out = {"resfilt": f"{res}/{route}"} if res or route else {}
        out.update((k, v) for k, v in (("mode", mode), ("vol", vol), ("cut", cut)) if v)
        return out

    def _tempo_pair(self) -> tuple[int, int]:
        """The song's initial tempo ``(a, b)``; a playroutine without a tempo steps each tick."""
        value = self.ir.get(tempo.NAME, tempo.default(self.pr))
        if value is None:
            value = 6 if self.pr.get(tempo.NAME) else 1
        return tempo.pair(value)

    def _lead_in(self, rows: list) -> dict:
        """The row before the song's first that delays it ``lead`` ticks, the native player's
        start: a step the init row times, then a step whose event restores the song's tempo,
        or that event on the song's first step where the lead is shorter than a step."""
        a, b = self._tempo_pair()
        back = f"{b}/{a}" if a != b else str(a)
        atoms = [Atom("-")]
        if self.lead > b:
            self.init_tempo = str(self.lead - b)
            atoms.append(Atom(f"-!tempo:{back}"))
        elif self.lead == b:
            self.init_tempo = back if a != b else None
        else:
            self.init_tempo = str(self.lead)
            self._restore(rows, f"{a}/{b}" if a != b else str(a))
        lead = self.fresh("phrase", Flow(atoms))
        defs = {"phrase": {lead: self.added["phrase"].pop(lead)}}
        return {"def": defs, "play": Flow([lead] * self.voices)}

    def _restore(self, rows: list, value: str) -> None:
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

    def _names(self, rows: list) -> None:
        """Every definition of the song by name, and each prefix's highest number."""
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

    def __call__(self, doc: dict) -> dict:
        rows = songio.song_rows(doc)
        self._names(rows)
        uses = self._phrase_uses(rows)
        self.runs = {n for a in self._atoms(rows) for n in _RUN.findall(a)} | {
            str(d["run"]) for d in _dicts(rows) if "run" in d
        }
        if self.g is not None:
            self._freq_offsets(rows)
        lead = self._lead_in(rows) if self.lead else None
        init = self.init(rows[0].get("def") or {}) if rows else None
        for item in rows:
            for kind, entries in (item.get("def") or {}).items():
                self._section(kind, entries, uses)
        self._own_timing(rows, init)
        self._settle(rows)
        if lead is not None:
            doc["song"].insert(0, lead)
            if isinstance(doc["song"][-1], dict):
                doc["song"][-1] = FlowMap(loop=doc["song"][-1]["loop"] + 1)
        return self.header(doc, init)

    def _settle(self, rows: list) -> None:
        """The rows without the restarts the instruments now hold and without empty
        definitions, the first row with the definitions the respelling adds."""
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

    def _own_timing(self, rows: list, init) -> None:
        """Each event of :attr:`Timing.events` with its own re-timed expressions: a copy of the
        program it runs or of its instrument, or its command naming the expression, in place
        of its command; a definition no event names any more is dropped."""
        made: dict = {}
        sections = [item.get("def") or {} for item in rows] + [self.added]
        for entries in (d.get("phrase") or {} for d in sections):
            for name, atoms in entries.items():
                original, copied = [str(a) for a in atoms], []
                for i in range(len(original)):
                    owns = self.timing.events.get((name, i), {})
                    for key, own in owns.items():
                        if own[2] != COPY:
                            atoms[i] = self._own(Atom(atoms[i]), key, own, made)
                    owns = {k: o for k, o in owns.items() if o[2] == COPY}
                    if owns:
                        before = atoms[i]
                        atoms[i] = self._own_ins(Atom(before), owns, made, (original, i))
                        copied += [i] if atoms[i] != before else []
                self._restore_ins(original, atoms, copied)
        named = {n for text in _values([rows, init, self.added]) for n in _NAMES.findall(text)}
        for defs in sections:
            for kind, entries in list(defs.items()):
                unnamed = [n for n in entries if str(n) in self.replaced - named]
                for name in unnamed:
                    del entries[name]
                if unnamed and not entries and defs is not self.added:
                    del defs[kind]

    def _own_ins(self, atom: Atom, owns: dict, made: dict, at: tuple) -> Atom:
        """Event ``atom`` playing a copy of its instrument with its own re-timings ``owns``,
        ``{cell key: (text, Schedule, how)}``; ``at`` is the phrase's atoms and its index."""
        ins = _instrument_at(*at)
        owns = {k: o for k, o in owns.items() if retime.retimed(o[0], o[1]) is not None}
        if ins is None or at[1] + 1 == len(at[0]) or not owns:
            return atom
        key = (ins, tuple(sorted(owns.items())))
        if key not in made:
            made[key] = self._ins_copy(ins, owns)
        return atom if made[key] is None else _with_ins(atom, made[key])

    def _own(self, atom: Atom, key: str, own: tuple, made: dict) -> Atom:
        """Event ``atom`` playing its own re-timing ``own`` of cell ``key``'s expression."""
        text, schedule, how = own
        if retime.retimed(text, schedule) is None:
            return atom
        if how == "run":
            match = re.search(rf"!run:({PREFIX['pw' if key == 'pw' else 'filter']}\d+)", atom)
            if match is None:
                return atom
            copy = made.get((match.group(1), schedule))
            if copy is None:
                copy = made[(match.group(1), schedule)] = self._run_copy(match.group(1), own)
            if copy is None:
                return atom
            self.replaced.add(match.group(1))
            return Atom(atom.replace(match.group(0), f"!run:{copy}"))
        x = made.get((key, own))
        if x is None:
            value = self.expr(text, key == "freq", how in (NOTED, "instrument"), key, schedule)
            x = made[(key, own)] = self.fresh("x", value)
        self.replaced.update(re.findall(rf"!{key}:(x\d+)", atom))
        atom = re.sub(rf"!{key}:[^!*]+", f"!{key}:{x}", atom)
        return Atom(with_command(atom, key, str(x)))

    def _ins_copy(self, name: str, owns: dict) -> Atom | None:
        """A copy of instrument ``name`` each of whose fields of ``owns``, a program or
        expression whose first row plays its text, plays its re-timing; None where none does."""
        value = dict(self.defined[name][2])
        self.forced = {}
        for key, (text, schedule, _) in owns.items():
            program = value.get(FIELD[key])
            if isinstance(program, Atom) and _NAME.fullmatch(program):
                program = value[FIELD[key]] = self.defined[str(program)][2]
            first = program[0] if isinstance(program, list) and program else program
            if isinstance(first, dict):
                first = first.get(key)
            if first is not None and not isinstance(first, (list, dict)):
                if _canonical(first) == text:
                    self.forced[(key, text)] = schedule
        if not self.forced:
            return None
        try:
            return self.fresh("ins", self.instrument(value, f"ins {name}"))
        finally:
            self.forced = {}

    @staticmethod
    def _restore_ins(original: list, atoms: list, copied: list) -> None:
        """After each event of ``copied``, given an instrument copy, the next note naming the
        instrument it played, or the phrase's last event where no note follows."""
        for i in copied:
            later = [j for j in range(i + 1, len(original)) if _is_note(original[j])]
            j = later[0] if later else len(original) - 1
            if _HEAD.match(original[j]).group(2) is None and j not in copied:
                atoms[j] = _with_ins(Atom(atoms[j]), _instrument_at(original, i))

    def _run_copy(self, name: str, own: tuple) -> Atom | None:
        """A copy of pulse or filter program ``name`` whose first row's expression, ``own``'s
        text, plays its re-timing; None where the program's first row does not hold it."""
        text, schedule, _ = own
        _, kind, value = self.defined[name]
        found = retime.retimed(text, schedule)
        field_ = "pw" if kind == "pw" else "cut"
        if isinstance(value, list):
            first = value[0] if value else None
            if not isinstance(first, dict) or _canonical(first.get(field_, "")) != text:
                return None
            value = [{**first, field_: found}, *value[1:]]
        elif _canonical(value) != text:
            return None
        else:
            value = found
        if found is None:
            return None
        return self.fresh(kind, self.program(value, f"{kind} {name}", kind))

    def _section(self, kind: str, entries: dict, uses: dict) -> None:
        """A row's definitions of ``kind`` respelled in place."""
        if kind not in SECTIONS:
            names = ", ".join(str(n) for n in entries)
            raise GenericError(f"record type {kind} ({names}) has no generic form")
        for name, value in list(entries.items()):
            where = f"{kind} {name}"
            if kind == "ins":
                entries[name] = self.instrument(value, where)
            elif kind == "x":
                use = uses.get(str(name))
                entries[name] = self.expr(value, use == "freq", cell=use)
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
        value = tempo.written(*self._tempo_pair())
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


def untimed(ir: dict, native: Native) -> tuple[str, int]:
    """``(text, lead)``: the generic document of ``ir`` with its expressions over the native
    firings, and the ticks its lead-in delays it (see :func:`_lead`)."""
    text = songio.dumps(ir, embed=True, respell=_Respell(ir, native))
    lead = _lead(text, native.starts, ONSET_LAG.get(ir["playroutine"]["name"], 0))
    if lead:
        text = songio.dumps(ir, embed=True, respell=_Respell(ir, native, lead))
    return text, lead


def dumps(ir: dict, native: Native | None = None) -> str:
    """The generic document of internal song ``ir``, which parses with ``unitracker_grammar``:
    :func:`untimed`, its expressions re-timed to the ticks its modulators play (:func:`timing`).

    Raises :class:`GenericError` naming the record and row the document cannot spell.
    """
    native = native_state(ir) if native is None else native
    text, lead = untimed(ir, native)
    retimed = timing(native, text) if native.fired else Timing()
    if retimed.base or retimed.events:
        text = songio.dumps(ir, embed=True, respell=_Respell(ir, native, lead, retimed))
    try:
        parse(text)
    except CanonicalError as exc:
        raise AssertionError(f"the generic writer wrote text outside the grammar: {exc}") from exc
    return text
