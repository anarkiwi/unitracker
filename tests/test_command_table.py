"""The per-player command table in docs/format.md, generated from the playroutines' commands."""

from importlib import resources
from pathlib import Path

import pytest

from unitracker import eventrestart, lifetime, songio
from unitracker.codec import NONE, COMMAND_NAMES, CodecError, CommandCodec, envelope_names
from unitracker.records import record_types

DOC = Path(__file__).resolve().parents[1] / "docs" / "format.md"
PLAYERS = sorted(
    p.name.removesuffix(".yaml")
    for p in resources.files("unitracker").joinpath("playroutines").iterdir()
    if p.name.endswith(".yaml")
)
HEADER = f"| command | {' | '.join(f'`{p}`' for p in PLAYERS)} |"
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
    cmds = CommandCodec(pr.get("commands"), record_types(pr))
    values, refs = {}, {}
    for code, name, ref, _ in cmds.entries:
        if ref is not None:
            refs.setdefault(name, []).append(f"`@{ref}`")
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


def documented() -> list[str]:
    """The command table's lines as docs/format.md has them."""
    lines = DOC.read_text().splitlines()
    start = lines.index(HEADER) if HEADER in lines else len(lines)
    end = next((k for k in range(start, len(lines)) if not lines[k].startswith("|")), len(lines))
    return lines[start:end]


def test_the_documented_command_table_is_the_playroutines():
    expected = table()
    assert documented() == expected, "\n".join(expected)


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
