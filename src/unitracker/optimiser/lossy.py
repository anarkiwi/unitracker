"""The lossy mode: each code modulator call replaced by a call of an expression template
fitted to its firings, or each group of code templates sharing state by expressions fitted to
the cell values it leaves, within a tolerance per cell, every other register kept
(docs/optimiser.md)."""

import ast
from collections import Counter, defaultdict
from dataclasses import dataclass, field, fields, is_dataclass, replace
from functools import partial

import numpy as np
from tqdm import tqdm
from unitracker_grammar import PAL_HZ
from unitracker_grammar import code as _code
from unitracker_grammar.document import (
    NONE,
    Call,
    Code,
    Document,
    Event,
    Expr,
    Instrument,
    Row,
    base,
    printed,
    read,
    signature,
)
from unitracker_grammar.expr import canonical, tree
from unitracker_player.player import load

from unitracker.optimiser import coupled, fit
from unitracker.optimiser.guarantee import GuaranteeError, grid
from unitracker.optimiser.measure import Rewrite, size
from unitracker.optimiser.trace import Trace, record
from unitracker.optimiser.tree import transform, walk

_RESERVED = frozenset(
    {"v", "phi", "nn", "ne", "a", "x", "y", "n", "N", "F", "T", "abs", "min", "max"}
    | {"none", "hold", "reset"}
)
_TABLE_LETTERS = "ABCDEGHIJKLMOPQRSUVWXYZ"


def cell_values(frames: np.ndarray, cell: str) -> np.ndarray:
    """``(ticks, voices)`` of a cell's register values per voice (the cutoff in column 0)."""
    f = frames.astype(np.int64)
    if cell == "cut":
        return (f[:, 21] & 7 | f[:, 22] << 3)[:, None]
    lo = 0 if cell == "freq" else 2
    hi = 0xFF if cell == "freq" else 15
    return np.stack([f[:, 7 * v + lo] | (f[:, 7 * v + lo + 1] & hi) << 8 for v in range(3)], 1)


def _registers(cell: str, voice: int) -> tuple:
    if cell == "cut":
        return 21, 22
    lo = 7 * voice + (0 if cell == "freq" else 2)
    return lo, lo + 1


@dataclass
class Replacement:  # pylint: disable=too-many-instance-attributes
    """A code template's calls, or a coupled group's (``templates``), becoming calls of
    expression templates: ``calls`` maps each replaced call to its new one (``none`` ending its
    binding), ``mods`` and ``tables`` are the records it adds, ``err`` the fitted errors,
    ``saved`` the tokens it saves alone, ``alternatives`` the group's other plans and
    ``fallback`` its templates' own replacements, tried in turn where it is refused."""

    name: str
    cell: str
    calls: dict
    mods: list
    tables: list
    err: np.ndarray = field(repr=False)
    saved: int = 0
    templates: tuple = ()
    alternatives: list = field(default_factory=list, repr=False)
    fallback: list = field(default_factory=list, repr=False)

    def __post_init__(self):
        self.templates = self.templates or (self.name,)

    @property
    def label(self) -> str:
        """The replacement's report line."""
        news = ", ".join(sorted({getattr(c, "name", str(c)) for c in self.calls.values()}))
        p50, p95, top = fit.stats(self.err)
        return (
            f"{self.name} ({len(self.calls)} calls, {self.cell}) -> {news}: "
            f"saves {self.saved} tokens, error p50 {p50:.1f} p95 {p95:.1f} max {top:.1f}"
        )


def _occurrences(doc: Document) -> Counter:
    return Counter(node for _, node in walk(doc) if isinstance(node, Call))


def _name(stem: str, taken: set) -> str:
    """``stem``, else ``stem`` and the least count from 2, not in ``taken`` (which takes it)."""
    k, name = 1, stem
    while name in taken:
        k += 1
        name = f"{stem}{k}"
    taken.add(name)
    return name


def _table_name(taken: set) -> str:
    for a in _TABLE_LETTERS:
        for b in _TABLE_LETTERS:
            if f"L{a}{b}" not in taken:
                taken.add(f"L{a}{b}")
                return f"L{a}{b}"
    raise ValueError("no table name left")


def _table(forms: list, params: dict, taken: set) -> tuple:
    """The one table of table forms ``forms``: each form's table after the one before, every
    value raised by ``z`` to be no negative; ``params`` gains each form's offset ``k`` and
    ``z``."""
    z = max(0, -min(min(f.params["table"]) for f in forms))
    values, offsets = [], []
    for f in forms:
        offsets.append(len(values))
        values += [x + z for x in f.params["table"]]
    params |= {"k": offsets, "z": [z] * len(forms)}
    return _table_name(taken), tuple(values)


def _body(text: str, params: dict, varying: list, scaled: dict, names: dict) -> Expr:
    """Template text ``text`` with each parameter not ``varying`` its literal, each ``scaled``
    one a multiple of another, and the rest renamed by ``names``."""

    class _Literal(ast.NodeTransformer):
        def visit_Name(self, n):  # pylint: disable=invalid-name,missing-function-docstring
            if n.id in scaled:
                x, c = scaled[n.id]
                return ast.BinOp(ast.Constant(c), ast.Mult(), ast.Name(names[x]))
            if n.id in params and n.id not in varying:
                return ast.Constant(params[n.id][0])
            return ast.Name(names[n.id]) if n.id in names else n

    return Expr(canonical(ast.unparse(_Literal().visit(tree(text)))))


# pylint: disable-next=too-many-locals
def template(group: list, taken: set, reserved=frozenset()) -> tuple:
    """``(signature, Expr, table, {call: new call})`` of one expression template for
    ``group``'s ``(call, form)`` pairs of one form key, named by what it does: a parameter per
    natural value of its shape (:func:`fit.natural`) and per value that differs between them,
    the rest literal, each parameter named by its role but for ``reserved`` and ``taken``."""
    forms = [f for _, f in group]
    shape, base_, delay = forms[0].key
    params = {k: [f.params[k] for f in forms] for k in forms[0].params if k != "table"}
    table = _table(forms, params, taken) if shape in fit.TABLES else None
    scaled = _scaled(params, [k for k, vals in params.items() if len(set(vals)) > 1])
    varying = [
        k
        for k in params
        if k not in scaled and (len(set(params[k])) > 1 or fit.natural(shape, k, params[k]))
    ]
    used, names = set(reserved | _RESERVED | taken), {}
    for k in varying:
        names[k] = fit.vacant(fit.role(shape, base_, k), used)
        used.add(names[k])
    text = forms[0].text().replace("TABLE", table[0] if table else "TABLE")
    name = _name(fit.stem(shape, base_, delay), taken | reserved | _RESERVED)
    taken.add(name)
    calls = {
        call: Call(name, tuple(int(params[k][i]) for k in varying))
        for i, (call, _) in enumerate(group)
    }
    sig = signature(name, tuple(names[k] for k in varying))
    return sig, _body(text, params, varying, scaled, names), table, calls


def _scaled(params: dict, varying: list) -> dict:
    """``{y: (x, c)}``: each varying parameter ``y`` that is an integer ``c`` times an
    earlier one ``x`` in every call."""
    out = {}
    for j, y in enumerate(varying):
        for x in varying[:j]:
            if x in out:
                continue
            xs, ys = np.array(params[x]), np.array(params[y])
            c = ys[xs != 0][0] // xs[xs != 0][0] if (xs != 0).any() else 0
            if c and np.array_equal(ys, c * xs):
                out[y] = (x, int(c))
                break
    return out


def _cost(made: tuple, occurrences: Counter) -> int:
    sig, body, table, calls = made
    text = f" {sig}:{body}\n" + (f" {table[0]}:[{','.join(map(str, table[1]))}]\n" if table else "")
    return size(text) + sum(occurrences[c] * size(str(n)) for c, n in calls.items())


@dataclass(frozen=True)
class Names:
    """A document's calls counted, the names its templates and tables take and the state
    variables a parameter may not be named."""

    occurrences: Counter
    taken: set
    reserved: frozenset

    @classmethod
    def of(cls, doc: Document) -> "Names":
        """The names of ``doc``."""
        taken = {_code.signature(k)[0] for k, _ in doc.mods} | {n for n, _ in doc.tables}
        taken |= {k for section in doc.defs for k, _ in section.records}
        reserved = frozenset(n for _, names in doc.header.state or () for n, _ in names)
        return cls(_occurrences(doc), taken, reserved)


def _choose(options: dict, names: Names) -> list:
    """The ``(call, form)`` groups, one template each, for one code template's calls and
    their fitting forms: the cheapest of each form key fitting every call, and each call's
    cheapest form grouped by key."""

    def cost(plan):
        return sum(
            _cost(template(g, set(names.taken), names.reserved), names.occurrences) for g in plan
        )

    keyed = {c: {f.key: f for f in forms} for c, forms in options.items()}
    common = set.intersection(*(set(k) for k in keyed.values()))
    plans = [[[(c, keyed[c][key]) for c in options]] for key in sorted(common)]
    groups = defaultdict(list)
    for call, forms in options.items():
        best = min(forms, key=lambda f, c=call: cost([[(c, f)]]))
        groups[best.key].append((call, best))
    plans.append(list(groups.values()))
    return min(plans, key=cost)


def _fitted(out: Replacement, options: dict, names: Names) -> list:
    """``out`` with the templates of the calls of ``options`` (each its fitting forms) added;
    their errors."""
    errs = []
    for group in _choose(options, names) if options else ():
        errs += [form.err for _, form in group]
        if group[0][1].key == coupled.IDENTITY:
            out.calls |= dict.fromkeys((c for c, _ in group), NONE)
            continue
        sig, body, table, calls = template(group, names.taken, names.reserved)
        out.calls |= calls
        out.mods.append((sig, body))
        out.tables += [table] if table else []
    return errs


def _replacement(name: str, options: dict, calls: dict, names: Names) -> Replacement:
    """The replacement of code template ``name``'s calls of ``options`` (each with its fitting
    forms, in ``calls`` with its ``(cell, firings)``)."""
    cell = "/".join(sorted({calls[c][0] for c in options}))
    out = Replacement(name, cell, {}, [], [], np.empty(0))
    out.err = np.concatenate(_fitted(out, options, names))
    return out


def _grouped(group: coupled.Group, plan: coupled.Plan, names: Names) -> Replacement:
    """The replacement of ``group``'s calls by ``plan``: its hosted and own calls by fitted
    templates, every other call of its templates ending its binding."""
    out = Replacement(group.name, group.cell, {}, [], [], np.empty(0), templates=group.templates)
    errs = _fitted(out, plan.hosted, names) + _fitted(out, plan.own, names)
    out.err = np.concatenate(errs)
    others = {c for c in names.occurrences if c.name in group.templates and c not in out.calls}
    out.calls |= dict.fromkeys(sorted(others | plan.ended, key=str), NONE)
    return out


def _options(calls: dict, cells: Counter, tolerances: dict, clock: int) -> dict:
    """``{call: fitting forms}`` of the calls of ``calls`` (each its ``(cell, firings)``) that
    fire on one cell and fit a form."""
    out = {}
    for call, (cell, rec) in calls.items():
        forms = fit.fits(fit.Firings(rec, cell, clock), tolerances[cell])
        if forms and cells[call] == 1:
            out[call] = forms
    return out


def _slots(doc: Document, cell: str) -> tuple:
    return tuple(s.name for s in doc.header.slot(cell))


# pylint: disable-next=too-many-locals
def propose(doc: Document, tr: Trace, tolerances: dict, progress: bool = False) -> tuple:
    """``(replacements, unfit)``: a :class:`Replacement` per code template sharing no state
    some of whose calls fit a form within its cell's tolerance over every firing, those calls
    to calls of expression templates, and per group of code templates sharing state whose
    calls all fit a plan (:mod:`coupled`), its other plans its alternatives; ``unfit`` the
    report line of each group no plan fits."""
    clock = (doc.header.clock or (None, PAL_HZ))[1] or PAL_HZ
    codes = {_code.signature(k)[0] for k, v in doc.mods if isinstance(v, Code)}
    by_template, cells = defaultdict(dict), Counter(call for call, _ in tr.firings)
    for (call, cell), rec in tr.firings.items():
        by_template[call.name][call] = (cell, rec)
    names, own = Names.of(doc), {}
    for name in tqdm(sorted(by_template), desc="lossy fit", unit="mod", disable=not progress):
        options = {}
        if name in codes:
            options = _options(by_template[name], cells, tolerances, clock)
        if options:
            own[name] = _replacement(name, options, by_template[name], names)
    groups = coupled.groups(doc)
    found, unfit = _groups(doc, tr, groups, tolerances, names, progress)
    for group in groups:
        fallback = [own.pop(t) for t in group.templates if t in own]
        if group.name in found:
            found[group.name].fallback = fallback
        else:
            found |= {r.name: r for r in fallback}
    return list(own.values()) + list(found.values()), unfit


# pylint: disable-next=too-many-arguments,too-many-positional-arguments
def _groups(doc: Document, tr: Trace, groups: list, tolerances: dict, names, progress) -> tuple:
    """``(replacements, unfit)`` of the coupled ``groups``: each group's first plan by its name,
    the rest its alternatives, and why each group no plan fits is unfit."""
    clock = (doc.header.clock or (None, PAL_HZ))[1] or PAL_HZ
    out, unfit = {}, []
    for group in tqdm(groups, desc="lossy fit groups", unit="group", disable=not progress):
        plans, why = coupled.plan(group, tr, _slots(doc, group.cell), tolerances[group.cell], clock)
        options = [_grouped(group, p, names) for p in plans]
        if options:
            options[0].alternatives = options[1:]
            out[group.name] = options[0]
        elif why:
            unfit.append(f"{group.name} ({group.cell}): " + "; ".join(why))
    return out, unfit


def _renamed(calls: dict, node):
    return calls.get(node, node) if isinstance(node, Call) else node


def _binding(node) -> bool:
    return (
        isinstance(node, tuple)
        and not isinstance(node, Call)
        and len(node) == 2
        and isinstance(node[0], str)
    )


def _slot_of(doc: Document, key: str) -> tuple | None:
    """The ``(cell, slot)`` a binding key binds, None for a key binding no cell's slot."""
    cell = base(key)
    if cell not in coupled.CELLS:
        return None
    return cell, key.rpartition(".")[0] or _slots(doc, cell)[0]


def unbound(doc: Document, slots: set) -> Document:
    """``doc`` without the ``none`` bindings of instruments, rows and commands of each of
    ``slots`` (``(cell, slot)``) nothing else binds."""
    bound = {(c, s.name) for c, ss in doc.header.slots or () for s in ss if s.mod is not None}
    for _, node in walk((doc.defs, doc.init, doc.rows)):
        if _binding(node) and node[1] != NONE and (key := _slot_of(doc, node[0])) in slots:
            bound.add(key)
    drop = slots - bound
    if not drop:
        return doc

    def kept(pairs):
        return tuple(p for p in pairs if not (p[1] == NONE and _slot_of(doc, p[0]) in drop))

    def visit(node):
        if isinstance(node, Instrument):
            return replace(node, fields=kept(node.fields))
        if isinstance(node, Row):
            return replace(node, keys=kept(node.keys))
        if isinstance(node, Event):
            return replace(node, commands=kept(node.commands))
        return node

    return transform(doc, visit)


def applied(doc: Document, replacements: list) -> Document:
    """``doc`` with ``replacements``' calls renamed and records added, then every code
    template they replaced that no call names any more dropped, with the state variables and
    tables nothing reads any more, and the ``none`` bindings of slots they left unbound."""
    calls = {old: new for r in replacements for old, new in r.calls.items()}
    ended = {old for old, new in calls.items() if new == NONE}
    cleared = {
        _slot_of(doc, node[0])
        for _, node in walk(doc)
        if _binding(node) and isinstance(node[1], Call) and node[1] in ended
    }
    doc = transform(doc, partial(_renamed, calls))
    doc = replace(
        doc,
        mods=doc.mods + tuple(m for r in replacements for m in r.mods),
        tables=doc.tables + tuple(t for r in replacements for t in r.tables),
    )
    doc = unbound(doc, cleared - {None})
    return pruned(doc, {t for r in replacements for t in r.templates})


def _reads(doc: Document) -> set:
    """The symbols, state variables and tables, the document's templates and bindings read."""
    out = set()
    for _, body in doc.mods:
        out |= _body_reads(body)
    for _, node in walk(doc):
        values = [getattr(node, f.name) for f in fields(node)] if is_dataclass(node) else node
        for value in values:
            if isinstance(value, (Code, Expr)):
                out |= _body_reads(value)
    return out


def _body_reads(body) -> set:
    if isinstance(body, Code):
        return {s.split(".")[0] for s in _code.symbols(body.statements)}
    return {n.value.id for n in ast.walk(tree(body.text)) if isinstance(n, ast.Subscript)}


def pruned(doc: Document, names: set) -> Document:
    """``doc`` without the code templates of ``names`` no call names, then without the state
    variables and tables nothing reads."""
    called = {node.name for _, node in walk(doc) if isinstance(node, Call)}
    mods = tuple(
        (k, v)
        for k, v in doc.mods
        if _code.signature(k)[0] in called or _code.signature(k)[0] not in names
    )
    doc = replace(doc, mods=mods)
    reads = _reads(doc)
    state = tuple(
        (cell, kept)
        for cell, names_ in doc.header.state or ()
        if (kept := tuple((n, s) for n, s in names_ if n in reads))
    )
    tables = tuple((k, v) for k, v in doc.tables if k in reads)
    return replace(doc, header=replace(doc.header, state=state or None), tables=tables)


@dataclass
class Check:
    """A candidate's verdict against the input: whether every register outside its targeted
    cells plays the input's, and each targeted cell's ``(p50, p95, max)`` error."""

    exact: bool
    stats: dict

    def holds(self, tolerances: dict) -> bool:
        """Whether every register outside the targets matches and each target keeps to its
        tolerance."""
        return self.exact and all(
            s[1] <= tolerances[c].p95 and s[2] <= tolerances[c].max for c, s in self.stats.items()
        )


def check(reference: np.ndarray, text: str, ticks, cells: dict, tolerances: dict) -> Check:
    """The verdict on candidate ``text`` against ``reference`` frames: ``cells`` maps each
    targeted ``(cell, voice)`` to the ticks a replaced call fired on it, whose errors (with
    every tick the cell differs) are measured."""
    try:
        frames, _ = grid(text, ticks)
    except ValueError:
        return Check(False, {})
    if frames.shape != reference.shape:
        return Check(False, {})
    keep = np.ones(reference.shape[1], dtype=bool)
    for cell, voice in cells:
        keep[list(_registers(cell, voice))] = False
    errs = defaultdict(list)
    for (cell, voice), ticks_ in cells.items():
        ref, got = cell_values(reference, cell)[:, voice], cell_values(frames, cell)[:, voice]
        at = np.zeros(len(ref), dtype=bool)
        at[ticks_] = True
        at |= ref != got
        errs[cell].append(fit.error(cell, tolerances[cell], got[at], ref[at]))
    stats = {c: fit.stats(np.concatenate(e)) for c, e in errs.items()}
    return Check(bool(np.array_equal(frames[:, keep], reference[:, keep])), stats)


@dataclass
class Lossy(Rewrite):
    """The lossy mode's result: its document and text, sizes before and after, the
    replacements kept and refused, the targeted cells' ``(p50, p95, max)`` errors and why each
    coupled group no plan fits is unfit."""

    stats: dict = field(default_factory=dict)
    unfit: list = field(default_factory=list)

    def report(self) -> list:
        """The result's report lines."""
        lines = [f"lossy: {r.label}" for r in self.kept]
        lines += [f"lossy refused: {r.name}" for r in self.refused]
        lines += [f"lossy unfit: {line}" for line in self.unfit]
        lines += [
            f"lossy {c} error: p50 {s[0]:.1f} p95 {s[1]:.1f} max {s[2]:.1f}"
            for c, s in sorted(self.stats.items())
        ]
        lines.append(f"lossy: {self.before} -> {self.after} tokens")
        return lines


def targets(firings: dict, replacements: list) -> dict:
    """The ``(cell, voice)`` cells ``replacements`` target, each with the ticks a replaced
    call of ``firings`` fired on it (voice 0 for the cutoff)."""
    out = defaultdict(list)
    calls = {c for r in replacements for c in r.calls}
    for (call, cell), rec in firings.items():
        if call in calls:
            voices = np.zeros(len(rec), dtype=int) if cell == "cut" else rec["voice"]
            for voice in np.unique(voices):
                out[cell, int(voice)].append(rec["t"][voices == voice])
    return {k: np.concatenate(v) for k, v in out.items()}


class Proof:  # pylint: disable=too-few-public-methods
    """The replacements kept so far, the document they give and its size; ``verdict`` of a
    text and its replacements the :class:`Check` against the input."""

    def __init__(self, doc: Document, verdict, tolerances: dict):
        self.base, self.verdict, self.tolerances = doc, verdict, tolerances
        self.kept, self.refused, self.stats = [], [], {}
        self.size = size(printed(doc))

    def prove(self, replacements: list, meter) -> None:
        """Keeps ``replacements`` together where they shrink the document and hold, else each
        half in turn."""
        trial = self.kept + replacements
        text = printed(applied(self.base, trial))
        smaller, verdict = size(text), self.verdict(text, trial)
        if smaller < self.size and verdict.holds(self.tolerances):
            self.kept, self.size, self.stats = trial, smaller, verdict.stats
            meter.update(len(replacements))
            return
        if len(replacements) == 1:
            self.refused += replacements
            meter.update(1)
            return
        half = len(replacements) // 2
        self.prove(replacements[:half], meter)
        self.prove(replacements[half:], meter)

    def alternatives(self, meter) -> None:
        """Each refused group's other plans proved in turn, the first that holds kept in its
        place, else its templates' own replacements proved as the rest."""
        for r in [r for r in self.refused if r.alternatives or r.fallback]:
            for alt in [[a] for a in r.alternatives] + ([r.fallback] if r.fallback else []):
                kept, refused = len(self.kept), len(self.refused)
                self.prove(alt, meter)
                if len(self.kept) > kept:
                    if alt is not r.fallback:
                        self.refused.remove(r)
                    break
                del self.refused[refused:]


def simplify(text: str, ticks: int | None, tolerances=None, progress: bool = False) -> Lossy:
    """``text`` with each code modulator call that fits an expression form within
    ``tolerances`` (by cell, :data:`fit.TOLERANCES` by default) a call of that form's template,
    kept where the document shrinks, every register outside the replaced calls' cells plays the
    input's and their cells keep to their tolerance over ``ticks`` (the song's end where None)."""
    tolerances = fit.TOLERANCES | (tolerances or {})
    if ticks is None and load(text).loop is not None:
        raise GuaranteeError("the lossy mode needs ticks for a looping song")
    doc = read(text)
    tr = record(text, ticks)

    def verdict(candidate: str, replacements: list) -> Check:
        return check(tr.frames, candidate, ticks, targets(tr.firings, replacements), tolerances)

    replacements, unfit = propose(doc, tr, tolerances, progress)
    proof = Proof(doc, verdict, tolerances)
    replacements = [x for r in replacements for x in _screened(proof, r)]
    total = sum(1 + len(r.alternatives) + len(r.fallback) for r in replacements)
    with tqdm(total=total, desc="lossy prove", unit="mod", disable=not progress) as meter:
        if replacements:
            proof.prove(replacements, meter)
            proof.alternatives(meter)
    out, names = renumbered(applied(doc, proof.kept), Names.of(doc))
    for r in proof.kept:
        r.calls = {k: _retitled(names, v) for k, v in r.calls.items()}
    return Lossy(
        out, printed(out), size(text), proof.size, proof.kept, proof.refused, proof.stats, unfit
    )


def _screened(proof: Proof, r: Replacement) -> list:
    """Of ``r`` and its alternatives, the one that shrinks the document alone the most, the
    rest that do its alternatives and its fallback's that do its fallback; else those of its
    fallback."""
    options, fallback = [r, *r.alternatives], r.fallback
    for option in options + fallback:
        option.saved = proof.size - size(printed(applied(proof.base, [option])))
        option.alternatives, option.fallback = [], []
    options = sorted((o for o in options if o.saved > 0), key=lambda o: -o.saved)
    fallback = [o for o in fallback if o.saved > 0]
    if not options:
        return fallback
    options[0].alternatives, options[0].fallback = options[1:], fallback
    return options[:1]


def _retitled(names: dict, node):
    """``node``, a call of a template of ``names`` called by its new name."""
    if isinstance(node, Call) and node.name in names:
        return Call(names[node.name], node.args)
    return node


def renumbered(doc: Document, before: Names) -> tuple:
    """``(doc, {old: new name})``: ``doc`` with the templates the lossy mode added (named
    nothing ``before`` names) merged where their signatures and bodies are the same, and named
    in order, each its stem and, after the first, the least count no other name of ``doc``
    takes."""
    added = [(k, b) for k, b in doc.mods if _code.signature(k)[0] not in before.taken]
    kept = Names.of(replace(doc, mods=tuple(m for m in doc.mods if m not in added)))
    used, names, first, mods = kept.taken | before.reserved | _RESERVED, {}, {}, []
    for key, body in doc.mods:
        name, params = _code.signature(key)
        if (key, body) not in added:
            mods.append((key, body))
        elif (params, body) in first:
            names[name] = first[params, body]
        else:
            names[name] = first[params, body] = _name(name.rstrip("0123456789"), used)
            mods.append((signature(names[name], params), body))
    if all(k == v for k, v in names.items()):
        return doc, {}
    return replace(transform(doc, partial(_retitled, names)), mods=tuple(mods)), names


def add_arguments(parser) -> None:
    """The lossy mode's command line options on ``parser``."""
    parser.add_argument(
        "--lossy",
        action="store_true",
        help="first replace code modulators by expressions fitted within the tolerances",
    )
    for cell, unit in (("freq", "cents"), ("pw", "register units"), ("cut", "register units")):
        parser.add_argument(
            f"--{cell}-tol",
            type=fit.Tolerance.parse,
            default=fit.TOLERANCES[cell],
            metavar="P95,MAX",
            help=f"--lossy: the {cell} error bound in {unit}"
            + (", or percent of the value with a %% suffix" if cell != "freq" else "")
            + f" (default {fit.TOLERANCES[cell]})",
        )


def tolerances_of(args) -> dict | None:
    """The tolerances by cell of parsed ``args``, None without ``--lossy``."""
    if not args.lossy:
        return None
    return {cell: getattr(args, f"{cell}_tol") for cell in fit.BITS}
