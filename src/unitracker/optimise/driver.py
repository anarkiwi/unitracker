"""The driver: screens each pass's candidates in parallel and keeps proved, smaller runs."""

import multiprocessing
from dataclasses import dataclass, field

from tqdm import tqdm

from unitracker.format import songio
from unitracker.modfit.exprs import default_workers
from unitracker.optimise.describe import describe
from unitracker.optimise.edits import EDITS
from unitracker.optimise.model import ERRORS
from unitracker.optimise.passes import LARGER_UNPROVED, PASSES
from unitracker.optimise.proof import Trace, mismatch, tokens
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


def _apply(ir: dict, edit: tuple) -> dict | None:
    op, args = edit
    try:
        return EDITS[op](ir, *args)
    except ERRORS:
        return None


def _written(ir: dict | None, embed: bool = False):
    """``(text, tokens, read back)`` of a candidate, or None when it can't be written. The
    writer restates the ``phrase_steps`` hint from the candidate's phrases."""
    if ir is None:
        return None
    if "phrase_steps" in (ir.get("hints") or {}):
        ir = {**ir, "hints": {k: v for k, v in ir["hints"].items() if k != "phrase_steps"}}
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


class _Screener:
    """Screens candidates in-process or over one pool of ``workers`` processes, started on first
    use and holding the reference for the optimiser's lifetime."""

    def __init__(self, workers: int, state: dict):
        self.workers, self.state, self.pool = workers, state, None

    def __call__(self, jobs: list, desc: str) -> list:
        """Every job's :func:`_screen` result, in job order."""
        progress = tqdm(total=len(jobs), desc=desc, unit="edit", leave=False)
        out = []
        if self.workers <= 1 or len(jobs) < 2:
            _init(self.state)
            for job in jobs:
                out.append(_screen(job))
                progress.update()
        else:
            if self.pool is None:
                ctx = multiprocessing.get_context("spawn")
                self.pool = ctx.Pool(self.workers, initializer=_init, initargs=(self.state,))
            for got in self.pool.imap_unordered(_screen, jobs, chunksize=1):
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
        self.findings = {}
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
        return Result(self.source, self.base, self.text, before, self.tokens, findings)


def optimise(ir: dict, passes=None, workers: int | None = None, progress: bool = True) -> Result:
    """The song rewritten by the passes, with the same register grid and fewer tokens."""
    return Optimiser(ir, passes, workers).run(progress)
