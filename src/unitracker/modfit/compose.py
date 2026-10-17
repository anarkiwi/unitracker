"""Command composition: a keyword's setup and per-tick effects as guarded updates.

A keyword's setup is the spec's action template with ``CODE`` and ``arg`` bound, run from a row
template. A tick list joins when it reads a cell the setup writes; cells the tick list moves are
freed, the rest keep their setup values, and each moved cell is fitted as one accumulator.
"""

import functools
import re

import z3
from tqdm import tqdm

from unitracker.modfit import playroutine, resolve, specs
from unitracker.modfit.fit import Fitter, src
from unitracker.modfit.flow import Liveness, action_lists, flow_of, persistent, walk
from unitracker.modfit.flow import names as names_of
from unitracker.modfit.form import Form, is_cond
from unitracker.modfit.flow import Writes
from unitracker.eventins import written_as
from unitracker.records import record_types

ARG = "arg"


def bind(doc, code: int):
    """``doc`` with every ``CODE`` in its strings replaced by ``code``."""
    if isinstance(doc, list):
        return [bind(x, code) for x in doc]
    if isinstance(doc, dict):
        return {k: bind(v, code) for k, v in doc.items()}
    if isinstance(doc, str):
        text = re.sub(r"\bCODE\b", str(code), doc)
        return int(text) if text == str(code) else text
    return doc


def keywords(pr: dict) -> list:
    """``[(code, name, when)]`` of a playroutine's ``commands`` map, variants in order; a code
    with no name of its own is named by the instrument key it is written as."""
    out = []
    for code, entry in (pr.get("commands") or {}).items():
        for e in entry if isinstance(entry, list) else [entry]:
            out.append((int(code), e.get("name") or written_as(pr, int(code)), e.get("when")))
    return out


class Merge(Form):
    """A form that joins branches: each cell's value one ``ite`` term, every proc recorded.

    ``fetch`` leaves the event cells as given, ``for_voices`` runs once for this voice and
    ``run`` takes a template with ``row`` bound, or each record row where its index is hit.
    """

    called: set = set()
    joins: set = set()

    def enter(self, proc: str) -> None:
        """Record each proc evaluated."""
        self.called = self.called | {proc}

    def merge(self, actions, state: dict, conds=()) -> tuple:
        """``(state, live)``: ``live`` holds while control is still in ``actions``.

        A ``return`` clears ``live``; later assignments keep the old value where it is false.
        Branches the path conditions ``conds`` rule out are skipped.
        """
        state, live = dict(state), True
        for a in actions or []:
            if live is False:
                break
            live = self.step(a, state, live, conds)
        return state, live

    def pick(self, c, a, b):
        """``a if c else b`` at a join, each side simplified under its condition."""
        if isinstance(c, bool):
            return a if c else b
        out = self.ite(c, self.under(a, c), self.under(b, self.negate(c)))
        self.joins = (
            self.joins | {c} | ({out[1]} if isinstance(out, tuple) and out[0] == "ite" else set())
        )
        return out

    def under(self, t, cond):
        """``t`` with every ``ite`` that ``cond``'s conjuncts decide replaced by its branch."""
        lits = {}
        for x in cond[1:] if isinstance(cond, tuple) and cond[0] == "land" else [cond]:
            lits[x], lits[self.negate(x)] = True, False
        return self.rebuild(t, lits, {}) if lits else t

    def rebuild(self, t, lits: dict, memo: dict):
        """``t`` rebuilt through the normalising constructors, subterms in ``lits`` replaced."""
        if not isinstance(t, tuple):
            return lits.get(t, t) if isinstance(t, str) else t
        if id(t) not in memo:
            memo[id(t)] = (t, self.reapply(t, lits, memo))
        return memo[id(t)][1]

    def reapply(self, t, lits: dict, memo: dict):
        """One node of ``rebuild``."""
        # pylint: disable=too-many-return-statements
        if t in lits:
            return lits[t]
        head = t[0]
        if head == "lin":
            return self.add(t[1], *(self.scale(k, self.rebuild(a, lits, memo)) for a, k in t[2]))
        if head in ("idx", "rec", "fld"):
            arg = self.rebuild(t[2], lits, memo)
            if head == "idx":
                return self.subscript(t[1], self.num(arg))
            return ("rec", t[1], arg) if head == "rec" else self.field(arg, t[1])
        args = [self.rebuild(x, lits, memo) for x in t[1:]]
        if (
            head == "ite"
            and not isinstance(args[0], bool)
            and {t[1], self.negate(t[1])} & self.joins
        ):
            self.joins = self.joins | {args[0]}
        ops = {
            "ite": self.ite,
            "lt": lambda e, _: self.ltz(e),
            "eq": lambda e, _: self.eq(e, 0),
            "and": self.band,
            "or": self.bor,
            "xor": self.bxor,
            "shr": self.shr,
            "mul": self.mul,
            "not": self.negate,
            "iv": self.iv,
        }
        if head in ops:
            return ops[head](*args)
        return self.junction(head, args) if head in ("land", "lor") else (head, *args)

    def subst(self, t, lits: dict):
        """``t`` with each cell or subterm in ``lits`` replaced, renormalised."""
        return self.rebuild(t, lits, {})

    def put(self, state: dict, cell: str, value, live) -> None:
        """``cell := value`` where ``live`` holds."""
        state[cell] = value if live is True else self.pick(live, value, state.get(cell, cell))

    def join(self, state: dict, other: dict, cond, live) -> None:
        """Take ``other``'s changed cells where ``cond`` holds."""
        for k, v in other.items():
            if v != state.get(k, k):
                self.put(state, k, self.pick(cond, v, state.get(k, k)), live)

    def step(self, a: dict, state: dict, live, conds):
        """Run one action on ``state`` in place; the new ``live``."""
        # pylint: disable=too-many-return-statements
        if "return" in a:
            return False
        if "set" in a or "with" in a:
            for k, v in {**(a.get("with") or {}), **(a.get("set") or {})}.items():
                self.put(state, k, self.wrap(k, self.expr(v, state)), live)
        if "if" in a:
            return self.merge_if(a, state, live, conds)
        if "call" in a:
            self.enter(a["call"])
            self.join(state, self.merge(self.procs[a["call"]], state, conds)[0], True, live)
        elif "accumulate" in a:
            return self.merge_acc(a, state, live, conds)
        elif "for_voices" in a:
            self.join(state, self.merge(a["for_voices"], state, conds)[0], True, live)
        elif "run" in a:
            self.merge_run(a["run"], state, live, conds)
        elif "set" not in a and "fetch" not in a:
            raise ValueError(f"unsupported action {a!r}")
        return live

    def merge_if(self, a: dict, state: dict, live, conds):
        """Both feasible branches from ``state``, joined under the test."""
        c = self.truth(self.expr(a["if"], state))
        outs = []
        for g, body in ((c, a.get("then")), (self.negate(c), a.get("else"))):
            if g is not False and (g is True or self.feasible((*conds, g))):
                outs.append((g, *self.merge(body, state, conds if g is True else (*conds, g))))
        old = dict(state)
        changed = {k for _, st, _ in outs for k, v in st.items() if v != old.get(k, k)}
        for k in changed:
            vals = [st.get(k, old.get(k, k)) for _, st, _ in outs]
            self.put(state, k, vals[0] if len(outs) == 1 else self.pick(outs[0][0], *vals), live)
        ends = [self.junction("land", [g, end]) for g, _, end in outs]
        return self.junction("land", [live, self.junction("lor", ends)])

    def merge_acc(self, a: dict, state: dict, live, conds):
        """The accumulator's cases joined; ``on_bound`` where a bound is hit."""
        steps = [
            (self.junction("land", list(g)), v, h)
            for g, v, h in self.acc_cases(a, state)
            if self.feasible((*conds, *g))
        ]
        value = steps[-1][1]
        for g, v, _ in reversed(steps[:-1]):
            value = self.pick(g, v, value)
        self.put(state, a["accumulate"], value, live)
        hit = self.junction("lor", [g for g, _, h in steps if h])
        if hit is False or not a.get("on_bound"):
            return live
        bound, out = self.merge(a["on_bound"], state, (*conds, hit))
        self.join(state, bound, hit, live)
        return self.junction("land", [live, self.junction("lor", [self.negate(hit), out])])

    def merge_run(self, run: dict, state: dict, live, conds) -> None:
        """A ``run``: a template with ``row`` bound, or each record row where its index is hit."""
        index = self.num(self.expr(run["index"], state))
        rt = record_types(self.pr).get(run["record"])
        if rt is not None and run["field"] in rt.templates:
            row = state.get("row", "row")
            out, _ = self.merge(
                rt.templates[run["field"]], {**state, "row": ("rec", run["record"], index)}, conds
            )
            out["row"] = row
            self.join(state, out, True, live)
            return
        for i, r in ((self.pr.get("records") or {}).get(run["record"]) or {}).items():
            hit = self.eq(index, int(i))
            if hit is not False and self.feasible((*conds, hit)):
                self.join(
                    state,
                    self.merge((r or {}).get(run["field"]), state, (*conds, hit))[0],
                    hit,
                    live,
                )


def cases(form: Merge, t, conds=()) -> list:
    """``[(conds, value)]`` of a joined term split at every recorded join condition."""
    if isinstance(t, tuple) and t[0] == "ite" and {t[1], form.negate(t[1])} & form.joins:
        out = []
        for c, branch in ((t[1], t[2]), (form.negate(t[1]), t[3])):
            if form.feasible((*conds, c)):
                out += cases(form, branch, (*conds, c))
        return out
    return [(conds, t)]


def unfold(form: Form, t, old, conds=()) -> list:
    """``[(conds, value)]`` of a joined term: split where a branch keeps ``old``, else whole."""
    if isinstance(t, tuple) and t[0] == "ite":
        out, kept = [], False
        for c, branch in ((t[1], t[2]), (form.negate(t[1]), t[3])):
            if form.feasible((*conds, c)):
                parts = unfold(form, branch, old, (*conds, c))
                kept |= any(v == old for _, v in parts) or not parts
                out += [(cs, v) for cs, v in parts if v != old]
        if kept:
            return out
    return [(conds, t)] if t != old else []


class Composer:
    """Keyword compositions of one library playroutine under its ``commands`` spec."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, name: str):
        pr, spec = playroutine(name), specs()["commands"][name]
        if ARG not in (pr.get("voice") or {}) and ARG not in (pr.get("global") or {}):
            pr = {**pr, "voice": {**(pr.get("voice") or {}), ARG: {"width": 8}}}
        self.name, self.pr, self.spec = name, pr, spec
        self.form = Merge(pr)
        self.fitter = Fitter(pr, self.form)
        self.routing = set(spec["routing"])
        self.keep = persistent(pr) - self.routing
        lv = Liveness(pr)
        self.ticks = [
            (t, resolve(pr, t)) if isinstance(t, str) else (str(t), t) for t in spec["ticks"]
        ]
        self.reads = [lv.seq(t, set())[0] for _, t in self.ticks]
        self.chain_procs = set()
        self.conds = self.routed_conditions()
        self.flow = flow_of(pr)
        self.fits, self.bases = {}, {}
        self.writes = Writes(pr)

    def routed_conditions(self) -> list:
        """``(path, source)`` of each test outside the setup's procs that reads a routing cell."""
        chain, todo = self.chain_procs, [self.spec["setup"]]
        while todo:
            for doc in walk(todo.pop()):
                if "call" in doc and doc["call"] not in chain:
                    chain.add(doc["call"])
                    todo.append(self.pr["procs"][doc["call"]])
        out = []
        for path, actions in action_lists(self.pr).items():
            if path.split(".")[-1] in chain and path.startswith("procs."):
                continue
            out += [(path, t) for t in tests(actions) if names_of(t) & self.routing]
        return out

    def reapplied(self) -> set:
        """Setup procs that also run from an action list outside the setup."""
        outside = [
            a for p, a in action_lists(self.pr).items() if p.split(".")[-1] not in self.chain_procs
        ]
        return {d["call"] for d in walk(outside) if d.get("call") in self.chain_procs}

    def start(self, row: str) -> dict:
        """The row template's state."""
        return dict(self.spec["rows"][row])

    def differ(self, conds, a: dict, b: dict, cells) -> set:
        """The ``cells`` whose values in states ``a`` and ``b`` can differ under ``conds``."""
        return {
            c
            for c in cells
            if a.get(c, c) != b.get(c, c) and can_differ(self.form, conds, a.get(c, c), b.get(c, c))
        }

    def baseline(self, row: str) -> dict:
        """The setup state of a row with no command."""
        if row not in self.bases:
            self.bases[row] = self.setup(0, None, row)[1]
        return self.bases[row]

    def setup(self, code: int, when, row: str) -> tuple:
        """``(conds, state)``: the setup from the row template, branches joined."""
        f = self.form
        conds = () if when is None else (f.truth(f.expr(when, {})),)
        return conds, f.merge(bind(self.spec["setup"], code), self.start(row), conds)[0]

    def chain(self, lists, conds, state) -> dict:
        """Action lists run one after another, joined into one state."""
        for actions in lists:
            state = self.form.merge(actions, state, conds)[0]
        return state

    def settle(self, conds, state: dict, lists) -> tuple:
        """``(tick start, joined tick end)``: the cells the lists move freed to a fixpoint."""
        moved = set()
        while True:
            begin = {k: v for k, v in state.items() if k not in moved}
            end = self.chain(lists, conds, begin)
            grown = moved | {c for c, v in end.items() if v != begin.get(c, c)}
            if grown == moved:
                return begin, end
            moved = grown

    def tick(self, conds, state: dict, changed: set) -> tuple:
        """``(chosen tick lists, tick start, joined tick end)``: the lists reading ``changed``."""
        chosen = [x for x, r in zip(self.ticks, self.reads) if r & changed]
        return (chosen, *self.settle(conds, state, [t for _, t in chosen]))

    def fit(self, cell: str, chosen: list, conds, begin: dict) -> list:
        """The per-tick fits of ``cell``, its cases split at the joins of the lists writing it."""
        paths = self.tick_paths(cell, chosen, conds, begin)
        key = (cell, tuple((cs, st.get(cell, cell)) for cs, st in paths))
        if key not in self.fits:
            self.fits[key] = self.fitter.fit_paths(paths, cell)
        return self.fits[key]

    def tick_paths(self, cell: str, chosen: list, conds, begin: dict) -> list:
        """``[(conds, state)]`` of ``cell``'s per-tick cases, split at the joins, pairs fused."""
        f = self.form
        parts = f.pair[cell][:2] if cell in f.pair else (cell,)
        f.joins = set()
        end = self.chain([t for _, t in chosen if self.writes.of(t) & set(parts)], conds, begin)
        paths = [((), {})]
        for p in parts:
            paths = [
                (cs, {**st, p: v})
                for prev, st in paths
                for cs, v in cases(f, end.get(p, p), prev or conds)
            ]
        return f.fused(paths) if cell in f.pair else paths

    def changes(self, conds, state: dict, start: dict) -> list:
        """``[(conds, {cell: value})]``: each reported cell's changed cases."""
        return [
            (cs, {c: v})
            for c in sorted(self.keep & set(state))
            if state[c] != start.get(c, c)
            for cs, v in unfold(self.form, state[c], start.get(c, c), conds)
        ]

    def compose(self, code: int, when, row: str, fits: bool = True) -> dict:
        """Setup, per-tick updates, per-tick fits and routed tests of one keyword on a row.

        On a row other than ``plain``, cells are reported only where they can differ from the
        same row with no command.
        """
        # pylint: disable=too-many-locals
        start = self.start(row)
        conds, state = self.setup(code, when, row)
        changed = {c for c, v in state.items() if v != start.get(c, c)}
        base = self.baseline(row) if row != "plain" else None
        if base is not None:
            changed = self.differ(conds, state, base, changed | set(base))
        self.form.called = set()
        chosen, begin, end = self.tick(conds, state, changed)
        called = self.form.called
        moved = {c for c, v in end.items() if c in self.keep and v != begin.get(c, c)}
        if base is not None:
            other = self.settle(conds, base, [t for _, t in chosen])[1]
            moved = self.differ(conds, end, other, moved)
        fitted = set(moved)
        for name, (lo, hi, _, _) in self.form.pair.items():
            if {lo, hi} <= fitted:
                fitted = (fitted - {lo, hi}) | {name}
        return {
            "start": start,
            "conds": conds,
            "state": state,
            "setup_upd": self.changes(conds, {c: state.get(c, c) for c in changed}, start),
            "ticks": [n for n, _ in chosen],
            "begin": begin,
            "end": end,
            "tick_upd": self.changes(conds, {c: end[c] for c in moved}, begin),
            "fits": {c: self.fit(c, chosen, conds, begin) for c in sorted(fitted) if fits},
            "tests": self.routed(conds, state, start),
            "called": called,
            "chosen": chosen,
            "moved": moved,
        }

    def influences(self, eff: dict, cells: set) -> set:
        """The setup ``cells`` whose value the joined tick end depends on."""
        lists = [t for _, t in eff["chosen"]]
        out = set()
        for c in cells - set(eff["moved"]):
            begin = {k: v for k, v in eff["begin"].items() if k != c}
            end = self.chain(lists, eff["conds"], begin)
            if any(end.get(k, k) != eff["end"].get(k, k) for k in self.keep - {c}):
                out.add(c)
        return out

    def routed(self, conds, state: dict, start: dict) -> list:
        """``(path, source, value)`` of the routed tests whose value the keyword changes."""
        f, out = self.form, []
        for path, text in self.conds:
            base, value = (f.truth(f.expr(text, st)) for st in (start, state))
            if value != base and can_differ(f, conds, value, base):
                out.append((path, text, value))
        return out


def can_differ(form: Form, conds, a, b) -> bool:
    """True when terms or conditions ``a`` and ``b`` can differ under ``conds``."""
    smt = form.solver()
    smt.solver.add(*(smt.cond(c) for c in conds))
    pick = smt.cond if is_cond(a) or is_cond(b) else smt.term
    smt.solver.add(pick(a) != pick(b))
    return smt.solver.check() != z3.unsat


def leaves(t) -> set:
    """The solver atoms of a term: cell names and table, record and field symbols."""
    if isinstance(t, str):
        return {t}
    if not isinstance(t, tuple):
        return set()
    if t[0] == "lin":
        return set().union(*(leaves(a) for a, _ in t[2]))
    if t[0] in ("idx", "rec", "fld"):
        return {f"{t[0]}:{t[1]}"} | leaves(t[2])
    return set().union(*(leaves(x) for x in t[1:]))


def tests(doc) -> list:
    """Every ``if`` source in an action list, nested ones included."""
    if isinstance(doc, list):
        return [t for a in doc for t in tests(a)]
    if not isinstance(doc, dict):
        return []
    own = [doc["if"]] if "if" in doc else []
    return own + [t for k, v in doc.items() if isinstance(v, (list, dict)) for t in tests(v)]


@functools.lru_cache(maxsize=None)
def composer(name: str) -> Composer:
    """The cached composer of a library playroutine."""
    return Composer(name)


def when_text(conds) -> str:
    """Source of a condition list, ``always`` when empty."""
    return " and ".join(src(c) for c in conds) or "always"


def command_lines(name: str, only: str | None = None) -> list:
    """Report lines of each keyword's composition on each row kind."""
    c = composer(name)
    lines = []
    units = [
        (k, n, w, r) for k, n, w in keywords(c.pr) if only in (None, n) for r in c.spec["rows"]
    ]
    for code, kw, when, row in tqdm(units, desc=f"{name} commands", unit="row"):
        e = c.compose(code, when, row)
        lines.append(f"== {name} !{kw} ({code}{', when ' + when if when else ''}), {row} row")
        lines += [
            f"   setup {cell} := {src(v)}  when {when_text(cs)}"
            for cs, u in e["setup_upd"]
            for cell, v in u.items()
        ]
        lines.append(f"   ticks: {', '.join(e['ticks']) or 'none'}")
        lines += [
            f"   tick {cell} := {src(v)}  when {when_text(cs)}"
            for cs, u in e["tick_upd"]
            for cell, v in u.items()
        ]
        for cell, groups in e["fits"].items():
            for guard, found in groups:
                lines.append(
                    f"   fit {cell}: {found or 'no single accumulator'}  when {when_text(guard)}"
                )
        lines += [f"   routed test {p}: {t} -> {src(v)}" for p, t, v in e["tests"]]
    return lines
