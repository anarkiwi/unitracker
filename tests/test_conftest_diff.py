"""The tests' failure reports: whydiff's explanation of the VM beside a native player or a raw
tune's 6502, the porting tool's grid diff, and their xfail line."""

import struct
from types import SimpleNamespace

import conftest
import numpy as np
import pytest
from pysidtracker.asm6502 import assemble
from pysidtracker.oracle import register_grid
from pysidtracker.player import MemPlayer

from unitracker.lift import verify
from unitracker.player.vm import SongPlayer


def mismatched_verdict() -> verify.Verdict:
    """A two-grid diff: theirs' voice 1 frequency lo byte set at tick 2, both grids four ticks."""
    ours = np.zeros((4, 25), dtype=np.uint8)
    theirs = np.zeros((4, 25), dtype=np.uint8)
    theirs[2, 0] = 7
    ver = verify.compare_grids(ours, [1], theirs)
    assert not ver.ok and ver.onset == (2, 0)
    return ver


def test_grid_diff_of_carries_the_porting_tools_report(request):
    ver = mismatched_verdict()
    assert conftest.grid_diff_of(request, ver) == conftest.grid_diff.report(ver)
    assert request.node.failure_report == conftest.grid_diff.report(ver)


def counting(step: int) -> dict:
    """A song whose ghost writes ``step * n + 2`` to $D418, ``n`` counting its ticks."""
    playroutine = {
        "global": {"n": {}},
        "tick": [{"set": {"n": "n + 1"}}],
        "ghost": {"global": {0x18: f"{step} * n + 2"}},
    }
    return conftest.mini_song(playroutine)


class Counter(MemPlayer):
    """A native player writing twice its frame count to $D418 on two frames of every three."""

    n = 0

    def _init(self, subtune):
        """No init."""

    def _frame(self):
        self.n += 1
        if self.n % 3:
            self._wr(self.SID_BASE + 0x18, 2 * self.n)


PLAY = "init: LDA #$00\nSTA $fb\nRTS\nplay: INC $fb\nLDA $fb\nASL A\nSTA $d418\nRTS"


def counter_tune() -> bytes:
    """A PSID whose play call writes twice its call count to $D418."""
    code, labels = assemble(PLAY, 0x1000)
    fields = struct.pack(">HHHHHHHI", 2, 0x7C, 0, labels["init"], labels["play"], 1, 1, 0)
    return b"PSID" + fields + bytes(102) + struct.pack("<H", 0x1000) + code


def verdict(song: dict, theirs) -> tuple:
    """``(verdict, grids)`` of ``song`` on the VM against ``theirs``."""
    player = SongPlayer(song)
    grid = player.render_grid(len(theirs))
    return verify.compare_grids(grid, player.starts, theirs), (grid, theirs)


def test_explain_traces_the_native_player_on_the_tick_it_last_stored_the_register():
    song, native = counting(2), lambda: Counter(b"", 0)
    ver, grids = verdict(song, np.asarray(native().render_grid(6), np.uint8))
    assert ver.onset == (2, 0x18)
    why = verify.explain(ver, song, grids, native, name="counter")
    left, right = why.left, why.right
    assert (left.frame, right.frame) == (2, 1) and why.values == {"generic": 6, "native": 4}
    assert left.events[left.root].text == "out[24] = (((2 * g[0]) + 2)) & 0xFF"
    assert "g[0] = (g[0] + 1)" in {left.events[i].text for i in left.keep}
    assert right.events[right.root].text == "self._mem[addr & 0xFFFF] = val & 0xFF"
    assert {"self.n += 1", "if self.n % 3:"} <= {right.events[i].text for i in right.keep}
    assert why.text().startswith("divergence: counter, reference frame 2, register $18")


def test_explain_runs_a_raw_tune_on_jennings_and_is_none_without_an_onset():
    raw = counter_tune()
    theirs = np.asarray(register_grid(raw, 4), np.uint8)
    assert theirs.T[0x18].tolist() == [2, 4, 6, 8]
    ver, grids = verdict(counting(3), theirs)
    why = verify.explain(ver, counting(3), grids, raw)
    cpu = why.right
    assert ver.onset == (1, 0x18) and why.values == {"generic": 5, "6502": 4}
    assert "jennings agrees with the reference" in why.notes
    assert "STA $d418" in cpu.lines[cpu.root]
    assert [cpu.events[i].text.split()[0] for i in cpu.keep] == ["INC", "LDA", "ASL", "STA"]
    ok, grids = verdict(counting(2), theirs)
    assert ok.ok and verify.explain(ok, counting(2), grids, raw) is None


def test_assert_plays_passes_a_matching_song_and_fails_with_the_explanation(request):
    raw = counter_tune()
    theirs = np.asarray(register_grid(raw, 4), np.uint8)
    conftest.assert_plays(request, counting(2), theirs, raw)
    assert not hasattr(request.node, "failure_report")
    with pytest.raises(AssertionError) as exc:
        conftest.assert_plays(request, counting(3), theirs, raw)
    report = request.node.failure_report
    assert all(line.strip() in str(exc.value) for line in report.splitlines())
    assert "WHY: the steps the diverging value came from" in report


def _run_hook(item, inner) -> object:
    """Drive the hook the way pluggy drives a hookwrapper; its returned report."""
    hook = conftest.pytest_runtest_makereport(item, None)
    next(hook)
    try:
        hook.send(inner)
    except StopIteration as exc:
        return exc.value
    raise AssertionError("the hookwrapper ended without returning")


def test_the_hook_appends_the_diff_to_the_expected_failure_line():
    item = SimpleNamespace(failure_report="DIFF")
    inner = SimpleNamespace(wasxfail="the reason")
    assert _run_hook(item, inner) is inner
    assert inner.wasxfail == "the reason\n\nDIFF"


def test_the_hook_leaves_a_report_with_no_attached_diff_alone():
    item, inner = SimpleNamespace(), SimpleNamespace(wasxfail="the reason")
    assert _run_hook(item, inner) is inner and inner.wasxfail == "the reason"


def test_the_hook_leaves_a_plain_failure_alone():
    item, inner = SimpleNamespace(failure_report="DIFF"), SimpleNamespace()
    assert _run_hook(item, inner) is inner and not hasattr(inner, "wasxfail")
