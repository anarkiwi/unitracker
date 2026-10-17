"""The driver: screens each pass's candidates in parallel and keeps proved, smaller runs."""

import multiprocessing
import os
from dataclasses import dataclass, field

from tqdm import tqdm

from unitracker import songio
from unitracker.optimise.describe import describe
from unitracker.optimise.edits import EDITS
from unitracker.optimise.model import ERRORS
from unitracker.optimise.passes import LARGER_UNPROVED, PASSES
from unitracker.optimise.proof import Trace, mismatch, tokens


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
    """``(index, delta or None, proved)`` of one candidate edit of the current base."""
    k, edit = job
    written = _written(_apply(_STATE["base"], edit), _STATE["embed"])
    if written is None or written[0] == _STATE["text"]:
        return k, None, False
    delta = written[1] - _STATE["tokens"]
    if delta >= 0 and edit[0] in LARGER_UNPROVED:
        return k, delta, False
    return k, delta, mismatch(_STATE["ref"], written[2]) is None


def _screened(jobs, workers: int, desc: str, state: dict) -> list:
    """Every job's :func:`_screen` result, in job order, over ``workers`` processes."""
    progress = tqdm(total=len(jobs), desc=desc, unit="edit", leave=False)
    out = []
    if workers <= 1 or len(jobs) < 2:
        _init(state)
        for job in jobs:
            out.append(_screen(job))
            progress.update()
    else:
        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(min(workers, len(jobs)), initializer=_init, initargs=(state,)) as pool:
            for got in pool.imap_unordered(_screen, jobs, chunksize=1):
                out.append(got)
                progress.update()
    progress.close()
    return sorted(out)


class Optimiser:
    """Runs the passes over a song to a fixpoint; every kept edit is proved on its written text."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, ir: dict, passes=None, workers: int | None = None):
        self.passes = list(passes or PASSES)
        self.workers = workers if workers is not None else os.cpu_count() or 1
        self.ref = Trace(ir)
        self.source = self.base = ir
        try:
            self.embed, self.text = False, songio.dumps(ir)
        except songio.SongFormatError:
            self.embed, self.text = True, songio.dumps(ir, embed=True)
        self.tokens = tokens(self.text)
        self.findings = {}

    def screen(self, name: str) -> list:
        """``(edit, finding key)`` of the pass's candidates the grid proves on the current song."""
        edits = PASSES[name](self.base)
        state = {k: getattr(self, k) for k in ("base", "text", "tokens", "ref", "embed")}
        out = []
        for k, delta, proved in _screened(list(enumerate(edits)), self.workers, name, state):
            if proved:
                forms = describe(self.base, edits[k])
                self.findings.setdefault((name, forms), Finding(name, edits[k], delta, forms=forms))
                out.append((edits[k], (name, forms)))
        return out

    def keep(self, name: str) -> bool:
        """Apply the proved edits that shrink the text in order and keep the longest run whose
        written text the grid proves, found by bisection; whether any edit was kept."""
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
        for key, cand, written, delta in steps[:good]:
            self.findings[key].kept, self.findings[key].delta = True, delta
            self.base, self.text, self.tokens = cand, written[0], written[1]
        return good > 0

    def run(self, progress: bool = True) -> Result:
        """Apply every pass until none keeps an edit; the text is written from its own reading,
        so that it is canonical, and that reading is proved too."""
        before = self.tokens
        changed = True
        while changed:
            changed = False
            for name in tqdm(self.passes, desc="passes", unit="pass", disable=not progress):
                while self.keep(name):
                    changed = True
            self.base = songio.loads(self.text)
            text = songio.dumps(self.base, self.embed)
            if text != self.text:
                if mismatch(self.ref, songio.loads(text)) is not None:
                    raise songio.SongFormatError(
                        "the optimised text does not read back canonically"
                    )
                self.text, self.tokens, changed = text, tokens(text), True
        findings = list(self.findings.values())
        return Result(self.source, self.base, self.text, before, self.tokens, findings)


def optimise(ir: dict, passes=None, workers: int | None = None, progress: bool = True) -> Result:
    """The song rewritten by the passes, with the same register grid and fewer tokens."""
    return Optimiser(ir, passes, workers).run(progress)
