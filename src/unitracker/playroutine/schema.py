"""The playroutine schema's conventions: a definition with each key it leaves to one filled in.

Every default here gives way to an explicit key (docs/playroutines/schema.md).
"""

import ast
import copy
import re

from unitracker.playroutine.codec import ROW_KEYS, command_variants
from unitracker.playroutine.records import EXPR, META_KEYS

NEXT = "next"
INS = "ins"
_PAIR = re.compile(r"^([A-Za-z_]\w*):([A-Za-z_]\w*)$")


class SchemaError(ValueError):
    """A playroutine key whose convention does not apply."""


def function(item) -> dict:
    """The function a ``fire`` entry names: ``{proc}``, ``{type, template}`` or ``{record, field}``
    (see the schema's modulator declarations); an entry that is no proc name or ``run`` is one."""
    if isinstance(item, str):
        return {"proc": item}
    if "run" not in item:
        return item
    run = item["run"]
    if "template" in run:
        return {"type": run["record"], "template": run["template"]}
    return {"record": run["record"], "field": run["field"]}


def run_of(ref: dict) -> dict:
    """The ``run`` spec, less its index, of a template or record function."""
    if "type" in ref:
        return {"record": ref["type"], "template": ref["template"]}
    return {"record": ref["record"], "field": ref["field"]}


def expand(pr: dict) -> dict:
    """A copy of playroutine ``pr`` with its conventions as explicit keys."""
    out = copy.deepcopy(pr)
    types = out.get("record_types") or {}
    for spec in types.values():
        _record_type(spec)
    for spec in types.values():
        if spec.get("codec"):
            commands = out.get("commands") or {}
            spec["codec"] = [s for c in spec["codec"] for s in _command_shapes(c, commands, types)]
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
    for name, fields in first_only(out).items():
        _first_rows(types[name].get("codec") or [], fields)
    return out


def first_only(pr: dict) -> dict:
    """``{record type: {fields}}``: the expression fields of the units whose start reads the
    expression on the first row of a program (a ``site`` of a chained type without ``row``)."""
    types = pr.get("record_types") or {}
    out = {}
    for unit in (pr.get("modulators") or {}).get("units") or []:
        site = unit.get("site") or {}
        spec = types.get(site.get("record")) or {}
        if "field" in site and not site.get("row") and spec.get("chain") is not None:
            out.setdefault(site["record"], set()).add(str(site["field"]))
    return out


def _first_rows(shapes: list, fields: set) -> None:
    """Each codec shape with a key holding the expression of one of ``fields`` fits a row that
    sets it only on a program's first row, where a start reads it."""
    for shape in shapes:
        keys = (shape.get("keys") or {}).values()
        held = sorted({k["expr"] for k in keys if isinstance(k, dict) and k.get("expr") in fields})
        cond = "(" + " and ".join(f"{f} == 0" for f in held) + " or first)"
        when = shape.get("when")
        if held and not str(when).startswith(cond):
            shape["when"] = cond + (f" and ({when})" if when else "")


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


def _bind(text, name: str, expr: str) -> str:
    """Expression ``text`` with name ``name`` replaced by ``expr``."""
    return re.sub(rf"(?<![\w.]){name}(?!\w)", _par(str(expr)), str(text))


def _command_shapes(shape: dict, commands: dict, types: dict) -> list:
    """The shapes a codec shape ``{command: c, arg: a}`` stands for: one per ``commands`` entry
    named a shared row key (not an expression's) whose code is not in its ``skip``, fitting a row
    whose ``c`` is the entry's code and ``a`` an argument the entry takes but its type's ``none``,
    and writing the key as the command writes its argument; its ``raw`` reads ``command`` as the
    code and ``arg`` as the argument."""
    if "command" not in shape:
        return [shape]
    code_x, arg_x = str(shape["command"]), str(shape["arg"])
    out = []
    for code, var in command_variants(commands):
        name, ref = var.get("name"), var.get("ref")
        if name not in ROW_KEYS or ref == EXPR or {"nibble", "args", "inline"} & var.keys():
            continue
        if code in (shape.get("skip") or ()):
            continue
        when = [shape.get("when"), f"({code_x}) == {code}", var.get("when")]
        if ref is not None:
            none = types[ref].get("none")
            when.append(None if none is None else f"arg != {none}")
            key = {"ref": ref, "field": arg_x}
        elif "byte" in var:
            key = {"byte": _bind(var["byte"], "arg", arg_x)}
        else:
            key = {"value": _bind(var.get("arg", "arg"), "arg", arg_x)}
        written = _bind(var.get("raw", "arg"), "arg", name)
        raw = {
            f: _bind(_bind(e, "command", code), "arg", written)
            for f, e in (shape.get("raw") or {}).items()
            if not (ref is not None and f == arg_x)
        }
        out.append(
            {
                "when": " and ".join(f"({_bind(w, 'arg', arg_x)})" for w in when if w),
                "keys": {name: key},
                "raw": raw,
            }
        )
    return out


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
    """A unit's ``x``, ``label`` and ``program`` from its ``site``, its init's ``x`` from its
    record, its ``scope`` from its cell, and its chip map's ``value`` and ``store`` from
    ``width`` and ``offset``."""
    site, init = unit.get("site") or {}, unit.get("init")
    if init:
        init.setdefault("x", f"{init['record']}[{init['index']}].{init['field']}")
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
    return text if re.fullmatch(r"\w+", text) else f"({text})"


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
