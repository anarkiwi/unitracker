"""The three reference tunes, each lifted once per session (``references``): their songs replay
their native players, their generic documents use only the shared vocabulary, and they lift,
check and optimise the same through the command-line tools."""

import re
from pathlib import Path

import numpy as np
import pytest
import yaml
from conftest import assert_plays
from pysidtracker.survey import load_render

from unitracker import cli, document
from unitracker.lift import backends, verify
from unitracker.player import oracle
from unitracker.playroutine.codec import COMMAND_NAMES, RESTART, ROW_KEYS
from unitracker.player.vm import SongPlayer

TUNES = ("metal_warrior", "crosseyed", "csillag")
BACKEND = {"metal_warrior": "goattracker2", "crosseyed": "defmon", "csillag": "sidwizard"}
COMMANDS = {
    "metal_warrior": {"run", "freq", "legato", "tempo", "vol", "pw"},
    "crosseyed": {"cut", "pw", "vol"},
    "csillag": {"freq", "ad", "sr", "run", "legato", "tempo", "vol", "pw"},
}
KINDS = {"ins", "ctrl", "pw", "filter", "restart", "x", "phrase"}
GENERIC = {
    "metal_warrior": (1550, 0x0E),  # voice 3's frequency word
    "crosseyed": (5, 0x15),  # the filter's first cutoff step (its detune plays as set {detune: 1})
    "csillag": (26, 0x07),  # voice 2's hard restart, where the native sets its next note's FREQ-HI
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
def test_the_lift_replays_its_native_player(references, name, request):
    ref = references[name]
    assert len(np.unique(ref.native, axis=0)) > 500
    assert_plays(request, ref.ir(), ref.native, lambda: backends.native_player(ref.raw))


def test_the_format_example_is_the_metal_warrior_document(references):
    example = (
        (ROOT / "docs" / "format" / "document.md")
        .read_text()
        .split("```yaml\n", 1)[1]
        .split("```", 1)[0]
    )
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


_BAG = re.compile(r"mod: (\S+) \{([^{}]*)\}")  # the modulator's bag, as one YAML mapping
SHAPES = ("set", "ramp", "slew_to_target", "slew_to_zero", "triangle")
BAG_KEYS = {
    "offset",
    "detune",
    "step",
    "delay",
    "skip",
    "ticks",
    "max",
    "target",
    "amp",
    "start",
    "extent",
}


def _vocabulary(text: str) -> tuple[set, set, set]:
    """``(def kinds, record keys, commands)`` a generic document writes, the commands of its
    init row and phrases: a modulator's bag read as its shape's and parameters' keys."""
    doc = yaml.safe_load(_BAG.sub(r"mod: {\1: {\2}}", text))
    kinds, keys, atoms = set(), set(), list(doc.get("init") or [])
    for item in doc["song"]:
        for kind, defs in (item.get("def") or {}).items():
            kinds.add(kind)
            for value in defs.values():
                if kind == "phrase":
                    atoms += value
                else:
                    keys |= _keys(value)
    commands = {re.split("[:*]", p)[0] for a in atoms for p in str(a).split("!")[1:]}
    return kinds, {k for k in keys if isinstance(k, str)} - {"loop"}, commands


def test_the_players_share_only_the_shared_vocabulary(references):
    for name in TUNES:
        kinds, keys, commands = _vocabulary(references[name].generic)
        allowed = set(ROW_KEYS) | {RESTART, "mod"} | set(SHAPES) | BAG_KEYS
        assert kinds <= KINDS and keys <= allowed
        assert commands <= set(COMMAND_NAMES)
        assert commands == COMMANDS[name]


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
    ref, tool = references["metal_warrior"], str(ROOT / "tools" / "corpus.py")
    grid, info = load_render(tool)(ref.raw, 1, 300)
    assert grid.shape == (300, 25) and grid.dtype.name == "int16"
    assert info == {"backend": "goattracker2", "chars": len(ref.generic)}
    assert (grid == oracle.play(ref.generic, 300)[0]).all()
    grid, info = load_render(tool.replace(".py", ".py:native"))(ref.raw, 1, 100)
    assert info == {"backend": "goattracker2"} and (grid == ref.native[:100]).all()


@pytest.mark.parametrize("name", TUNES)
def test_the_optimised_tune_plays_its_native_player(references, optimised_tunes, name, request):
    result = optimised_tunes[name][1]
    assert result.after < result.before and any(f.kept for f in result.findings)
    assert result.text == document.write(result.ir)
    ref = references[name]
    assert_plays(request, result.ir, ref.native, lambda: backends.native_player(ref.raw))
