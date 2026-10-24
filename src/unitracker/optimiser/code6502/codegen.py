"""Deterministic instruction selection from a decision diagram to a code template: a test is
a compare and a branch, a leaf straight-line code ending in ``rts``. The selector tracks the
term each register and byte holds and what the carry is, so nothing is loaded or set twice;
intermediate words go to ``phi`` (discarded at exit) once nothing reads it."""

import itertools
from collections import deque

from unitracker_grammar.code import Imm, Label, Mem, Op, op

from unitracker.optimiser.code6502.diagram import nodes
from unitracker.optimiser.code6502.semantics import SPAN, SYMBOLS

_ALU = {"Add": "adc", "Sub": "sbc", "And": "and", "Or": "ora", "Xor": "eor"}
_NZ = frozenset(
    "lda ldx ldy tax tay txa tya adc sbc and ora eor cmp cpx cpy asl lsr rol ror inc dec inx "
    "iny dex dey bit pla".split()
)
_CARRY = frozenset("adc sbc cmp cpx cpy asl lsr rol ror clc sec plp".split())
INFINITE = float("inf")
ORDERS = 120


class CodegenError(ValueError):
    """A diagram the selector has no instructions for."""


def lo(w):
    """The low byte term of word term ``w``."""
    return _byte(w, 0)


def hi(w):
    """The high byte term of word term ``w``."""
    return _byte(w, 1)


def _byte(w, k):
    kind = w[0]
    if kind == "WC":
        return ("BC", w[1] >> 8 * k & 0xFF)
    if kind == "Cat":
        return w[2 - k]
    if kind == "Zext":
        return w[1] if k == 0 else ("BC", 0)
    if kind == "Sext" and k == 0:
        return w[1]
    return ("Lo" if k == 0 else "Hi", w)


def _reads(term, out=None) -> set:
    """The subterms of ``term``."""
    out = set() if out is None else out
    if isinstance(term, tuple) and term not in out:
        out.add(term)
        for a in term[1:]:
            _reads(a, out)
    return out


def _uses(term) -> bool:
    """Whether byte ``term`` is a table read through an index register."""
    where = address(term)
    return where is not None and where[2] is not None


def _indexed(operand) -> bool:
    return isinstance(operand, Mem) and bool(operand.index)


def _needed(held, live: set) -> bool:
    """Whether byte term ``held``, or the word it is a byte of, is in ``live``."""
    return held in live or (held[0] in ("Lo", "Hi") and held[1] in live)


def _linear(index):
    """``(constant, byte)`` of a word ``constant + Zext byte`` (``byte`` None if absent)."""
    kind = index[0]
    if kind == "WC":
        return index[1], None
    if kind == "Zext":
        return 0, index[1]
    if kind in ("WAdd", "WSub"):
        (ca, ba), (cb, bb) = _linear(index[1]), _linear(index[2])
        if bb is None:
            return (ca + cb if kind == "WAdd" else ca - cb), ba
        if kind == "WAdd" and ba is None:
            return ca + cb, bb
    raise CodegenError(f"table index {index}")


def address(term):
    """``(table, offset, index byte)`` of a table read byte term (``offset`` from ``T.lo``),
    else None."""
    if term[0] == "TabB":
        table, index, half = term[1], term[2], 0
    elif term[0] in ("Lo", "Hi") and term[1][0] == "Tab":
        table, index, half = term[1][1], term[1][2], SPAN * (term[0] == "Hi")
    else:
        return None
    constant, byte = _linear(index)
    if not 0 <= half + constant < 2 * SPAN:
        raise CodegenError(f"table offset {half + constant}")
    return table, half + constant, byte


def size(statements, zero_page) -> int:
    """The bytes of ``statements`` with the symbols ``zero_page`` in zero page."""
    total = 0
    for s in statements:
        if isinstance(s, Op):
            operand = s.operand
            if operand is None:
                total += 1
            elif isinstance(operand, Imm):
                total += 2
            elif isinstance(operand, Mem):
                total += 2 if operand.symbol in zero_page else 3
            else:
                total += 3 if s.mnemonic == "jmp" else 2
    return total


class _Machine:  # pylint: disable=too-few-public-methods
    """What the selector knows at a point of the code: each byte's and register's term, the
    term N and Z reflect, and the carry (None when unknown)."""

    def __init__(self, homes: dict, reg=None, carry=None):
        self.mem = dict(homes)
        self.reg = dict(reg or {"a": None, "x": None, "y": None})
        self.flags = None
        self.carry = carry

    def copy(self) -> "_Machine":
        """A copy."""
        out = _Machine(self.mem, self.reg, self.carry)
        out.flags = self.flags
        return out


class Codegen:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """Code for the diagrams of routines of one :class:`~.semantics.Shape`; ``wide`` the
    parameters whose both bytes the routine reads, ``leaves`` a :class:`~.superopt.Leaves`
    making each final leaf minimal, if given."""

    def __init__(self, shape, wide=(), leaves=None):
        self.shape, self.wide, self.leaves = shape, frozenset(wide), leaves
        self.sizes = dict(SYMBOLS + shape.state)
        self.homes = {}
        for name, width in self.sizes.items():
            whole = ("WV", name) if width == 2 else ("BV", name)
            for k in range(width):
                self.homes[(name, k)] = _byte(whole, k) if width == 2 else whole
        self.code, self.count, self.pending, self.everything = [], 0, set(), ()
        self.at = _Machine(self.homes)

    def emit(self, mnemonic: str, operand=None) -> None:
        """Appends an op, forgetting the flags it changes."""
        self.code.append(op(mnemonic, operand))
        if mnemonic in _NZ:
            self.at.flags = None
        if mnemonic in _CARRY:
            self.at.carry = None

    def label(self) -> str:
        """A fresh label name."""
        while f"l{self.count}" in self.sizes:
            self.count += 1
        self.count += 1
        return f"l{self.count - 1}"

    def bytes(self, statements) -> int:
        """The size of ``statements``."""
        return size(statements, self.sizes)

    # -- operands and bytes ---------------------------------------------------------------

    def operand(self, term):
        """The immediate or memory operand holding byte ``term`` now, else None."""
        m = self.at
        if term[0] == "BC":
            return Imm(term[1])
        if term[0] in ("Lo", "Hi") and term[1][0] == "WV" and term[1][1][0] == "#":
            name = term[1][1][1:]
            return Imm(name, ">" if term[0] == "Hi" else "<" if name in self.wide else "")
        for (symbol, k), held in sorted(m.mem.items()):
            if held == term:
                return Mem(symbol, k)
        where = address(term)
        if where is not None:
            table, offset, byte = where
            half, offset = ("lo", offset) if offset < SPAN else ("hi", offset - SPAN)
            if byte is None:
                return Mem(f"{table}.{half}", offset)
            for reg in ("x", "y"):
                if m.reg[reg] == byte:
                    return Mem(f"{table}.{half}", offset, reg)
        return None

    def index(self, term, avoid=frozenset()) -> None:
        """Puts the index of byte ``term``'s table read, if it is one, in Y or X (one not
        holding a byte of ``avoid``)."""
        where = address(term)
        if where is None or where[2] is None:
            return
        byte, m = where[2], self.at
        if byte in (m.reg["x"], m.reg["y"]):
            return
        free = [r for r in ("y", "x") if m.reg[r] not in avoid]
        if not free:
            raise CodegenError("no index register free")
        source = self.operand(byte)
        if source is not None and m.reg["a"] != byte and not _indexed(source):
            self.emit(f"ld{free[0]}", source)
        else:
            self.load(byte)
            self.emit("ta" + free[0])
        m.reg[free[0]] = m.flags = byte

    def indices(self, terms) -> None:
        """Puts the index of each table read of ``terms`` in a register."""
        need = {w[2] for w in map(address, terms) if w is not None and w[2] is not None}
        for t in sorted(terms, key=lambda t: len(_reads(address(t)[2])) if _uses(t) else 0):
            self.index(t, need)

    def load(self, term) -> None:
        """Puts byte ``term`` in A."""
        m = self.at
        if m.reg["a"] == term:
            return
        for reg in ("x", "y"):
            if m.reg[reg] == term:
                self.emit(f"t{reg}a")
                m.reg["a"] = m.flags = term
                return
        self.index(term)
        source, kind = self.operand(term), term[0]
        if source is not None:
            self.emit("lda", source)
        elif kind in _ALU:
            self.alu(term)
        elif kind in ("Shl", "Shr"):
            self.load(term[1])
            for _ in range(term[2]):
                self.emit("asl" if kind == "Shl" else "lsr")
            m.carry = None
        elif kind == "Hi" and term[1][0] in ("WAdd", "WSub") and self.chained(term[1]):
            pass
        elif kind in ("Lo", "Hi") and term[1][0] not in ("WV", "Tab"):
            self.word(term[1], self.temp(term[1]))
            self.emit("lda", self.operand(term))
        else:
            raise CodegenError(f"no code for byte {term}")
        m.reg["a"] = m.flags = term

    def chained(self, w) -> bool:
        """Puts the high byte of word add or subtract ``w`` in A, its low byte discarded,
        where both operands' bytes are ready; whether it could."""
        kind, a, b = w
        parts = [lo(a), lo(b), hi(a), hi(b)]
        if not all(self.ready(p) for p in parts) or any(x[0] == "Sext" for x in (a, b)):
            return False
        self.indices(parts)
        for x, y in ((lo(a), lo(b)), (hi(a), hi(b))):
            if self.operand(y) is None:
                return False
        self.carry(kind == "WSub")
        for x, y in ((lo(a), lo(b)), (hi(a), hi(b))):
            self.load(x)
            self.emit("adc" if kind == "WAdd" else "sbc", self.operand(y))
            self.at.reg["a"] = None
        return True

    def alu(self, term) -> None:
        """Puts byte ``term``, an add, subtract or logical op, in A."""
        kind, a, b = term
        m = self.at
        self.indices((a, b))
        if kind != "Sub" and (self.operand(b) is None or m.reg["a"] == b):
            a, b = b, a
        if kind == "Sub" and self.operand(b) is None and self.operand(a) is not None:
            self.load(b)
            self.emit("eor", Imm(255))
            self.carry(True)
            self.emit("adc", self.operand(a))
            m.carry = None
            return
        if self.operand(b) is None:
            raise CodegenError(f"{kind} of two computed bytes")
        self.load(a)
        if kind in ("Add", "Sub"):
            self.carry(kind == "Sub")
            m.carry = None
        self.emit(_ALU[kind], self.operand(b))

    def carry(self, value: bool) -> None:
        """Sets the carry to ``value`` unless it is known to be."""
        if self.at.carry is not value:
            self.emit("sec" if value else "clc")
            self.at.carry = value

    def store(self, symbol: str, k: int, term) -> None:
        """Stores A, holding byte ``term``, to ``symbol+k``."""
        self.emit("sta", Mem(symbol, k))
        self.at.mem[(symbol, k)] = term

    # -- words ----------------------------------------------------------------------------

    def ready(self, b) -> bool:
        """Whether byte ``b`` loads into A without touching the carry."""
        if b in self.at.reg.values() or self.operand(b) is not None:
            return True
        return address(b) is not None

    def live(self, skip=None) -> set:
        """The subterms of what is pending but ``skip``."""
        out = set()
        for t in self.pending - {skip}:
            _reads(t, out)
        return out

    def temp(self, w, keep=()) -> str:
        """``phi``, if nothing pending but ``w`` (nor ``keep``) reads what it holds."""
        live = self.live(w)
        for t in keep:
            _reads(t, live)
        if not any(_needed(self.at.mem[("phi", k)], live) for k in (0, 1)):
            return "phi"
        raise CodegenError("no free word for an intermediate")

    def operandise(self, w, dest: str, keep):
        """``w`` where both its bytes are ready, else ``w`` computed to ``dest`` (or ``phi``
        where ``keep`` or anything pending reads ``dest``)."""
        if w[0] == "Sext":
            if not self.ready(w[1]):
                raise CodegenError("a sign extension of a computed byte")
            return w
        if self.ready(lo(w)) and self.ready(hi(w)):
            return w
        live = self.live(w)
        _reads(keep, live)
        clear = not any(_needed(self.at.mem[(dest, k)], live) for k in (0, 1))
        where = dest if clear else self.temp(w, (keep,))
        self.word(w, where)
        return ("Cat", self.at.mem[(where, 1)], self.at.mem[(where, 0)])

    def word(self, w, dest: str) -> None:
        """Computes word ``w`` to the 2-byte location ``dest``."""
        m = self.at
        if (m.mem[(dest, 0)], m.mem[(dest, 1)]) == (lo(w), hi(w)):
            return
        kind = w[0]
        self.pending.add(w)
        if kind in ("WAdd", "WSub"):
            self.chain(w, dest)
        elif kind in ("WShr", "WShl"):
            self.shift(w, dest)
        elif kind == "Sext":
            self.index(w[1])
            where = address(w[1])
            self.sign(w[1], {where[2]} if where else set())
            self.store(dest, 0, w[1])
            self.load(hi(w))
            self.store(dest, 1, hi(w))
        elif kind in ("WV", "WC", "Cat", "Zext", "Tab"):
            parts = (lo(w), hi(w))
            self.indices(parts)
            for k, b in enumerate(parts):
                self.load(b)
                self.store(dest, k, b)
        else:
            raise CodegenError(f"no code for word {kind}")
        self.pending.discard(w)

    def sign(self, b, avoid=frozenset()) -> None:
        """Puts the sign byte of ``b`` (0 or 255) in Y or X, one holding no byte of
        ``avoid``; A holds ``b`` after."""
        m = self.at
        free = [r for r in ("y", "x") if m.reg[r] not in avoid]
        if not free:
            raise CodegenError("no register free for a sign")
        reg, skip = free[0], self.label()
        if m.reg[reg] != ("BC", 0):
            self.emit(f"ld{reg}", Imm(0))
            m.reg[reg] = m.flags = ("BC", 0)
        self.load(b)
        if m.flags != b:
            self.emit("cmp", Imm(0))
            m.carry = True
        self.emit("bpl", skip)
        self.emit(f"de{reg}")
        self.code.append(Label(skip))
        m.reg[reg] = ("Hi", ("Sext", b))

    def chain(self, w, dest: str) -> None:
        """An add or subtract of two words, byte by byte through the carry."""
        kind, a, b = w
        m = self.at
        a = self.operandise(a, dest, b)
        b = self.operandise(b, dest, a)
        if kind == "WAdd" and (m.mem[(dest, 0)], m.mem[(dest, 1)]) == (lo(b), hi(b)):
            a, b = b, a
        parts = [lo(a), hi(a), lo(b), hi(b)]
        self.indices(parts)
        need = {w[2] for w in map(address, parts) if w is not None and w[2] is not None}
        for t in (a, b):
            if t[0] == "Sext":
                self.sign(t[1], need)
        self.carry(kind == "WSub")
        for k in (0, 1):
            x, y = _byte(a, k), _byte(b, k)
            if kind == "WAdd" and (self.operand(y) is None or m.reg["a"] == y):
                x, y = y, x
            if self.operand(y) is None:
                raise CodegenError("a word operand byte is not addressable")
            self.load(x)
            self.emit("adc" if kind == "WAdd" else "sbc", self.operand(y))
            m.reg["a"] = m.flags = _byte(w, k)
            self.store(dest, k, _byte(w, k))
        m.carry = None

    def shift(self, w, dest: str) -> None:
        """A word shifted by a byte count, by a loop on X unless the count is a constant."""
        kind, a, count = w
        m = self.at
        self.word(a, dest)
        steps = (("lsr", 1), ("ror", 0)) if kind == "WShr" else (("asl", 0), ("rol", 1))
        if count[0] == "WC" and count[1] <= 4:
            for _ in range(count[1]):
                for mnemonic, k in steps:
                    self.emit(mnemonic, Mem(dest, k))
        else:
            byte = lo(count)
            source = self.operand(byte)
            if source is None or _indexed(source):
                self.load(byte)
                self.emit("tax")
            else:
                self.emit("ldx", source)
            done, loop = self.label(), self.label()
            if count[0] != "WC":
                self.emit("beq", done)
            self.code.append(Label(loop))
            for mnemonic, k in steps:
                self.emit(mnemonic, Mem(dest, k))
            self.emit("dex")
            self.emit("bne", loop)
            if count[0] != "WC":
                self.code.append(Label(done))
            m.reg["x"] = ("BC", 0)
        m.carry = m.flags = None
        for k in (0, 1):
            m.mem[(dest, k)] = _byte(w, k)
        if m.reg["a"] in (lo(a), hi(a)):
            m.reg["a"] = None

    # -- tests ----------------------------------------------------------------------------

    def test(self, atom, target: str, when: bool) -> tuple:
        """Code jumping to ``target`` when ``atom`` is ``when``; ``(fall, taken)``, what is
        known on each way, as ``{"carry": bool, "a": term}``."""
        m = self.at
        kind = atom[0]
        if kind == "Not":
            return self.test(atom[1], target, not when)
        if kind in ("Ult", "WUlt"):
            facts = self.compare(atom)
        elif kind == "Odd":
            self.load(atom[1])
            self.emit("lsr")
            m.reg["a"] = m.flags = ("Shr", atom[1], 1)
            facts = ("bcs", {"carry": True}, {"carry": False})
        elif kind == "WEq" and ("WC", 0) in atom[1:]:
            w = atom[2] if atom[1] == ("WC", 0) else atom[1]
            self.indices((lo(w), hi(w)))
            self.load(lo(w))
            if self.operand(hi(w)) is None:
                raise CodegenError("a word test of a computed byte")
            self.emit("ora", self.operand(hi(w)))
            m.reg["a"] = m.flags = ("Or", lo(w), hi(w))
            facts = ("beq", {"a": ("BC", 0)}, {})
        elif kind in ("Eq", "Sgn"):
            facts = self.equal(atom)
        else:
            raise CodegenError(f"no test for {kind}")
        branch, true, false = facts
        self.emit(branch if when else _INVERSE[branch], target)
        return (false, true) if when else (true, false)

    def compare(self, atom) -> tuple:
        """The compare code of an ``Ult`` or ``WUlt`` atom; ``(branch, true, false)``."""
        m = self.at
        kind, a, b = atom
        if kind == "WUlt" and not (self.ready(lo(b)) and self.ready(hi(b))):
            self.word(b, self.temp(b, (a,) + self.everything))
            b = ("Cat", self.at.mem[("phi", 1)], self.at.mem[("phi", 0)])
        parts = [(a, b)] if kind == "Ult" else [(lo(a), lo(b)), (hi(a), hi(b))]
        self.indices([t for pair in parts for t in pair])
        for k, (x, y) in enumerate(parts):
            if self.operand(y) is None:
                raise CodegenError("a compare with a computed byte")
            self.load(x)
            self.emit("cmp" if k == 0 else "sbc", self.operand(y))
        m.flags = None
        if kind == "WUlt":
            m.reg["a"] = None
        return "bcc", {"carry": False}, {"carry": True}

    def equal(self, atom) -> tuple:
        """The code of an ``Eq`` or ``Sgn`` atom; ``(branch, true, false)``."""
        m = self.at
        kind = atom[0]
        a, b = (atom[1], atom[2]) if kind == "Eq" else (atom[1], ("BC", 0))
        if kind == "Eq" and (a[0] == "BC" or (self.operand(b) is None and m.reg["a"] != a)):
            a, b = b, a
        if b == ("BC", 0):
            self.load(a)
            if m.flags != a:
                self.emit("cmp", Imm(0))
                m.carry = True
        else:
            self.indices((a, b))
            self.load(a)
            if self.operand(b) is None:
                raise CodegenError("a compare with a computed byte")
            self.emit("cmp", self.operand(b))
            m.carry, m.flags = None, None
        if kind == "Sgn":
            return "bmi", {}, {}
        return "beq", ({"a": b} if b[0] == "BC" else {}), {}

    # -- leaves ---------------------------------------------------------------------------

    def leaf(self, terms, cont) -> None:
        """Straight-line code storing each output, in the order whose code is smallest,
        then ``rts`` or a jump to ``cont``."""
        best, cheapest = None, INFINITE
        for order in itertools.islice(itertools.permutations(terms), ORDERS):
            mark = (len(self.code), self.at.copy(), self.count)
            try:
                self.outputs(order)
                cost = self.bytes(self.code[mark[0] :])
            except CodegenError:
                cost = INFINITE
            self.code, self.at, self.count = self.code[: mark[0]], mark[1], mark[2]
            if cost < cheapest:
                best, cheapest = order, cost
        if best is None:
            raise CodegenError("no order of the outputs computes them")
        mark, entry = len(self.code), self.at.copy()
        self.outputs(best)
        if cont is None and self.leaves is not None:
            better = self.leaves.improve(self.code[mark:], [n for n, _ in terms], entry)
            if better is not None and self.bytes(better) < self.bytes(self.code[mark:]):
                self.code[mark:] = list(better)
        self.emit("rts" if cont is None else "jmp", cont)

    def outputs(self, order) -> None:
        """Stores each output of ``order`` in turn; CodegenError where one overwrites an
        input a later one reads."""
        for k, (name, term) in enumerate(order):
            rest = order[k + 1 :]
            self.pending = {t for _, t in rest} | {term}
            live = set()
            for _, t in rest:
                _reads(t, live)
            if any(_needed(self.at.mem[(name, j)], live) for j in range(self.sizes[name])):
                raise CodegenError(f"{name} is read after it is written")
            if self.sizes[name] == 2:
                self.word(term, name)
            else:
                self.load(term)
                self.store(name, 0, term)
        self.pending = set()

    def cost(self, terms, gone=()) -> float:
        """The bytes of a leaf of ``terms`` with its ``rts``, knowing nothing of the
        registers and the outputs ``gone`` as their primes; INFINITE where there is no code."""
        self.code, self.count, self.at = [], 0, _Machine(self.homes)
        for name in gone:
            self.prime(self.at, name)
        try:
            self.leaf(terms, None)
        except CodegenError:
            return INFINITE
        return self.bytes(self.code)

    def test_cost(self, atom, gone=()) -> float:
        """The bytes of a test of ``atom``, knowing nothing of the registers and the outputs
        ``gone`` as their primes."""
        self.code, self.count, self.at = [], 0, _Machine(self.homes)
        for name in gone:
            self.prime(self.at, name)
        try:
            self.test(atom, "l", True)
        except CodegenError:
            return INFINITE
        return self.bytes(self.code)

    # -- the diagram ----------------------------------------------------------------------

    def diagram(self, root) -> tuple:
        """The statements of a diagram, entered with A, X, Y 0 and the carry clear."""
        self.code, self.count = [], 0
        self.everything = tuple(_terms(root))
        zero = ("BC", 0)
        self.region(root, _Machine(self.homes, {"a": zero, "x": zero, "y": zero}, False), None)
        return self.relax(_peephole(self.code))

    def relax(self, code) -> tuple:
        """``code`` with each branch out of range a reversed branch over a ``jmp``."""
        code = list(code)
        while True:
            at, labels, pc = [], {}, 0
            for s in code:
                at.append(pc)
                if isinstance(s, Label):
                    labels[s.name] = pc
                pc += self.bytes([s])
            far = [
                k
                for k, s in enumerate(code)
                if isinstance(s, Op)
                and s.mnemonic in _INVERSE
                and not -128 <= labels[s.operand] - at[k] - 2 < 128
            ]
            if not far:
                return tuple(code)
            k = far[0]
            skip = self.label()
            code[k : k + 1] = [
                op(_INVERSE[code[k].mnemonic], skip),
                op("jmp", code[k].operand),
                Label(skip),
            ]

    def region(self, root, entry: _Machine, cont) -> list:
        """Emits diagram ``root`` entered knowing ``entry``, its leaves ending in ``rts`` or
        a jump to label ``cont``; a node with several predecessors is emitted once, labelled,
        entered knowing nothing of the registers. Returns what is known at each jump."""
        preds = nodes(root)
        names, done, exits = {}, set(), []
        work = deque([(root, entry.copy())])
        while work:
            node, machine = work.popleft()
            if node in done:
                continue
            if node in names:
                self.code.append(Label(names[node]))
            self.at = machine if preds[node] == 1 else _Machine(entry.mem)
            while node is not None:
                done.add(node)
                node = self.step(node, (preds, names, done, work, entry), cont, exits)
        return exits

    def step(self, node, region, cont, exits):  # pylint: disable=too-many-locals
        """Emits one node; the node falling through next, else None."""
        preds, names, done, work, entry = region
        if node[0] == "leaf":
            self.leaf(node[1], cont)
            exits.append(self.at.copy())
            return None
        if node[0] == "seq":
            self.seq(node, cont, exits)
            return None
        _, atom, then, other = node
        if other in done:
            fall, _ = self.test(atom, names[other], False)
            follow = then
        elif then in done:
            fall, _ = self.test(atom, names[then], True)
            follow = other
        else:
            names.setdefault(other, self.label())
            fall, taken = self.test(atom, names[other], False)
            branch = self.at.copy()
            _know(branch, taken)
            work.appendleft((other, branch))
            follow = then
        _know(self.at, fall)
        if follow in done:
            self.emit("jmp", names[follow])
            return None
        if preds[follow] > 1:
            names.setdefault(follow, self.label())
            self.code.append(Label(names[follow]))
            self.at = _Machine(entry.mem)
        return follow

    def prime(self, machine: _Machine, name: str) -> None:
        """Records in ``machine`` that output ``name`` holds its primed value, as do the
        registers holding what was stored to it."""
        primed = ("WV" if self.sizes[name] == 2 else "BV", f"{name}'")
        for k in range(self.sizes[name]):
            held = _byte(primed, k) if self.sizes[name] == 2 else primed
            machine.reg = {
                r: held if t == machine.mem[(name, k)] else t for r, t in machine.reg.items()
            }
            machine.mem[(name, k)] = held

    def seq(self, node, cont, exits) -> None:
        """Emits ``("seq", name, first, rest)``: ``first``'s leaves jump to where ``rest``
        starts, which knows output ``name`` as its primed input."""
        _, name, first, rest = node
        join = self.label()
        joined = self.region(first, self.at, join)
        self.code.append(Label(join))
        for m in joined:
            self.prime(m, name)
        self.at = _merge(joined)
        exits.extend(self.region(rest, self.at, cont))


_INVERSE = {"beq": "bne", "bmi": "bpl", "bcc": "bcs", "bcs": "bcc"}
_INVERSE.update({v: k for k, v in list(_INVERSE.items())} | {"bvc": "bvs", "bvs": "bvc"})


def _terms(node):
    """Every leaf term and atom of a diagram."""
    if node[0] == "leaf":
        yield from (t for _, t in node[1])
    elif node[0] == "seq":
        yield from _terms(node[2])
        yield from _terms(node[3])
    else:
        yield node[1]
        yield from _terms(node[2])
        yield from _terms(node[3])


def _know(machine: _Machine, facts: dict) -> None:
    """Records ``facts`` (``carry`` and ``a``) in ``machine``."""
    if "carry" in facts:
        machine.carry = facts["carry"]
    if "a" in facts:
        machine.reg["a"] = facts["a"]


def _merge(machines: list) -> _Machine:
    """What is known in each of ``machines``."""
    out = machines[0].copy()
    for m in machines[1:]:
        for key in out.mem:
            if out.mem[key] != m.mem[key]:
                out.mem[key] = ("Joined",) + key
        for reg in out.reg:
            if out.reg[reg] != m.reg[reg]:
                out.reg[reg] = None
        if out.carry != m.carry:
            out.carry = None
    out.flags = None
    return out


def _peephole(code: list) -> tuple:
    """``code`` without jumps to the label that follows them."""
    out = []
    for k, s in enumerate(code):
        nxt = code[k + 1] if k + 1 < len(code) else None
        if isinstance(s, Op) and s.mnemonic == "jmp" and nxt == Label(s.operand):
            continue
        out.append(s)
    return tuple(out)
