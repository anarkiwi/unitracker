"""Content-keyed on-disk caching and the process pool shared by conversion and optimisation."""

import fcntl
import hashlib
import os
import pickle
from pathlib import Path

from pysidtracker import jobs
from tqdm import tqdm

CACHE = Path(os.environ.get("UNITRACKER_CACHE", "out/modfit/cache"))


def content_key(*parts) -> str:
    """The sha256 of ``parts`` (bytes, text or anything with a stable ``repr``)."""
    h = hashlib.sha256()
    for part in parts:
        h.update(part if isinstance(part, bytes) else str(part).encode())
        h.update(b"\0")
    return h.hexdigest()


def load(path: Path):
    """A pickled cache entry."""
    return pickle.loads(path.read_bytes())


def save(path: Path, obj) -> None:
    """Pickle ``obj`` to ``path``."""
    path.write_bytes(pickle.dumps(obj))


def cached(path: Path | None, compute, load_fn=load, save_fn=save):
    """``compute()``, read from and written to ``path`` when caching.

    One process computes an entry while others wait on its lock; the entry appears whole.
    """
    if path is None:
        return compute()
    if path.exists():
        return load_fn(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            return load_fn(path)
        out = compute()
        tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp{path.suffix}")
        save_fn(tmp, out)
        tmp.replace(path)
    return out


def default_workers() -> int:
    """``UNITRACKER_WORKERS`` from the environment, else the host's cores."""
    return int(os.environ.get("UNITRACKER_WORKERS", 0)) or len(os.sched_getaffinity(0))


class Pool:
    """A pool of ``workers`` processes (:func:`pysidtracker.jobs.imap`) with two BLAS threads
    each, or in-process for one worker unless ``isolated``.

    A job's :func:`pysidtracker.jobs.deadline` is enforced by killing its worker's process
    group, nested pools included.
    """

    def __init__(self, workers: int, items: int, isolated: bool = False):
        self.workers = max(1, min(workers or default_workers(), items))
        self.isolated = isolated or self.workers > 1
        if self.isolated:
            for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
                os.environ[var] = "2"

    def map(self, fn, tasks: list, desc: str, failed=None) -> list:
        """``[fn(*task)]`` in order, with a progress bar over completed tasks; ``failed(task,
        error)`` stands for a task that raised, ran past its deadline or lost its worker
        (without it the error is raised)."""
        progress = tqdm(total=len(tasks), desc=desc, unit="task", leave=False)
        out = [None] * len(tasks)
        if not self.isolated:
            for i, t in enumerate(tasks):
                out[i] = fn(*t)
                progress.update()
            progress.close()
            return out
        try:
            for i, value, error in jobs.imap(fn, tasks, self.workers):
                if error is not None and failed is None:
                    raise error
                out[i] = value if error is None else failed(tasks[i], error)
                progress.update()
        finally:
            progress.close()
        return out

    def close(self) -> None:
        """Nothing to shut down: :meth:`map` reaps its workers."""
