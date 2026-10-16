"""Effect signatures of keywords and record fields, matched across playroutines.

An entry is ``(register role, operator kind, argument use)``: the SID bits the effect reaches
through the ghost dataflow, the kind from the composed setup and the per-tick fits, and how the
argument (or field) enters, as bits reaching the role or the table it indexes.
"""

import ast
import functools

from tqdm import tqdm

from unitracker.modfit import specs
from unitracker.modfit.compose import ARG, can_differ, composer, keywords, leaves, tests
from unitracker.modfit.fit import src
from unitracker.modfit.flow import NAMED, REGISTERS, action_lists, names, parse, walk, walk_sets
from unitracker.records import record_types

FULL = {"FREQ": 16, "PW": 12, "CUT": 11}
ORDER = list(dict.fromkeys(q for q, _, _ in REGISTERS.values())) + ["TIMING"]
NONE = "-"


def bits(m: int) -> list:
    """The set bits of ``m``, lowest first."""
    return [i for i in range(m.bit_length()) if m >> i & 1]


def span(xs) -> str:
    """``a:b`` for a contiguous run, else the comma list."""
    xs = sorted(xs)
    if xs == list(range(xs[0], xs[-1] + 1)):
        return f"{xs[0]}:{xs[-1]}" if len(xs) > 1 else str(xs[0])
    return ",".join(map(str, xs))


def qtext(q: str, qbits) -> str:
    """A quantity and its bits: ``FREQ``, ``CUT[3:10]``, ``AD:A``, ``CTRL:GATE``."""
    if q in NAMED:
        parts = sorted({NAMED[q][b] for b in qbits if b < 8})
        return q if set(parts) == set(NAMED[q]) else f"{q}:{'+'.join(parts)}"
    return q if q not in FULL or FULL[q] == len(set(qbits)) else f"{q}[{span(qbits)}]"


def role_text(role: dict) -> str:
    """``{quantity: bits}`` as text, quantities in register order."""
    return "+".join(qtext(q, role[q]) for q in sorted(role, key=ORDER.index)) or NONE


def union(roles) -> dict:
    """The union of ``{quantity: bits}`` roles."""
    out = {}
    for r in roles:
        for q, b in r.items():
            out.setdefault(q, set()).update(b)
    return out


def fit_terms(found: dict, form) -> list:
    """The terms of a fitted action: its step, bounds and target, or its set value."""
    if "set" in found:
        return [form.expr(v, {}) for v in found["set"].values()]
    return [form.expr(found[k], {}) for k in ("step", "toward", "min", "max") if k in found]


def fit_kind(found) -> str:
    """The operator kind of a fitted per-tick action."""
    if found is None:
        return "MOD"
    if "set" in found:
        return "LOAD"
    if found.get("clamp") == "reflect":
        return "REFLECT"
    return "TOWARD" if "toward" in found else "ACC"


def index_names(node) -> set:
    """Names an index reads directly, not through a nested lookup or call."""
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Subscript, ast.Attribute, ast.Call)):
        return set()
    return set().union(*(index_names(c) for c in ast.iter_child_nodes(node)))


def copy(rel: dict) -> bool:
    """True for a relation keeping each source bit's position as its lowest result bit."""
    return all(mask & -mask == 1 << i for i, mask in rel.items())


def runs(lists) -> list:
    """Every ``run`` action's spec under ``lists``."""
    return [d["run"] for d in walk(list(lists)) if "run" in d]


class Signer:
    """Signatures of one library playroutine's keywords and record fields."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, name: str):
        self.name = name
        self.comp = composer(name)
        self.pr, self.form, self.flow = self.comp.pr, self.comp.form, self.comp.flow
        self.rtypes = record_types(self.pr)
        self.lists = list(action_lists(self.pr).values())
        self.tests = [t for actions in self.lists for t in tests(actions)]
        self.pointers = self.pointer_cells()
        self.notes = self.copies({"ev_note"})
        orbit = (specs().get("orbits") or {}).get(name)
        self.orbit = (orbit["path"].split(".")[-1], orbit["cell"]) if orbit else None
        self.setup_tests = [t for p in self.comp.chain_procs for t in tests(self.pr["procs"][p])]

    # dataflow facts ---------------------------------------------------------
    def copies(self, start: set) -> set:
        """Cells reached from ``start`` by bit-preserving copies (sums included)."""
        seen, todo = set(), list(start)
        while todo:
            n = todo.pop()
            for m, rel in self.flow.edges.get(n, {}).items():
                if m not in seen and copy(rel):
                    seen.add(m)
                    todo.append(m)
        return seen

    def pointer_cells(self) -> dict:
        """``{cell: program type}``: cells indexing a chained record type, or copied only into such.

        The greatest set where every copy of a non-indexing pointer goes to a pointer of its type.
        """
        records = {n for n, rt in self.rtypes.items() if rt.chain}
        direct = {}
        for text in [v for _, v in walk_sets(self.lists)] + self.tests:
            for n in ast.walk(parse(str(text))) if isinstance(text, str) else ():
                if isinstance(n, ast.Subscript) and getattr(n.value, "id", None) in records:
                    for c in index_names(n.slice):
                        direct.setdefault(c, n.value.id)
        for run in runs(self.lists):
            if run["record"] in records:
                for c in index_names(parse(str(run["index"]))):
                    direct.setdefault(c, run["record"])
        cells = set(self.flow.width)
        out = {c: r for c, r in direct.items() if c in cells}
        for c in cells - set(out):
            types = {out[p] for p in self.copies({c}) if p in out}
            if len(types) == 1:
                out[c] = types.pop()
        shrunk = True
        while shrunk:
            shrunk = False
            for c in [c for c in out if c not in direct]:
                outs = {m for m, rel in self.flow.edges.get(c, {}).items() if copy(rel)}
                if any(out.get(m) != out[c] for m in outs):
                    del out[c]
                    shrunk = True
        return out

    def type_role(self, rtype: str) -> dict:
        """The role of a record type: what its fields other than its chain reach."""
        rt = self.rtypes.get(rtype)
        if rt is None:
            return {}
        return union(self.flow.role(f"{rtype}.{f}") for f in rt.fields if f != rt.chain)

    def through(self, rel: dict, cell: str) -> dict:
        """``{(quantity, bit): source bits}`` of a relation ``{source bit: cell bits}``."""
        out = {}
        for r, rb in self.flow.reach.get(cell, {}).items():
            q, off, _ = REGISTERS.get(r, (f"${r:02X}", 0, 0xFF))
            for i, m in rel.items():
                reg = 0
                for j in bits(m):
                    reg |= rb.get(j, 0)
                for k in bits(reg):
                    out.setdefault((q, off + k), set()).add(i)
        return out

    def cell_role(self, cell: str) -> dict:
        """The role of a cell or fused pair; a pointer adds its record type's."""
        parts = self.form.pair[cell][:2] if cell in self.form.pair else (cell,)
        own = [self.flow.role(p) for p in parts]
        return union(own + [self.type_role(self.pointers[p]) for p in parts if p in self.pointers])

    def mapping(self, rel: dict, cell: str) -> tuple:
        """``(role, use)`` of source bits entering ``cell``: the role bits and their sources."""
        role, srcbits = {}, {}
        for (q, b), ins in self.through(rel, cell).items():
            role.setdefault(q, set()).add(b)
            srcbits.setdefault(q, set()).update(ins)
        if cell in self.flow.timing and rel:
            role["TIMING"], srcbits["TIMING"] = {0}, set(rel)
        use = " ".join(
            f"[{span(srcbits[q])}]→{qtext(q, role[q])}" for q in sorted(role, key=ORDER.index)
        )
        return role, use or NONE

    def arg_use(self, term) -> str:
        """How ``arg`` enters a term: a record reference, a note-relative table, or directly."""
        tags = set()
        self.contexts(term, None, tags)
        return "+".join(sorted(tags)) if tags else NONE

    def contexts(self, t, inside, tags: set) -> None:
        """Collect the contexts of ``arg`` in ``t``; ``inside`` is the enclosing lookup."""
        if t == ARG:
            tags.add(inside or "direct")
        elif isinstance(t, tuple) and t[0] in ("lt", "eq", "not", "land", "lor", "iv"):
            return
        elif isinstance(t, tuple) and t[0] == "ite":
            for x in t[2:]:
                self.contexts(x, inside, tags)
        elif isinstance(t, tuple) and t[0] in ("rec", "idx"):
            if ARG in leaves(t[2]):
                note = leaves(t[2]) & self.notes
                tag = f"ref {t[1]}" if t[0] == "rec" else ("note-relative" if note else "table")
                self.contexts(t[2], tag, tags)
        elif isinstance(t, tuple):
            for x in [a for a, _ in t[2]] if t[0] == "lin" else t[1:]:
                self.contexts(x, inside, tags)

    # keywords ---------------------------------------------------------------
    def keyword(self, code: int, when) -> dict:
        """``{"sig", "entries", "effect"}`` of a keyword on a row without a note."""
        # pylint: disable=too-many-locals
        eff = self.comp.compose(code, when, "plain")
        moved = set(eff["fits"])
        setup = {}
        for _, upd in eff["setup_upd"]:
            for c, v in upd.items():
                setup.setdefault(c, []).append(v)
        reads = self.comp.influences(eff, set(setup))
        entries, params = [], []
        for c, values in setup.items():
            if c in self.pointers and any(ARG in leaves(v) for v in values):
                entries.append((role_text(self.cell_role(c)), "PTR", f"ref {self.pointers[c]}", c))
            elif c in reads and moved:
                params += values
                if all(isinstance(v, int) for v in values):
                    entries.append((role_text(union(map(self.cell_role, moved))), "ARM", NONE, c))
            else:
                entries += self.setup_entry(c, values)
        ptrs = {self.pointers[e[3]] for e in entries if e[1] == "PTR"}
        for c in sorted(moved):
            entries += self.tick_entry(c, eff, params, ptrs)
        return {"sig": frozenset(e[:3] for e in entries), "entries": entries, "effect": eff}

    def setup_entry(self, cell: str, values: list) -> list:
        """Signature entries of one setup-updated cell the ticks do not read."""
        loaded = [v for v in values if ARG in leaves(v)]
        if loaded:
            rel = self.flow.expr(src(loaded[0]), {}).rel.get(ARG, {})
            role, use = self.mapping(rel, cell)
            if role:
                return [(role_text(role), "LOAD", use, cell)]
        elif all(isinstance(v, int) for v in values):
            role = self.cell_role(cell)
            if role:
                return [(role_text(role), "RESET", NONE, cell)]
        else:
            return []
        if any(cell in names(t) for t in self.setup_tests):
            return [(f"({cell})", "MODE", self.arg_use(loaded[0]) if loaded else NONE, cell)]
        return []

    def tick_entry(self, cell: str, eff: dict, params: list, ptrs: set) -> list:
        """Signature entries of one cell the ticks move; a started table's own steps are not."""
        # pylint: disable=too-many-locals
        fits = [found for _, found in eff["fits"].get(cell, [])]
        terms = [t for found in fits if found for t in fit_terms(found, self.form)]
        parts = self.form.pair[cell][:2] if cell in self.form.pair else (cell,)
        values = [eff["end"].get(p, p) for p in parts]
        role = self.cell_role(cell)
        steer = {f"rec:{p}" for p in ptrs} | {c for c, t in self.pointers.items() if t in ptrs}
        if not role or any(leaves(t) & steer for t in terms + values):
            return []
        kinds = {fit_kind(found) for found in fits}
        steps = {str(found.get("step")) for found in fits if found and "accumulate" in found}
        turning = {c for t in terms for c in leaves(t)} & (set(eff["moved"]) - set(parts))
        procs = eff["called"] | {n.split(".")[-1] for n in eff["ticks"]}
        if (
            self.orbit
            and self.orbit[1] in parts
            and self.orbit[0] in procs
            and (len(steps) > 1 or turning)
        ):
            kinds = {"REFLECT"}
        use = {self.arg_use(t) for t in terms} - {NONE}
        use = use or {self.arg_use(v) for v in params} - {NONE}
        return [(role_text(role), "|".join(sorted(kinds)), "+".join(sorted(use)) or NONE, cell)]

    # fields -----------------------------------------------------------------
    def field(self, rtype: str, name: str) -> dict:
        """``{"sig", "entries"}`` of a record field through every assignment reading it.

        A declared reference is a program pointer (chained type) or a table reference.
        """
        node = f"{rtype}.{name}"
        entries = self.refs(rtype, name)
        if entries:
            return {"sig": frozenset(e[:3] for e in entries), "entries": entries}
        for path, cell, val, step in self.flow.log:
            rel = val.rel.get(node)
            if not rel:
                continue
            if cell in self.pointers:
                ref = f"ref {self.pointers[cell]}"
                entries.append((role_text(self.cell_role(cell)), "PTR", ref, path))
                continue
            role, use = self.mapping(rel, cell)
            if role:
                entries.append((role_text(role), "ACC" if step else "LOAD", use, path))
        tested = self.copies({node}) & {n for t in self.tests for n in names(t)}
        if tested or any(self.reads_field(t, rtype, name) for t in self.tests):
            entries.append((NONE, "TEST", NONE, rtype))
        return {"sig": frozenset(e[:3] for e in entries), "entries": entries}

    def refs(self, rtype: str, name: str) -> list:
        """Entries of a field's declared references: ``PTR`` to a chained type, else ``REF``."""
        out = []
        for target, _ in self.rtypes[rtype].refs.get(name, []):
            kind = "PTR" if target in self.rtypes and self.rtypes[target].chain else "REF"
            out.append((role_text(self.type_role(target)), kind, f"ref {target}", rtype))
        return out

    def reads_field(self, text, rtype: str, name: str) -> bool:
        """True when a test source reads ``rtype``'s field ``name``."""
        for n in ast.walk(parse(str(text))):
            if isinstance(n, ast.Attribute) and n.attr == name:
                if isinstance(n.value, ast.Subscript) and getattr(n.value.value, "id", "") == rtype:
                    return True
                if getattr(n.value, "id", "") == "row" and self.in_template(text, rtype):
                    return True
        return False

    def in_template(self, text, rtype: str) -> bool:
        """True when a test source belongs to one of ``rtype``'s templates."""
        rt = self.rtypes.get(rtype)
        return rt is not None and any(text in tests(t) for t in rt.templates.values())


@functools.lru_cache(maxsize=None)
def signer(name: str) -> Signer:
    """The cached signer of a library playroutine."""
    return Signer(name)


SHARED_COMMANDS = {"run", "run2", "ad", "sr", "ctrl", "resfilt", "vol", "cut", "tempo"}
SHARED_COMMANDS |= {"vtempo", "funk", "slideup", "slidedown", "porta", "vib", "mark"}
SHARED_KEYS = {"ctrl", "note", "wait", "hold", "ad", "sr", "pw", "cut", "resfilt", "mode"}


def coarse(role: str) -> frozenset:
    """The quantities of a role text, bits and sub-fields dropped."""
    return frozenset(r.split(":")[0].split("[")[0] for r in role.split("+"))


def width(use: str) -> str:
    """``nibble``, ``byte`` or ``n bits`` for the first source span of a use text."""
    if not use.startswith("["):
        return use
    count = 0
    for part in use[1 : use.index("]")].split(","):
        lo, _, hi = part.partition(":")
        count += int(hi or lo) - int(lo) + 1
    return {4: "nibble", 8: "byte"}.get(count, f"{count} bits")


class Item:
    """One keyword or record field with its signature."""

    # pylint: disable=too-few-public-methods,too-many-arguments,too-many-positional-arguments

    def __init__(self, pr: str, label: str, sig: frozenset, entries: list, shared: str, key=None):
        self.pr, self.label, self.sig, self.entries, self.shared, self.key = (
            pr,
            label,
            sig,
            entries,
            shared,
            key,
        )
        self.core = {(coarse(r), k) for r, k, _ in sig}


def codec_keys(rt) -> dict:
    """``{field: shared keys}`` a record type's codecs write from each raw field."""
    out = {}
    for shape in rt.codec:
        for key, spec in (shape.get("keys") or {}).items():
            value = spec.get("value") if isinstance(spec, dict) else spec
            for f in names(value) & set(rt.fields):
                out.setdefault(f, set()).add(key)
    return out


def items(names_=("goattracker2", "defmon", "sidwizard")) -> list:
    """Every keyword and record field of the playroutines, signed."""
    out = []
    for name in names_:
        s = signer(name)
        for code, kw, when in tqdm(keywords(s.pr), desc=f"{name} keywords", unit="kw"):
            r = s.keyword(code, when)
            label = f"!{kw} ({code}{', ' + when if when else ''})"
            shared = "shared" if kw in SHARED_COMMANDS else ""
            out.append(Item(name, label, r["sig"], r["entries"], shared, (code, when)))
        for rname, rt in s.rtypes.items():
            keys = codec_keys(rt)
            for f in rt.fields:
                r = s.field(rname, f)
                via = sorted(keys.get(f, set()) & SHARED_KEYS)
                shared = f"via {','.join(via)}" if via else ""
                out.append(Item(name, f"{rname}.{f}", r["sig"], r["entries"], shared))
    return out


def classify(x: Item, y: Item) -> tuple:
    """``(rank, overlap)``: 0 identical, 1 differing only in argument use, 2 partial, 3 none."""
    if x.sig == y.sig:
        return 0, len(x.sig)
    if {e[:2] for e in x.sig} == {e[:2] for e in y.sig}:
        return 1, len(x.sig)
    common = len(x.core & y.core)
    return (2, common / len(x.core | y.core)) if common else (3, 0)


def best(x: Item, pool: list) -> tuple:
    """``(rank, matches)``: the best class of ``pool`` against ``x`` and its members."""
    scored = [(classify(x, y), y) for y in pool if y is not x]
    if not x.sig or not scored:
        return 3, []
    top = min(scored, key=lambda s: (s[0][0], -s[0][1]))[0]
    return top[0], [y for s, y in scored if s == top] if top[0] < 3 else []


def diff_reason(x: Item, y: Item) -> str:
    """How two signatures differ, entry by entry."""
    out = []
    for r, k, u in sorted(x.sig - y.sig):
        twin = [e for e in y.sig - x.sig if e[1] == k and coarse(e[0]) & coarse(r)]
        if twin and twin[0][0] != r:
            out.append(f"{r} vs {twin[0][0]} ({width(u)} vs {width(twin[0][2])})")
        elif twin:
            out.append(f"{k} {u} vs {twin[0][2]}")
        else:
            out.append(f"only {x.pr}: {r} {k}")
    out += [
        f"only {y.pr}: {r} {k}"
        for r, k, _ in sorted(y.sig - x.sig)
        if not any(e[1] == k and coarse(e[0]) & coarse(r) for e in x.sig - y.sig)
    ]
    return "; ".join(dict.fromkeys(out))


def notes(x: Item) -> list:
    """Reasons carried by the entries themselves: sticky overrides and modes for later rows."""
    s = signer(x.pr)
    out = [f"a mode for later notes ({e[3]})" for e in x.entries if e[1] == "MODE"]
    loads = [e for e in x.entries if e[1] == "LOAD" and len(e) > 3]
    for a in loads:
        for b in loads:
            if a is not b and b[3] in s.flow.edges.get(a[3], {}) and coarse(a[0]) & coarse(b[0]):
                out.append(f"sticky override {a[3]} (re-applied to {b[3]})")
    return out


def note_row(x: Item, y: Item) -> str | None:
    """How two keywords of one playroutine differ on a row with a note, or None."""
    comp = composer(x.pr)
    a, b = (comp.compose(*i.key, "note", fits=False) for i in (x, y))
    f, conds = comp.form, a["conds"] + b["conds"]
    for cell in sorted(comp.keep & (set(a["state"]) | set(b["state"]))):
        va, vb = a["state"].get(cell, cell), b["state"].get(cell, cell)
        if va != vb and can_differ(f, conds, va, vb):
            return f"note row: setup {cell} differs"
    tests_a = {(p, t): v for p, t, v in a["tests"]}
    tests_b = {(p, t): v for p, t, v in b["tests"]}
    for key in sorted(set(tests_a) | set(tests_b)):
        va = tests_a.get(key, f.truth(f.expr(key[1], a["start"])))
        vb = tests_b.get(key, f.truth(f.expr(key[1], b["start"])))
        if va != vb and can_differ(f, conds, va, vb):
            return f"note row: {key[0]} test `{key[1]}` is {src(va)} vs {src(vb)}"
    return None


def verdict(x: Item, pool: list) -> tuple:
    """``(matches, verdict, reason)``: the other playroutines' best class, and duplicates here.

    A keyword with an identical signature in its own playroutine is a duplicate; it stays
    ``partial`` when the two differ on a row with a note.
    """
    if not x.sig:
        return [], "no effect", "no register, timing or table effect"
    rank, matches = best(x, [y for y in pool if y.pr != x.pr])
    same = [y for y in pool if y.pr == x.pr and y is not x and y.key and x.key and y.sig == x.sig]
    reasons = [r for y in same if (r := note_row(x, y))]
    if matches and rank in (1, 2):
        reasons.append(diff_reason(x, matches[0]))
    reasons += notes(x)
    kinds = ("same effect", "same effect, different argument decoding", "partial", "different")
    v = kinds[rank]
    if same:
        v = "partial" if any(r.startswith("note row") for r in reasons) else "same effect"
    return (
        same + matches,
        v,
        "; ".join(dict.fromkeys(reasons))
        or ("identical signature" if v == "same effect" else "no shared role and kind"),
    )


def table(names_=("goattracker2", "defmon", "sidwizard")) -> list:
    """Report lines: one row per keyword or field with its matches and verdict."""
    pool = items(names_)
    head = ["keyword/field", "playroutine", "shared", "signature", "matches", "verdict", "reason"]
    rows = []
    for x in tqdm(pool, desc="match", unit="item"):
        matches, v, reason = verdict(x, pool)
        sig = "; ".join(f"{r} {k} {u}" for r, k, u in sorted(x.sig)) or NONE
        found = ", ".join(f"{y.pr}:{y.label}" for y in matches) or NONE
        rows.append([x.label, x.pr, x.shared or NONE, sig, found, v, reason])
    return [" | ".join(head), " | ".join("---" for _ in head)] + [" | ".join(r) for r in rows]
