"""The three reference tunes, each lifted once per session (``references``): their documents
round-trip canonically, play their native players, use only the shared vocabulary, and lift,
check and optimise the same through the command-line tools."""

import re
from pathlib import Path

import numpy as np
import pytest
from pysidtracker.survey import load_render

from unitracker import cli
from unitracker.format import songio
from unitracker.format.codec import COMMAND_NAMES, ROW_KEYS
from unitracker.format.yamlstyle import parse
from unitracker.lift import backends, verify
from unitracker.player import oracle
from unitracker.player.vm import SongPlayer, first_mismatch

TUNES = ("metal_warrior", "crosseyed", "csillag")
BACKEND = {"metal_warrior": "goattracker2", "crosseyed": "defmon", "csillag": "sidwizard"}
COMMANDS = {
    "metal_warrior": {"run", "freq", "legato", "tempo"},
    "crosseyed": set(),
    "csillag": {"freq", "ad", "sr", "run", "legato"},
}
GENERIC = {
    "metal_warrior": (105, 0x0B),  # #186: GoatTracker 2 keys off on the tick it reads the step
    "crosseyed": (5, 0x07),  # #185: defMON detunes voice 2 by 1
    "csillag": (4, 0x0E),  # #185: SID-Wizard adds the carry once the wavetable program ends
}
ROOT = Path(__file__).resolve().parents[1]
ELIDED = "..."


def elided_in(example: str, text: str) -> bool:
    """Whether ``example`` is ``text`` with lines left out (a line ``...``) and line tails cut
    (a line ending in ``...]``): each other line is one of ``text``'s, in order."""
    lines, pos = text.splitlines(), 0
    for line in example.splitlines():
        if line.strip() == ELIDED:
            continue
        head = line[: line.index(ELIDED)] if line.endswith(f"{ELIDED}]") else None
        while pos < len(lines) and not (
            lines[pos] == line if head is None else lines[pos].startswith(head)
        ):
            pos += 1
        if pos == len(lines):
            return False
        pos += 1
    return True


def test_elided_in():
    text = "a: 1\nb: [1, 2, 3]\nc: 3\n"
    assert elided_in("a: 1\n  ...\nc: 3", text)
    assert elided_in("b: [1, ...]", text)
    assert not elided_in("c: 3\na: 1", text)
    assert not elided_in("b: [2, ...]", text)


@pytest.mark.parametrize("name", TUNES)
def test_the_document_round_trips_and_plays_its_native_player(references, name):
    ref = references[name]
    song = ref.ir()
    assert songio.dumps(song) == ref.text
    embedded = songio.dumps(song, embed=True)
    assert songio.dumps(songio.loads(embedded), embed=True) == embedded
    assert len(np.unique(ref.native, axis=0)) > 500
    assert SongPlayer(song).mismatch(ref.native) is None
    assert songio.dumps(songio.loads(embedded)) == ref.text


def test_the_format_example_is_the_metal_warrior_document(references):
    example = (ROOT / "docs" / "format.md").read_text().split("```yaml\n", 1)[1].split("```", 1)[0]
    assert elided_in(example, references["metal_warrior"].generic)


def test_flushes_rebuild_the_grid(references):
    song = references["metal_warrior"].ir()
    regs, grid = [0] * 25, SongPlayer(song).render_grid(500)
    for tick, flush in enumerate(SongPlayer(song).iter_flushes(500)):
        for reg, value in flush:
            regs[reg] = value
        assert regs == list(grid[tick])


def _keys(node) -> set:
    if isinstance(node, dict):
        return set(node).union(*map(_keys, node.values()))
    if isinstance(node, list):
        return set().union(*map(_keys, node))
    return set()


def _vocabulary(text: str) -> tuple[set, set]:
    """``(record keys, commands)`` a document writes."""
    keys, commands = set(), set()
    for item in parse(text)["song"]:
        for tname, defs in (item.get("def") or {}).items():
            for value in defs.values():
                if tname == "phrase":
                    commands |= {
                        re.split("[:*]", p)[0] for a in value for p in str(a).split("!")[1:]
                    }
                else:
                    keys |= _keys(value)
    return {k for k in keys if isinstance(k, str)} - {"loop"}, commands


def test_the_players_share_only_the_shared_vocabulary(references):
    used = {name: _vocabulary(references[name].text) for name in TUNES}
    for name, (_, commands) in used.items():
        assert commands == COMMANDS[name]
    for a in TUNES:
        for b in set(TUNES) - {a}:
            assert used[a][0] & used[b][0] <= set(ROW_KEYS) | {"restart"}
    assert not used["csillag"][0] & (set(COMMAND_NAMES) - set(ROW_KEYS))


@pytest.mark.parametrize("name", TUNES)
def test_the_generic_document_plays_unitracker_player_to_its_known_difference(references, name):
    ref = references[name]
    verdict = verify.compare(ref.generic, ref.native)
    assert verdict.onset == GENERIC[name] and not verdict.ok
    assert verdict.played == len(ref.native) and verdict.whole[0] <= verdict.onset[0]
    assert verdict.line().startswith(f"mismatch: tick {GENERIC[name][0]}, register $")


@pytest.mark.parametrize("name", TUNES)
def test_the_lift_tool_writes_the_generic_document_and_its_verdict(
    references, name, tmp_path, capsys
):
    ref = references[name]
    assert backends.detect(ref.raw) == BACKEND[name]
    out = tmp_path / "doc.yaml"
    argv = [str(ref.sid)] if name == "metal_warrior" else [str(ref.sid), "-o", str(out)]
    assert cli.lift_main(argv) == 1
    captured = capsys.readouterr()
    tick, reg = GENERIC[name]
    assert captured.err.splitlines()[-1].startswith(f"mismatch: tick {tick}, register ${reg:02X}")
    assert (captured.out if name == "metal_warrior" else out.read_text()) == ref.generic


def test_an_unknown_format_is_refused(references):
    raw = bytearray(references["metal_warrior"].raw)
    raw[0x7C:] = bytes(len(raw) - 0x7C)
    with pytest.raises(backends.UnknownFormat):
        backends.detect(bytes(raw))


def test_the_survey_renderer_plays_the_document_as_the_native_player(references):
    ref, tool = references["metal_warrior"], str(ROOT / "tools" / "oracle_hvsc.py")
    grid, info = load_render(tool)(ref.raw, 1, 300)
    assert grid.shape == (300, 25) and grid.dtype.name == "int16"
    assert info == {"backend": "goattracker2", "chars": len(ref.generic)}
    assert (grid == oracle.play(ref.generic, 300)[0]).all()
    grid, info = load_render(tool.replace(".py", ".py:native"))(ref.raw, 1, 100)
    assert info == {"backend": "goattracker2"} and (grid == ref.native[:100]).all()


@pytest.mark.parametrize("name", TUNES)
def test_the_optimised_tune_plays_its_native_player(references, optimised_tunes, name):
    result = optimised_tunes[name][1]
    assert result.after < result.before and any(f.kept for f in result.findings)
    player = SongPlayer(songio.loads(result.text))
    native = references[name].native
    assert first_mismatch(player.render_grid(len(native)), native, player.starts) is None
