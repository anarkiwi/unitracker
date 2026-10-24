# The generic optimiser

`unitracker.optimiser` rewrites any canonical document (format 1.23, and the forms of earlier
versions the grammar reads) into a smaller one that plays the same register grid on
unitracker-player. It knows no tracker: it reads and writes the document only through
unitracker-grammar's model (`unitracker_grammar.document`), whose `printed` is the one canonical
text. It replaces `unitracker.optimise` ([optimise.md](optimise.md)), which works on the
lifters' song model.

```sh
python -m unitracker.optimiser IN.yaml [-o OUT.yaml] [--ticks N] [--rounds R]
```

prints the kept edits, the refused count and the size before and after (stderr), and writes the
document to `OUT.yaml` or stdout.

```python
from unitracker.optimiser import optimise
result = optimise(text, ticks=None)   # Result: doc, text, before, after, kept, refused
```

## Driver

The passes run in a fixed order, the sequence repeated until a round keeps no edit (at most
`rounds`). Each pass proposes candidate edits of the current model, each a pure function of a
document, in document order. A candidate is screened alone for size: it is a candidate only where
it shrinks the document. Size is the canonical text's token count under `cl100k_base`
(`tiktoken`). The candidates are then proved together: their composite is kept where it shrinks
the document and the guarantee holds; a refused composite is split in halves, each proved in turn
on the result of the one before, so `k` refusals of `n` candidates cost `O(k log n)` proofs. tqdm
shows each pass's progress.

## The guarantee

`Guarantee(text, ticks)` holds for a candidate whose grid (`guarantee.grid`) equals the input's:

| input | horizon |
|---|---|
| `ticks` given | the first `ticks` frames (fewer where the song ends first: the counts must match) |
| an ending song | every frame to the song's end |
| a looping song without code | unitracker-player's loop proof (`render`): the frames to the first recurrence of the player's state at the loop row, `frames[loop:]` repeating forever; two such grids are compared over both preperiods and both periods, which decides their equality forever (Fine and Wilf) |
| a looping song with a code modulator | refused (`GuaranteeError`): the loop proof does not cover code, so `ticks` must be given |

A candidate the grammar or the player refuses does not hold.

## Passes

| pass | proposes |
|---|---|
| `defaults` | dropping what is spelled at the value it has anyway ([semantics](https://github.com/anarkiwi/unitracker-player/blob/main/docs/semantics.md)): `tempo: 6`, `voices: 3`, a `clock` at PAL's `clock_hz`, `tempo: [a, a]` as `a`; a row's `wait: 0` and `hold: 1`; `!tempo:a/a` and `!vtempo:a/a` as `a`, `*1`, and a non-note's `!legato`, `!pw`, `!filter`; a `none` (row key, instrument field or command) of a slot nothing binds, and for `freq` nothing holds; an instrument's binding equal to its slot's default; a first row's `note: +0` of a control program no `run` starts (a note clears the rows' notes as it starts it) |
| `merge` | each definition equal to an earlier one of its kind becoming it, its record dropped and its references renamed: instruments, `ctrl`, `pw`, `filter` and `restart` programs, `x` instances, phrases, and templates of modulators and programs, equal up to their parameters' names |
| `hoist` | a binding every instrument carries identically for a slot without a default becoming that slot's default in the `slots` line, reset on `note` where it reads φ, its fields dropped (format 1.22 on) |

Each pass's edits are what the semantics make equal in general; whether the song plays the same
is the guarantee's to decide (a default binding is bound from tick 0, an instrument's from its
note; merged templates keep the φ of one call where two names restarted it).

## Tests

`tests/test_optimiser.py`: each pass on the hand-written documents of `tests/optimiser_docs.py`,
kept and refused; the guarantee's horizons; the CLI; and, for each `.tunecache/direct/*.yaml`
(or `$UNITRACKER_DIRECT_DOCS`), a lifted document optimised over 3000 ticks. Those are written by
the GoatTracker 2 transcriber (`python -m unitracker.direct.goattracker2 TUNE.sid > DOC.yaml`, on
the `gt2-direct` branch).
