"""Every write to a modulator unit's cell in a playroutine, and which starts write it.

    python tools/cell_writers.py goattracker2 [sidwizard defmon ...]

For each unit: its cell (a pair's parts), every ``set`` or ``accumulate`` of it with where it sits
(proc, record row or template) and its expression, the cell's init value, and each start hook
with whether the function it names writes the cell. A start whose function does not write the
cell opens its occurrence at the value the last writer left.
"""

import sys

from unitracker.playroutine.library import library

WRITERS = ("set", "accumulate")


def walk(actions, where, out):
    """Collect ``(where, kind, cell, expr)`` for every write in an action list."""
    for action in actions or ():
        if not isinstance(action, dict):
            continue
        for cell, expr in (action.get("set") or {}).items():
            out.append((where, "set", cell, expr))
        if "accumulate" in action:
            out.append((where, "accumulate", action["accumulate"], action.get("step")))
        for cell, expr in (action.get("with") or {}).items():
            out.append((where, "with", cell, expr))
        for key in ("then", "else"):
            walk(action.get(key), where, out)
        walk(action.get("for_voices"), where, out)


def writes(pr: dict) -> list:
    """Every write in the playroutine's procs, records, record-type templates, init and tick."""
    out = []
    for name, actions in (pr.get("procs") or {}).items():
        walk(actions, f"proc {name}", out)
    for rname, rows in (pr.get("records") or {}).items():
        for index, row in rows.items():
            for field, actions in row.items():
                walk(actions, f"record {rname}[{index}].{field}", out)
    for tname, spec in (pr.get("record_types") or {}).items():
        for key, value in spec.items():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                walk(value, f"type {tname}.{key}", out)
    walk(pr.get("init"), "init", out)
    walk(pr.get("tick"), "tick", out)
    return out


def parts(cell: str) -> set:
    """The cell names of a cell or ``hi:lo[:k]`` pair."""
    return set(cell.split(":")[:2])


def hook_function(hook: dict) -> str:
    """The name of a hook's function as :func:`writes` labels it."""
    at = hook.get("at") or {}
    if "proc" in at:
        return f"proc {at['proc']}"
    if "record" in at:
        return f"record {at['record']}"
    if "type" in at:
        return f"type {at['type']}.{at.get('template')}"
    return str(at)


def report(name: str) -> str:
    """The report for one library playroutine."""
    pr = library(name)
    all_writes = writes(pr)
    cells = {**(pr.get("global") or {}), **(pr.get("voice") or {})}
    lines = [f"# {name}"]
    for unit in pr["modulators"]["units"]:
        own = parts(unit["cell"])
        lines.append(f"\n## unit {unit['name']}: key {unit['key']}, cell {unit['cell']}")
        for c in sorted(own):
            lines.append(f"  init {c} = {(cells.get(c) or {}).get('init', 0)}")
        for where, kind, cell, expr in all_writes:
            if cell in own:
                lines.append(f"  {kind:10s} {cell:8s} {where:36s} {expr}")
        for hook in unit.get("hooks") or [{"at": unit.get("site", {})}]:
            fn = hook_function(hook)
            hit = any(cell in own and where.startswith(fn) for where, _, cell, _ in all_writes)
            lines.append(f"  start {fn}: {'writes the cell' if hit else 'inherits the cell'}")
    return "\n".join(lines)


def main(argv=None) -> int:
    """Entry point."""
    for name in argv or sys.argv[1:]:
        print(report(name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
