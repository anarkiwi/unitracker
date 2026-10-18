"""The driver: screens each pass's candidates in parallel and keeps proved, smaller runs."""

import functools
import multiprocessing
import os
from dataclasses import dataclass, field
from pathlib import Path

from tqdm import tqdm

from unitracker.format import songio
from unitracker.modfit.convert import CACHE, _load, _save, ir_key
from unitracker.modfit.exprs import _cached, content_key, default_workers
from unitracker.optimise.describe import describe
from unitracker.optimise.edits import EDITS
from unitracker.optimise.model import ERRORS
from unitracker.optimise.passes import CANONICAL, LARGER_UNPROVED, PASSES
from unitracker.optimise.proof import Trace, mismatch, tokens
from unitracker.player import reach
from unitracker.player.checkpoint import Baseline
from unitracker.player.vm import SongPlayer


@dataclass
class Finding:
    """A proved edit: the pass, the edit, the token delta, and whether the driver kept it."""

    pass_name: str
    edit: tuple
    delta: int
    kept: bool = False
    forms: tuple = ()


@dataclass
class Result:
    """The input song, the optimised song and its text, the token counts and every proved edit."""

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


def _written(ir: dict | None, embed: bool = False):
    """``(text, tokens, read back)`` of a candidate, or None when it can't be written."""
    if ir is None:
        return None
    try:
        text = songio.dumps(ir, embed)
        return text, tokens(text), songio.loads(text)
    except ERRORS:
        return None


_STATE = {}


def _init(state: dict) -> None:
    _STATE.update(state)


def _screen(job):
    """``(index, delta or None, proved)`` of one candidate edit of ``(base, text, tokens)``."""
    k, edit, (base, text, size) = job
    written = _written(_apply(base, edit), _STATE["embed"])
    if written is None or written[0] == text:
        return k, None, False
    delta = written[1] - size
    if delta >= 0 and edit[0] in LARGER_UNPROVED:
        return k, delta, False
    return k, delta, mismatch(_STATE["ref"], written[2]) is None


def _count(job):
    """``(index, token delta)`` of one candidate edit unproved: ``"refused"`` where the edit or
    the written text's reading refuses it, ``"same"`` where the text is unchanged."""
    k, edit, (base, text, size) = job
    written = _written(_apply(base, edit), _STATE["embed"])
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
    """Runs the passes over a song to a fixpoint; every kept edit is proved on its written text."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, ir: dict, passes=None, workers: int | None = None):
        self.passes = list(passes or PASSES)
        self.workers = workers if workers is not None else default_workers()
        self.ref = Trace(ir)
        self.source = ir
        try:
            self.embed, self.text = False, songio.dumps(ir)
        except songio.SongFormatError:
            self.embed, self.text = True, songio.dumps(ir, embed=True)
        self.base, self.tokens = songio.loads(self.text), tokens(self.text)
        if mismatch(self.ref, self.base) is None:
            self.ref.baseline = Baseline(SongPlayer(self.base))
        self.findings, self.tally = {}, {}
        self.settled = set()
        self.screener = _Screener(self.workers, {"ref": self.ref, "embed": self.embed})

    def screen(self, name: str) -> list:
        """``(edit, finding key)`` of the pass's candidates the grid proves on the current song."""
        edits = PASSES[name](self.base)
        base = (self.base, self.text, self.tokens)
        out = []
        for k, delta, proved in self.screener([(k, e, base) for k, e in enumerate(edits)], name):
            if proved:
                forms = describe(self.base, edits[k])
                self.findings.setdefault((name, forms), Finding(name, edits[k], delta, forms=forms))
                out.append((edits[k], (name, forms)))
        return out

    def keep(self, name: str) -> bool:
        """Apply the proved edits that shrink the text in order and keep the longest run whose
        written text the grid proves, found by bisection; whether any edit was kept.

        The current song is always its text read back, so screening is a function of the pass
        and the text: a pass that kept nothing from a text keeps nothing from it again."""
        if (name, self.text) in self.settled:
            return False
        if name in CANONICAL:
            return self.settle(name)
        steps, base, size = [], self.base, self.tokens
        for edit, key in self.screen(name):
            cand = _apply(base, edit)
            written = _written(cand, self.embed)
            if written is not None and written[1] < size:
                steps.append((key, cand, written, written[1] - size))
                base, size = cand, written[1]
        good, high = min(1, len(steps)), len(steps)
        while good < high:
            mid = (good + high + 1) // 2
            if mismatch(self.ref, steps[mid - 1][2][2]) is None:
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
        """Apply a canonical pass's candidates that read back no larger all at once and keep
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
            written = _written(cand, self.embed)
            if written is not None and mismatch(self.ref, written[2]) is None:
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
            forms = describe(self.base, edits[k])
            self.findings[(name, forms)] = Finding(name, edits[k], counted[k], True, forms)
        self.text, self.tokens, self.base = _written(song, self.embed)
        self.settled.add((name, self.text))
        return True

    def run(self, progress: bool = True) -> Result:
        """Apply every pass until none keeps an edit; the text is written from its own reading,
        so that it is canonical, and that reading is proved too."""
        try:
            return self._run(progress)
        finally:
            self.screener.close()

    def _run(self, progress: bool) -> Result:
        before = self.tokens
        changed = True
        while changed:
            changed = False
            for name in tqdm(self.passes, desc="passes", unit="pass", disable=not progress):
                while self.keep(name):
                    changed = True
            text = songio.dumps(self.base, self.embed)
            if text != self.text:
                back = songio.loads(text)
                if mismatch(self.ref, back) is not None:
                    raise songio.SongFormatError(
                        "the optimised text does not read back canonically"
                    )
                self.base, self.text, self.tokens, changed = back, text, tokens(text), True
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
    the conversion's; ``ir`` itself where none keeps an edit or its state does not recur."""

    def compute() -> dict:
        try:
            result = optimise(ir, list(CANONICAL), workers, progress=False)
        except reach.ReachabilityError:
            return ir
        return result.ir if any(f.kept for f in result.findings) else ir

    where = cache / f"canonical-{content_key(ir_key(ir), _code())}.pkl" if cache else None
    return _cached(where, compute, _load, _save)
