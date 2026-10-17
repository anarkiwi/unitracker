"""Lift-time conversion of a song's native modulations to the format's expressions.

The song is replayed until its state recurs, and one period more. Each occurrence's start is
derived and proved (in parallel, cached by content) and its expression written where its data
lives: a record site (a program's first row, an instrument field) or the event's ``!freq``.
"""

import ast
import copy
import dataclasses
import importlib.util
import json
import pickle
from pathlib import Path

import sympy

from unitracker.format.codec import evaluate
from unitracker.format.records import chained, program_path, record_types
from unitracker.format.songio import codecs
from unitracker.format.xexpr import ExpressionError, source
from unitracker.modfit import grammar as G
from unitracker.modfit.exprs import CHUNK, Analysis, Pool, _cached, content_key
from unitracker.modfit.replay import Replay, Stepper
from unitracker.player import reach
from unitracker.player.modulation import declared
from unitracker.player.vm import SongPlayer, first_mismatch, fold_empty

CACHE = Path("out/modfit/cache")
IDENTITY = "v"
_HERE = Path(__file__).resolve().parent
SOURCES = [
    _HERE / f
    for f in (
        "convert.py",
        "exprs.py",
        "phase.py",
        "grammar.py",
        "form.py",
        "chipinv.py",
        "invariant.py",
    )
] + [
    Path(importlib.util.find_spec(f"unitracker.{m}").origin)
    for m in ("player.modulation", "player.vm", "format.records", "format.codec")
]


class ConversionError(ValueError):
    """A modulation the format's expressions do not cover."""


def expand_repeats(ir: dict) -> dict:
    """``ir`` with each repeated play written out, so every play is one phrase run."""
    out = copy.deepcopy(ir)
    for track in out["tracks"]:
        plays, index = [], {}
        for i, play in enumerate(track["plays"]):
            index[i] = len(plays)
            one = {k: v for k, v in play.items() if k != "repeat"}
            plays += [dict(one) for _ in range(int(play.get("repeat", 1)))]
        if track.get("loop") is not None:
            track["loop"] = index[track["loop"]]
        track["plays"] = plays
    return out


class Observer(Replay):
    """A native replay that records the event instance each occurrence belongs to.

    An instance is ``(voice, play count, step)``; ``events`` maps ``(unit, instance)`` to the
    occurrence a start hook of the unit was applying when that event was the voice's last.
    """

    def __init__(self, ir: dict):
        super().__init__(ir, lambda unit, occ: None)
        voices = len(self.player.voice_cells)
        self.count, self.last = [0] * voices, [None] * voices
        self.plays = [[] for _ in range(voices)]
        self.hooked, self.started, self.events = {}, {}, {}
        fetch = self.ns["_fetch"]

        def observed(v):
            voice, cursor = self.voice_of[id(v)], v[-1]
            self.last[voice] = None
            if not cursor.ended:
                if cursor.step == 0:
                    self.plays[voice].append(cursor.index)
                    self.count[voice] += 1
                self.last[voice] = (voice, self.count[voice] - 1, cursor.step)
            fetch(v)

        self.ns["_fetch"] = observed

    def applied(self, unit: dict, voice) -> None:
        self.hooked[(unit["name"], voice)] = None if voice is None else self.last[voice]

    def restart(self, unit: dict, voice, hook: tuple) -> None:
        super().restart(unit, voice, hook)
        key = (unit["name"], None if unit.get("scope") == "global" else voice)
        self.started[key] = None if voice is None else self.last[voice]

    def open(self, unit: dict, name: tuple, voice, snap: dict, before: int) -> None:
        super().open(unit, name, voice, snap, before)
        occ = self.active[name]
        occ.instance, occ.index = self.started.pop(name, None), len(self.occurrences) - 1

    def fired(self, unit: dict, occ) -> None:
        inst = self.hooked.pop((unit["name"], occ.voice), None)
        if inst is not None:
            self.events.setdefault((unit["name"], inst), occ.index)


def ir_key(ir: dict) -> str:
    """The content key of an internal song and the sources its conversion depends on."""
    text = json.dumps(ir, sort_keys=True, default=repr)
    return content_key(text, *(p.read_text() for p in SOURCES))


def window(ir: dict) -> int:
    """Ticks that play every state the song reaches twice: to its recurrence and a period more."""
    _, ticks, period = reach.table_reads(ir)
    return ticks + period


def observe(ir: dict, ticks: int, cache: Path | None) -> Observer:
    """The song's native occurrences and event attribution over ``ticks``, cached by content."""

    def compute():
        obs = Observer(ir)
        obs.run(ticks, desc="starts")
        return {k: getattr(obs, k) for k in ("occurrences", "events", "plays")}

    where = cache / f"convert-observe-{ir_key(ir)}-{ticks}.pkl" if cache else None
    return _cached(where, compute, _load, _save)


def _load(path: Path):
    return pickle.loads(path.read_bytes())


def _save(path: Path, obj) -> None:
    path.write_bytes(pickle.dumps(obj))


_ANALYSES = {}


def _analysis(ir: dict, key: str) -> Analysis:
    if key not in _ANALYSES:
        _ANALYSES[key] = Analysis(ir)
    return _ANALYSES[key]


def keys_task(ir: dict, key: str, occurrences: list) -> list:
    """``(start key, error)`` of a chunk of occurrences."""
    an = _analysis(ir, key)
    out = []
    for occ in occurrences:
        start = an.key(an.unit(occ.unit), occ)
        out.append((start, occ.error))
    return out


def derive_task(ir: dict, key: str, start: tuple, cache: Path | None):
    """The proved expressions of one start, cached by content."""
    where = cache / "derived" / f"{content_key(key, repr(start))}.pkl" if cache else None
    return _cached(where, lambda: _analysis(ir, key).derived(start), _load, _save)


def checked(derived, a, f) -> str:
    """The grammar text of value update ``f`` with phase update ``a``: one per firing."""
    if derived.order.kind != "time" or int(a) != int(G.PHI in f.free_symbols):
        raise ConversionError(f"phase {G.text(a)} is not one per firing: {G.text(f)}")
    return G.text(f)


def groups(occs: list) -> dict:
    """``{(unit, label): [occurrence index]}`` in order of first start."""
    out = {}
    for i, occ in enumerate(occs):
        out.setdefault((occ.unit, occ.label), []).append(i)
    return out


def carried(an: Analysis, occ, a, f) -> int:
    """The constant a start carries in: ``f``'s first value less the player's first value."""
    if occ.first is None:
        return 0
    st = Stepper(a, f, an.tables, an.freq, an.offset)
    st.note = occ.start.get(an.spec["notes"][0], 0) - an.shift(occ.start)
    return st.step(occ.v_in, 0)[0] - occ.first


def with_carried(an: Analysis, occ, d, pair: tuple) -> tuple:
    """``(a, f)`` of an occurrence: ``pair`` with the constant its start carries in, ``f - c·[φ <
    1]``; a closed form that misses its first value gives way to the recurrence."""
    c = carried(an, occ, *pair)
    if c and d.closed is not None and pair[1] is not d.value:
        pair = (d.phase, d.value)
        c = carried(an, occ, *pair)
    if not c:
        return pair
    return sympy.Integer(1), pair[1] - c * G.lt(G.PHI, 1)


def site_of(spec: dict, occ) -> tuple | None:
    """``(record, index)`` where unit ``spec`` writes an occurrence's expression, or None for
    its event's command."""
    site = spec.get("site")
    if site is None or ("when" in site and not evaluate(site["when"], occ.start)):
        return None
    return site["record"], evaluate(site["index"], occ.start)


def falls_back(spec: dict) -> bool:
    """True when unit ``spec``'s selector names its site's record field itself: a step that
    restarts the unit without its command selects that record's expression, so a start the
    site does not cover is the command on each step it spans, not on its first alone."""
    site = spec.get("site")
    if not site or "x" not in spec:
        return False
    return any(
        isinstance(n, ast.Attribute)
        and n.attr == site["field"]
        and isinstance(n.value, ast.Subscript)
        and ast.unparse(n.value.value) == site["record"]
        and ast.unparse(n.value.slice) == str(site["index"])
        for n in ast.walk(ast.parse(str(spec["x"]), mode="eval"))
    )


def diagnosis(spec: dict, occ) -> str:
    """The unit, program and start of an occurrence, as a lift error names them."""
    where = site_of(spec, occ)
    program = f"{where[0]} {where[1]}" if where else f"the event's !{spec['key']}"
    cells = ", ".join(f"{c} {v}" for c, v in zip(spec.get("label") or [], occ.label))
    return f"{occ.unit} program {program}, start [{cells}] at tick {occ.tick}"


def group_texts(an: Analysis, occs: list, found: dict) -> list:
    """Each occurrence's expression text (None: no modulation), by the canonical rule.

    Within a unit and label the shared closed form, else the shared recurrence, else each
    start's own; a start that carries a constant in adds it at its first firing.
    """
    # pylint: disable=too-many-locals
    for occ in occs:
        if occ.key is None:
            raise ConversionError(f"{diagnosis(an.unit(occ.unit), occ)}: {occ.error}")
        d, _ = found[occ.key]
        if d.error or not d.exact:
            raise ConversionError(
                f"{diagnosis(an.unit(occ.unit), occ)}: not proved: {d.error or d.proof}"
            )
    out = [None] * len(occs)
    for members in groups(occs).values():
        live = {occs[i].key: found[occs[i].key][0] for i in members}
        live = {k: d for k, d in live.items() if not d.identity}
        closed = {G.text(d.closed) for d in live.values() if d.closed is not None}
        recur = {G.text(d.value) for d in live.values()}
        shared, first = None, next(iter(live.values()), None)
        if len(closed) == 1 and all(d.closed is not None for d in live.values()):
            shared = first.update
        elif len(recur) == 1 and live:
            shared = (first.phase, first.value)
        once = bool((an.unit(occs[members[0]].unit).get("site") or {}).get("row"))
        for i in members:
            d = found[occs[i].key][0]
            if not d.identity:
                a, f = with_carried(an, occs[i], d, shared or d.update)
                out[i] = G.text(f.subs(G.PHI, 0)) if once else checked(d, a, f)
    tables = set((declared(an.ir["playroutine"]).get("tables") or {}))
    for text in {t for t in out if t is not None}:
        try:
            source(text, tables)
        except ExpressionError as exc:
            raise ConversionError(f"{text}: outside the fixed grammar: {exc}") from exc
    return out


def derive_all(ir: dict, obs: dict, cache: Path | None, workers=None) -> tuple:
    """``(start derivations, each occurrence's text)``, every distinct start derived in parallel."""
    # pylint: disable=too-many-locals
    key = ir_key(ir)
    occs = obs["occurrences"]
    chunks = [occs[i : i + CHUNK] for i in range(0, len(occs), CHUNK)]
    pool = Pool(workers, max(len(chunks), 1))
    try:
        got = pool.map(keys_task, [(ir, key, c) for c in chunks], "start keys")
        for chunk, keys in zip(chunks, got):
            for occ, (start, error) in zip(chunk, keys):
                occ.key, occ.error = start, error
        starts = list(dict.fromkeys(o.key for o in occs if o.key is not None))
        found = dict(
            zip(starts, pool.map(derive_task, [(ir, key, s, cache) for s in starts], "derivations"))
        )
    finally:
        pool.close()
    return found, group_texts(_analysis(ir, key), occs, found)


# ---- rewriting -----------------------------------------------------------------------


def _program(rows: dict, rtype, entry: int) -> tuple:
    """``(row indices, loop position or None)`` of a program, a missing row read as defaults."""
    return program_path(rtype, lambda i: {**rtype.fields, **rows.get(i, {})}, entry)


class Rewrite:  # pylint: disable=too-many-instance-attributes
    """A song's modulations rewritten as expressions, from its occurrences and their texts."""

    def __init__(self, ir: dict, obs: dict, texts: list):
        self.ir = copy.deepcopy(ir)
        self.pr = self.ir["playroutine"]
        self.rtypes = record_types(self.pr)
        self.spec = declared(self.pr)
        self.units = {u["name"]: u for u in self.spec.get("units") or []}
        self.obs, self.texts = obs, texts
        self.exprs = {int(i): str(t) for i, t in (self.ir.get("exprs") or {}).items()}
        self.xindex = {t: i for i, t in sorted(self.exprs.items(), reverse=True)}
        self.events = {}
        self.instrument = self.pr.get("instrument") or {}
        self.records = self.ir.setdefault("records", {})
        self.codecs = codecs(self.pr, self.ir.get("tables"))

    def x(self, text: str) -> int:
        """The index of an expression text, after the song's own expressions."""
        if text not in self.xindex:
            self.xindex[text] = max(self.exprs, default=0) + 1
            self.exprs[self.xindex[text]] = text
        return self.xindex[text]

    def site(self, occ):
        """``(record, index)`` of the record an occurrence's expression is written in, or None."""
        return site_of(self.units[occ.unit], occ)

    def run(self) -> dict:
        """The rewritten internal song."""
        occs = self.obs["occurrences"]
        sites = {}
        for i, occ in enumerate(occs):
            where = self.site(occ)
            if where is not None:
                sites.setdefault(where, {}).setdefault(occ.unit, {}).setdefault(
                    self.texts[i], []
                ).append(occ)
        for where, by_unit in sites.items():
            self.record_site(where, by_unit)
        self.event_sites()
        self.retire()
        self.phrases()
        self.ir["exprs"] = self.exprs
        self.check_shared({s["record"] for u in self.units.values() if (s := u.get("site"))})
        return self.ir

    # -- record sites --
    def _unshared(self, name: str, entry: int) -> tuple | None:
        """``(row position, row)`` of the first row of program ``entry`` that has no shared
        form, or None."""
        rtype, codec = self.rtypes[name], self.codecs.get(name)
        if rtype.chain is None or codec is None:
            return None
        rows = self.records.get(name, {})
        path, loop = _program(rows, rtype, entry)
        own = rtype.body(rows.get(path[0], {}))
        if rtype.expr is not None and len(path) == 1 and loop is None and own.get(rtype.expr):
            if all(v == rtype.default(f) for f, v in own.items() if f != rtype.expr):
                return None
        for k, i in enumerate(path):
            place = {"first": int(k == 0), "last": int(loop is None and k + 1 == len(path))}
            if codec.encode({**rtype.fields, **rows.get(i, {})}, place) is None:
                return k, rtype.body(rows.get(i, {}))
        return None

    def check_shared(self, names: set) -> None:
        """Raise, naming the program and row, for a program of a site type the rewritten song
        references whose rows have no shared form: no start proved an expression for it."""
        for name in sorted(n for n in names if self.rtypes[n].chain is not None):
            units = [
                n for n, u in self.units.items() if (u.get("site") or {}).get("record") == name
            ]
            for entry in sorted(self._referenced(name)):
                bad = self._unshared(name, entry)
                if bad is not None:
                    raise ConversionError(
                        f"{name} program {entry}: row {bad[0]} {bad[1]} has no shared form, "
                        f"and no start of {', '.join(units)} proves its expression"
                    )

    def _referenced(self, name: str) -> set:
        """The entries of record type ``name`` that records, phrases or pins reference."""
        rtype = self.rtypes[name]
        out = set(rtype.pin) | {int(row[field]) for row, field in self._referrers(name)}
        codes = self.commands(name)
        for events in self.ir["phrases"].values():
            out |= {int(c[1]) for e in events for c in _cmds(e) if int(c[0]) in codes}
        return out - ({rtype.none} if rtype.none is not None else set())

    def record_site(self, where: tuple, by_unit: dict) -> None:
        """Write a record's units' expressions; a unit with several gets one copy per text.

        A unit whose site is a program row writes every start's text there, the identity too.
        """
        rowed = {u for u in by_unit if self.units[u]["site"].get("row")}
        texts = {
            u: {(IDENTITY if t is None and u in rowed else t): o for t, o in got.items()}
            for u, got in by_unit.items()
        }
        texts = {u: {t: o for t, o in got.items() if t is not None} for u, got in texts.items()}
        texts = {u: got for u, got in texts.items() if got and not self.native(where, u, got)}
        if not texts:
            return
        many = [u for u, got in texts.items() if len(got) > 1]
        if len(many) > 1:
            raise ConversionError(f"{where}: units {many} each need several expressions")
        if set(many) & rowed:
            raise ConversionError(
                f"{where[0]} row {where[1]}: its runs need several {self.units[many[0]]['key']}"
                f" expressions: {', '.join(sorted(texts[many[0]]))}"
            )
        single = {u: next(iter(got)) for u, got in texts.items() if u not in many}
        if not many:
            self.redirect(where, self.written(where, single))
            return
        unit = many[0]
        ordered = sorted(texts[unit].items(), key=lambda kv: (-len(kv[1]), kv[0]))
        for text, occs in ordered[1:]:
            clone = self.written(where, {**single, unit: text}, copy_row=True)
            for occ in occs:
                self.select(occ, where, clone)
        self.redirect(where, self.written(where, {**single, unit: ordered[0][0]}))

    def native(self, where: tuple, unit: str, texts: dict) -> bool:
        """True when a unit's constant values stay the program's own rows: rows with other data
        that the format writes as they are."""
        name, index = where
        rtype, rows = self.rtypes[name], self.records.get(name, {})
        spec = self.units[unit]["site"]
        if rtype.chain is None or spec.get("row") or not all(_integer(t) for t in texts):
            return False
        path, _ = _program(rows, rtype, index)
        full = [{**rtype.fields, **rows.get(i, {})} for i in path]
        if not any(self.effective(rtype, {**r, **self.cleared(spec, r)}) for r in full):
            return False
        codec = self.codecs.get(name)
        return codec is None or all(codec.encode(r) is not None for r in full)

    def written(self, where: tuple, texts: dict, copy_row: bool = False) -> int:
        """The record (a program copy, or one row) with the units' expressions written."""
        # pylint: disable=too-many-locals
        name, index = where
        rtype, rows = self.rtypes[name], self.records.setdefault(name, {})
        specs = [self.units[u]["site"] for u in texts]
        if rtype.chain is None or all(s.get("row") for s in specs):
            row = dict(rows.get(index, {}))
            for unit, spec in zip(texts, specs):
                if index not in rtype.pin:
                    row.update(self.cleared(spec, {**rtype.fields, **row}))
                row[spec["field"]] = self.x(texts[unit])
            if copy_row:
                return self.allocate(rtype, [row])
            rows[index] = row
            return index
        path, loop = _program(rows, rtype, index)
        out = []
        for k, i in enumerate(path):
            row = rtype.body(rows.get(i, {}))
            for spec in specs:
                row.update(self.cleared(spec, {**rtype.fields, **row}))
                if k == 0:
                    row.update({f: int(v) for f, v in (spec.get("first") or {}).items()})
            out.append(row)
        for unit, spec in zip(texts, specs):
            out[0][spec["field"]] = self.x(texts[unit])
        live = [k for k, row in enumerate(out) if self.effective(rtype, row)]
        last = live[-1] if live else 0
        if loop is None or loop > last:
            out, loop = out[: last + 1], None
        if loop is None and rtype.end:
            out[-1].update({f: int(v) for f, v in rtype.end.items()})
        return self.allocate(rtype, out, loop)

    def retire(self) -> None:
        """Clear, in every row of its record, the native data a unit's expressions replace."""
        for unit in self.units.values():
            spec = unit.get("retire")
            if spec:
                rtype = self.rtypes[spec["record"]]
                for row in self.records.get(rtype.name, {}).values():
                    row.update(self.cleared(spec, {**rtype.fields, **row}))

    @staticmethod
    def cleared(spec: dict, row: dict) -> dict:
        """A row's fields with the unit's data cleared."""
        return {f: int(evaluate(str(e), row)) for f, e in (spec.get("clear") or {}).items()}

    @staticmethod
    def effective(rtype, row: dict) -> bool:
        """True when a row sets anything: a field off its default."""
        return any(row.get(f, d) != d for f, d in rtype.fields.items() if f != rtype.chain)

    def allocate(self, rtype, rows: list, loop=None) -> int:
        """New rows of a record type, chained as a program when it is one; the first index."""
        table = self.records.setdefault(rtype.name, {})
        start = rtype.free(table)
        if start + len(rows) - 1 > rtype.max:
            raise ConversionError(f"{rtype.name}: more than {rtype.max} rows")
        chain = rtype.chain is not None
        table.update(chained(rtype, rows, start, loop) if chain else dict(enumerate(rows, start)))
        return start

    def _referrers(self, name: str):
        """``(row, field)`` of each record field (other than a program's chain) that references
        record type ``name``."""
        for tname, rows in self.records.items():
            rtype = self.rtypes.get(tname)
            for row in rows.values() if rtype is not None else ():
                for field, targets in rtype.refs.items():
                    if field == rtype.chain or field not in row:
                        continue
                    full = {**rtype.fields, **row}
                    if any(t == name and (w is None or evaluate(w, full)) for t, w in targets):
                        yield row, field

    def redirect(self, where: tuple, new: int) -> None:
        """Every reference to a record (other than a program's chain) to its rewritten copy."""
        name, old = where
        for row, field in self._referrers(name):
            if row[field] == old:
                row[field] = new
        commands = self.commands(name)
        for events in [*self.ir["phrases"].values(), list(self.events.values())]:
            for event in events:
                self.point(event, name, old, new, commands)

    def commands(self, name: str) -> set:
        """The command codes whose argument references record type ``name``."""
        return {
            int(code)
            for code, var in (self.pr.get("commands") or {}).items()
            for v in (var if isinstance(var, list) else [var])
            if v.get("ref") == name
        }

    def point(self, event: dict, name: str, old: int, new: int, commands: set) -> bool:
        """Make an event's reference to record ``old`` of ``name`` reference ``new``."""
        hit = False
        base = int(self.instrument.get("base", 0))
        if self.instrument.get("type") == name and event.get("instrument") == old + base:
            event["instrument"], hit = new + base, True
        cmds = event.get("commands") or ([event["command"]] if event.get("command") else [])
        for cmd in cmds:
            if int(cmd[0]) in commands and int(cmd[1]) == old:
                cmd[1], hit = new, True
        return hit

    def select(self, occ, where: tuple, clone: int) -> None:
        """Make the event that started ``occ`` select record copy ``clone``."""
        if occ.instance is None:
            raise ConversionError(f"{occ.unit} at tick {occ.tick}: no event selects {where}")
        event = self.edit(occ.instance)
        if not self.point(event, where[0], where[1], clone, self.commands(where[0])):
            raise ConversionError(f"{occ.unit} at tick {occ.tick}: its event does not name {where}")

    # -- event sites --
    def event_at(self, instance: tuple) -> tuple:
        """``(phrase name, step)`` of an instance, or None past its phrase's end."""
        voice, count, step = instance
        play = self.ir["tracks"][voice]["plays"][self.obs["plays"][voice][count]]
        if step >= len(_steps(self.ir["phrases"][play["phrase"]])):
            return None
        return play["phrase"], step

    def _step_event(self, where: tuple) -> dict:
        """The event a phrase plays on a step, an empty one inside a folded event."""
        return _steps(self.ir["phrases"][where[0]])[where[1]]

    def edit(self, instance: tuple) -> dict:
        """The instance's own copy of its step's event, to be written into its phrase's variant."""
        if instance not in self.events:
            where = self.event_at(instance)
            if where is None:
                raise ConversionError(f"no step plays at {instance}")
            self.events[instance] = copy.deepcopy(self._step_event(where))
        return self.events[instance]

    def event_sites(self) -> None:
        """Each event's native modulator commands as its expression's ``!freq`` command, and each
        commandless step a driven occurrence spans after the voice's first note."""
        # pylint: disable=too-many-locals
        occs = self.obs["occurrences"]
        legato = self.spec.get("legato") or {}
        for (unit, instance), i in sorted(self.obs["events"].items(), key=repr):
            spec, occ, text = self.units[unit], occs[i], self.texts[i]
            if "command" not in spec or self.site(occ) is not None:
                continue
            where = self.event_at(instance)
            if where is None:
                continue
            event = self._step_event(where)
            native = [c for c in _cmds(event) if int(c[0]) in spec.get("commands", ())]
            started = occ.instance == instance
            onward = (
                falls_back(spec) and not _cmds(event) and occ.start.get(self.pr.get("onset"), 1)
            )
            if not (native or (text is not None and (started or onward))):
                continue
            ev = self.edit(instance)
            cmds = [c for c in _cmds(ev) if int(c[0]) not in spec.get("commands", ())]
            if (
                started
                and isinstance(ev.get("note"), int)
                and legato
                and occ.start.get(legato["cell"])
                and [legato["command"], 0] not in cmds
            ):
                cmds.insert(0, [legato["command"], 0])
            index = self.x(text or IDENTITY)
            if index > 0xFF:
                raise ConversionError(f"!freq names expression {index}: an argument is a byte")
            cmds.append([spec["command"], index])
            _set_cmds(ev, cmds)

    # -- phrases and tracks --
    def phrases(self) -> None:
        """Phrase variants for instances whose events changed, tracks unrolled where needed."""
        # pylint: disable=too-many-locals
        variants, names = {}, {}
        per = {}
        for (voice, count, step), event in self.events.items():
            per.setdefault((voice, count), {})[step] = event
        for voice, track in enumerate(self.ir["tracks"]):
            counts = self.obs["plays"][voice]
            n, loop = len(track["plays"]), track.get("loop")
            by_pos = {}
            # A last pass the window cuts short repeats a whole pass a period earlier.
            last = len(counts) - 1 if counts.count(counts[-1]) > 1 else len(counts)
            for count, pos in enumerate(counts[:last]):
                cycle = 0 if count < n else 1 + (count - n) // max(1, n - (loop or 0))
                edits = per.get((voice, count), {})
                frozen = tuple(sorted((s, _frozen(e)) for s, e in edits.items()))
                key = (track["plays"][pos]["phrase"], frozen)
                by_pos.setdefault(pos, []).append((cycle, key, edits))
            plays = [dict(p) for p in track["plays"]]
            tail = None
            for pos, seen in by_pos.items():
                first = seen[0]
                later = {k for c, k, _ in seen if c > 0}
                if len(later) > 1:
                    raise ConversionError(f"voice {voice} play {pos}: its loop passes differ")
                plays[pos]["phrase"] = self.variant(first[1], first[2], variants, names)
                if later and later != {first[1]}:
                    tail = tail or {}
                    edits = next(e for c, k, e in seen if c > 0)
                    tail[pos] = self.variant(next(iter(later)), edits, variants, names)
            if tail is not None:
                extra = []
                for pos in range(loop, n):
                    extra.append({**plays[pos], "phrase": tail.get(pos, plays[pos]["phrase"])})
                plays += extra
                track["loop"] = n
            track["plays"] = plays

    def variant(self, key: tuple, edits: dict, variants: dict, names: dict) -> str:
        """The phrase of ``key``: the phrase itself, or a copy with the instance's events."""
        phrase, changed = key
        if not changed:
            return phrase
        if key not in variants:
            events = copy.deepcopy(_steps(self.ir["phrases"][phrase]))
            for step, event in edits.items():
                events[step] = copy.deepcopy(event)
            names[phrase] = names.get(phrase, 0) + 1
            variants[key] = f"{phrase}~{names[phrase]}"
            self.ir["phrases"][variants[key]] = fold_empty(events)
        return variants[key]


def _integer(text: str) -> bool:
    try:
        int(text)
    except ValueError:
        return False
    return True


def _steps(events: list) -> list:
    """A phrase's events one per step: a folded event's later steps are empty events."""
    out = []
    for event in events:
        out.append({k: v for k, v in event.items() if k != "steps"})
        out += [{} for _ in range(int(event.get("steps", 1)) - 1)]
    return out


def _cmds(event: dict) -> list:
    return [
        list(c)
        for c in (event.get("commands") or ([event["command"]] if event.get("command") else []))
    ]


def _set_cmds(event: dict, cmds: list) -> None:
    event.pop("command", None)
    event.pop("commands", None)
    if len(cmds) > 2:
        raise ConversionError(f"an event needs three commands: {cmds}")
    if len(cmds) == 1:
        event["command"] = cmds[0]
    elif cmds:
        event["commands"] = cmds


def _frozen(event: dict) -> str:
    return json.dumps(event, sort_keys=True)


def convert(ir: dict, cache: Path | None = CACHE, workers=None) -> dict:
    """``ir`` with every native modulation its playroutine declares written as an expression.

    Raises ConversionError when a modulation has no expression or the rewritten song plays
    differently from the native one.
    """
    if not declared(ir["playroutine"]):
        return ir
    ir = expand_repeats(ir)
    where = cache / f"convert-{ir_key(ir)}.pkl" if cache else None
    return _cached(where, lambda: _converted(ir, cache, workers), _load, _save)


@dataclasses.dataclass
class Conversion:
    """A song's conversion: its occurrences, their starts' derivations and texts, the result."""

    ir: dict
    ticks: int
    obs: dict
    found: dict
    texts: list
    out: dict

    @property
    def analysis(self) -> Analysis:
        """The analysis of the song's units."""
        return _analysis(self.ir, ir_key(self.ir))


def analyse(ir: dict, cache: Path | None = CACHE, workers=None) -> Conversion:
    """A song's conversion, verified: its expressions play its native register grid exactly,
    each voice from its first note (see ``first_mismatch``), to its state's recurrence and one
    period more."""
    ir = expand_repeats(ir)
    ticks = window(ir)
    obs = observe(ir, ticks, cache)
    found, texts = derive_all(ir, obs, cache, workers)
    out = Rewrite(ir, obs, texts).run()
    native = SongPlayer(ir)
    want = native.render_grid(ticks)
    bad = first_mismatch(SongPlayer(out).render_grid(ticks), want, native.starts)
    if bad is not None:
        raise ConversionError(f"the expressions play differently: tick {bad[0]}, register {bad[1]}")
    return Conversion(ir, ticks, obs, found, texts, out)


def _converted(ir: dict, cache: Path | None, workers) -> dict:
    """``convert`` of a song with its repeats written out."""
    return analyse(ir, cache, workers).out
