"""The library playroutines as their docs describe them: the command and `ctrl` row-key tables of
docs/playroutines/<name>.md, and every key a playroutine uses in docs/playroutines/schema.md."""

import contextlib
import itertools
import math
import re
from importlib import resources
from pathlib import Path

import pytest

from unitracker.playroutine.codec import (
    ARGLESS,
    COMMAND_NAMES,
    NONE,
    ROW_KEYS,
    CodecError,
    RowCodec,
    CommandCodec,
    envelope_names,
)
from unitracker.playroutine.library import library
from unitracker.playroutine.records import record_types
from unitracker.playroutine import tempo
from unitracker.document.events import lifetime, restart, running

PLAYERS = sorted(
    p.name.removesuffix(".yaml")
    for p in resources.files("unitracker").joinpath("playroutines").iterdir()
    if p.name.endswith(".yaml")
)
LIBS = {p: library(p) for p in PLAYERS}
TYPES = {p: record_types(pr) for p, pr in LIBS.items()}
COMMANDS = {p: CommandCodec(pr.get("commands"), TYPES[p]) for p, pr in LIBS.items()}
DOCS = Path(__file__).resolve().parents[1] / "docs" / "playroutines"
HEADER = "| command | argument |"
KEYS_HEADER = "| `ctrl` row key | forms |"


def spans(values) -> str:
    """Sorted integers as runs of one step: ``a``, ``a-b`` or ``a-b step s``."""
    values, out, k = sorted(values), [], 0
    while k < len(values):
        j, step = k, values[k + 1] - values[k] if k + 1 < len(values) else 1
        while j + 1 < len(values) and values[j + 1] - values[j] == step:
            j += 1
        if j - k < (1 if step == 1 else 2):
            out.append(f"{values[k]}")
            k += 1
            continue
        out.append(f"{values[k]}-{values[j]}" + ("" if step == 1 else f" step {step}"))
        k = j + 1
    return ", ".join(out)


def cell(values: set) -> str:
    """The written values a command takes: single values' runs, then for an argument of several
    values each position's runs joined by ``/``, which hold only when every combination is
    taken."""
    ints = {v for v in values if not isinstance(v, tuple)}
    tuples = values - ints
    out = [spans(ints)] if ints else []
    if tuples:
        columns = [{v[k] for v in tuples} for k in range(len(next(iter(tuples))))]
        equal = len(set.intersection(*columns)) if not any(len(set(v)) == 1 for v in tuples) else 0
        assert len(tuples) + equal == math.prod(map(len, columns)), sorted(tuples)
        out.append("/".join(spans(c) for c in columns) + (" (two different)" if equal else ""))
    return ", ".join(out)


def readable(cmds, code: int, name: str, var: dict):
    """The written values of command ``name`` that variant ``var`` of ``code`` reads: each tuple of
    bytes for one with ``args``, else what encoding each byte argument gives."""
    if "args" in var:
        tried = itertools.product(range(256), repeat=len(var["args"]))
    else:
        tried = []
        for arg in range(256):
            with contextlib.suppress(CodecError):
                ename, value = cmds.encode(code, arg)
                tried += [value] if ename == name else []
    for value in tried:
        with contextlib.suppress(CodecError):
            cmds.decode(name, value)
            yield value


def taken(cmds, rtypes) -> tuple[dict, dict]:
    """``({name: values}, {name: [reference cells]})`` of a playroutine's commands."""
    values, refs = {}, {}
    for code, name, ref, var in cmds.entries:
        if ref is not None and cmds.inline(code):
            own = rtypes[ref].own
            for row in itertools.product(range(256), repeat=len(own)):
                with contextlib.suppress(CodecError):
                    cmds.check_inline(name, dict(zip(own, row)))
                    values.setdefault(name, set()).add(row)
            continue
        if ref is not None:
            refs.setdefault(name, []).append(f"`{rtypes[ref].written}` name")
            continue
        values.setdefault(name, set()).update(readable(cmds, code, name, var))
    for name, vals in values.items():
        values[name] = {v if tempo.one(name, v) is None else tempo.one(name, v) for v in vals}
    return values, refs


def accepted(player: str) -> dict:
    """``{name: cell}``: what each shared command takes in the playroutine."""
    pr, cmds = LIBS[player], COMMANDS[player]
    values, refs = taken(cmds, TYPES[player])
    cells = {n: "no argument" if n in ARGLESS else cell(v) for n, v in values.items()}
    for name in envelope_names(pr.get("commands")):
        cells[name] += ", at most one nibble from the instrument's"
    for name, names in refs.items():
        with contextlib.suppress(CodecError):
            stops = name != running.NAME or running.lasts_step(pr)
            names += [f"`{NONE}`"] if cmds.decode(name, NONE) and stops else []
        cells[name] = ", ".join(names)
    for slot in ("pw", "filter"):
        stops = []
        with contextlib.suppress(CodecError):
            stops = [f"`{NONE}`"] if cmds.decode(slot, NONE) else []
        lives = ["no argument"] if slot in (pr.get(lifetime.NAME) or ()) else []
        if stops or lives:
            cells[slot] = ", ".join(lives + stops)
    if pr.get(restart.NAME):
        cells[restart.NAME] = f"`{restart.NAME}` name"
    return cells


def key_forms(player: str) -> dict:
    """``{key: text}``: the forms each row key takes in the playroutine's ``ctrl`` programs."""
    rtypes = TYPES[player]
    rtype = next(t for t in rtypes.values() if t.written == "ctrl")
    forms = {}
    for shape in RowCodec(rtype, rtype.codec, 0).shapes:
        for key, spec in shape.keys.items():
            found = forms.setdefault(key, {})
            found.update(dict.fromkeys(["value"] if "value" in spec else []))
            found.update(dict.fromkeys(["`^n`"] if "eor" in spec else []))
            found.update(dict.fromkeys(["`+ m`, `-`"] if "rel" in spec else []))
            found.update(dict.fromkeys(["expression"] if "expr" in spec else []))
            found.update(dict.fromkeys([f"`{NONE}`"] if spec.get("none") else []))
            found.update(
                dict.fromkeys([f"`{rtypes[spec['ref']].written}` name"] if "ref" in spec else [])
            )
    return {k: ", ".join(v) for k, v in forms.items()}


def documented(player: str, header: str) -> list[str]:
    """The lines of the table under ``header`` as docs/playroutines/<player>.md has them."""
    lines = (DOCS / f"{player}.md").read_text().splitlines()
    start = lines.index(header) if header in lines else len(lines)
    end = next((k for k in range(start, len(lines)) if not lines[k].startswith("|")), len(lines))
    return lines[start:end]


def tables(player: str) -> dict:
    """``{header: lines}``: the command and ``ctrl`` row-key tables of a playroutine's doc, a
    table with no rows left out."""
    cells, keys = accepted(player), key_forms(player)
    commands = [f"| `!{n}` | {cells[n]} |" for n in COMMAND_NAMES if n in cells]
    row_keys = [f"| `{k}` | {keys[k]} |" for k in ROW_KEYS if k in keys]
    return {
        h: [h, "|---|---|", *rows] if rows else []
        for h, rows in ((HEADER, commands), (KEYS_HEADER, row_keys))
    }


@pytest.mark.parametrize("player", PLAYERS)
def test_the_documented_tables_are_the_playroutines(player):
    for header, lines in tables(player).items():
        assert documented(player, header) == lines, "\n".join(lines)


def test_spans():
    cases = [
        ([0], "0"),
        (range(256), "0-255"),
        (range(0, 2041, 8), "0-2040 step 8"),
        ([1, 2, 3, 7], "1-3, 7"),
        ([1, 5, 6, 7], "1, 5-7"),
        ([2, 4, 9], "2, 4, 9"),
        ([2, 4, 6, 9], "2-6 step 2, 9"),
    ]
    assert [spans(values) for values, _ in cases] == [text for _, text in cases]


SCHEMA = (DOCS / "schema.md").read_text()


def schema_section(heading: str) -> str:
    """The text of docs/playroutines/schema.md under ``## heading``, up to the next one."""
    return SCHEMA.split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]


def table_rows(text: str) -> dict[str, str]:
    """Each backticked name of a table's first column, mapped to its row's last cell."""
    rows = (line.split("|")[1:-1] for line in text.splitlines() if line.startswith("| `"))
    return {n: cells[-1] for cells in rows for n in re.findall(r"`(\w+)", cells[0])}


def code_words(text: str) -> set[str]:
    """The identifiers in the backticked spans and fenced blocks of ``text``."""
    code = re.findall(r"```.*?```|`[^`]+`", text, flags=re.S)
    return {w for s in code for w in re.findall(r"[A-Za-z_]\w*", s)}


def brace_keys(text: str) -> set[str]:
    """The names a ``{a, b: ...}`` form in ``text`` lists."""
    return set(re.findall(r"[{,]\s*(\w+)", text))


def schema_keys() -> dict[str, set[str]]:
    """Per schema level, the keys docs/playroutines/schema.md documents."""
    fence = re.search(r"```yaml\n(.*?)```", SCHEMA, flags=re.S).group(1)
    units = table_rows(schema_section("Modulator declarations").split("| unit key |", 1)[1])
    out = {
        "top": set(re.findall(r"(?m)^(\w+):", fence)),
        "block": code_words(f"`{fence}`"),
        "record type": set(table_rows(schema_section("Record types"))),
        "codec": code_words(schema_section("Codecs")),
        "action": set(table_rows(schema_section("Actions"))),
        "modulators": set(table_rows(schema_section("Modulator declarations"))),
        "unit": set(units),
    }
    out.update({f"unit.{k}": brace_keys(units[k]) for k in ("site", "retire", "hooks", "program")})
    out["unit.chip"] = brace_keys(units["chip"])
    return out


def used_keys(pr: dict):
    """(level, key) for each key a playroutine definition uses, at the levels the schema lists."""
    yield from (("top", k) for k in pr)
    nested = [pr.get("instrument") or {}, (pr.get("instrument") or {}).get("event") or {}]
    nested += [pr.get("restart") or {}, pr.get("defaults") or {}]
    life = pr.get("lifetime") or {}
    nested += [*(life.values() if isinstance(life, dict) else []), *pr["voice"].values()]
    nested += [*pr["global"].values()]
    yield from (("block", k) for d in nested if isinstance(d, dict) for k in d)
    for spec in pr["record_types"].values():
        for k, v in spec.items():
            if k != "codec" and isinstance(v, list) and all(isinstance(a, dict) for a in v):
                yield from (("action", [*a][0]) for a in v)
            else:
                yield "record type", k
        yield from (("codec", k) for shape in spec.get("codec") or [] for k in shape)
    for entry in pr["commands"].values():
        yield from (
            ("codec", k) for e in (entry if isinstance(entry, list) else [entry]) for k in e
        )
    yield from (("modulators", k) for k in pr["modulators"])
    for unit in pr["modulators"]["units"]:
        yield from (("unit", k) for k in unit)
        for k in ("site", "retire", "hooks", "program", "chip"):
            for d in unit.get(k) if isinstance(unit.get(k), list) else [unit.get(k) or {}]:
                yield from ((f"unit.{k}", f) for f in d)


@pytest.mark.parametrize("player", PLAYERS)
def test_every_key_a_library_playroutine_uses_is_documented(player):
    known = schema_keys()
    assert {(lv, k) for lv, k in used_keys(LIBS[player]) if k not in known[lv]} == set()


def test_an_undocumented_playroutine_key_is_found():
    pr = {"started": 1, "voice": {}, "global": {}}
    pr["record_types"] = {"t": {"speed": 1, "do": [{"set": {}}, {"jump": 1}]}}
    pr.update(commands={1: {"name": "x", "scale": 2}}, modulators={"units": [{"scale": 1}]})
    known = schema_keys()
    found = {(lv, k) for lv, k in used_keys(pr) if k not in known[lv]}
    assert found == {
        ("top", "started"),
        ("record type", "speed"),
        ("action", "jump"),
        ("codec", "scale"),
        ("unit", "scale"),
    }
