"""The driver: passes in a fixed order propose edits of the document model; an edit is kept
when the document shrinks and the guarantee holds, proved in groups split on refusal."""

from collections import Counter
from dataclasses import dataclass, field

from tqdm import tqdm
from unitracker_grammar.document import Document, printed, read

from unitracker.optimiser import lossy as _lossy
from unitracker.optimiser import chords, names, passes, prelude, records, snap, templates, transpose
from unitracker.optimiser.guarantee import Guarantee
from unitracker.optimiser.inline import inline as inline_definitions
from unitracker.optimiser.measure import Rewrite, size

PASSES = (
    ("defaults", passes.defaults),
    ("merge", passes.merge),
    ("transpose", transpose.transpose),
    ("snap", snap.snap),
    ("template", templates.template),
    ("hoist", passes.hoist),
    ("once", passes.once),
    ("prelude", prelude.prelude),
    ("instruments", records.instruments),
    ("phrases", records.phrases),
    ("chords", chords.chords),
)


@dataclass
class Result(Rewrite):
    """The optimised document, its text, the sizes before and after, each kept edit as
    ``(pass, label)``, each refused edit's label, the lossy mode's result where it ran, and
    the tokens each pass saved (``names`` negative where its names cost more)."""

    lossy: _lossy.Lossy | None = None
    saved: Counter = field(default_factory=Counter)


class _Run:
    """One optimisation: the guarantee, the current document and its size."""

    def __init__(self, doc: Document, guarantee: Guarantee, progress: bool):
        self.doc, self.guarantee, self.progress = doc, guarantee, progress
        self.size, self.refused, self.saved = size(doc), [], Counter()
        self.horizon = len(guarantee.reference[0])

    def _apply(self, edits: list) -> Document | None:
        doc = self.doc
        for edit in edits:
            doc = edit.apply(doc)
        return doc

    def prove(self, edits: list, meter) -> list:
        """The edits of ``edits`` kept: all where their composite proves, else each half's
        in turn, so ``k`` refusals of ``n`` cost ``O(k log n)`` proofs."""
        doc = self._apply(edits)
        smaller = size(doc)
        if smaller < self.size and self.guarantee.holds(printed(doc)):
            self.doc, self.size = doc, smaller
            meter.update(len(edits))
            return edits
        if len(edits) == 1:
            meter.update(1)
            self.refused.append(edits[0].label)
            return []
        half = len(edits) // 2
        return self.prove(edits[:half], meter) + self.prove(edits[half:], meter)

    def run(self, name: str, propose, round_: int) -> list:
        """The edits pass ``name`` proposes and the guarantee keeps."""
        candidates, before = [], self.size
        traced = (self.horizon,) if getattr(propose, "traced", False) else ()
        for edit in propose(self.doc, *traced):
            if size(edit.apply(self.doc)) < self.size:
                candidates.append(edit)
        if not candidates:
            return []
        desc = f"{name} {round_}"
        with tqdm(
            total=len(candidates), desc=desc, unit="edit", disable=not self.progress
        ) as meter:
            kept = self.prove(candidates, meter)
        self.saved[name] += before - self.size
        return kept

    def rename(self) -> list:
        """The :func:`names.rename` of the document, kept where the guarantee holds."""
        doc, renamed = names.rename(self.doc)
        if not renamed:
            return []
        if not self.guarantee.holds(printed(doc)):
            self.refused.append("names")
            return []
        before, self.doc, self.size = self.size, doc, size(doc)
        self.saved["names"] += before - self.size
        return [("names", f"{old} -> {new}") for old, new in renamed.items()]


def optimise(  # pylint: disable=too-many-arguments,too-many-locals
    text: str,
    ticks: int | None = None,
    order=PASSES,
    rounds: int = 8,
    *,
    progress: bool = False,
    lossy: dict | None = None,
    inline: str = "named",
    rename: bool = True,
) -> Result:
    """``text`` rewritten by the passes of ``order``, repeated until a round keeps no edit
    (at most ``rounds``), every kept edit playing the input's grid (:class:`Guarantee`).
    With ``lossy`` tolerances by cell, the lossy mode (:func:`lossy.simplify`) rewrites
    ``text`` first, and the passes keep its result's grid; then the definitions of ``inline``'s
    mode are written inline (:mod:`unitracker.optimiser.inline`). Between, the generated
    program templates take structural names (:func:`names.rename`) where ``rename``, whatever
    their size."""
    before, kept, simplified = size(text), [], None
    if lossy is not None:
        simplified = _lossy.simplify(text, ticks, lossy, progress)
        text = simplified.text
    run = _Run(read(text), Guarantee(text, ticks), progress)
    for round_ in range(1, rounds + 1):
        found = [(name, e.label) for name, propose in order for e in run.run(name, propose, round_)]
        kept += found
        if not found:
            break
    kept += run.rename() if rename else []
    if inline != "named":
        run.doc, refused = inline_definitions(run.doc, inline, run.guarantee.holds)
        run.size, run.refused = size(run.doc), run.refused + refused
    text = printed(run.doc)
    return Result(run.doc, text, before, run.size, kept, run.refused, simplified, run.saved)
