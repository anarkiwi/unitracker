"""The process pool: a job past its deadline is killed with every process it started, while
blocked in C or waiting on a nested pool, and stands as its failed row."""

import hashlib
import os
import time
from pathlib import Path

import pytest
from pysidtracker.jobs import deadline

from unitracker.cache import Pool

FOREVER = (1 << 31) - 1


def spin(seconds: float) -> bytes:
    """Hash in C for ``FOREVER`` rounds under a deadline of ``seconds``."""
    with deadline(seconds):
        return hashlib.pbkdf2_hmac("sha256", b"x", b"y", FOREVER)


def pid_then_spin(where: str) -> bytes:
    """Write this process's pid to ``where``, then hash in C without a deadline."""
    Path(where).write_text(str(os.getpid()), encoding="utf-8")
    return hashlib.pbkdf2_hmac("sha256", b"x", b"y", FOREVER)


def nested(where: str, seconds: float) -> list:
    """Wait under a deadline of ``seconds`` on a nested pool whose job spins in C."""
    with deadline(seconds):
        return Pool(1, 1, isolated=True).map(pid_then_spin, [(where,)], "nested")


def alive(pid: int) -> bool:
    """Whether ``pid`` runs (a zombie does not)."""
    try:
        return Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[2] != "Z"
    except FileNotFoundError:
        return False


def test_a_job_blocked_in_c_or_on_a_nested_pool_is_killed_at_its_deadline(tmp_path):
    where = tmp_path / "pid"
    start = time.monotonic()
    got = Pool(2, 3).map(
        nested,
        [(str(where), 1), (str(tmp_path / "other"), 1)],
        "jobs",
        lambda task, error: type(error).__name__,
    )
    assert got == ["TimeoutError", "TimeoutError"] and time.monotonic() - start < 120
    pid = int(where.read_text(encoding="utf-8"))
    for _ in range(100):
        if not alive(pid):
            break
        time.sleep(0.1)
    assert not alive(pid)
    assert Pool(1, 1, isolated=True).map(spin, [(1,)], "spin", lambda t, e: "late") == ["late"]
    with pytest.raises(TimeoutError):
        Pool(2, 2).map(spin, [(1,), (1,)], "spin")
    assert Pool(1, 2).map(divmod, [(7, 2), (9, 4)], "in-process") == [(3, 1), (2, 1)]
