"""The lossy mode: each code modulator call replaced by a call of an expression template
fitted to its firings, within a tolerance per cell, every other register kept
(docs/optimiser.md)."""

import ast
from collections import Counter, defaultdict
from dataclasses import dataclass, field, fields, is_dataclass, replace
from functools import partial

import numpy as np
from tqdm import tqdm
from unitracker_grammar import PAL_HZ
from unitracker_grammar import code as _code
from unitracker_grammar.document import Call, Code, Document, Expr, printed, read
from unitracker_grammar.expr import canonical, tree
from unitracker_player.player import load

from unitracker.optimiser import fit
from unitracker.optimiser.guarantee import GuaranteeError, grid
from unitracker.optimiser.measure import Rewrite, size
from unitracker.optimiser.trace import trace
from unitracker.optimiser.tree import transform, walk

_RESERVED = frozenset({"v", "phi", "nn", "ne", "a", "x", "y", "n", "none", "hold", "reset"})
_STEMS = {("keep", "F(N)"): "note", ("keep", "F(n)"): "event"}
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
    """One code template's calls becoming calls of expression templates: ``calls`` maps each
    replaced call to its new one, ``mods`` and ``tables`` are the records it adds, ``err`` the
    fitted errors by cell, ``saved`` the tokens it saves alone."""

    name: str
    cell: str
    calls: dict
    mods: list
    tables: list
    err: np.ndarray = field(repr=False)
    saved: int = 0

    @property
    def label(self) -> str:
        """The replacement's report line."""
        news = ", ".join(sorted({c.name for c in self.calls.values()}))
        p50, p95, top = fit.stats(self.err)
        return (
            f"{self.name} ({len(self.calls)} calls, {self.cell}) -> {news}: "
            f"saves {self.saved} tokens, error p50 {p50:.1f} p95 {p95:.1f} max {top:.1f}"
        )


def _occurrences(doc: Document) -> Counter:
    return Counter(node for _, node in walk(doc) if isinstance(node, Call))


def _name(stem: str, taken: set) -> str:
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


def _template(group: list, taken: set, reserved=frozenset()) -> tuple:
    """``(signature, Expr, table, {call: new call})`` of one expression template for
    ``group``'s ``(call, form)`` pairs of one form key: a parameter per value that differs
    between them, the rest literal, a parameter named as one of ``reserved`` renamed."""
    forms = [f for _, f in group]
    params = {k: [f.params[k] for f in forms] for k in forms[0].params if k != "table"}
    table = _table(forms, params, taken) if forms[0].shape in fit.TABLES else None
    varying = [k for k, vals in params.items() if len(set(vals)) > 1]
    scaled = _scaled(params, varying)
    varying = [k for k in varying if k not in scaled]
    names = {}
    for k in varying:
        names[k] = k
        while names[k] in reserved | _RESERVED:
            names[k] += "_"
    text = forms[0].text().replace("TABLE", table[0] if table else "TABLE")
    name = _name(_STEMS.get(forms[0].key[:2], forms[0].shape), taken)
    calls = {
        call: Call(name, tuple(int(params[k][i]) for k in varying))
        for i, (call, _) in enumerate(group)
    }
    sig = f"{name}({', '.join(names.values())})"
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


def _cost(template: tuple, occurrences: Counter) -> int:
    sig, body, table, calls = template
    text = f"  {sig}: {body}\n" + (f"  {table[0]}: {list(table[1])}\n" if table else "")
    return size(text) + sum(occurrences[c] * size(str(n)) for c, n in calls.items())


@dataclass(frozen=True)
class _Names:
    """A document's calls counted, the names its templates and tables take and the state
    variables a parameter may not be named."""

    occurrences: Counter
    taken: set
    reserved: frozenset

    @classmethod
    def of(cls, doc: Document) -> "_Names":
        """The names of ``doc``."""
        taken = {_code.signature(k)[0] for k, _ in doc.mods} | {n for n, _ in doc.tables}
        reserved = frozenset(n for _, names in doc.header.state or () for n, _ in names)
        return cls(_occurrences(doc), taken, reserved)


def _choose(options: dict, names: _Names) -> list:
    """The ``(call, form)`` groups, one template each, for one code template's calls and
    their fitting forms: the cheapest of each form key fitting every call, and each call's
    cheapest form grouped by key."""

    def cost(plan):
        return sum(
            _cost(_template(g, set(names.taken), names.reserved), names.occurrences) for g in plan
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


def _replacement(name: str, options: dict, calls: dict, names: _Names) -> Replacement:
    """The replacement of code template ``name``'s calls of ``options`` (each with its fitting
    forms, in ``calls`` with its ``(cell, firings)``)."""
    cell = "/".join(sorted({calls[c][0] for c in options}))
    out, errs = Replacement(name, cell, {}, [], [], np.empty(0)), []
    for group in _choose(options, names):
        sig, body, table, calls = _template(group, names.taken, names.reserved)
        out.calls |= calls
        out.mods.append((sig, body))
        out.tables += [table] if table else []
        errs += [form.err for _, form in group]
    out.err = np.concatenate(errs)
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


def propose(doc: Document, firings: dict, tolerances: dict, progress: bool = False) -> list:
    """A :class:`Replacement` per code template some of whose calls fit a form within its
    cell's tolerance over every firing, those calls to calls of expression templates."""
    clock = (doc.header.clock or (None, PAL_HZ))[1] or PAL_HZ
    codes = {_code.signature(k)[0] for k, v in doc.mods if isinstance(v, Code)}
    by_template, cells = defaultdict(dict), Counter(call for call, _ in firings)
    for (call, cell), rec in firings.items():
        by_template[call.name][call] = (cell, rec)
    names, out = _Names.of(doc), []
    for name in tqdm(sorted(by_template), desc="lossy fit", unit="mod", disable=not progress):
        options = _options(by_template[name], cells, tolerances, clock) if name in codes else {}
        if options:
            out.append(_replacement(name, options, by_template[name], names))
    return out


def _renamed(calls: dict, node):
    return calls.get(node, node) if isinstance(node, Call) else node


def applied(doc: Document, replacements: list) -> Document:
    """``doc`` with ``replacements``' calls renamed and records added, then every code
    template they replaced that no call names any more dropped, with the state variables and
    tables nothing reads any more."""
    calls = {old: new for r in replacements for old, new in r.calls.items()}
    doc = transform(doc, partial(_renamed, calls))
    doc = replace(
        doc,
        mods=doc.mods + tuple(m for r in replacements for m in r.mods),
        tables=doc.tables + tuple(t for r in replacements for t in r.tables),
    )
    return pruned(doc, {r.name for r in replacements})


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
    replacements kept and refused, and the targeted cells' ``(p50, p95, max)`` errors."""

    stats: dict = field(default_factory=dict)

    def report(self) -> list:
        """The result's report lines."""
        lines = [f"lossy: {r.label}" for r in self.kept]
        lines += [f"lossy refused: {r.name}" for r in self.refused]
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


class _Proof:  # pylint: disable=too-few-public-methods
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


def simplify(text: str, ticks: int | None, tolerances=None, progress: bool = False) -> Lossy:
    """``text`` with each code modulator call that fits an expression form within
    ``tolerances`` (by cell, :data:`fit.TOLERANCES` by default) a call of that form's template,
    kept where the document shrinks, every register outside the replaced calls' cells plays the
    input's and their cells keep to their tolerance over ``ticks`` (the song's end where None)."""
    tolerances = fit.TOLERANCES | (tolerances or {})
    if ticks is None and load(text).loop is not None:
        raise GuaranteeError("the lossy mode needs ticks for a looping song")
    doc = read(text)
    reference, firings = trace(text, ticks)

    def verdict(candidate: str, replacements: list) -> Check:
        return check(reference, candidate, ticks, targets(firings, replacements), tolerances)

    replacements = propose(doc, firings, tolerances, progress)
    proof = _Proof(doc, verdict, tolerances)
    for r in replacements:
        r.saved = proof.size - size(printed(applied(doc, [r])))
    replacements = [r for r in replacements if r.saved > 0]
    with tqdm(
        total=len(replacements), desc="lossy prove", unit="mod", disable=not progress
    ) as meter:
        if replacements:
            proof.prove(replacements, meter)
    out = applied(doc, proof.kept)
    return Lossy(out, printed(out), size(text), proof.size, proof.kept, proof.refused, proof.stats)


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
