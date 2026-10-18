"""The playroutine schema's conventions: a definition with each key it leaves to one filled in.

Every default here gives way to an explicit key (docs/playroutines/schema.md).
"""

import ast
import copy
import re

from unitracker.format.records import EXPR, META_KEYS
from unitracker.player.modulation import function

NEXT = "next"
INS = "ins"
_PAIR = re.compile(r"^([A-Za-z_]\w*):([A-Za-z_]\w*)$")


class SchemaError(ValueError):
    """A playroutine key whose convention does not apply."""


def expand(pr: dict) -> dict:
    """A copy of playroutine ``pr`` with its conventions as explicit keys."""
    out = copy.deepcopy(pr)
    types = out.get("record_types") or {}
    for spec in types.values():
        _record_type(spec)
    if "instrument" not in out:
        ins = [n for n, s in types.items() if (s or {}).get("writes_as", n) == INS]
        if ins:
            out["instrument"] = {"type": ins[0], "base": 0}
    out.setdefault("note_offset", 0)
    ghost = out.get("ghost") or {}
    for scope in ("global", "voice"):
        if ghost.get(scope):
            ghost[scope] = _registers(ghost[scope], out.get(scope) or {})
    mods = out.get("modulators") or {}
    _named_tables(mods, out.get("functions") or {})
    bodies = None
    for unit in mods.get("units") or []:
        _unit(unit, out.get("voice") or {}, out.get("global") or {})
        if "hooks" not in unit:
            bodies = _bodies(out) if bodies is None else bodies
            _hooks(unit, bodies, out.get("records") or {})
    return out


def _record_type(spec: dict) -> None:
    """A record type is chained by its ``next`` field, ``none`` is 0 but for a ``fixed`` type,
    and its ``expr`` field references ``x``."""
    if "chain" not in spec and NEXT in (spec.get("fields") or ()):
        spec["chain"] = NEXT
    if "none" not in spec and not spec.get("fixed"):
        spec["none"] = 0
    if spec.get("expr") is not None:
        if not spec.get("refs"):
            spec["refs"] = {}
        spec["refs"].setdefault(spec["expr"], EXPR)


def _registers(regs: dict, cells: dict) -> dict:
    """Ghost registers with a word cell or a ``hi:lo`` pair at ``r`` given as ``r`` and ``r + 1``
    (low byte first) unless ``r + 1`` has its own entry."""
    out = {}
    for reg, text in regs.items():
        pair = _PAIR.match(str(text))
        width = (cells.get(text) or {}).get("width") if isinstance(text, str) else None
        if pair:
            parts = {reg: pair[2], reg + 1: pair[1]}
        elif width is not None and width > 8:
            parts = {reg: f"{text} & 0xFF", reg + 1: f"{text} >> 8"}
        else:
            parts = {reg: text}
        out.update((r, t) for r, t in parts.items() if r == reg or r not in regs)
    return out


def _unit(unit: dict, voice: dict, glob: dict) -> None:
    """A unit's ``x``, ``label`` and ``program`` from its ``site``, its ``scope`` from its cell,
    and its chip map's ``value`` and ``store`` from ``width`` and ``offset``."""
    site = unit.get("site") or {}
    if {"record", "index", "field"} <= site.keys():
        unit.setdefault("x", f"{site['record']}[{site['index']}].{site['field']}")
        unit.setdefault("label", [site["index"]])
        if unit.get("program") is not None:
            unit["program"].setdefault("record", site["record"])
            unit["program"].setdefault("pointer", site["index"])
    parts = str(unit.get("cell", "")).split(":")
    if "scope" not in unit and parts[0] not in voice and parts[0] in glob:
        unit["scope"] = "global"
    chip = unit.get("chip")
    if chip and "value" not in chip:
        chip.update(_chip(parts, int(chip["width"]), chip.get("offset"), {**glob, **voice}))


def _chip(parts: list, width: int, offset, cells: dict) -> dict:
    """``{value, store}`` of a chip map: the value is the cells less ``offset``, masked to
    ``width`` unless the cells fit it and there is no offset; a cell narrower than the chip holds
    its top bits, and ``store`` inverts ``value``."""
    off = None if offset is None else str(offset)
    if off is not None and not isinstance(
        ast.parse(off, mode="eval").body, (ast.Name, ast.Constant, ast.Subscript, ast.Call)
    ):
        off = f"({off})"
    shift, k = 0, int(parts[2]) if len(parts) > 2 else 8
    bits = int((cells.get(parts[0]) or {}).get("width") or width)
    if len(parts) == 1:
        shift = max(0, width - bits)
        joined = f"{parts[0]} << {shift}" if shift else parts[0]
    else:
        bits += k
        joined = f"(({parts[0]} << {k}) | ({parts[1]} & {_hex(k)}))"
    value = joined if off is None else f"({joined} - {off})"
    if bits > width or off is not None:
        value = f"{value} & {_hex(width)}"
    back = "value" if off is None else f"value + {off}"
    if len(parts) == 1:
        return {"value": value, "store": {parts[0]: f"{_par(back)} >> {shift}" if shift else back}}
    hi = f"({_par(back)} >> {k}) & {_hex(width - k)}"
    return {"value": value, "store": {parts[0]: hi, parts[1]: f"{_par(back)} & {_hex(k)}"}}


def _hex(bits: int) -> str:
    return f"0x{(1 << bits) - 1:X}"


def _par(text: str) -> str:
    return text if text.isidentifier() else f"({text})"


def _named_tables(mods: dict, functions: dict) -> None:
    """``freq`` named by a function reading one word table, or a hi/lo pair, at its argument; a
    table given by a one-argument ``function``: its ``entry`` that function's body, and the
    function rewritten over the table."""
    if isinstance(mods.get("freq"), str):
        mods["freq"] = _word(mods["freq"], functions)
    for name, table in (mods.get("tables") or {}).items():
        if "function" not in table:
            continue
        fn = functions[table["function"]]
        (arg,) = fn["args"]
        table.setdefault("entry", re.sub(rf"(?<![\w.]){arg}(?!\w)", "i", str(fn["expr"])))
        mods.setdefault("functions", {}).setdefault(table["function"], f"{name}[{arg}]")


def _word(name: str, functions: dict) -> dict:
    """``{word: t}`` or ``{hi: t, lo: u}`` of function ``name``."""
    fn = functions[name]
    (arg,) = fn["args"]
    body = ast.parse(str(fn["expr"]), mode="eval").body

    def table(node):
        idx = node.slice if isinstance(node, ast.Subscript) else None
        if isinstance(idx, ast.BinOp) and isinstance(idx.op, ast.BitAnd):
            idx = idx.left if ast.unparse(idx.right) in ("255", "0xFF") else None
        ok = isinstance(idx, ast.Name) and idx.id == arg and isinstance(node.value, ast.Name)
        return node.value.id if ok else None

    if table(body):
        return {"word": table(body)}
    if isinstance(body, ast.BinOp) and isinstance(body.op, (ast.BitOr, ast.Add)):
        for hi, lo in ((body.left, body.right), (body.right, body.left)):
            if isinstance(hi, ast.BinOp) and isinstance(hi.op, ast.LShift) and table(lo):
                if ast.unparse(hi.right) == "8" and table(hi.left):
                    return {"hi": table(hi.left), "lo": table(lo)}
    raise SchemaError(f"freq: {name} reads no word table or hi/lo pair at its argument")


def _bodies(pr: dict) -> dict:
    """``{function: (cells it writes, functions it calls)}`` of every proc, record row action
    list and template; a function is ``("proc", p)``, ``("record", r, f, i)`` or
    ``("type", t, f)``, and a call of a record's field ``("record", r, f)``."""
    lists = {("proc", p): a for p, a in (pr.get("procs") or {}).items()}
    for r, rows in (pr.get("records") or {}).items():
        for i, row in rows.items():
            lists.update((("record", r, f, i), a) for f, a in row.items() if isinstance(a, list))
    for t, spec in (pr.get("record_types") or {}).items():
        lists.update(
            (("type", t, k), a)
            for k, a in spec.items()
            if k not in META_KEYS and isinstance(a, list)
        )
    out = {}
    for key, actions in lists.items():
        out[key] = (set(), [])
        _effects(actions, *out[key])
    return out


def _effects(actions, writes: set, calls: list) -> None:
    for act in actions or []:
        writes.update(act.get("set") or {}, act.get("with") or {})
        writes.update([act["accumulate"]] if "accumulate" in act else [])
        calls.extend([("proc", act["call"])] if "call" in act else [])
        run = act.get("run")
        if run:
            calls.append(
                ("type", run["record"], run["template"])
                if "template" in run
                else ("record", run["record"], run["field"])
            )
        for k in ("then", "else", "on_bound", "for_voices"):
            _effects(act.get(k), writes, calls)


def _hooks(unit: dict, bodies: dict, records: dict) -> None:
    """A unit's starts: each of its firing's functions when its site is the row the firing runs,
    else each function outside its firing that writes its pointer cell."""
    site, prog = unit.get("site") or {}, unit.get("program")
    if site.get("row"):
        unit["hooks"] = [{"at": copy.deepcopy(function(e))} for e in unit.get("fire") or []]
        return
    pointer = (prog or {}).get("pointer", site.get("index"))
    if pointer is None:
        return
    todo = [k for e in unit.get("fire") or [] for k in _keys(function(e), records)]
    fired = set()
    while todo:
        key = todo.pop()
        if key not in fired:
            fired.add(key)
            todo.extend(
                k for call in bodies.get(key, ((), ()))[1] for k in _keys(_ref(call), records)
            )
    kind = "pointer" if prog else "written"
    unit["hooks"] = [
        {"at": _ref(key), kind: [pointer]}
        for key, (writes, _) in bodies.items()
        if pointer in writes and key not in fired
    ]


_FIELDS = {"proc": ("proc",), "type": ("type", "template"), "record": ("record", "field", "index")}


def _ref(key: tuple) -> dict:
    """The function reference of a function key."""
    return dict(zip(_FIELDS[key[0]], key[1:]))


def _keys(ref: dict, records: dict) -> list:
    """The function keys of a function reference, a record's rows those of its ``index``."""
    if "proc" in ref:
        return [("proc", ref["proc"])]
    if "type" in ref:
        return [("type", ref["type"], ref["template"])]
    index = ref.get("index")
    rows = records.get(ref["record"]) or {}
    wanted = rows if index is None else (index if isinstance(index, list) else [index])
    return [("record", ref["record"], ref["field"], i) for i in rows if i in wanted]
