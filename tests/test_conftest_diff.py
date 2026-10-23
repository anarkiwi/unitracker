"""The tests' failure reports: whydiff's explanation of the VM beside a native player or a raw
tune's 6502, the verdict's grid diff, and their xfail line."""

from functools import partial
from types import SimpleNamespace

import conftest
import numpy as np
import pytest
from pysidtracker import gridcmp, write_psid
from pysidtracker.asm6502 import assemble
from pysidtracker.oracle import register_grid
from pysidtracker.player import MemPlayer

from test_tempo import scale_song

from unitracker import document
from unitracker.lift import verify
from unitracker.player import oracle
from unitracker.player.vm import SongPlayer


def mismatched_verdict() -> gridcmp.Verdict:
    """A two-grid diff: theirs' voice 1 frequency lo byte set at tick 2, both grids four ticks."""
    ours = np.zeros((4, 25), dtype=np.uint8)
    theirs = np.zeros((4, 25), dtype=np.uint8)
    theirs[2, 0] = 7
    ver = gridcmp.verdict_onset(ours, [1], theirs)
    assert not ver.ok and ver.onset == (2, 0)
    return ver


def test_grid_diff_of_carries_the_verdicts_report(request):
    ver = mismatched_verdict()
    report = gridcmp.runs_report(ver, **verify.REPORT)
    assert conftest.grid_diff_of(request, ver) == report
    assert request.node.failure_report == report


def test_the_report_names_each_differing_register_its_runs_and_both_grids_values():
    ver = gridcmp.Verdict(
        n=12,
        unit="tick",
        played=12,
        starts=(1, 4, None),
        onset=(3, 5),
        whole=(2, 11),
        cells=(
            (2, 11, 0, 7),
            (3, 11, 5, 7),
            (3, 5, 1, 9),
            (7, 5, 8, 0),
            (7, 8, 0, 2),
            (8, 8, 4, 2),
        ),
    )
    assert gridcmp.runs_report(ver, **verify.REPORT) == (
        "mismatch: tick 3, register $05 v1 ad (from tick 0: tick 2, $0B v2 ctrl)\n"
        "  $05 v1 ad: 2 cells, ticks 3-7\n"
        "    3: doc 01, native 09\n"
        "    7: doc 08, native 00\n"
        "  $08 v2 freq hi: 2 cells, ticks 7-8\n"
        "    7: doc 00, native 02\n"
        "    8: doc 04, native 02\n"
        "  $0B v2 ctrl: 2 cells, ticks 2-3\n"
        "    2: doc 00, native 07\n"
        "    3: doc 05, native 07"
    )


def test_the_report_counts_the_value_lines_it_omits_and_names_a_verified_or_ended_document():
    cells = tuple((t, 0, 1 if t % 2 else 2, 0) for t in range(10))
    text = tick_report(n=10, played=10, starts=(0,), onset=(0, 0), whole=(0, 0), cells=cells)
    assert "  $00 v1 freq lo: 10 cells, ticks 0-9" in text
    assert "    0: doc 02, native 00" in text and "    ... 4 more value lines" in text
    verified = tick_report(n=8, ok=True, played=8, starts=(0, 0, 0))
    assert verified == "verified: 8 ticks play the native grid"
    ended = tick_report(n=8, played=4, starts=(0, 0, 0), whole=(2, 0))
    assert ended == "mismatch: the document ends at tick 4 of 8"


def tick_report(**fields) -> str:
    """``gridcmp.runs_report`` of a tick verdict with ``fields``."""
    return gridcmp.runs_report(gridcmp.Verdict(unit="tick", **fields), **verify.REPORT)


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
    return write_psid(load=0x1000, init=labels["init"], play=labels["play"], image=code)


def verdict(song: dict, theirs) -> tuple:
    """``(verdict, grids)`` of ``song`` on the VM against ``theirs``."""
    player = SongPlayer(song)
    grid = player.render_grid(len(theirs))
    return gridcmp.verdict_onset(grid, player.starts, theirs), (grid, theirs)


def test_explain_traces_the_native_player_on_the_tick_it_last_stored_the_register():
    song, native = counting(2), partial(Counter, b"", 0)
    ver, grids = verdict(song, np.asarray(native().render_grid(6), np.uint8))
    assert ver.onset == (2, 0x18)
    why = verify.explain(ver, song, grids, native, name="counter")
    left, right = why.left, why.right
    assert (left.traced, right.frame, right.traced) == (2, 2, 1)
    assert why.values == {"generic": 6, "native": 4}
    assert left.events[left.root].text == "out[24] = ((2 * g[0] + 2)) & 0xFF"
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
    assert ver.onset == (1, 0x18) and why.values == {"generic": 5, "6502": 4, "reference": 4}
    assert "jennings agrees with the reference to here" in why.notes
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


def test_the_hook_adds_the_diff_to_a_failure_and_leaves_a_pass_alone():
    item = SimpleNamespace(failure_report="DIFF")
    passed = SimpleNamespace(failed=False, sections=[])
    assert _run_hook(item, passed) is passed and passed.sections == []
    failed = SimpleNamespace(failed=True, sections=[])
    assert _run_hook(item, failed).sections == [("explain", "DIFF")]
    assert not hasattr(failed, "wasxfail")


def documents() -> tuple:
    """``(edited, source, song)``: a scale song, its document, and the document with ``!ad`` on
    its first phrase's second note."""
    song = scale_song("sidwizard")
    text = document.write(song)
    edited = text.replace("p2: [C4.i1!tempo:6, D4,", "p2: [C4.i1!tempo:6, D4!ad:90,", 1)
    assert edited != text
    return edited, text, song


def onset(subject, against, ticks: int = 40) -> tuple:
    """``(verdict, grids)`` of ``subject`` against ``against``, each played ``ticks`` ticks."""
    grids = tuple(conftest.render(w, ticks) for w in (subject, against))
    return gridcmp.verdict_onset(*oracle.play(subject, ticks), grids[1]), grids


def test_explain_slices_a_documents_setattr_store_to_its_command():
    edited, text, song = documents()
    for against, right in ((text, "reference"), (song, "generic")):
        ver, grids = onset(edited, against)
        assert ver.onset[1] == 5
        why = verify.explain(ver, edited, grids, against, name="ad")
        assert why.values == {"document": 90, right: 0}
        left = [why.left.events[i].text for i in why.left.keep]
        assert left[-1] == "regs[at + 5] = voice.ad"
        assert "setattr(voice, key, value & 0xFF)" in left
        assert any(t.startswith("for key, value in event.ops") for t in left)
        assert why.right.keep and why.text().startswith("divergence: ad, reference frame")


def test_explain_runs_a_document_against_a_raw_tune_on_jennings():
    raw = counter_tune()
    theirs = np.asarray(register_grid(raw, 4), np.uint8)
    text = documents()[1]
    ver = gridcmp.verdict_onset(oracle.play(text, 4)[0], [0, 0, 0], theirs)
    assert ver.onset == (0, 0x18)
    why = verify.explain(ver, text, (verify.player(text).render_grid(4), theirs), raw)
    assert why.values == {"document": 15, "6502": 2, "reference": 2}
    assert why.left.events[why.left.root].text == "regs[24] = self.mode | self.vol"
    assert "STA $d418" in why.right.lines[why.right.root]


def test_grid_diff_of_explains_a_document_and_a_failure_carries_it(request):
    edited, text, _ = documents()
    ver, _ = onset(edited, text)
    report = conftest.grid_diff_of(request, ver, edited, text)
    assert report.startswith(gridcmp.runs_report(ver, **verify.REPORT))
    assert "WHY: the steps the diverging value came from" in report
    assert "DOCUMENT" in report and "REFERENCE" in report
    assert request.node.failure_report == report
