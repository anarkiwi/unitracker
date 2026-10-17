"""The ``exprs`` report: each song's modulators, failures, shared forms and token counts."""

from pathlib import Path

import sympy

from unitracker import songio
from unitracker.modfit import grammar as G
from unitracker.modfit.exprs import (
    Modulator,
    Names,
    Result,
    Song,
    SongReport,
    _flow,
    analyse_all,
    document,
    dump,
    event_commands,
)

# ---- report ----------------------------------------------------------------------------


def leftovers(song: Song) -> list:
    """``(form, rows or events)`` of the forms v1.4 left in their own shape, in this song."""
    rec = song.ir.get("records") or {}

    def rows(name, test):
        return sum(1 for r in (rec.get(name) or {}).values() if test(r))

    def events(code):
        return sum(
            1
            for ph in song.ir["phrases"].values()
            for e in ph
            if code in [c for c, _ in event_commands(e)]
        )

    player = song.player
    if player == "goattracker2":
        return [
            ("!vib", events(4)),
            ("instrument vibrato (ins speed)", rows("ins", lambda r: r.get("speed", 0) != 0)),
            (
                "wavetable command rows $F1-$F4",
                rows("ctrltab", lambda r: 0xF1 <= r.get("w", 0) <= 0xF4),
            ),
            ("pulse rows [0, r]", rows("pulsetab", lambda r: r.get("l", 0) == 0)),
            (
                "filter rows carrying cut",
                rows("filttab", lambda r: r.get("l", 0) >= 0x80 and r.get("cut", -1) >= 0),
            ),
        ]
    if player == "defmon":
        return [
            ("acid", rows("sidtab", lambda r: r.get("acid", -1) >= 0)),
            ("af 0", rows("sidtab", lambda r: r.get("af", -1) == 0)),
            ("af 1-$7F", rows("sidtab", lambda r: 0 < r.get("af", -1) < 0x80)),
            (
                "ps beside af",
                rows("sidtab", lambda r: r.get("af", -1) >= 0x80 and r.get("ps", -1) >= 0),
            ),
        ]
    return [
        ("!notevib", events(608)),
        ("!noteglide", events(632)),
        (
            "keyboard-tracked rows",
            rows("pwtab", lambda r: r.get("k", 0)) + rows("flttab", lambda r: r.get("k", 0)),
        ),
        ("filter rows with res", rows("flttab", lambda r: r.get("res", 0) != 0)),
    ]


def origin(names: Names, mod: Modulator) -> str:
    """The modulator's form as the v1.4 document writes it."""
    lab = mod.label
    first = lab.split()[0]
    if lab.startswith("!"):
        return names.origin(lab)
    if first in names.defs:
        kind, value = names.defs[first]
        field = lab.split()[-1]
        if isinstance(value, dict):
            if field == "vibrato":
                keys = {k: value[k] for k in ("vibrato", "vibdelay") if k in value}
                return f"{kind} {first}: {_flow(keys) if keys else 'no vibrato'}"
            if field in value:
                return f"{kind} {first} {field}: {_flow(value[field])}"
        return f"{kind} {first}: {_flow(value)}"
    return lab


def start_lines(r: Result, tag: str) -> list:
    """The expressions and proof of one start."""
    d = r.derived
    if d.error:
        return [f"{tag} not derived: {d.error}"]
    o = d.order
    kind = {"time": "elapsed firings", "turn": "turns"}[o.kind]
    head = f"{tag} phase ({kind}, {d.states} control states, prefix {o.prefix}, period {o.period})"
    lines = [f"{head}: φ' = φ + {G.text(d.phase)}", f"{tag} value: v' = {G.text(d.value)}"]
    if d.closed is not None:
        lines.append(f"{tag} closed form (start value {_start(r)}): v = {G.text(d.closed)}")
    else:
        why = "the start value is the incoming one" if r.v0 is None else "a value update reads v"
        lines.append(f"{tag} closed form: none ({why})")
    bad = [p for p in d.proof if not p[1]]
    if bad:
        lines.append(f"{tag} proof: FAILED " + "; ".join(f"{p[0]}: {p[2]}" for p in bad))
    else:
        lines.append(f"{tag} proof: " + "; ".join(p[0] for p in d.proof) + ": proved")
    return lines


def mismatch_text(o) -> str:
    """The first difference of an occurrence's replay."""
    parts = [f"from tick {o.tick}"]
    if o.mismatch:
        tick, phi, v, native, expr = o.mismatch
        parts.append(f"at tick {tick}, φ = {phi}, v = {v}: native {native}, expression {expr}")
    if o.crossed:
        parts.append(f"the cell was written between firings at tick {o.crossed[0]} "
                     f"({o.crossed[2]} -> {o.crossed[3]})")  # fmt: skip
    return "; ".join(parts)


def modulator_lines(names: Names, mod: Modulator) -> list:
    """One modulator: its form, expressions, proofs and replay verdict."""
    lines = [f"### {mod.label} ({mod.unit['name']}, cell {mod.unit['cell']})"]
    lines.append(f"    original: {origin(names, mod)}")
    starts = mod.live
    still = len([r for r in mod.results if r is not None]) - len(starts)
    lines.append(f"    occurrences {len(mod.occurrences)}, distinct starts {len(starts)}"
                 + (f" (and {still} where it does not fire)" if still else ""))  # fmt: skip
    for i, r in enumerate(starts):
        lines += start_lines(r, f"    start {i + 1}" if len(starts) > 1 else "   ")
    lines += [f"    not derived: {e}" for e in mod.errors]
    bad = mod.mismatches
    if bad:
        lines.append(f"    replay: {len(bad)} of {len(mod.occurrences)} occurrences differ, "
                     f"first {mismatch_text(bad[0])}")  # fmt: skip
    else:
        firings = sum(o.firings for o in mod.occurrences)
        lines.append(f"    replay: {firings} firings equal the native cell")
    inh = mod.inherited
    if inh is not None:
        if inh.update is None:
            lines.append(f"    inherited {', '.join(inh.cells)}: not one expression: {inh.reason}")
        else:
            what = "inputs" if inh.kind == "input" else "phase start offset from"
            lines.append(f"    inherited ({what} {', '.join(inh.cells)}): {inh.proof}")
    if mod.canonical is not None:
        lines.append(f"    canonical: {mod.canonical}")
        lines.append(f"    reads: {', '.join(reads(mod))}")
    elif len(mod.forms) > 1:
        lines.append(
            f"    canonical: none, {len(mod.forms)} expressions decided by {verdict_state(mod)}"
        )
    return lines


def reads(mod: Modulator) -> list:
    """What the canonical expression reads besides ``v``: the phase, the note, tables, inputs."""
    a, f = mod.canonical_exprs
    syms = {x.name for x in a.free_symbols | f.free_symbols}
    out = ["φ"] if "φ" in syms else []
    out += ["N"] if "N" in syms else []
    out += sorted({type(x).__name__ for x in (a.atoms(sympy.core.function.AppliedUndef)
                                              | f.atoms(sympy.core.function.AppliedUndef))})  # fmt: skip
    out += [f"{c} (a cell read when it starts)" for c in sorted(syms - {"φ", "N", "v"})]
    return out or ["v only"]


def _start(r: Result) -> str:
    """The text of a start value."""
    return "any" if r.start_value is None else G.text(r.start_value)


def verdict_state(mod: Modulator) -> str:
    """The start cells that decide a modulator's expression, with their values.

    A cell decides it when starts with equal values of the cell share one expression; with
    none such, every cell that differs between starts.
    """
    starts = [(dict(r.start), r.derived.canonical) for r in mod.live]
    varying = sorted(
        {c for s, _ in starts for c in s if len({str(x.get(c)) for x, _ in starts}) > 1}
    )
    deciding = [
        c
        for c in varying
        if all(
            fa == fb for (a, fa) in starts for (b, fb) in starts if str(a.get(c)) == str(b.get(c))
        )
    ]
    shown = deciding or varying
    vals = {c: sorted({str(x.get(c)) for x, _ in starts}, key=_num_key) for c in shown}
    return ", ".join(f"{c} in {{{', '.join(v)}}}" for c, v in vals.items()) or "the start value"


def _num_key(s: str):
    """Numbers in numeric order, then text."""
    try:
        return 0, int(s), ""
    except ValueError:
        return 1, 0, s


def failures(reports: list) -> list:
    """Every modulator with no single proved expression, with its counterexample."""
    out = []
    for rep in reports:
        names = Names(rep.song)
        for m in rep.modulators:
            why = list(m.errors)
            why += [f"{p[0]}: {p[2]}" for r in m.live for p in r.derived.proof if not p[1]]
            if m.inherited is not None and m.inherited.update is None:
                why.append(f"inherited {', '.join(m.inherited.cells)}: {m.inherited.reason}")
            elif m.canonical is None and not m.errors:
                why.append(f"needs state that is no phase or modulator value: {len(m.forms)} "
                           f"expressions decided by {verdict_state(m)}")  # fmt: skip
            why += [f"replay differs {mismatch_text(o)}" for o in m.mismatches[:1]]
            if why:
                head = f"  {rep.song.path.stem} {m.label} ({origin(names, m)})"
                out.append(f"{head}: " + "; ".join(why))
    return out or ["  none"]


def groups(reports: list) -> list:
    """Canonical texts shared by modulators, with their players and forms."""
    found = {}
    for rep in reports:
        for m in rep.modulators:
            if m.canonical is not None:
                found.setdefault(m.canonical, []).append((rep.player, rep.song.path.stem, m.label))
    lines = []
    for text, users in sorted(
        found.items(), key=lambda kv: (-len({p for p, _, _ in kv[1]}), kv[0])
    ):
        players = sorted({p for p, _, _ in users})
        if len(users) > 1:
            who = ", ".join(f"{p} {s} {lab}" for p, s, lab in users)
            lines.append(f"  [{len(players)} player{'s' * (len(players) > 1)}] {text}\n      {who}")
    return lines or ["  none"]


def shape(expr) -> str:
    """The text of an expression with every integer other than 0 and ±1 written ``k``."""
    k = sympy.Symbol("k")
    big = {n: k for n in expr.atoms(sympy.Integer) if abs(int(n)) > 1}
    return G.text(expr.xreplace(big))


def shapes(reports: list) -> list:
    """Canonical forms equal up to their constants, used by more than one player."""
    found = {}
    for rep in reports:
        for m in rep.modulators:
            if m.canonical is None:
                continue
            a, f = m.canonical_exprs
            key = f"{shape(a)}; {shape(f)}"
            found.setdefault(key, []).append((rep.player, rep.song.path.stem, m.label))
    lines = []
    for key, users in sorted(found.items(), key=lambda kv: (-len({p for p, _, _ in kv[1]}), kv[0])):
        players = sorted({p for p, _, _ in users})
        if len(players) > 1:
            who = ", ".join(f"{p} {s} {lab}" for p, s, lab in users)
            lines.append(f"  [{len(players)} players] {key}\n      {who}")
    return lines or ["  none"]


def report(
    paths, before: Path | None = None, after: Path | None = None, ticks=None, workers=None
) -> list:
    """The ``exprs`` report of tunes, with token counts of their documents when given."""
    reports = analyse_all(paths, ticks, workers)
    lines = [
        "# Modulators as phase and value expressions",
        "",
        "Per firing, on the state before it: v' = f(v, φ) and φ' = φ + a(v, φ). φ starts at 0,",
        "v at the cell's value when the modulator starts; a closed form g is the value after the",
        "firing at φ. N is the voice's sounding note in format numbering, F(k) the player's",
        "frequency of note k, W the SID-Wizard speed table (exptab below 107, then frequencies).",
        "Proofs are z3 over 48-bit integers: every reachable control state's paths against f and",
        "of f and a over the cycle, and g(φ + 1) = f(g(φ), φ + 1). A cell the previous modulator",
        "left is an input of the expression, read when the modulator starts, or a start offset of φ.",
        "",
    ]
    for rep in reports:
        names = Names(rep.song)
        lines.append(f"## {rep.song.path.stem} ({rep.player}), {rep.ticks} ticks")
        count = sum(len(m.occurrences) for m in rep.modulators)
        lines.append(f"  modulators {len(rep.modulators)}, occurrences {count}")
        if rep.grid_equal:
            verdict = "equal to"
        else:
            verdict = f"differs at tick {rep.first_diff[0]}, register {rep.first_diff[1]}, from"
        lines.append("  with every modulator's cells driven by its expressions the register grid "
                     f"is {verdict} the native player's")  # fmt: skip
        if rep.quiet:
            lines.append("  not modulations (the value never changes): " + ", ".join(rep.quiet))
        if rep.unproved_functions:
            lines.append(f"  named-table functions NOT equal: {rep.unproved_functions}")
        lines.append("")
        for m in rep.modulators:
            lines += modulator_lines(names, m) + [""]
        found = leftovers(rep.song)
        lines.append("  v1.4 leftover forms: " + ", ".join(f"{f} {n}" for f, n in found))
        lines.append("")
    lines += ["## Failures", ""] + failures(reports) + [""]
    lines += ["## Identical expressions", ""] + groups(reports) + [""]
    lines += ["## Same form up to constants", ""] + shapes(reports) + [""]
    lines += ["## Tokens", ""] + token_lines(reports, before, after)
    return lines


# ---- tokens ----------------------------------------------------------------------------

POPS = {
    "pitch": ("freq", ("step", "for")),
    "pulse": ("pw", ("pw", "ps", "at")),
    "acid": ("acid", ("acid",)),
}


def expression_document(rep: SongReport) -> tuple:  # pylint: disable=too-many-locals
    """``(text, defs)``: the v1.4 document with each converted modulator an expression def.

    Expressions are ``x`` records ``{a, v}`` deduplicated by text; commands start them with
    ``!mod:@x``, programs and instrument fields name them; unreferenced defs are dropped.
    """
    doc = document(rep.song.text)
    names, converted = {}, {}
    for m in rep.modulators:
        if m.canonical is not None and m.proved and not m.mismatches:
            converted[m.label] = names.setdefault(m.canonical, f"x{len(names) + 1}")
    rows = doc["song"]
    for row in rows:
        defs = row.get("def") or {}
        for atoms_ in (defs.get("phrase") or {}).values():
            atoms_[:] = [_atom(a, converted) for a in atoms_]
        for name, value in (defs.get("ins") or {}).items():
            _instrument(name, value, converted)
        for name, prog in (defs.get("sidtab") or {}).items():
            _program(name, prog, converted)
    _drop_unreferenced(doc)
    defs = {}
    for text, name in names.items():
        a, f = text.split("; ", 1)
        defs[name] = songio.FlowMap(({} if a == "0" else {"a": _num(a)}) | {"v": _num(f)})
    first = rows[0].setdefault("def", {})
    rows[0]["def"] = {"x": defs, **first}
    return dump(doc), defs


def _instrument(name: str, value: dict, converted: dict) -> None:
    """An instrument's converted program fields and vibrato as expression names."""
    for field in ("pulse", "filter"):
        if f"{name} {field}" in converted:
            value[field] = songio.Atom(converted[f"{name} {field}"])
    if f"{name} vibrato" in converted:
        value["vibrato"] = songio.Atom(converted[f"{name} vibrato"])
        value.pop("vibdelay", None)


def _program(name: str, prog, converted: dict) -> None:
    """A sidTAB program with each converted modulator's keys replaced by its expression."""
    for kind, (key, pops) in POPS.items():
        if f"{name} {kind}" not in converted or not isinstance(prog, list):
            continue
        for row in (r for r in prog if isinstance(r, dict)):
            for k in pops:
                row.pop(k, None)
            if row.get("reg") == key:
                row.pop("reg")
        prog[0][key] = songio.Atom(converted[f"{name} {kind}"])


def _num(s: str):
    """An integer written as one, else the text."""
    try:
        return int(s)
    except ValueError:
        return s


def _atom(atom, converted: dict):
    """A phrase atom with each converted command naming its expression."""
    text = str(atom)
    body, star, steps = text.partition("*")
    head, *cmds = body.split("!")
    cmds = [f"mod:@{converted['!' + c]}" if "!" + c in converted else c for c in cmds]
    out = "!".join([head, *cmds]) + star + steps
    return songio.Atom(out) if out != text else atom


def _drop_unreferenced(doc) -> None:
    """Remove ``mod`` and ``speedtab`` defs no longer named anywhere in the document."""
    for row in doc["song"]:
        for kind in ("mod", "speedtab"):
            items = (row.get("def") or {}).get(kind)
            for name in list(items or {}):
                rest = dump({**doc, "song": [_without(r, kind, name) for r in doc["song"]]})
                if f"@{name}" not in rest and f": {name}" not in rest:
                    del items[name]
            if items == {}:
                del row["def"][kind]


def _without(row, kind: str, name: str):
    """A song row without one definition."""
    if kind not in (row.get("def") or {}):
        return row
    defs = dict(row["def"])
    defs[kind] = {k: v for k, v in defs[kind].items() if k != name}
    return {**row, "def": defs}


def token_lines(reports: list, before: Path | None, after: Path | None) -> list:
    """Tokens of each song in v1.3, v1.4 and with modulators as expressions."""
    lines = ["  song: v1.3 / v1.4 / expressions (expression defs)"]
    totals = [0, 0, 0]
    for rep in reports:
        stem = rep.song.path.stem
        old = _tokens_of(before / f"{stem}.yaml") if before else None
        new = _tokens_of(after / f"{stem}.yaml") if after else G.tokens(rep.song.text)
        text, defs = expression_document(rep)
        expr = G.tokens(text)
        for i, n in enumerate((old, new, expr)):
            totals[i] += n or 0
        lines.append(f"  {stem}: {old if old is not None else '-'} / {new} / {expr} ({len(defs)})")
    lines.append(f"  total: {totals[0] or '-'} / {totals[1]} / {totals[2]}")
    return lines


def _tokens_of(path: Path) -> int | None:
    """Tokens of a file, or None when it is missing."""
    return G.tokens(path.read_text(encoding="utf-8")) if path.exists() else None
