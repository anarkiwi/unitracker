"""The optimiser's term language: bytes ``B``, words ``W`` and predicates ``P``, as tuples
``(constructor, *args)``, translated from and to z3 and printed as egglog s-expressions."""

import re

import z3

BYTE = frozenset("BC BV Lo Hi Add Sub And Or Xor Shl Shr Ite Bit TabB".split())
WORD = frozenset("WC WV Cat Zext Sext WAdd WSub WAnd WOr WXor WShl WShr WIte Tab WAdcS".split())
PRED = frozenset(
    "PT PF Not PAnd POr Eq Ult Ule WEq WUlt WUle Sgn Odd AdcC SbcC WAdcC WSbcC".split()
)
COMMUTATIVE = frozenset("Add And Or Xor WAdd WAnd WOr WXor PAnd POr Eq WEq".split())
_Z3_BIN = {
    z3.Z3_OP_BADD: ("Add", "WAdd"),
    z3.Z3_OP_BSUB: ("Sub", "WSub"),
    z3.Z3_OP_BAND: ("And", "WAnd"),
    z3.Z3_OP_BOR: ("Or", "WOr"),
    z3.Z3_OP_BXOR: ("Xor", "WXor"),
    z3.Z3_OP_EQ: ("Eq", "WEq"),
    z3.Z3_OP_ULT: ("Ult", "WUlt"),
    z3.Z3_OP_ULEQ: ("Ule", "WUle"),
}


class TermError(ValueError):
    """A z3 term outside the term language."""


def from_z3(term, memo=None):  # pylint: disable=too-many-statements
    """The term of a z3 term (as the executor builds them); ``memo`` holds each z3 term it
    keys so no id is reused."""
    # pylint: disable=too-many-return-statements,too-many-branches
    memo = {} if memo is None else memo
    key = term.get_id()
    if key in memo:
        return memo[key][1]

    def go(t):
        return from_z3(t, memo)

    kind, args = term.decl().kind(), term.children()
    word = z3.is_bv(term) and term.size() == 16
    if z3.is_bv_value(term):
        out = ("WC" if word else "BC", term.as_long())
    elif z3.is_true(term):
        out = ("PT",)
    elif z3.is_false(term):
        out = ("PF",)
    elif kind == z3.Z3_OP_UNINTERPRETED:
        out = ("WV" if word else "BV", term.decl().name())
    elif kind == z3.Z3_OP_EXTRACT:
        hi, lo = term.params()
        if (hi, lo) not in ((7, 0), (15, 8)) or args[0].size() != 16:
            raise TermError(f"extract {hi}:{lo}")
        out = ("Lo" if lo == 0 else "Hi", go(args[0]))
    elif kind == z3.Z3_OP_CONCAT:
        out = ("Cat", go(args[0]), go(args[1]))
    elif kind == z3.Z3_OP_ZERO_EXT:
        out = ("Zext", go(args[0]))
    elif kind == z3.Z3_OP_SIGN_EXT:
        out = ("Sext", go(args[0]))
    elif kind in _Z3_BIN:
        wide = args[0].size() == 16 if z3.is_bv(args[0]) else False
        name = _Z3_BIN[kind][wide]
        out = go(args[0])
        for a in args[1:]:
            out = (name, out, go(a))
    elif kind == z3.Z3_OP_DISTINCT:
        out = ("Not", go(args[0] == args[1]))
    elif kind in (z3.Z3_OP_BSHL, z3.Z3_OP_BLSHR):
        name = ("Shl", "Shr") if not word else ("WShl", "WShr")
        name = name[kind == z3.Z3_OP_BLSHR]
        if word:
            out = (name, go(args[0]), go(args[1]))
        elif z3.is_bv_value(args[1]):
            out = (name, go(args[0]), args[1].as_long())
        else:
            raise TermError("a variable byte shift")
    elif kind == z3.Z3_OP_ITE:
        name = "WIte" if word else "Ite"
        out = (name, go(args[0]), go(args[1]), go(args[2]))
    elif kind == z3.Z3_OP_NOT:
        out = ("Not", go(args[0]))
    elif kind in (z3.Z3_OP_AND, z3.Z3_OP_OR):
        name = "PAnd" if kind == z3.Z3_OP_AND else "POr"
        out = go(args[0])
        for a in args[1:]:
            out = (name, out, go(a))
    elif kind == z3.Z3_OP_SELECT:
        out = ("TabB", args[0].decl().name(), go(args[1]))
    else:
        raise TermError(f"z3 {term.decl()}")
    memo[key] = (term, out)
    return out


def to_z3(term, inputs: dict, memo=None):  # pylint: disable=R0914,R0915
    """The z3 term of a term over ``inputs`` ``{name: z3 term}``."""
    # pylint: disable=too-many-return-statements,too-many-branches
    memo = {} if memo is None else memo
    if term in memo:
        return memo[term]

    def go(t):
        return to_z3(t, inputs, memo)

    op, args = term[0], term[1:]
    one, zero = z3.BitVecVal(1, 8), z3.BitVecVal(0, 8)
    simple = {
        "Add": lambda a, b: a + b,
        "Sub": lambda a, b: a - b,
        "And": lambda a, b: a & b,
        "Or": lambda a, b: a | b,
        "Xor": lambda a, b: a ^ b,
        "Eq": lambda a, b: a == b,
        "Ult": z3.ULT,
        "Ule": z3.ULE,
        "PAnd": z3.And,
        "POr": z3.Or,
        "WShl": lambda a, b: a << b,
        "WShr": z3.LShR,
    }
    if op in ("BC", "WC"):
        out = z3.BitVecVal(args[0], 8 if op == "BC" else 16)
    elif op in ("BV", "WV"):
        out = inputs[args[0]]
    elif op in ("PT", "PF"):
        out = z3.BoolVal(op == "PT")
    elif op == "TabB":
        out = z3.Select(inputs[args[0]], go(args[1]))
    elif op == "Tab":
        table, index = inputs[args[0]], go(args[1])
        out = z3.Concat(z3.Select(table, index + 256), z3.Select(table, index))
    elif op in ("Shl", "Shr"):
        a = go(args[0])
        out = a << args[1] if op == "Shl" else z3.LShR(a, args[1])
    else:
        a = [go(x) for x in args]
        name = op[1:] if op[0] == "W" and op[1:] in simple else op
        if name in simple:
            out = simple[name](*a)
        elif op == "Lo":
            out = z3.Extract(7, 0, a[0])
        elif op == "Hi":
            out = z3.Extract(15, 8, a[0])
        elif op == "Cat":
            out = z3.Concat(a[0], a[1])
        elif op == "Zext":
            out = z3.ZeroExt(8, a[0])
        elif op == "Sext":
            out = z3.SignExt(8, a[0])
        elif op in ("Ite", "WIte"):
            out = z3.If(*a)
        elif op == "Bit":
            out = z3.If(a[0], one, zero)
        elif op == "Not":
            out = z3.Not(a[0])
        elif op == "Sgn":
            out = z3.ULT(z3.BitVecVal(127, 8), a[0])
        elif op == "Odd":
            out = a[0] & 1 == 1
        elif op in ("AdcC", "WAdcC"):
            w = a[0].size()
            carry = z3.If(a[2], z3.BitVecVal(1, w + 1), z3.BitVecVal(0, w + 1))
            total = z3.ZeroExt(1, a[0]) + z3.ZeroExt(1, a[1]) + carry
            out = z3.Extract(w, w, total) == 1
        elif op in ("SbcC", "WSbcC"):
            w = a[0].size()
            borrow = z3.If(a[2], z3.BitVecVal(0, w + 1), z3.BitVecVal(1, w + 1))
            out = z3.ULE(z3.ZeroExt(1, a[1]) + borrow, z3.ZeroExt(1, a[0]))
        elif op == "WAdcS":
            out = a[0] + a[1] + z3.If(a[2], z3.BitVecVal(1, 16), z3.BitVecVal(0, 16))
        else:
            raise TermError(f"constructor {op}")
    memo[term] = out
    return out


def sexp(term, names: dict, lets: list) -> str:
    """The egglog text of a term, each compound subterm bound once: ``names`` ``{term: name}``
    and ``lets`` ``[(name, text)]`` (in dependency order) are filled in."""
    if term in names:
        return names[term]
    parts = [
        sexp(a, names, lets) if isinstance(a, tuple) else f'"{a}"' if isinstance(a, str) else str(a)
        for a in term[1:]
    ]
    text = f"({' '.join([term[0]] + parts)})"
    if not any(isinstance(a, tuple) for a in term[1:]):
        return text
    names[term] = f"$t{len(lets)}"
    lets.append((names[term], text))
    return names[term]


_TOKEN = re.compile(r'\(|\)|"[^"]*"|[^\s()]+')


def parse(text: str):
    """The term of an egglog s-expression."""
    stack, out = [[]], None
    for token in _TOKEN.findall(text):
        if token == "(":
            stack.append([])
        elif token == ")":
            node = stack.pop()
            stack[-1].append(tuple(node))
        elif token[0] == '"':
            stack[-1].append(token[1:-1])
        elif re.fullmatch(r"-?\d+", token):
            stack[-1].append(int(token))
        else:
            stack[-1].append(token)
    out = stack[0][0]
    return out


def size(term) -> int:
    """The number of constructors in a term as a tree."""
    return 1 + sum(size(a) for a in term[1:] if isinstance(a, tuple))


def dag(terms) -> int:
    """The number of distinct compound subterms of ``terms``."""
    seen = set()

    def walk(t):
        if t in seen:
            return
        seen.add(t)
        for a in t[1:]:
            if isinstance(a, tuple):
                walk(a)

    for t in terms:
        walk(t)
    return len(seen)


def substitute(term, mapping: dict):
    """``term`` with each subterm in ``mapping`` replaced."""
    if term in mapping:
        return mapping[term]
    if not isinstance(term, tuple):
        return term
    return (term[0],) + tuple(substitute(a, mapping) for a in term[1:])
