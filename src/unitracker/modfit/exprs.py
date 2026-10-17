"""Every modulator of a song as phase and value expressions, proved and replayed.

Occurrences come from replaying the song: each unit's starts give their control states, the
expressions are derived and proved per distinct start, and the song is replayed with each
labelled modulator's cells driven by its expressions against the native player's grid.
"""

import concurrent.futures
import dataclasses
import functools
import hashlib
import multiprocessing
import os
import pickle
from pathlib import Path

import numpy as np
import sympy
import yaml
from tqdm import tqdm

from unitracker import backends, songio
from unitracker.modfit import grammar as G
from unitracker.modfit import specs
from unitracker.modfit.form import atoms
from unitracker.modfit.phase import Lift, Machine, derive, dispatch, unproved_functions
from unitracker.modfit.inherit import Inherited, inherit
from unitracker.modfit.replay import G_value, Replay, Stepper

TICKS = {"goattracker2": 2 * 3840, "defmon": 95 + 2 * 1152 + 100, "sidwizard": 2304 * 6 + 600}


# ---- the song and its names ------------------------------------------------------------


class _Loader(yaml.SafeLoader):  # pylint: disable=too-many-ancestors
    """A loader keeping flow collections and plain atoms, so the document dumps back as read."""


def _seq(loader, node):
    vals = loader.construct_sequence(node, deep=True)
    return songio.Flow(vals) if node.flow_style else vals


def _map(loader, node):
    vals = loader.construct_mapping(node, deep=True)
    return songio.FlowMap(vals) if node.flow_style else dict(vals)


def _str(loader, node):
    value = loader.construct_yaml_str(node)
    return songio.Atom(value) if node.style is None else value


def _int(loader, node):
    return (
        songio.Atom(node.value) if node.value.startswith("+") else loader.construct_yaml_int(node)
    )


_Loader.add_constructor("tag:yaml.org,2002:seq", _seq)
_Loader.add_constructor("tag:yaml.org,2002:map", _map)
_Loader.add_constructor("tag:yaml.org,2002:str", _str)
_Loader.add_constructor("tag:yaml.org,2002:int", _int)
_Loader.add_constructor("tag:yaml.org,2002:bool", lambda loader, node: songio.Atom(node.value))


def document(text: str):
    """A v1.x document that ``dump`` writes back as ``text``."""
    return yaml.load(text, Loader=_Loader)  # nosec B506 - SafeLoader subclass


def dump(doc) -> str:
    """A document written as ``songio.dumps`` writes it."""
    # pylint: disable=protected-access
    return yaml.dump(
        doc, Dumper=songio._Dumper, sort_keys=False, allow_unicode=True, width=songio.WIDTH
    )


@dataclasses.dataclass
class Song:
    """A tune lifted to v1.4, its internal form, its document and its native grid."""

    path: Path
    text: str
    ir: dict
    doc: object
    raw: bytes

    @property
    def player(self) -> str:
        """The playroutine name."""
        return self.ir["playroutine"]["name"]


LIFTERS = [
    Path(__file__).resolve().parent.parent / f
    for f in ("backends.py", "goattracker.py", "defmon.py", "sidwizard.py", "songio.py", "codec.py")
]


def load(path: Path, cache: Path | None = None) -> Song:
    """A tune (or a v1.x YAML) read for analysis; a lifted tune's text cached by content."""
    raw = path.read_bytes()
    if path.suffix in (".yaml", ".yml"):
        text = raw.decode()
    else:
        parts = (raw, *(p.read_text() for p in LIFTERS), _library_text())
        where = cache / f"lift-{content_key(*parts)}.yaml" if cache else None
        text = _cached(
            where,
            lambda: songio.dumps(backends.lift(raw, None)),
            lambda p: p.read_text(encoding="utf-8"),
            lambda p, t: p.write_text(t, encoding="utf-8"),
        )
    return Song(path, text, songio.loads(text), document(text), raw)


def native_grid(song: Song, ticks: int) -> np.ndarray:
    """The native player's register grid of a tune."""
    # pylint: disable=import-outside-toplevel
    name = song.player
    if name == "goattracker2":
        from pygoattracker import decompile_sid

        grid = decompile_sid(song.raw, subtune=0).player().render_grid(ticks)
    elif name == "defmon":
        from pydefmon import DefmonPlayer

        grid = DefmonPlayer(song.raw).render_grid(ticks)
    else:
        from pysidwizard import SWMPlayer, parse_sid_song

        grid = SWMPlayer(parse_sid_song(song.raw, 1)).render_grid(ticks)
    return np.asarray(grid, dtype=np.uint8)


def atom_commands(atom: str) -> list:
    """The ``!command`` parts of a phrase atom."""
    return ["!" + c for c in str(atom).split("*", maxsplit=1)[0].split("!")[1:]]


def atom_instrument(atom: str) -> str | None:
    """The instrument name of a phrase atom, if any."""
    head = str(atom).split("*", maxsplit=1)[0].split("!", maxsplit=1)[0]
    return head.split(".")[1] if "." in head else None


def event_commands(event: dict) -> list:
    """An internal event's ``(code, arg)`` pairs."""
    cmds = event.get("commands") or ([event["command"]] if event.get("command") else [])
    return [tuple(int(x) for x in c) for c in cmds]


class Names:
    """Document names of a song's commands, instruments and programs, by internal value."""

    def __init__(self, song: Song):
        self.commands, self.instruments, self.defs = {}, {}, {}
        for row in song.doc["song"]:
            for kind, items in (row.get("def") or {}).items():
                for name, value in (items or {}).items():
                    self.defs[str(name)] = (kind, value)
            for pname, atoms_ in ((row.get("def") or {}).get("phrase") or {}).items():
                for atom, event in zip(atoms_, song.ir["phrases"][pname]):
                    for text, cmd in zip(atom_commands(atom), event_commands(event)):
                        self.commands.setdefault(cmd, text)
                    if event.get("instrument") and atom_instrument(atom):
                        self.instruments.setdefault(int(event["instrument"]), atom_instrument(atom))

    def command(self, code: int, arg: int) -> str:
        """The document text of a command."""
        return self.commands.get((code, arg), f"command {code} {arg}")

    def instrument(self, number: int) -> str:
        """The document name of an instrument."""
        return self.instruments.get(number, f"instrument {number}")

    def origin(self, text: str) -> str:
        """The definition behind a ``!cmd:@name`` atom or a name, as written."""
        name = text.split("@")[-1]
        if name in self.defs:
            kind, value = self.defs[name]
            return f"{kind} {name}: {_flow(value)}"
        return text


def _flow(value) -> str:
    """A document value on one line."""
    dumper = songio._Dumper  # pylint: disable=protected-access
    return yaml.dump(value, Dumper=dumper, default_flow_style=True, width=1 << 20).strip()


def label(song: Song, names: Names, unit: dict, occ) -> str:
    """The document name of the modulator an occurrence starts."""
    # pylint: disable=too-many-return-statements,too-many-locals
    lab, ir = occ.label, song.ir
    player, name = song.player, unit["name"]
    records = ir.get("records") or {}
    if player == "goattracker2" and name == "command":
        command, cmddata = lab
        if command == 0:
            return f"instrument vibrato, speed row {cmddata}"
        return names.command(command, cmddata)
    if player in ("goattracker2", "sidwizard") and name in ("pulse", "filter"):
        field = name
        ins = [i for i, r in (records.get("ins") or {}).items() if r.get(field) == lab[0]]
        return f"{names.instrument(ins[0])} {field}" if ins else f"{field} row {lab[0]}"
    if player == "defmon":
        row = lab[0]
        if name.endswith("A"):
            return f"{names.instrument(row + 1)} {name.split()[0]}"
        return f"{names.command(1, row).split('@')[-1]} {name.split()[0]}"
    svib, pk, pfx, pfv, pnfx, pleg, ci = lab
    hook = occ.hook[0]
    if hook.endswith("run_deferred"):
        return names.command(pfx, pfv) if pfx in (1, 2, 3) else "deferred FX"
    if pk == 1 and svib == 0x83 and pfx != 3:
        return "!legato:0 glide" if pleg else "glide after !noteglide:0"
    if pfx in (1, 2, 3) and svib in (0x81, 0x82, 0x83):
        return names.command(pfx, pfv)
    if 0x60 <= pnfx < 0x78:
        return names.command(608, pnfx - 0x60)
    if pnfx == 0x78:
        return names.command(632, 0)
    return f"{names.instrument(ci)} vibrato"


# ---- derivation --------------------------------------------------------------------------


@dataclasses.dataclass
class Result:
    """One start's derived expressions with what it was derived from."""

    derived: object
    start: tuple
    v0: object
    stepper: Stepper | None = None
    start_value: object = None

    def step(self, v: int, phi: int, n: int) -> tuple:
        """``(v', φ')`` of one firing."""
        self.stepper.note = n
        return self.stepper.step(v, phi)


class Analysis:  # pylint: disable=too-many-instance-attributes
    """The units of one song: machines, derivations cached by start, and the replay hooks."""

    def __init__(self, song: Song):
        self.song = song
        self.spec = specs()["exprs"][song.player]
        self.units = self.spec["units"]
        self.freq = self.spec["freq"]
        self.names = Names(song)
        self.machines = {u["name"]: Machine(song.ir, u, self.spec) for u in self.units}
        self.offset = int(song.ir["playroutine"].get("note_offset", 0))
        self.tables = next(iter(self.machines.values())).form.pr["tables"]
        self.cache = {}

    def shift(self, cells: dict) -> int:
        """The internal note less ``N``: the note offset less the voice's transpose."""
        return self.offset - int(cells.get(self.spec.get("transpose"), 0) or 0)

    def note_term(self, cells: dict):
        """The internal note cell of sounding format note ``N``."""
        k = self.shift(cells)
        return ("lin", k, (("N", 1),)) if k else "N"

    def note(self, cells, voice) -> int:
        """The sounding format note of a voice in a running player."""
        if voice is None:
            return 0
        snap = (
            {self.spec.get("transpose"): cells.get(self.spec["transpose"], voice)}
            if self.spec.get("transpose")
            else {}
        )
        return cells.get(self.spec["notes"][0], voice) - self.shift(snap)

    def overlay(self, machine: Machine, hook: tuple, start: dict) -> dict:
        """Cells the start hook writes from the note, as terms of ``N`` at the start's transpose."""
        # pylint: disable=too-many-locals
        at, row, pre = hook
        parts = at.split(".")
        if parts[0] == "procs":
            actions = [{"call": parts[1]}]
        else:
            index = (
                row if parts[0] == "record_types" else (int(parts[3]) if len(parts) > 3 else None)
            )
            if index is None:
                return {}
            actions = [{"run": {"record": parts[1], "index": int(index), "field": parts[2]}}]
        state = {k: v for k, v in pre.items() if isinstance(v, int) and k not in machine.parts}
        for c in self.spec["notes"]:
            state[c] = self.note_term(start)
        try:
            paths = machine.form.paths(_dispatched(machine, actions), (), state)
        except (ValueError, KeyError):
            return {}
        if len(paths) != 1:
            return {}
        final = paths[0][1]
        out = {c: t for c, t in final.items() if not isinstance(t, int) and "N" in atoms(t)}
        written = final.get(machine.cell, machine.cell)
        if machine.cell not in machine.form.pair and written != machine.cell:
            out[machine.cell] = written
        return out

    def key(self, unit: dict, occ) -> tuple | None:
        """``(unit, start control, start value term)`` of an occurrence, or None (``occ.error``).

        The start hook's cells written from the note become terms of ``N`` where they still
        hold their values at the first firing.
        """
        machine = self.machines[unit["name"]]
        values = dict(occ.start)
        lay = self.overlay(machine, occ.hook, occ.start) if occ.hook else {}
        for c in self.spec["notes"]:
            values[c] = self.note_term(occ.start)
        n = occ.start.get(self.spec["notes"][0], 0) - self.shift(occ.start)
        for c, t in lay.items():
            if c in machine.reads or c in machine.control:
                if self.evaluate(machine, t, n) == occ.start.get(c):
                    values[c] = t
        v0 = lay.get(unit["cell"])
        if v0 is not None and self.evaluate(machine, v0, n) != occ.v:
            v0 = None
        try:
            return unit["name"], machine.start(values), v0
        except ValueError as exc:
            occ.error = str(exc)
            return None

    def derived(self, key: tuple):
        """The proved expressions of a start key."""
        name, start, v0 = key
        machine = self.machines[name]
        lift = Lift(machine.form, machine.cell, self.freq)
        v0e = None if v0 is None else lift.fold(lift.term(v0))
        return derive(machine, start, v0e, self.freq), v0e

    def result(self, key: tuple, found: tuple) -> Result:
        """A start's result with a stepper over this song's tables."""
        d, v0e = found
        res = Result(d, key[1], key[2], start_value=v0e)
        if d.value is not None:
            res.stepper = Stepper(*d.update, self.tables, self.freq, self.offset)
        return res

    def derive(self, unit: dict, occ):
        """The derivation of an occurrence's start, cached by key (for in-process replays)."""
        key = self.key(unit, occ)
        occ.key = key
        if key is None:
            return None
        if key not in self.cache:
            self.cache[key] = self.result(key, self.derived(key))
        res = self.cache[key]
        return res if res.stepper is not None else None

    def evaluate(self, machine: Machine, term, n: int):
        """A term of ``N`` at note ``n``."""
        lift = Lift(machine.form, machine.cell, self.freq)
        e = lift.fold(lift.term(term))
        if e.free_symbols - {G.N}:
            return None
        return _eval(e, n, self.tables, self.freq, self.offset)


def _eval(e, n: int, tables: dict, freq: dict, offset: int):
    """``e`` at ``N = n``."""
    st = Stepper(0, e, tables, freq, offset)
    st.note = n
    return st.step(0, 0)[0]


def _dispatched(machine: Machine, actions: list) -> list:
    """``actions`` with playroutine record runs as ``if`` chains."""
    return dispatch(machine.form.pr, actions)


# ---- modulators ------------------------------------------------------------------------


@dataclasses.dataclass
class Modulator:
    """The occurrences of one document form, their distinct starts and verdicts."""

    unit: dict
    label: str
    occurrences: list
    results: list
    inherited: Inherited | None = None

    @property
    def identity(self) -> bool:
        """True when no start changes the value: not a modulation."""
        return not self.live and not self.errors

    @property
    def live(self) -> list:
        """The starts whose value update is not the identity: where the modulator fires."""
        return [r for r in self.results if r is not None and not r.derived.identity]

    @property
    def errors(self) -> list:
        """Derivation errors of the occurrences."""
        return sorted(
            {o.error for o in self.occurrences if o.error}
            | {r.derived.error for r in self.results if r is not None and r.derived.error}
        )

    @property
    def forms(self) -> list:
        """The distinct canonical texts of the starts."""
        return list(
            dict.fromkeys(r.derived.canonical for r in self.live if r.derived.value is not None)
        )

    @property
    def recurrences(self) -> list:
        """The distinct recurrence texts ``a; f`` of the starts."""
        return list(
            dict.fromkeys(
                f"{G.text(r.derived.phase)}; {G.text(r.derived.value)}"
                for r in self.live
                if r.derived.value is not None
            )
        )

    @property
    def canonical(self) -> str | None:
        """One text for every start: the shared closed form, else the shared recurrence, else the
        expression with inherited inputs."""
        if self.inherited is not None and self.inherited.update is not None:
            return self.inherited.text
        if len(self.forms) == 1 and not self.errors:
            return self.forms[0]
        if len(self.recurrences) == 1 and not self.errors:
            return self.recurrences[0]
        return None

    @property
    def canonical_exprs(self) -> tuple:
        """``(a, f)`` of ``canonical``."""
        if self.inherited is not None and self.inherited.update is not None:
            return self.inherited.update
        d = self.live[0].derived
        return d.update if self.canonical in self.forms else (d.phase, d.value)

    @property
    def proved(self) -> bool:
        """True when every start's obligations were proved."""
        return all(r.derived.exact for r in self.live)

    @property
    def mismatches(self) -> list:
        """Occurrences whose expressions left the native value or whose cell another list wrote."""
        return [o for o in self.occurrences if o.mismatch or o.crossed]


def modulators(song: Song, an: Analysis, occurrences: list, results: dict) -> list:
    """The occurrences grouped by unit and document form, in order of first start."""
    found = {}
    for occ in occurrences:
        unit = next(u for u in an.units if u["name"] == occ.unit)
        key = (occ.unit, label(song, an.names, unit, occ))
        found.setdefault(key, (unit, []))[1].append(occ)
    out = []
    for (_, lab), (unit, occs) in found.items():
        mine = list(dict.fromkeys(o.key for o in occs))
        out.append(Modulator(unit, lab, occs, [results.get(k) for k in mine]))
    return out


@dataclasses.dataclass
class SongReport:
    """A song's modulators, the replay verdicts and token counts."""

    song: Song
    ticks: int
    modulators: list
    grid_equal: bool
    first_diff: tuple | None
    unproved_functions: list
    quiet: list

    @property
    def player(self) -> str:
        """The playroutine name."""
        return self.song.player


CACHE = Path("out/modfit/cache")
CHUNK = 256
PLAYROUTINES = Path(__file__).resolve().parent.parent / "playroutines"
SOURCES = [Path(__file__).resolve().parent.parent / f for f in ("vm.py", "modfit/replay.py")]


def content_key(*parts) -> str:
    """The sha256 of ``parts`` (bytes, text or anything with a stable ``repr``)."""
    h = hashlib.sha256()
    for part in parts:
        h.update(part if isinstance(part, bytes) else str(part).encode())
        h.update(b"\0")
    return h.hexdigest()


def _library_text() -> str:
    """The playroutine library's text."""
    return "".join(p.read_text() for p in sorted(PLAYROUTINES.glob("*.yaml")))


def _song_parts(path: Path, ticks: int) -> tuple:
    """The content a song's replays depend on: its bytes, playroutines and ticks."""
    return Path(path).read_bytes(), _library_text(), ticks


def _cached(path: Path | None, compute, load_fn, save_fn):
    """``compute()``, read from and written to ``path`` when caching."""
    if path is not None and path.exists():
        return load_fn(path)
    out = compute()
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        save_fn(path, out)
    return out


@functools.lru_cache(maxsize=None)
def _analysis(path: str, cache: Path | None = None) -> Analysis:
    """A song's analysis, once per process."""
    return Analysis(load(Path(path), cache))


def keys_task(path: str, occurrences: list, cache: Path | None) -> list:
    """The start keys of a chunk of a tune's occurrences, with their errors."""
    an = _analysis(path, cache)
    out = []
    for occ in occurrences:
        unit = next(u for u in an.units if u["name"] == occ.unit)
        key = an.key(unit, occ)
        out.append((key, occ.error))
    return out


def native_task(path: str, ticks: int, cache: Path | None):
    """The native grid of a tune, cached by its content."""
    where = cache / f"native-{content_key(*_song_parts(path, ticks))}.npy" if cache else None
    return _cached(where, lambda: native_grid(load(Path(path)), ticks), np.load, np.save)


def observe_task(path: str, ticks: int, cache: Path | None) -> list:
    """The occurrences of a tune's units (starts only, no expressions), cached by content."""

    def compute():
        an = _analysis(path, cache)
        rep = Replay(an.song.ir, an.units, lambda unit, occ: None, note=an.note)
        rep.run(ticks, desc=f"{Path(path).stem} starts")
        return rep.occurrences

    parts = (
        *_song_parts(path, ticks),
        yaml.dump(specs()["exprs"]),
        *(p.read_text() for p in SOURCES),
    )
    where = cache / f"observe-{content_key(*parts)}.pkl" if cache else None
    return _cached(where, compute, lambda p: pickle.loads(p.read_bytes()),
                   lambda p, x: p.write_bytes(pickle.dumps(x)))  # fmt: skip


DERIVERS = [Path(__file__).resolve().parent / f for f in ("phase.py", "grammar.py", "form.py")]


def derive_task(path: str, key: tuple, cache: Path | None):
    """The proved expressions of one start of a tune, cached by content."""
    parts = (Path(path).read_bytes(), _library_text(), yaml.dump(specs()["exprs"]),
             *(p.read_text() for p in DERIVERS), repr(key))  # fmt: skip
    where = cache / "derived" / f"{content_key(*parts)}.pkl" if cache else None
    return _cached(where, lambda: _analysis(path, cache).derived(key),
                   lambda p: pickle.loads(p.read_bytes()),
                   lambda p, x: p.write_bytes(pickle.dumps(x)))  # fmt: skip


def driven_task(path: str, ticks: int, found: dict, driven: set, position: int, cache,
                inputs: dict | None = None) -> tuple:  # fmt: skip
    """``(grid, occurrences)`` of a tune with every driven modulator's cells overridden.

    ``inputs`` maps ``(unit, label)`` to one ``(a, f)`` whose input cells are read at each start.
    """
    # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    an = _analysis(path, cache)
    song = an.song
    results = {k: an.result(k, v) for k, v in found.items()}
    inputs, bound = inputs or {}, {}

    def lookup(unit, occ):
        occ.key = an.key(unit, occ)
        res = results.get(occ.key)
        if res is None or res.stepper is None:
            return None
        lab = _relabel(song, an, occ)
        if lab not in inputs:
            return res
        a, f = inputs[lab]
        cells = sorted({x.name for x in a.free_symbols | f.free_symbols} - {"v", "φ", "N"})
        bind = (lab, tuple((c, G_value(occ.start, c)) for c in cells))
        if bind not in bound:
            sub = {sympy.Symbol(c, integer=True): x for c, x in bind[1]}
            stepper = Stepper(a.xreplace(sub), f.xreplace(sub), an.tables, an.freq, an.offset)
            bound[bind] = dataclasses.replace(res, stepper=stepper)
        return bound[bind]

    seen = {}

    def override(occ) -> bool:
        if id(occ) not in seen:
            seen[id(occ)] = _relabel(song, an, occ) in driven
        return seen[id(occ)]

    rep = Replay(song.ir, an.units, lookup, override=override, note=an.note)
    grid = np.asarray(rep.run(ticks, desc=f"{song.path.stem} driven", position=position))
    for occ in rep.occurrences:
        occ.derived = None
    return grid.astype(np.uint8), rep.occurrences


class Pool:
    """A process pool over the host's cores with two BLAS threads per worker, or in-process."""

    def __init__(self, workers: int, items: int):
        self.workers = max(1, min(workers or os.cpu_count() or 1, items))
        self.pool = None
        if self.workers > 1:
            for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
                os.environ[var] = "2"
            ctx = multiprocessing.get_context("spawn")
            self.pool = concurrent.futures.ProcessPoolExecutor(self.workers, mp_context=ctx)

    def map(self, fn, tasks: list, desc: str) -> list:
        """``[fn(*task)]`` in order, with a progress bar over completed tasks."""
        progress = tqdm(total=len(tasks), desc=desc, unit="task", leave=False)
        if self.pool is None:
            out = []
            for t in tasks:
                out.append(fn(*t))
                progress.update()
            progress.close()
            return out
        futures = {self.pool.submit(fn, *t): i for i, t in enumerate(tasks)}
        out = [None] * len(tasks)
        for f in concurrent.futures.as_completed(futures):
            out[futures[f]] = f.result()
            progress.update()
        progress.close()
        return out

    def close(self) -> None:
        """Shut the pool down."""
        if self.pool is not None:
            self.pool.shutdown()


def analyse_all(paths, ticks=None, workers=None, cache: Path | None = CACHE) -> list:
    """Every modulator of tunes derived, proved and replayed against their native players.

    Native grids and starts run in parallel per song (cached by content), the distinct starts'
    derivations in parallel, then one driven replay per song in parallel.
    """
    # pylint: disable=too-many-locals
    paths = [str(p) for p in paths]
    songs = {p: _analysis(p, cache).song for p in paths}
    ticks = {p: ticks or TICKS[songs[p].player] for p in paths}
    pool = Pool(workers, os.cpu_count() or 1)
    try:
        first = pool.map(
            _stage, [(kind, p, ticks[p], cache) for p in paths for kind in ("native", "observe")],
            "grids and starts",
        )  # fmt: skip
        native = dict(zip(paths, first[0::2]))
        observed = dict(zip(paths, first[1::2]))
        chunks = [
            (p, observed[p][i : i + CHUNK], cache)
            for p in paths
            for i in range(0, len(observed[p]), CHUNK)
        ]
        keyed = pool.map(keys_task, chunks, "start keys")
        keys = {}
        for (p, occs, _), got in zip(chunks, keyed):
            for occ, (key, error) in zip(occs, got):
                occ.key, occ.error = key, error
                if key is not None:
                    keys.setdefault((p, key), None)
        work = [(p, k, cache) for p, k in keys]
        found = dict(zip(keys, pool.map(derive_task, work, "derivations")))
        reports = {}
        drive = []
        for i, p in enumerate(paths):
            an = _analysis(p, cache)
            mine = {k: v for (q, k), v in found.items() if q == p}
            every = modulators(
                songs[p], an, observed[p], {k: an.result(k, v) for k, v in mine.items()}
            )
            mods = [m for m in every if not m.identity]
            for m in mods:
                if m.canonical is None and not m.errors and len(m.forms) > 1:
                    m.inherited = inherit(an, m)
            inputs = {(m.unit["name"], m.label): m.inherited.update for m in mods
                      if m.inherited is not None and m.inherited.update is not None}  # fmt: skip
            driven = {(m.unit["name"], m.label) for m in mods}
            live = {k: v for k, v in mine.items() if not v[0].identity}
            drive.append((p, ticks[p], live, driven, i, cache, inputs))
            reports[p] = every
        runs = pool.map(driven_task, drive, "driven replays")
    finally:
        pool.close()
    out = []
    for p, (grid, occs) in zip(paths, runs):
        an = _analysis(p, cache)
        mine = {k: an.result(k, v) for (q, k), v in found.items() if q == p}
        every = modulators(songs[p], an, occs, mine)
        mods = [m for m in every if not m.identity]
        before = {(m.unit["name"], m.label): m.inherited for m in reports[p]}
        for m in mods:
            m.inherited = before.get((m.unit["name"], m.label))
        quiet = [m.label for m in reports[p] if m.identity]
        diff = np.argwhere(grid != native[p])
        first_diff = tuple(int(x) for x in diff[0]) if len(diff) else None
        unproved = unproved_functions(songs[p].ir, an.spec)
        out.append(
            SongReport(songs[p], ticks[p], mods, first_diff is None, first_diff, unproved, quiet)
        )
    return out


def _stage(kind: str, path: str, ticks: int, cache):
    """A native grid or an observe replay."""
    return (native_task if kind == "native" else observe_task)(path, ticks, cache)


def analyse(
    path: Path, ticks: int | None = None, workers: int = 1, cache: Path | None = None
) -> SongReport:
    """``analyse_all`` of one tune."""
    return analyse_all([path], ticks, workers, cache)[0]


def _relabel(song: Song, an: Analysis, occ) -> tuple:
    """``(unit, form)`` of an occurrence of a second replay."""
    unit = next(u for u in an.units if u["name"] == occ.unit)
    return occ.unit, label(song, an.names, unit, occ)
