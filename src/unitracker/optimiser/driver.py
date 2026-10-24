"""The driver: passes in a fixed order propose edits of the document model; an edit is kept
when the document shrinks and the guarantee holds, proved in groups split on refusal."""

import functools
from dataclasses import dataclass, field

from tqdm import tqdm
from unitracker_grammar.document import Document, printed, read

from unitracker.optimiser import passes, templates
from unitracker.optimiser.guarantee import Guarantee

ENCODING = "cl100k_base"
PASSES = (
    ("defaults", passes.defaults),
    ("merge", passes.merge),
    ("template", templates.template),
    ("hoist", passes.hoist),
)


@functools.cache
def _encoding():
    import tiktoken  # pylint: disable=import-outside-toplevel

    return tiktoken.get_encoding(ENCODING)


def size(doc: Document | str) -> int:
    """A document's size: its canonical text's token count under :data:`ENCODING`."""
    return len(_encoding().encode(doc if isinstance(doc, str) else printed(doc)))


@dataclass
class Result:
    """The optimised document, its text, the sizes before and after, each kept edit as
    ``(pass, label)`` and each refused edit's label."""

    doc: Document
    text: str
    before: int
    after: int
    kept: list = field(default_factory=list)
    refused: list = field(default_factory=list)


class _Run:
    """One optimisation: the guarantee, the current document and its size."""

    def __init__(self, doc: Document, guarantee: Guarantee, progress: bool):
        self.doc, self.guarantee, self.progress = doc, guarantee, progress
        self.size, self.refused = size(doc), []

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
        candidates = []
        for edit in propose(self.doc):
            if size(edit.apply(self.doc)) < self.size:
                candidates.append(edit)
        if not candidates:
            return []
        desc = f"{name} {round_}"
        with tqdm(
            total=len(candidates), desc=desc, unit="edit", disable=not self.progress
        ) as meter:
            return self.prove(candidates, meter)


def optimise(
    text: str,
    ticks: int | None = None,
    order=PASSES,
    rounds: int = 8,
    progress: bool = False,
) -> Result:
    """``text`` rewritten by the passes of ``order``, repeated until a round keeps no edit
    (at most ``rounds``), every kept edit playing the input's grid (:class:`Guarantee`)."""
    run = _Run(read(text), Guarantee(text, ticks), progress)
    before, kept = size(text), []
    for round_ in range(1, rounds + 1):
        found = [(name, e.label) for name, propose in order for e in run.run(name, propose, round_)]
        kept += found
        if not found:
            break
    return Result(run.doc, printed(run.doc), before, run.size, kept, run.refused)
