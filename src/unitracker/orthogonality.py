"""Where the format or the VM offers two ways to say one thing: the orthogonality report.

Four kinds of finding (see docs/orthogonality.md): what the optimiser proved by the register
grid, a playroutine's dead state found statically, and the vocabulary no lifted row reaches.
"""

import ast
import itertools
import re
from dataclasses import dataclass

from unitracker import songio
from unitracker.codec import COMMAND_NAMES, ROW_KEYS, CodecError, evaluate, row_place
from unitracker.eventins import written_as
from unitracker.optimise import programs_of, view
from unitracker.records import META_KEYS
from unitracker.vm import PSEUDO

VOCABULARY = "vocabulary redundancy"
KEY = "non-orthogonal key"
DEAD = "VM dead state"
UNREACHABLE = "unreachable vocabulary"
SONG = "song redundancy"
KINDS = (VOCABULARY, KEY, DEAD, UNREACHABLE, SONG)
LEGEND = (
    "Written by `unitracker-optimise --report`: the optimiser's edits the register grid proves"
    " (kept, or proved but not kept when they write more tokens), the playroutines' dead state"
    " found statically, and the vocabulary no lifted row reaches. One line per finding: player"
    " · place: the two forms; *the proposed resolution*. Places and names are those of the"
    " document each edit was proved on, which the optimiser renumbers as it goes."
)
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_MAX_COMBINATIONS = 1 << 12


@dataclass(frozen=True, order=True)
class Line:
    """One finding: kind, player(s), place, the two forms and the proposed resolution."""

    kind: str
    players: str
    where: str
    forms: str
    resolution: str

    def text(self) -> str:
        """The report line."""
        return f"- {self.players} · {self.where}: {self.forms}. *{self.resolution}*"


# ---- the optimiser's proved edits ------------------------------------------------------

_EDIT_KINDS = {
    "hoist": (VOCABULARY, "canonicalise: a command every note of an instrument carries is a field"),
    "drop_command": (KEY, "canonicalise: the writer drops a command its context already plays"),
    "drop_instrument": (KEY, "canonicalise: name an instrument only where the voice changes it"),
    "drop_keep": (KEY, "canonicalise: !pulse:0 and !filter:0 only where the note restarts one"),
    "merge": (SONG, "optimiser: one record for two that play alike"),
    "truncate": (SONG, "optimiser: rows no start reaches drop"),
    "reloop": (SONG, "optimiser: a loop is written with its shortest cycle"),
    "alias": (SONG, "optimiser: one phrase for two that play alike"),
    "factor": (SONG, "optimiser: a repeating phrase is plays of its part"),
}
_CLEAR = {
    "fixed": "codec rule: the lifter leaves out initial state the song overwrites unheard",
    "chain": "codec rule: a program row key the context already gives is left out",
    "record": "canonicalise: an instrument field with no effect where it plays is left out",
}


def _clear_kind(ir: dict, tname: str) -> str:
    rtype = view(ir).rtypes[tname]
    return "fixed" if rtype.fixed else ("chain" if rtype.chain is not None else "record")


def from_findings(label: str, ir: dict, findings: list) -> list:
    """Report lines for the optimiser's proved edits of song ``label``, alike ones joined."""
    groups = {}
    for found in findings:
        op = found.edit[0]
        if op == "clear":
            kind, resolution = KEY, _CLEAR[_clear_kind(ir, found.edit[1][0])]
        else:
            kind, resolution = _EDIT_KINDS[op]
        where, before, after = found.forms
        forms = f"`{before}` ≡ `{after}`"
        key = (kind, ir["playroutine"]["name"], forms, resolution)
        groups.setdefault(key, []).append(f"{where}{'' if found.kept else ' (not kept)'}")
    return [
        Line(kind, player, f"{label} {', '.join(places)}", forms, resolution)
        for (kind, player, forms, resolution), places in groups.items()
    ]


# ---- static analysis of a playroutine ---------------------------------------------------


def _names(text) -> tuple[set, set]:
    """``(names, {(record, field)})`` an expression reads; ``row.f`` is ``("row", f)``."""
    try:
        tree = ast.parse(str(text).strip(), mode="eval")
    except SyntaxError:
        return set(_IDENT.findall(str(text))), set()
    names, fields = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            base = node.value.value if isinstance(node.value, ast.Subscript) else node.value
            if isinstance(base, ast.Name):
                fields.add((base.id, node.attr))
    return names, fields


class Static:
    """A playroutine's cells, procs, fields and constants, and which of them are dead."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, pr: dict):
        self.pr = pr
        self.cells = {**(pr.get("global") or {}), **(pr.get("voice") or {})}
        self.functions = pr.get("functions") or {}
        self.deps, self.roots, self.writes = {}, set(), {}
        self.calls, self.fields, self.conditions = set(), set(), []
        for owner, actions in self._lists():
            self._walk(actions, owner)
        for text in _modulator_reads(pr):
            self._read(text, self.roots, None)
        mods = pr.get("modulators") or {}
        for text in _strings(mods):
            self.calls.update(re.findall(r"procs\.([A-Za-z_]\w*)", text))
        self.calls.update(n for u in mods.get("units", []) for n in _procs(u))

    def _lists(self):
        yield None, self.pr.get("init") or []
        yield None, self.pr.get("tick") or []
        for actions in (self.pr.get("procs") or {}).values():
            yield None, actions
        for tname, spec in (self.pr.get("record_types") or {}).items():
            for key, actions in (spec or {}).items():
                if key not in META_KEYS and isinstance(actions, list):
                    yield tname, actions
        for rows in (self.pr.get("records") or {}).values():
            for row in (rows or {}).values():
                for actions in (row or {}).values():
                    if isinstance(actions, list):
                        yield None, actions

    def _read(self, text, into: set, owner) -> None:
        names, fields = _names(text)
        for name in [n for n in names if n in self.functions]:
            body = self.functions[name]
            args = set(body.get("args") or [])
            inner = set()
            self._read(body.get("expr", ""), inner, owner)
            names |= inner - args
        into.update(names)
        self.fields.update((owner if r == "row" else r, f) for r, f in fields)

    def _write(self, cell, texts, owner) -> None:
        reads = self.deps.setdefault(cell, set())
        for text in texts:
            self._read(text, reads, owner)
        self.writes.setdefault(cell, []).extend(texts)

    def _walk(self, actions, owner) -> tuple[set, bool]:
        """``(cells written, control)`` of an action list; a test's reads are kept with them."""
        written, control = set(), False
        for act in actions or []:
            if not isinstance(act, dict):
                continue
            for cell, text in {**(act.get("set") or {}), **(act.get("with") or {})}.items():
                self._write(cell, [text], owner)
                written.add(cell)
            if "if" in act:
                reads = set()
                self._read(act["if"], reads, owner)
                inner = [self._walk(act.get(k), owner) for k in ("then", "else")]
                cells, ctl = set().union(*(w for w, _ in inner)), any(c for _, c in inner)
                self.conditions.append((act["if"], reads, cells, ctl))
                written |= cells
                control |= ctl
            if "run" in act:
                self._read(act["run"].get("index", 0), self.roots, owner)
            if "accumulate" in act:
                texts = [act[k] for k in ("step", "min", "max", "toward") if k in act]
                self._write(act["accumulate"], texts, owner)
                written.add(act["accumulate"])
                cells, ctl = self._walk(act.get("on_bound"), owner)
                written |= cells
                control |= ctl
            if act.get("fetch"):
                for cell in PSEUDO:
                    self._write(cell, [], owner)
                written.update(PSEUDO)
            cells, ctl = self._walk(act.get("for_voices"), owner)
            written |= cells
            control |= ctl or any(k in act for k in ("call", "run", "fetch", "return"))
            if "call" in act:
                self.calls.add(act["call"])
        return written, control

    def live(self) -> set:
        """The cells whose value can reach a register, a modulator, or a test guarding either."""
        live, todo = set(), list(self.roots)
        while todo:
            while todo:
                name = todo.pop()
                if name not in live:
                    live.add(name)
                    todo.extend(self.deps.get(name, ()))
            for _, reads, cells, control in self.conditions:
                if (control or cells & live) and not reads <= live:
                    todo.extend(reads - live)
        return live

    def dead_cells(self) -> list:
        """``(cell, reason)`` for each declared cell whose value never reaches anything."""
        live = self.live()
        read = set().union(*self.deps.values(), self.roots, *(c[1] for c in self.conditions))
        return [
            (c, "written, never read" if c not in read else "read only by dead cells")
            for c in self.cells
            if c not in live
        ]

    def dead_procs(self) -> list:
        """The procs no action list or modulator calls."""
        return [p for p in self.pr.get("procs") or {} if p not in self.calls]

    def dead_fields(self) -> list:
        """``(type, field)`` of record fields the VM never reads."""
        out = []
        for tname, spec in (self.pr.get("record_types") or {}).items():
            fields = (spec or {}).get("fields") or {}
            for f in fields if isinstance(fields, list) else list(fields):
                if (tname, f) not in self.fields:
                    out.append((tname, f))
        return out

    def dead_constants(self) -> list:
        """The constants no expression of the playroutine names."""
        used = set()
        for key, value in self.pr.items():
            if key not in ("constants", "tables"):
                used.update(n for t in _strings(value) for n in _IDENT.findall(t))
        return [c for c in self.pr.get("constants") or {} if c not in used]

    def constant_tests(self) -> list:
        """``(test, value)`` of the tests that every value their cells take decides alike."""
        values = self._value_sets()
        consts = {k: int(v) for k, v in (self.pr.get("constants") or {}).items()}
        out = []
        for text, *_ in self.conditions:
            names, fields = _names(text)
            if fields or not names or not names <= set(values) | set(consts):
                continue
            cells = sorted(names - set(consts))
            sets = [sorted(values[c]) for c in cells]
            if not cells or _product(sets) > _MAX_COMBINATIONS:
                continue
            got = {
                bool(evaluate(str(text), {**consts, **dict(zip(cells, combo))}))
                for combo in itertools.product(*sets)
            }
            if len(got) == 1:
                out.append((str(text), got.pop()))
        return sorted(set(out))

    def _value_sets(self) -> dict:
        """``{cell: values}`` for the cells every write sets to an integer literal."""
        out = {}
        for cell, spec in self.cells.items():
            texts = self.writes.get(cell, [])
            if cell in PSEUDO or cell in self._accumulated():
                continue
            if all(isinstance(t, int) or str(t).strip().isdigit() for t in texts):
                init = int((spec or {}).get("init", 0))
                out[cell] = {init, *(int(t) for t in texts)}
        return out

    def _accumulated(self) -> set:
        found = set()
        for _, actions in self._lists():
            found.update(_accumulates(actions))
        return found


def _accumulates(actions):
    for act in actions or []:
        if isinstance(act, dict):
            if "accumulate" in act:
                yield act["accumulate"]
            for key in ("then", "else", "on_bound", "for_voices"):
                yield from _accumulates(act.get(key))


def _product(sets) -> int:
    out = 1
    for s in sets:
        out *= len(s)
    return out


def _procs(unit: dict):
    for item in unit.get("fire") or []:
        if isinstance(item, str):
            yield item


def _modulator_reads(pr: dict):
    """The expressions and cell names the ghost and the modulator machinery read."""
    yield from (str(t) for t in (pr.get("ghost") or {}).get("global", {}).values())
    yield from (str(t) for t in (pr.get("ghost") or {}).get("voice", {}).values())
    yield from (str(pr[k]) for k in ("onset", "started") if pr.get(k))
    mods = pr.get("modulators") or {}
    yield from mods.get("notes") or []
    yield from [mods["transpose"]] if mods.get("transpose") else []
    yield from [mods["legato"]["cell"]] if mods.get("legato") else []
    yield from _strings(mods.get("tables"))
    yield from _strings(mods.get("functions"))
    for unit in mods.get("units") or []:
        for key in ("cell", "mirror"):
            yield from str(unit.get(key) or "").split(":")
        chip = unit.get("chip") or {}
        yield from [str(chip.get("value", "")), *map(str, chip.get("store") or {})]
        yield from _strings(list((chip.get("store") or {}).values()))
        yield from [*(unit.get("label") or []), str(unit.get("x", ""))]
        for hook in unit.get("hooks") or []:
            yield from [*(hook.get("written") or []), *(hook.get("key") or [])]
            yield from hook.get("pointer") or []
        yield from [str((unit.get("program") or {}).get("pointer", ""))]
        site = unit.get("site") or {}
        yield from [str(site.get("index", "")), str(site.get("when", ""))]


def _strings(value):
    """Every string under ``value``, keys left out."""
    if isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)
    elif isinstance(value, str):
        yield value


def dead_state(pr: dict) -> list:
    """Report lines for a playroutine's dead cells, procs, fields, constants and tests."""
    st, name = Static(pr), pr["name"]
    fix = "transcription fix: remove it, or carry what reads it"
    out = [Line(DEAD, name, f"cell `{c}`", why, fix) for c, why in st.dead_cells()]
    out += [Line(DEAD, name, f"proc `{p}`", "never called", fix) for p in st.dead_procs()]
    out += [
        Line(DEAD, name, f"field `{t}.{f}`", "never read by the VM", _field_fix(pr, t, f))
        for t, f in st.dead_fields()
    ]
    out += [Line(DEAD, name, f"constant `{c}`", "never named", fix) for c in st.dead_constants()]
    out += [
        Line(
            DEAD,
            name,
            f"test `{t}`",
            f"always {str(v).lower()} for every value its cells take",
            "unreachable from the format: the native feature the branch transcribes has no form",
        )
        for t, v in st.constant_tests()
    ]
    return out


def _field_fix(pr: dict, tname: str, fname: str) -> str:
    spec = (pr.get("record_types") or {}).get(tname) or {}
    retired = {
        f
        for u in (pr.get("modulators") or {}).get("units", [])
        for f in ((u.get("retire") or {}).get("clear") or {})
        if (u.get("retire") or {}).get("record") == tname
    }
    if fname in retired:
        return "retired: the expression replaces it, the format does not write it"
    if tname == "restart" and pr.get("restart"):
        return "format-side: the reader plays the note's restart as a program of the player's own"
    if fname == spec.get("chain") or fname in (spec.get("refs") or {}):
        return "read by the format's program walk, not by the player"
    return "transcription fix: the player ignores what the lifter writes here"


# ---- vocabulary the lifted rows reach ---------------------------------------------------


def shape_uses(ir: dict) -> dict:
    """``{(type, shape index): rows}`` of the codec shape each record row is written with
    (index -1: no shape fits), each program row once per program place."""
    v = view(ir)
    codecs = songio.codecs(ir["playroutine"], ir.get("tables"))
    out = {}
    places = _places(ir)
    for tname, rows in (ir.get("records") or {}).items():
        codec = codecs.get(tname)
        if codec is None:
            continue
        rtype = v.rtypes[tname]
        for index, row in rows.items():
            full = {f: int(row.get(f, d)) for f, d in rtype.fields.items()}
            for place in places.get((tname, index), [None]):
                env = {**full, **(place or {})}
                try:
                    k = next(
                        (
                            k
                            for k, s in enumerate(codec.shapes)
                            if s.when is None or codec.ev(s.when, env)
                        ),
                        -1,
                    )
                except CodecError:
                    k = -1
                out[(tname, k)] = out.get((tname, k), 0) + 1
    return out


def _places(ir: dict) -> dict:
    out = {}
    for tname, _, path, loop in programs_of(ir):
        for k, index in enumerate(path):
            place = row_place(k, len(path), loop is None)
            if place not in out.setdefault((tname, index), []):
                out[(tname, index)].append(place)
    return out


def command_uses(ir: dict) -> dict:
    """``{name: events}`` of the shared commands the song's events play."""
    v = view(ir)
    out = {}
    for events in ir["phrases"].values():
        for event in events:
            for code, arg in event.get("commands") or (
                [event["command"]] if event.get("command") else []
            ):
                try:
                    name = v.commands.encode(int(code), int(arg))[0]
                except CodecError:
                    name = written_as(ir["playroutine"], int(code)) or next(
                        (n for c, (n, _) in v.nibbles.items() if c == code), str(code)
                    )
                out[name] = out.get(name, 0) + 1
    return out


def _codec_keys(pr: dict) -> dict:
    """``{(type, shape index): shared keys}`` of a playroutine's codec shapes, by native type."""
    out = {}
    for tname, spec in (pr.get("record_types") or {}).items():
        for k, shape in enumerate((spec or {}).get("codec") or []):
            out[(tname, k)] = sorted((shape.get("keys") or {}).keys())
    return out


def vocabulary(songs: list) -> list:
    """Report lines for shared keys and commands no codec has, and codec shapes and commands
    no lifted row of ``songs`` (``(label, ir)``) plays."""
    prs = {}
    for _, ir in songs:
        prs.setdefault(ir["playroutine"]["name"], ir["playroutine"])
    for name in ("goattracker2", "sidwizard", "defmon"):
        prs.setdefault(name, songio.library(name))
    keys = {
        k: [n for n, pr in prs.items() if any(k in ks for ks in _codec_keys(pr).values())]
        for k in ROW_KEYS
    }
    cmds = {
        c: [n for n, pr in prs.items() if any(c == _cname(pr, k, v) for k, v in _variants(pr))]
        for c in COMMAND_NAMES
    }
    out = [
        Line(
            UNREACHABLE,
            "all",
            f"row key `{k}`",
            "no player's codec writes it",
            "retire, or give a player its shape",
        )
        for k, players in keys.items()
        if not players
    ]
    out += [
        Line(UNREACHABLE, "all", f"command `!{c}`", "no player's commands name it", "retire")
        for c, players in cmds.items()
        if not players and c != "restart"
    ]
    for label, ir in songs:
        out += _unreached(label, ir)
    return out


def _unreached(label: str, ir: dict) -> list:
    """Report lines for the codec shapes and commands no lifted row of song ``label`` plays."""
    pr = ir["playroutine"]
    used, rtypes, out = shape_uses(ir), view(ir).rtypes, []
    for (tname, k), shared in _codec_keys(pr).items():
        if not used.get((tname, k)):
            shape = f"{rtypes[tname].written}[{k}] `{{{', '.join(shared)}}}`"
            out.append(
                Line(
                    UNREACHABLE,
                    pr["name"],
                    f"{label} codec shape {shape}",
                    "no lifted row is written with it",
                    "hand-written only: keep with a test, or retire",
                )
            )
    played = command_uses(ir)
    for c in sorted({_cname(pr, code, v) for code, v in _variants(pr)} - set(played)):
        out.append(
            Line(
                UNREACHABLE,
                pr["name"],
                f"{label} command `!{c}`",
                "no lifted event plays it",
                "hand-written only",
            )
        )
    return out


def _variants(pr: dict):
    for code, variants in (pr.get("commands") or {}).items():
        yield from (
            (int(code), v) for v in (variants if isinstance(variants, list) else [variants])
        )


def _cname(pr: dict, code: int, variant: dict) -> str:
    return str(variant.get("name") or written_as(pr, code))


# ---- the report ----------------------------------------------------------------------


def lines(songs: list) -> list:
    """Every finding for ``songs`` (``(label, optimiser Result)``), sorted and unique."""
    out = []
    for label, result in songs:
        out += from_findings(label, result.source, result.findings)
    prs = {r.source["playroutine"]["name"]: r.source["playroutine"] for _, r in songs}
    for pr in prs.values():
        out += dead_state(pr)
    out += vocabulary([(label, r.source) for label, r in songs])
    return sorted(set(out), key=lambda ln: (KINDS.index(ln.kind), ln.players, ln.where, ln.forms))


def report(songs: list) -> str:
    """The orthogonality report as Markdown: one section per kind, one line per finding."""
    found = lines(songs)
    out = ["# Orthogonality and redundancy", "", LEGEND, ""]
    for kind in KINDS:
        mine = [ln for ln in found if ln.kind == kind]
        out += [f"## {kind[0].upper()}{kind[1:]}", ""]
        out += [ln.text() for ln in mine] or ["- none"]
        out.append("")
    return "\n".join(out)
