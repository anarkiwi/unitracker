"""The ``prelude`` pass: each call of a document's own modulator template, of code or an
expression, that plays exactly as a prelude template's call becomes that call, and a template
no call names any more is dropped (docs/optimiser.md)."""

from collections import defaultdict
from functools import partial

from unitracker_grammar import code
from unitracker_grammar.chords import CHORDS
from unitracker_grammar.document import Call, Document, printed

from unitracker.optimiser import fit
from unitracker.optimiser.lossy import clock_of, pruned
from unitracker.optimiser.passes import Edit
from unitracker.optimiser.trace import trace
from unitracker.optimiser.tree import transform, walk


def core(call: Call) -> Call:
    """``call`` as the player reads it: each chord name its intervals."""
    args = []
    for a in call.args:
        args += CHORDS[a] if isinstance(a, str) and a in CHORDS else [a]
    return Call(call.name, tuple(args))


def _renamed(calls: dict, name: str, doc: Document) -> Document:
    doc = transform(doc, lambda n: calls.get(n, n) if isinstance(n, Call) else n)
    return pruned(doc, {name})


def prelude(doc: Document, ticks: int) -> list:
    """Per template of the document's own, an edit making each of its calls whose firings
    over ``ticks`` a prelude call plays exactly (:func:`fit.prelude_call`) that call, the
    template dropped where no call names it any more."""
    own = {code.signature(k)[0] for k, _ in doc.mods}
    calls = {n for _, n in walk(doc) if isinstance(n, Call) and n.name in own}
    calls = sorted((c for c in calls if all(isinstance(a, int) for a in core(c).args)), key=str)
    if not calls:
        return []
    _, firings = trace(printed(doc), ticks)
    cells = defaultdict(list)
    for (call, cell), rec in sorted(firings.items(), key=lambda kv: kv[0][1]):
        cells[call].append(fit.Firings(rec, cell, *clock_of(doc)))
    found = defaultdict(dict)
    for call in calls:
        best = fit.prelude_call(cells[core(call)]) if core(call) in cells else None
        if best is not None:
            found[call.name][call] = Call(*best)
    return [
        Edit(
            f"{name} -> {', '.join(sorted({c.name for c in mapping.values()}))}",
            partial(_renamed, mapping, name),
        )
        for name, mapping in sorted(found.items())
    ]


prelude.traced = True  # the driver passes the guarantee's horizon
