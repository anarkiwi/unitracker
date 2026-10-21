"""The conftest's grid diff: the porting tool's report on a test's failure and its xfail line."""

from types import SimpleNamespace

import conftest
import numpy as np

from unitracker.lift import verify


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
    assert conftest.grid_diff.report(ver).splitlines()[0] in request.node.grid_diff


def test_assert_plays_passes_a_matching_verdict_and_fails_with_the_diff(request):
    zero = np.zeros((2, 25), dtype=np.uint8)
    conftest.assert_plays(request, verify.compare_grids(zero, [0], zero))
    assert not hasattr(request.node, "grid_diff")
    diff = conftest.grid_diff.report(mismatched_verdict())
    try:
        conftest.assert_plays(request, mismatched_verdict())
    except AssertionError as exc:
        assert all(line.strip() in str(exc) for line in diff.splitlines())
    else:
        raise AssertionError("assert_plays passed a mismatched verdict") from None


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
    item = SimpleNamespace(grid_diff="DIFF")
    inner = SimpleNamespace(wasxfail="the reason")
    assert _run_hook(item, inner) is inner
    assert inner.wasxfail == "the reason\n\nDIFF"


def test_the_hook_leaves_a_report_with_no_attached_diff_alone():
    item, inner = SimpleNamespace(), SimpleNamespace(wasxfail="the reason")
    assert _run_hook(item, inner) is inner and inner.wasxfail == "the reason"


def test_the_hook_leaves_a_plain_failure_alone():
    item, inner = SimpleNamespace(grid_diff="DIFF"), SimpleNamespace()
    assert _run_hook(item, inner) is inner and not hasattr(inner, "wasxfail")
