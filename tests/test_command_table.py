"""The per-player command and `ctrl` row key tables in docs/format.md, generated from the
playroutines' commands and codecs."""

from importlib import resources
from pathlib import Path

import pytest

from unitracker import eventrestart, lifetime, songio
from unitracker.codec import NONE, COMMAND_NAMES, ROW_KEYS, CodecError, CommandCodec
from unitracker.codec import envelope_names
from unitracker.records import record_types

DOC = Path(__file__).resolve().parents[1] / "docs" / "format.md"
PLAYERS = sorted(
    p.name.removesuffix(".yaml")
    for p in resources.files("unitracker").joinpath("playroutines").iterdir()
    if p.name.endswith(".yaml")
)
HEADER = f"| command | {' | '.join(f'`{p}`' for p in PLAYERS)} |"
KEYS_HEADER = f"| `ctrl` row key | {' | '.join(f'`{p}`' for p in PLAYERS)} |"
ABSENT = "—"


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


def accepted(pr: dict) -> dict:
    """``{name: cell}``: what each shared command takes in playroutine ``pr``."""
    rtypes = record_types(pr)
    cmds = CommandCodec(pr.get("commands"), rtypes)
    values, refs = {}, {}
    for code, name, ref, _ in cmds.entries:
        if ref is not None and cmds.inline(code):
            fields = [f for f in rtypes[ref].fields if f != rtypes[ref].chain]
            refs.setdefault(name, []).append("/".join(["0-255"] * len(fields)))
            continue
        if ref is not None:
            refs.setdefault(name, []).append(f"`@{rtypes[ref].written}`")
            continue
        for arg in range(256):
            try:
                ename, value = cmds.encode(code, arg)
            except CodecError:
                continue
            if ename == name and cmds.decode(name, value):
                values.setdefault(name, set()).add(value)
    cells = {n: spans(v) for n, v in values.items()}
    for name in envelope_names(pr.get("commands")):
        cells[name] += ", at most one nibble from the instrument's"
    for name, names in refs.items():
        try:
            names += [f"`{NONE}`"] if cmds.decode(name, NONE) else []
        except CodecError:
            pass
        cells[name] = ", ".join(names)
    if pr.get(eventrestart.NAME):
        cells[eventrestart.NAME] = f"`@{eventrestart.NAME}`"
    cells.update({n: "0" for n in pr.get(lifetime.NAME) or {}})
    return cells


def table() -> list[str]:
    """The command table's lines, one row per shared command."""
    cells = {p: accepted(songio.library(p)) for p in PLAYERS}
    rows = [HEADER, f"|---|{'---|' * len(PLAYERS)}"]
    for name in (*COMMAND_NAMES, *lifetime.NAMES):
        row = [cells[p].get(name, ABSENT) for p in PLAYERS]
        rows.append(f"| `!{name}` | {' | '.join(row)} |")
    return rows


def key_forms(pr: dict, written: str = "ctrl") -> dict:
    """``{key: text}``: the forms each row key takes in the playroutine's ``written`` programs."""
    rtypes = record_types(pr)
    rtype = next(t for t in rtypes.values() if t.written == written)
    forms = {}
    for shape in rtype.codec:
        for key, spec in (shape.get("keys") or {}).items():
            spec = spec if isinstance(spec, dict) else {"value": spec}
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


def key_table() -> list[str]:
    """The `ctrl` row key table's lines, one row per key some player's `ctrl` program takes."""
    cells = {p: key_forms(songio.library(p)) for p in PLAYERS}
    rows = [KEYS_HEADER, f"|---|{'---|' * len(PLAYERS)}"]
    for key in (k for k in ROW_KEYS if any(k in c for c in cells.values())):
        rows.append(f"| `{key}` | {' | '.join(cells[p].get(key, ABSENT) for p in PLAYERS)} |")
    return rows


def documented(header: str = HEADER) -> list[str]:
    """The lines of the table under ``header`` as docs/format.md has them."""
    lines = DOC.read_text().splitlines()
    start = lines.index(header) if header in lines else len(lines)
    end = next((k for k in range(start, len(lines)) if not lines[k].startswith("|")), len(lines))
    return lines[start:end]


def test_the_documented_command_table_is_the_playroutines():
    expected = table()
    assert documented() == expected, "\n".join(expected)


def test_the_documented_ctrl_key_table_is_the_playroutines():
    expected = key_table()
    assert documented(KEYS_HEADER) == expected, "\n".join(expected)
    assert key_forms(songio.library("goattracker2"))["run"] == "`pw` name, `filter` name"


@pytest.mark.parametrize(
    "values,text",
    [
        ([0], "0"),
        (range(256), "0-255"),
        (range(0, 2041, 8), "0-2040 step 8"),
        ([1, 2, 3, 7], "1-3, 7"),
        ([1, 5, 6, 7], "1, 5-7"),
        ([2, 4, 9], "2, 4, 9"),
        ([2, 4, 6, 9], "2-6 step 2, 9"),
    ],
)
def test_spans(values, text):
    assert spans(values) == text


def _doc(player: str, atom: str) -> str:
    return (
        f"unitracker: {songio.VERSION}\nplayroutine: {{ref: {player}}}\nsong:\n  - def:\n"
        f"      phrase:\n        p1: ['{atom}']\n    play: [p1, p1, p1]\nloop: 0\n"
    )


@pytest.mark.parametrize(
    "player,command",
    [("goattracker2", "tempo:2"), ("sidwizard", "tempo:6"), ("defmon", "mark:1")],
)
def test_an_unsupported_timing_command_is_refused_naming_it_and_the_player(player, command):
    with pytest.raises(
        songio.SongFormatError, match=f"^{player}: the player can't express !{command}"
    ):
        songio.loads(_doc(player, f"-!{command}"))
