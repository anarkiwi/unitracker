"""The driver: screens each pass's candidates in parallel and keeps proved, smaller runs.

A candidate is proved when the lifter's VM replays it as the reference and its generic document
plays on unitracker-player as the input's does."""

import functools
import multiprocessing
import os
from dataclasses import dataclass, field
from pathlib import Path

from tqdm import tqdm

import numpy as np
from unitracker_player import PlayError

from unitracker import document
from unitracker.playroutine.library import SongFormatError
from unitracker.cache import CACHE, cached, content_key, default_workers
from unitracker.modfit.convert import ir_key
from unitracker.modfit.grammar import tokens
from unitracker.optimise.edits import EDITS
from unitracker.optimise.model import ERRORS
from unitracker.optimise.passes import CANONICAL, LARGER_UNPROVED, PASSES
from unitracker.optimise.proof import Trace, mismatch
from unitracker.player import oracle, reach
from unitracker.player.checkpoint import Baseline
from unitracker.player.vm import SongPlayer


@dataclass
class Finding:
    """A proved edit: the pass, the edit, the token delta, and whether the driver kept it."""

    pass_name: str
    edit: tuple
    delta: int
    kept: bool = False


@dataclass
class Result:
    """The input song, the optimised song and its generic document, the token counts and every
    proved edit."""

    source: dict
    ir: dict
    text: str
    before: int
    after: int
    findings: list = field(default_factory=list)
    tally: dict = field(default_factory=dict)


def _apply(ir: dict, edit: tuple) -> dict | None:
    op, args = edit
    try:
        return EDITS[op](ir, *args)
    except ERRORS:
        return None


def _written(ir: dict | None, native: document.Native):
    """``(generic text, tokens, ir)`` of a candidate, or None when it can't be written: its
    expressions timed by its own firings over ``native``'s ticks."""
    if ir is None:
        return None
    try:
        text = document.write(ir, document.native_over(ir, native.ticks))
    except (*ERRORS, PlayError):
        return None
    return text, tokens(text), ir


def _plays(grid: np.ndarray, text: str) -> bool:
    """Whether generic document ``text`` plays ``grid`` on unitracker-player."""
    try:
        ours = oracle.play(text, len(grid))[0]
    except PlayError:
        return False
    return ours.shape == grid.shape and bool((ours == grid).all())


def _proved(state: dict, written) -> bool:
    """Whether a written candidate replays the reference and its document plays as the input's."""
    return mismatch(state["ref"], written[2]) is None and _plays(state["grid"], written[0])


_STATE = {}


def _init(state: dict) -> None:
    _STATE.update(state)


def _screen(job):
    """``(index, delta or None, proved)`` of one candidate edit of ``(base, text, tokens)``."""
    k, edit, (base, text, size) = job
    cand = _apply(base, edit)
    if cand is None or mismatch(_STATE["ref"], cand) is not None:
        return k, None, False
    written = _written(cand, _STATE["native"])
    if written is None or written[0] == text:
        return k, None, False
    delta = written[1] - size
    if delta >= 0 and edit[0] in LARGER_UNPROVED:
        return k, delta, False
    return k, delta, _plays(_STATE["grid"], written[0])


def _count(job):
    """``(index, token delta)`` of one candidate edit unproved: ``"refused"`` where the edit or
    its generic document refuses it, ``"same"`` where the text is unchanged."""
    k, edit, (base, text, size) = job
    written = _written(_apply(base, edit), _STATE["native"])
    if written is None:
        return k, "refused"
    return k, "same" if written[0] == text else written[1] - size


class _Screener:
    """Screens candidates in-process or over one pool of ``workers`` processes, started on first
    use and holding the reference for the optimiser's lifetime."""

    def __init__(self, workers: int, state: dict):
        self.workers, self.state, self.pool = workers, state, None

    def __call__(self, jobs: list, desc: str, fn=_screen) -> list:
        """Every job's ``fn`` result (:func:`_screen` by default), in job order."""
        progress = tqdm(total=len(jobs), desc=desc, unit="edit", leave=False)
        out = []
        if self.workers <= 1 or len(jobs) < 2:
            _init(self.state)
            for job in jobs:
                out.append(fn(job))
                progress.update()
        else:
            if self.pool is None:
                for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
                    os.environ[var] = "2"
                ctx = multiprocessing.get_context("spawn")
                self.pool = ctx.Pool(self.workers, initializer=_init, initargs=(self.state,))
            for got in self.pool.imap_unordered(fn, jobs, chunksize=1):
                out.append(got)
                progress.update()
        progress.close()
        return sorted(out)

    def close(self) -> None:
        """Stop the pool."""
        if self.pool is not None:
            self.pool.close()
            self.pool.join()
            self.pool = None


class Optimiser:
    """Runs the passes over a song to a fixpoint; every kept edit is proved on its replay and
    its generic document."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, ir: dict, passes=None, workers: int | None = None):
        self.passes = list(passes or PASSES)
        self.workers = workers if workers is not None else default_workers()
        player = SongPlayer(ir, record_reads=True)
        log = document.fired_log(player)
        baseline = Baseline(player)
        tick, period = baseline.found
        document.play_to(player, tick + period)
        self.ref = Trace.replayed(baseline.rows, player.starts, tick - period, period, baseline)
        self.source = self.base = ir
        native = document.native_state_from(
            player, ir, tick + period, baseline.rows[0] if baseline.rows else None, log
        )
        self.text = document.write(ir, native)
        self.tokens = tokens(self.text)
        state = {"ref": self.ref, "native": native, "grid": oracle.play(self.text, native.ticks)[0]}
        self.state = state
        self.findings, self.tally = {}, {}
        self.settled = set()
        self.screener = _Screener(self.workers, state)

    def screen(self, name: str) -> list:
        """``(edit, finding key)`` of the pass's candidates the grid proves on the current song."""
        edits = PASSES[name](self.base)
        base = (self.base, self.text, self.tokens)
        out = []
        for k, delta, proved in self.screener([(k, e, base) for k, e in enumerate(edits)], name):
            if proved:
                self.findings.setdefault((name, edits[k]), Finding(name, edits[k], delta))
                out.append((edits[k], (name, edits[k])))
        return out

    def keep(self, name: str) -> bool:
        """Apply the proved edits that shrink the text in order and keep the longest run that
        proves, found by bisection; whether any edit was kept. A pass that kept nothing from a
        text keeps nothing from it again."""
        if (name, self.text) in self.settled:
            return False
        if name in CANONICAL:
            return self.settle(name)
        steps, base, size = [], self.base, self.tokens
        for edit, key in self.screen(name):
            cand = _apply(base, edit)
            written = _written(cand, self.state["native"])
            if written is not None and written[1] < size:
                steps.append((key, cand, written, written[1] - size))
                base, size = cand, written[1]
        good, high = min(1, len(steps)), len(steps)
        while good < high:
            mid = (good + high + 1) // 2
            if _proved(self.state, steps[mid - 1][2]):
                good = mid
            else:
                high = mid - 1
        for key, _, written, delta in steps[:good]:
            self.findings[key].kept, self.findings[key].delta = True, delta
            self.text, self.tokens, self.base = written
        if not good:
            self.settled.add((name, self.text))
        return good > 0

    def settle(self, name: str) -> bool:
        """Apply a canonical pass's candidates that write no larger all at once and keep
        them where the composite proves, else each half in turn (``k`` refused of ``n`` cost
        ``O(k log n)`` proofs), a part of at most ``workers`` screened at once; whether any was
        kept."""
        edits = PASSES[name](self.base)
        base = (self.base, self.text, self.tokens)
        counted = dict(self.screener([(k, e, base) for k, e in enumerate(edits)], name, _count))
        tally = self.tally.setdefault(name, dict.fromkeys(("found", "refused", "proved"), 0))
        tally["found"] += len(edits)
        tally["refused"] += sum(d == "refused" for d in counted.values())
        chosen = [k for k, d in counted.items() if isinstance(d, int) and d <= 0]
        song, kept = self.base, []

        def solve(subset: list, screened: bool = False) -> None:
            nonlocal song
            if not subset:
                return
            cand = functools.reduce(lambda s, k: _apply(s, edits[k]) or s, subset, song)
            written = _written(cand, self.state["native"])
            if written is not None and _proved(self.state, written):
                song = cand
                kept.extend(subset)
            elif not screened and 1 < len(subset) <= self.workers:
                jobs = [(k, edits[k], base) for k in subset]
                solve([k for k, _, ok in self.screener(jobs, name) if ok], True)
            elif len(subset) > 1:
                solve(subset[: len(subset) // 2], screened)
                solve(subset[len(subset) // 2 :], screened)

        solve(chosen)
        tally["proved"] += len(kept)
        if not kept:
            self.settled.add((name, self.text))
            return False
        for k in kept:
            self.findings[(name, edits[k])] = Finding(name, edits[k], counted[k], True)
        self.text, self.tokens, self.base = _written(song, self.state["native"])
        self.settled.add((name, self.text))
        return True

    def run(self, progress: bool = True) -> Result:
        """Apply every pass until none keeps an edit."""
        try:
            before, changed = self.tokens, True
            while changed:
                changed = False
                for name in tqdm(self.passes, desc="passes", unit="pass", disable=not progress):
                    while self.keep(name):
                        changed = True
        finally:
            self.screener.close()
        findings = list(self.findings.values())
        return Result(self.source, self.base, self.text, before, self.tokens, findings, self.tally)


def optimise(ir: dict, passes=None, workers: int | None = None, progress: bool = True) -> Result:
    """The song rewritten by the passes, with the same register grid and fewer tokens."""
    return Optimiser(ir, passes, workers).run(progress)


@functools.cache
def _code() -> str:
    return content_key(*(p.read_bytes() for p in sorted(Path(__file__).parent.glob("*.py"))))


def canonical(ir: dict, workers: int | None = None, cache: Path | None = CACHE) -> dict:
    """``ir`` with the canonical passes kept, proved as every pass is, cached by content beside
    the conversion's; ``ir`` itself where none keeps an edit, its state does not recur or it has
    no generic document."""

    def compute() -> dict:
        try:
            result = optimise(ir, list(CANONICAL), workers, progress=False)
        except (reach.ReachabilityError, document.GenericError, SongFormatError, PlayError):
            return ir
        return result.ir if any(f.kept for f in result.findings) else ir

    where = cache / f"canonical-{content_key(ir_key(ir), _code())}.pkl" if cache else None
    return cached(where, compute)
