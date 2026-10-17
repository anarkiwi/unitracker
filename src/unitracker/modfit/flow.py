"""Dataflow of a playroutine: temporaries, bit-level influence, SID register roles and pairs.

A value is a bit relation: for each source name, which result bits each source bit can reach,
plus the bits a constant part can set. Carries spread upwards, masks and shifts move bits.
"""

import ast
import functools

from unitracker.format.records import record_types
from unitracker.player.vm import PSEUDO, acc_mode

BITS = 48
ALL = (1 << BITS) - 1

# SID register: (quantity, bit offset of register bit 0, significant register bits).
REGISTERS = {
    0: ("FREQ", 0, 0xFF),
    1: ("FREQ", 8, 0xFF),
    2: ("PW", 0, 0xFF),
    3: ("PW", 8, 0x0F),
    4: ("CTRL", 0, 0xFF),
    5: ("AD", 0, 0xFF),
    6: ("SR", 0, 0xFF),
    0x15: ("CUT", 0, 0x07),
    0x16: ("CUT", 3, 0xFF),
    0x17: ("RESFILT", 0, 0xFF),
    0x18: ("MODEVOL", 0, 0xFF),
}
NAMED = {
    "CTRL": ("GATE", "SYNC", "RING", "TEST", "TRI", "SAW", "PUL", "NOI"),
    "AD": ("D",) * 4 + ("A",) * 4,
    "SR": ("R",) * 4 + ("S",) * 4,
    "RESFILT": ("FILT",) * 4 + ("RES",) * 4,
    "MODEVOL": ("VOL",) * 4 + ("MODE",) * 3 + ("3OFF",),
}


@functools.lru_cache(maxsize=None)
def parse(src: str) -> ast.expr:
    """The cached AST of an expression source."""
    return ast.parse(src, mode="eval").body


def names(src) -> set:
    """Cell-like names read by an expression source."""
    if isinstance(src, (bool, int, float)) or src is None:
        return set()
    return {n.id for n in ast.walk(parse(str(src))) if isinstance(n, ast.Name)}


def descendants(edges: dict, start) -> set:
    """Every node reachable from ``start``, itself included."""
    seen, todo = {start}, [start]
    while todo:
        for nxt in edges.get(todo.pop(), set()) - seen:
            seen.add(nxt)
            todo.append(nxt)
    return seen


def shifted(n) -> tuple | None:
    """``(x, k)`` when node ``n`` is ``x << k`` for a name ``x`` and a constant ``k``."""
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.LShift) and isinstance(n.left, ast.Name):
        if isinstance(n.right, ast.Constant) and isinstance(n.right.value, int):
            return n.left.id, n.right.value
    return None


def low(n, k: int) -> str | None:
    """``x`` when node ``n`` is a name ``x`` or ``x & (2**k - 1)``."""
    if isinstance(n, ast.Name):
        return n.id
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitAnd) and isinstance(n.left, ast.Name):
        if isinstance(n.right, ast.Constant) and n.right.value == (1 << k) - 1:
            return n.left.id
    return None


def joined(src) -> set:
    """``(lo, hi, k)`` for each ``(hi << k) | lo`` or ``(hi << k) | (lo & mask)`` (or ``+``)."""
    if not isinstance(src, str):
        return set()
    out = set()
    for n in ast.walk(parse(src)):
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.BitOr, ast.Add)):
            for a, b in ((n.left, n.right), (n.right, n.left)):
                hk = shifted(a)
                if hk and low(b, hk[1]):
                    out.add((low(b, hk[1]), *hk))
    return out


def cells_of(pr: dict) -> dict:
    """``{cell: width or None}`` over global and voice cells."""
    cells = {**(pr.get("global") or {}), **(pr.get("voice") or {})}
    return {k: (v or {}).get("width") for k, v in cells.items()}


def action_lists(pr: dict) -> dict:
    """``{dotted path: actions}`` of every proc, template, record action list, init and tick."""
    out = {f"procs.{k}": v for k, v in (pr.get("procs") or {}).items()}
    for name, rt in record_types(pr).items():
        out.update({f"record_types.{name}.{k}": v for k, v in rt.templates.items()})
    for name, rows in (pr.get("records") or {}).items():
        for i, row in rows.items():
            out.update({f"records.{name}.{i}.{k}": v for k, v in (row or {}).items()})
    out.update({k: pr[k] for k in ("init", "tick") if pr.get(k)})
    return out


def run_lists(pr: dict, run: dict) -> list:
    """The action lists a ``run`` action can execute."""
    rt = record_types(pr).get(run["record"])
    if rt is not None and run["field"] in rt.templates:
        return [rt.templates[run["field"]]]
    rows = (pr.get("records") or {}).get(run["record"]) or {}
    return [r[run["field"]] for r in rows.values() if (r or {}).get(run["field"])]


NESTED = ("then", "else", "on_bound")


def direct_writes(doc) -> set:
    """Cells assigned in ``doc`` itself, not through calls, runs or ``for_voices`` bodies."""
    if isinstance(doc, list):
        return set().union(*(direct_writes(a) for a in doc))
    out = set(doc.get("set") or {}) | set(doc.get("with") or {})
    if "accumulate" in doc:
        out.add(doc["accumulate"])
    return out.union(*(direct_writes(doc.get(k) or []) for k in NESTED))


class Writes:
    """Cells an action list can assign, through calls, runs and ``for_voices`` bodies."""

    def __init__(self, pr: dict):
        self.pr = pr
        self.procs = {}

    def proc(self, name: str) -> set:
        """Cells a proc can assign."""
        if name not in self.procs:
            self.procs[name] = set()
            self.procs[name] = self.of(self.pr["procs"][name])
        return self.procs[name]

    def of(self, doc) -> set:
        """Cells ``doc`` can assign."""
        if isinstance(doc, list):
            return set().union(*(self.of(a) for a in doc))
        out = direct_writes(doc) | (set(PSEUDO) if "fetch" in doc else set())
        if "call" in doc:
            out |= self.proc(doc["call"])
        if "run" in doc:
            out |= self.of([a for body in run_lists(self.pr, doc["run"]) for a in body])
        return out.union(*(self.of(doc.get(k) or []) for k in (*NESTED, "for_voices")))


def meet(a, b):
    """Intersection of two optional sets, ``None`` meaning no path."""
    return b if a is None else a if b is None else a & b


class Liveness:
    """Def-before-use summaries: the cells each action list reads before it assigns them.

    With ``calls_define`` a call assigns what its proc always assigns; without, only the
    list's own assignments count, so a value passed through a call stays live.
    """

    def __init__(self, pr: dict, calls_define: bool = True):
        self.pr, self.calls_define = pr, calls_define
        self.cells = set(cells_of(pr)) | set(PSEUDO)
        self.memo = {}

    def proc(self, name: str) -> tuple:
        """``(live, must)`` of a proc."""
        if name not in self.memo:
            self.memo[name] = (set(), set())
            live, fall, ret = self.seq(self.pr["procs"][name], set())
            self.memo[name] = (live, meet(fall, ret) or set())
        return self.memo[name]

    def reads(self, src, d) -> set:
        """Cells ``src`` reads that ``d`` has not assigned."""
        return (names(src) & self.cells) - d

    def seq(self, actions, d) -> tuple:
        """``(live, assigned at fall-through or None, assigned at every return or None)``."""
        live, ret = set(), None
        for a in actions or []:
            if d is None:
                break
            l, d, r = self.action(a, d)
            live |= l
            ret = meet(ret, r) if r is not None else ret
        return live, d, ret

    def branch(self, a: dict, d: set, live: set) -> tuple:
        """``seq`` of an ``if`` whose test and earlier sets read ``live``."""
        (l1, f1, r1), (l2, f2, r2) = (self.seq(a.get(k), d) for k in ("then", "else"))
        fall = None if f1 is None and f2 is None else meet(f1, f2)
        return live | l1 | l2, fall, meet(r1, r2)

    def action(self, a: dict, d: set) -> tuple:
        """``seq`` of one action."""
        # pylint: disable=too-many-return-statements
        if "return" in a:
            return set(), None, d
        if "fetch" in a:
            return set(), d | set(PSEUDO), None
        live, d = set(), set(d)
        for key in ("set", "with"):
            for cell, src in (a.get(key) or {}).items():
                live |= self.reads(src, d)
                d.add(cell)
        if "if" in a:
            return self.branch(a, d, live | self.reads(a["if"], d))
        if "call" in a:
            lp, mp = self.proc(a["call"])
            return live | (lp - d), d | mp if self.calls_define else d, None
        if "accumulate" in a:
            keys = ("accumulate", "step", "min", "max", "toward")
            live |= set().union(*(self.reads(a.get(k), d) for k in keys))
            return live | self.seq(a.get("on_bound"), d | {a["accumulate"]})[0], d, None
        if "for_voices" in a:
            l, f, r = self.seq(a["for_voices"], d)
            return live | l, d | (meet(f, r) or set()), None
        if "run" in a:
            live |= self.reads(a["run"]["index"], d)
            for body in run_lists(self.pr, a["run"]):
                live |= self.seq(body, d)[0]
        return live, d, None


def entry_live(pr: dict, calls_define: bool) -> set:
    """Cells live at the entry of any action list."""
    lv = Liveness(pr, calls_define)
    return set().union(*(lv.seq(v, set())[0] for v in action_lists(pr).values()))


def persistent(pr: dict) -> set:
    """Cells whose value can carry from one tick to the next or reach the ghost."""
    ghost = pr.get("ghost") or {}
    read = set().union(*(names(s) for g in ghost.values() for s in (g or {}).values()))
    lv = Liveness(pr)
    roots = [lv.seq(pr.get(k), set())[0] for k in ("init", "tick")]
    return (set().union(read, *roots)) & set(cells_of(pr))


def smear(m: int) -> int:
    """Every bit, when ``m`` has any."""
    return ALL if m else 0


def spread(m: int) -> int:
    """The bits a carry from ``m``'s lowest set bit can reach."""
    return ALL & ~((m & -m) - 1) if m else 0


class Val:
    """A bit relation ``{source: {source bit: result bits}}`` and a constant part's bits."""

    __slots__ = ("rel", "const")

    def __init__(self, rel=None, const: int = 0):
        self.rel, self.const = rel or {}, const

    def support(self) -> int:
        """The bits that can be nonzero."""
        out = self.const
        for bits in self.rel.values():
            for m in bits.values():
                out |= m
        return out

    def map(self, f, const=None) -> "Val":
        """Each result mask through ``f``; the constant through ``const`` (default ``f``)."""
        rel = {n: {i: f(m) for i, m in b.items() if f(m)} for n, b in self.rel.items()}
        return Val({n: b for n, b in rel.items() if b}, (const or f)(self.const))

    def __or__(self, other: "Val") -> "Val":
        rel = {n: dict(b) for n, b in self.rel.items()}
        for n, b in other.rel.items():
            mine = rel.setdefault(n, {})
            for i, m in b.items():
                mine[i] = mine.get(i, 0) | m
        return Val(rel, self.const | other.const)

    def onto(self, mask: int) -> "Val":
        """Every source bit selecting all of ``mask``, as a condition steering a value."""
        return self.map(lambda m: mask if m else 0, lambda c: 0)


def source(name: str, width) -> Val:
    """The identity relation of a source of ``width`` bits (``None``: unbounded)."""
    return Val({name: {i: 1 << i for i in range(width or BITS)}})


class Flow:
    """Bit relations of every assignment, register reach and roles of one playroutine."""

    # pylint: disable=too-many-instance-attributes

    steer = True

    def __init__(self, pr: dict):
        self.base(pr)
        ghost = pr.get("ghost") or {}
        read = set().union(*(names(s) for g in ghost.values() for s in (g or {}).values()))
        local = set(self.width) - entry_live(pr, False) - read
        self.local = {c for c in local if self.width[c] is None}
        self.edges, self.writes, self.log, self.path = {}, {}, [], None
        for self.path, actions in action_lists(pr).items():
            rec = self.path.split(".")[1] if self.path.startswith("record_types.") else None
            self.block(actions, {}, rec)
        self.reach = self.registers()
        self.timing = self.timed()

    def base(self, pr: dict) -> None:
        """The playroutine's cells, constants, tables, functions and record types."""
        self.pr = pr
        self.width = cells_of(pr)
        self.consts = {k: int(v) for k, v in (pr.get("constants") or {}).items()}
        self.tables = {
            k: ALL if min(v, default=0) < 0 else (1 << max(v, default=0).bit_length()) - 1
            for k, v in (pr.get("tables") or {}).items()
        }
        self.functions = pr.get("functions") or {}
        self.rtypes = record_types(pr)

    # values -----------------------------------------------------------------
    def field(self, rtype: str, attr: str) -> Val:
        """A record field as a source: a byte unless its default is negative."""
        rt = self.rtypes.get(rtype)
        signed = rt is not None and rt.default(attr) < 0
        return source(f"{rtype}.{attr}", None if signed else 8)

    def row(self, rec, attr: str) -> Val:
        """``row.attr`` inside a template of record type ``rec``."""
        return self.field(rec, attr)

    def select(self, rtype: str, index: Val, out: Val) -> Val:
        """A record field ``out`` of ``rtype`` read at ``index``: the index steers every bit."""
        # pylint: disable=unused-argument
        return out | index.onto(out.support())

    def value(self, n, env: dict, rec) -> Val:
        """The bit relation of an AST node."""
        # pylint: disable=too-many-return-statements,too-many-branches
        if isinstance(n, ast.Constant):
            return Val(const=int(n.value) & ALL)
        if isinstance(n, ast.Name):
            if n.id in env:
                return env[n.id]
            if n.id in self.consts:
                return Val(const=self.consts[n.id] & ALL)
            return source(n.id, self.width.get(n.id))
        if isinstance(n, ast.BinOp):
            return self.binop(n, self.value(n.left, env, rec), self.value(n.right, env, rec))
        if isinstance(n, ast.UnaryOp):
            v = self.value(n.operand, env, rec)
            if isinstance(n.op, ast.Not):
                return v.map(lambda m: 1 if m else 0, lambda c: 1) if self.steer else Val(const=1)
            if isinstance(n.op, ast.Invert):
                return v.map(lambda m: m, lambda c: ALL)
            return v.map(spread)
        if isinstance(n, (ast.Compare, ast.BoolOp)):
            if not self.steer:
                return Val(const=1)
            parts = [n.left, *n.comparators] if isinstance(n, ast.Compare) else n.values
            out = Val(const=1)
            for p in parts:
                out = out | self.value(p, env, rec).map(lambda m: 1 if m else 0, lambda c: 0)
            return out
        if isinstance(n, ast.IfExp):
            body = self.value(n.body, env, rec) | self.value(n.orelse, env, rec)
            if not self.steer:
                return body
            return body | self.value(n.test, env, rec).onto(body.support())
        if isinstance(n, ast.Subscript):
            index = self.value(n.slice, env, rec)
            return index.onto(self.tables.get(n.value.id, ALL))
        if isinstance(n, ast.Attribute):
            if isinstance(n.value, ast.Name) and n.value.id == "row" and rec:
                return self.row(rec, n.attr)
            if not isinstance(n.value, ast.Subscript):
                raise ValueError(f"unsupported expression {ast.dump(n)}")
            rtype = n.value.value.id
            return self.select(
                rtype, self.value(n.value.slice, env, rec), self.field(rtype, n.attr)
            )
        if isinstance(n, ast.Call):
            fn = self.functions[n.func.id]
            args = [self.value(a, env, rec) for a in n.args]
            return self.value(
                parse(str(fn["expr"])), {**env, **dict(zip(fn.get("args") or [], args))}, rec
            )
        raise ValueError(f"unsupported expression {ast.dump(n)}")

    def binop(self, n: ast.BinOp, a: Val, b: Val) -> Val:
        """A binary operator: shifts and masks move bits, arithmetic carries upwards."""
        # pylint: disable=too-many-return-statements
        k = n.right.value if isinstance(n.right, ast.Constant) else None
        if isinstance(n.op, ast.Mult) and isinstance(n.left, ast.Constant):
            a, b, k = b, a, n.left.value
        if isinstance(n.op, ast.Mult) and k is not None and k > 0 and k & (k - 1) == 0:
            return a.map(lambda m: (m << (k.bit_length() - 1)) & ALL)
        if isinstance(n.op, ast.Add) and not a.support() & b.support():
            return a | b
        if isinstance(n.op, ast.LShift) and k is not None:
            return a.map(lambda m: (m << k) & ALL)
        if isinstance(n.op, ast.RShift) and k is not None:
            return a.map(lambda m: m >> k)
        if isinstance(n.op, ast.BitAnd):
            sa, sb = a.support(), b.support()
            return a.map(lambda m: m & sb) | b.map(lambda m: m & sa)
        if isinstance(n.op, ast.BitOr):
            ones_a, ones_b = (0 if v.rel else v.const for v in (a, b))
            return a.map(lambda m: m & ~ones_b) | b.map(lambda m: m & ~ones_a)
        if isinstance(n.op, ast.BitXor):
            return a | b
        if isinstance(n.op, ast.Mult) and k is not None:
            return a.map(lambda m: spread(m * (k & -k)) if k else 0)
        if isinstance(n.op, (ast.Add, ast.Sub, ast.Mult)):
            return (a | b).map(spread)
        return (a | b).map(smear)

    def expr(self, src, env: dict, rec=None) -> Val:
        """The bit relation of an expression source."""
        if isinstance(src, (bool, int)):
            return Val(const=int(src) & ALL)
        return self.value(parse(str(src)), env, rec)

    # assignments ------------------------------------------------------------
    def assign(self, cell: str, v: Val, env: dict, step: Val | None = None) -> None:
        """Record ``cell := v`` (wrapped to its width), or bind a local temporary.

        ``log`` keeps ``(list path, cell, value or step, is a step)`` per assignment.
        """
        w = self.width.get(cell)
        if w is not None:
            v = v.map(lambda m: m & ((1 << w) - 1))
        if cell in self.local:
            env[cell] = v
            return
        self.log.append((self.path, cell, v if step is None else step, step is not None))
        for n, b in v.rel.items():
            mine = self.edges.setdefault(n, {}).setdefault(cell, {})
            for i, m in b.items():
                mine[i] = mine.get(i, 0) | m
        self.writes[cell] = self.writes.get(cell, 0) | v.support()

    def block(self, actions, env: dict, rec) -> dict | None:
        """Walk an action list; the temporaries' relations at fall-through, or None."""
        env = dict(env)
        for a in actions or []:
            if "return" in a:
                return None
            for key in ("set", "with"):
                for cell, src in (a.get(key) or {}).items():
                    self.assign(cell, self.expr(src, env, rec), env)
            if "if" in a:
                outs = [self.block(a.get(b), env, rec) for b in ("then", "else")]
                outs = [o for o in outs if o is not None]
                if not outs:
                    return None
                env = {
                    k: functools.reduce(Val.__or__, [o[k] for o in outs if k in o])
                    for k in set().union(*outs)
                }
            elif "accumulate" in a:
                self.accumulate(a, env, rec)
            elif "for_voices" in a:
                self.block(a["for_voices"], env, rec)
        return env

    def accumulate(self, a: dict, env: dict, rec) -> None:
        """``cell := cell ± step``, or a bound; the comparisons steer every result bit."""
        cell = a["accumulate"]
        parts = [self.expr(a[k], env, rec) for k in ("min", "max", "toward") if k in a]
        step = self.expr(a.get("step", 0), env, rec)
        v = functools.reduce(
            Val.__or__, parts, (source(cell, self.width.get(cell)) | step).map(spread)
        )
        self.assign(cell, v | v.onto(v.support()), env, step)
        self.block(a.get("on_bound"), env, rec)

    # registers --------------------------------------------------------------
    def registers(self) -> dict:
        """``{node: {register: {node bit: register bits}}}``, to a fixpoint over the edges."""
        reach, todo = {}, []
        for regs in (self.pr.get("ghost") or {}).values():
            for r, src in (regs or {}).items():
                r = int(r)
                sig = REGISTERS.get(r, (None, 0, 0xFF))[2]
                for n, b in self.expr(src, {}).rel.items():
                    mine = reach.setdefault(n, {}).setdefault(r, {})
                    for i, m in b.items():
                        mine[i] = mine.get(i, 0) | (m & sig)
                    todo.append(n)
        back = {}
        for n, outs in self.edges.items():
            for m in outs:
                back.setdefault(m, set()).add(n)
        while todo:
            m = todo.pop()
            for n in back.get(m, ()):
                if self.compose(reach, n, m):
                    todo.append(n)
        return {
            n: {r: {i: b for i, b in bits.items() if b} for r, bits in regs.items()}
            for n, regs in reach.items()
        }

    def compose(self, reach: dict, n: str, m: str) -> bool:
        """Extend ``n``'s reach through its edge to ``m``; True when it grew."""
        grew = False
        for r, mb in reach.get(m, {}).items():
            mine = reach.setdefault(n, {}).setdefault(r, {})
            for i, out in self.edges[n][m].items():
                add = 0
                while out:
                    j = (out & -out).bit_length() - 1
                    add |= mb.get(j, 0)
                    out &= out - 1
                if add & ~mine.get(i, 0):
                    mine[i] = mine.get(i, 0) | add
                    grew = True
        return grew

    def timed(self) -> set:
        """Cells that can steer a test guarding a ``fetch``, by data or the branches it guards."""
        writes, edges, seeds = Writes(self.pr), {}, set()
        for actions in action_lists(self.pr).values():
            for test, body in guarded(actions):
                for n in names(test):
                    edges.setdefault(n, set()).update(writes.of(body))
                if "ev_kind" in writes.of(body):
                    seeds |= names(test)
        for n, outs in self.edges.items():
            edges.setdefault(n, set()).update(outs)
        back = {}
        for n, outs in edges.items():
            for m in outs:
                back.setdefault(m, set()).add(n)
        out, todo = set(seeds), list(seeds)
        while todo:
            for n in back.get(todo.pop(), set()) - out:
                out.add(n)
                todo.append(n)
        return out

    def regs(self, name: str) -> set:
        """The registers ``name`` can reach."""
        return {r for r, bits in self.reach.get(name, {}).items() if any(bits.values())}

    def role(self, name: str) -> dict:
        """``{quantity: set of quantity bits}`` that ``name`` can reach."""
        out = {}
        for r, bits in self.reach.get(name, {}).items():
            q, off, _ = REGISTERS.get(r, (f"${r:02X}", 0, 0xFF))
            for m in bits.values():
                out.setdefault(q, set()).update(off + j for j in range(8) if m >> j & 1)
        if name in self.timing:
            out["TIMING"] = {0}
        return {q: b for q, b in out.items() if b}

    def support(self, cell: str) -> int:
        """The bits any write or the initial value of ``cell`` can set."""
        spec = {**(self.pr.get("global") or {}), **(self.pr.get("voice") or {})}.get(cell) or {}
        return self.writes.get(cell, 0) | (int(spec.get("init", 0)) & ALL)

    # pairs ------------------------------------------------------------------
    def pairs(self) -> set:
        """``(lo, hi, k)``: ``P = hi * 2**k + lo``, ``lo`` never above ``k`` bits.

        Pairs are joined in an expression, or ``lo`` reaches only the low register and ``hi``
        only the high register of one SID register pair, ``k`` the bits ``lo`` is ever given.
        """
        found = set()
        for _, src in self.sources():
            found |= joined(src)
        single = {}
        for n in self.reach:
            regs = self.regs(n)
            if len(regs) == 1 and self.width.get(n) and self.support(n):
                single.setdefault(regs.pop(), []).append(n)
        for r in (0, 2, 0x15):
            for lo in single.get(r, ()):
                found |= {(lo, hi, self.support(lo).bit_length()) for hi in single.get(r + 1, ())}
        return {
            (lo, hi, k)
            for lo, hi, k in found
            if self.width.get(lo) and self.width.get(hi) and self.support(lo) >> k == 0
        }

    def sources(self):
        """``(cell, source)`` of every assignment in the playroutine."""
        for actions in action_lists(self.pr).values():
            yield from walk_sets(actions)


class Composed(Flow):
    """Register reach through the composed ``init`` and ``tick``, by value only.

    Calls are inlined with their ``with`` bindings and every cell is resolved along its path, so
    a parameter or scratch cell carries only what each call site gives it; tests steer nothing.
    With ``plain``, ``&`` of two variables, or ``|``/``^`` over bits both can set, passes neither.
    """

    steer = False

    def __init__(self, pr: dict, plain: bool = False):
        # pylint: disable=super-init-not-called
        self.base(pr)
        self.plain, self.steps, self.ranges = plain, {}, {}
        self.timing = flow_of(pr).timing
        self.ends = {k: self.join(self.seq(pr.get(k), {}, None)) for k in ("init", "tick")}
        self.edges = {}
        for key, env in self.ends.items():
            for cell, v in env.items():
                for n, b in v.rel.items():
                    if key == "tick" or n not in self.width:
                        mine = self.edges.setdefault(n, {}).setdefault(cell, {})
                        for i, m in b.items():
                            mine[i] = mine.get(i, 0) | m
        self.reach = self.registers()

    def binop(self, n: ast.BinOp, a: Val, b: Val) -> Val:
        """``Flow.binop``; with ``plain``, a mask of one variable by another is dropped."""
        if self.plain and a.rel and b.rel and isinstance(n.op, (ast.BitAnd, ast.BitOr, ast.BitXor)):
            if isinstance(n.op, ast.BitAnd) or (
                a.support() & b.support() and self.live(a) & self.live(b)
            ):
                return Val()
        return Flow.binop(self, n, a, b)

    def live(self, v: Val) -> int:
        """The bits ``v`` can set, each source cell held to its proved bit range."""
        out = v.const
        for n, b in v.rel.items():
            keep = self.bit_range(n)
            for i, m in b.items():
                out |= m if keep >> i & 1 else 0
        return out

    def bit_range(self, cell: str) -> int:
        """The mask of bits ``[lo, hi)`` that inductive invariants leave ``cell`` able to set."""
        # pylint: disable=import-outside-toplevel,cyclic-import
        from unitracker.modfit.invariant import inductive

        w = self.width.get(cell)
        if not w:
            return ALL
        if cell not in self.ranges:
            hi = next(
                k for k in range(1, w + 1) if k == w or inductive(self.pr, f"{cell} < {1 << k}")
            )
            lo = next(
                (
                    j
                    for j in range(hi - 1, 0, -1)
                    if inductive(self.pr, f"({cell} & {(1 << j) - 1}) == 0")
                ),
                0,
            )
            self.ranges[cell] = ((1 << hi) - 1) & ~((1 << lo) - 1)
        return self.ranges[cell]

    def row(self, rec, attr: str) -> Val:
        """A template's ``row.attr``: the field, selected by the run's index."""
        return self.select(rec[0], rec[1], self.field(rec[0], attr))

    def select(self, rtype: str, index: Val, out: Val) -> Val:
        """``Flow.select``; a program pointer into a chained type selects control, not a value."""
        rt = self.rtypes.get(rtype)
        return out if rt is not None and rt.chain else Flow.select(self, rtype, index, out)

    def join(self, envs: list) -> dict:
        """The union of the environments; a cell missing from one holds its start value there."""
        envs = [e for e in envs if e is not None]
        if len(envs) == 1:
            return envs[0]
        out = {}
        for c in set().union(*envs):
            vals = [e.get(c) for e in envs]
            if any(v is not vals[0] for v in vals):
                start = source(c, self.width.get(c))
                out[c] = functools.reduce(Val.__or__, [start if v is None else v for v in vals])
            else:
                out[c] = vals[0]
        return out

    def put(self, env: dict, cell: str, v: Val) -> None:
        """``cell := v`` wrapped to its width."""
        w = self.width.get(cell)
        env[cell] = v.map(lambda m: m & ((1 << w) - 1)) if w else v

    def seq(self, actions, env: dict, rec) -> list:
        """The environments at fall-through and at each ``return`` of ``actions``."""
        # pylint: disable=too-many-branches
        ends = []
        for a in actions or []:
            if "return" in a:
                return ends + [env]
            env = dict(env)
            for key in ("set", "with"):
                for cell, text in (a.get(key) or {}).items():
                    self.put(env, cell, self.expr(text, env, rec))
            if "if" in a:
                outs = [self.seq(a.get(k), env, rec) for k in ("then", "else")]
                ends += [e for o in outs for e in o[:-1]]
                falls = [o[-1] for o in outs if o[-1] is not None]
                if not falls:
                    return ends + [None]
                env = self.join(falls)
            elif "call" in a:
                env = self.join(self.seq(self.pr["procs"][a["call"]], env, rec))
            elif "for_voices" in a:
                env = self.join(self.seq(a["for_voices"], env, rec))
            elif "accumulate" in a:
                env = self.accumulate(a, env, rec)
            elif "run" in a:
                env = self.join([env] + self.run(a["run"], env, rec))
            elif "fetch" in a:
                env.update({p: source(p, self.width.get(p)) for p in PSEUDO})
        return ends + [env]

    def run(self, run: dict, env: dict, rec) -> list:
        """The environments after each action list a ``run`` can execute."""
        rt = self.rtypes.get(run["record"])
        if rt is not None and run["field"] in rt.templates:
            row = (run["record"], self.expr(run["index"], env, rec))
            return [self.join(self.seq(rt.templates[run["field"]], env, row))]
        return [self.join(self.seq(body, env, rec)) for body in run_lists(self.pr, run)]

    def accumulate(self, a: dict, env: dict, rec) -> dict:
        """``cell ± step``, the bound on a clamp; ``on_bound`` joined in."""
        cell = a["accumulate"]
        step = self.expr(a.get("step", 0), env, rec)
        self.steps[cell] = self.steps.get(cell, Val()) | step
        v = (env.get(cell, source(cell, self.width.get(cell))) | step).map(spread)
        if acc_mode(a, self.width.get(cell) is not None)[0] == 1:
            for k in ("min", "max", "toward"):
                if k in a:
                    v = v | self.expr(a[k], env, rec)
        out = dict(env)
        self.put(out, cell, v)
        return self.join([out, *self.seq(a.get("on_bound"), out, rec)])


def walk(doc):
    """Every action dict under ``doc``, nested ones included."""
    todo = [doc]
    while todo:
        d = todo.pop()
        if isinstance(d, list):
            todo += reversed(d)
        elif isinstance(d, dict):
            yield d
            todo += [v for v in d.values() if isinstance(v, (list, dict))]


def guarded(doc):
    """``(test, branch actions)`` of every ``if`` under ``doc``."""
    if isinstance(doc, list):
        for a in doc:
            yield from guarded(a)
    elif isinstance(doc, dict):
        if "if" in doc:
            yield doc["if"], (doc.get("then") or []) + (doc.get("else") or [])
        for v in doc.values():
            if isinstance(v, (list, dict)):
                yield from guarded(v)


def walk_sets(doc):
    """``(cell, source)`` of every ``set``/``with`` entry under ``doc``."""
    if isinstance(doc, list):
        for x in doc:
            yield from walk_sets(x)
    elif isinstance(doc, dict):
        for key, v in doc.items():
            if key in ("set", "with") and isinstance(v, dict):
                yield from v.items()
            elif isinstance(v, (dict, list)):
                yield from walk_sets(v)


FLOWS = {}


def flow_of(pr: dict) -> Flow:
    """The dataflow of ``pr``, shared by playroutines with the same actions and cells."""
    parts = tuple(pr.get(k) for k in ("procs", "record_types", "records", "ghost", "init", "tick"))
    key = (*map(id, parts), tuple(sorted(cells_of(pr).items())))
    if key not in FLOWS:
        FLOWS[key] = (parts, Flow(pr))
    return FLOWS[key][1]
