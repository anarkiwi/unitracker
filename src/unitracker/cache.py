"""Content-keyed on-disk caching and the process pool shared by conversion and optimisation."""

import concurrent.futures
import fcntl
import hashlib
import multiprocessing
import os
import pickle
from pathlib import Path

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
    """A process pool over the host's cores with two BLAS threads per worker, or in-process."""

    def __init__(self, workers: int, items: int):
        self.workers = max(1, min(workers or default_workers(), items))
        self.pool = None
        if self.workers > 1:
            for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
                os.environ[var] = "2"
            ctx = multiprocessing.get_context("spawn")
            self.pool = concurrent.futures.ProcessPoolExecutor(self.workers, mp_context=ctx)

    def map(self, fn, tasks: list, desc: str) -> list:
        """``[fn(*task)]`` in order, with a progress bar over completed tasks."""
        progress = tqdm(total=len(tasks), desc=desc, unit="task", leave=False)
        if self.pool is None:
            out = []
            for t in tasks:
                out.append(fn(*t))
                progress.update()
            progress.close()
            return out
        futures = {self.pool.submit(fn, *t): i for i, t in enumerate(tasks)}
        out = [None] * len(tasks)
        for f in concurrent.futures.as_completed(futures):
            out[futures[f]] = f.result()
            progress.update()
        progress.close()
        return out

    def close(self) -> None:
        """Shut the pool down."""
        if self.pool is not None:
            self.pool.shutdown()
