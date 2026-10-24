# The generic optimiser

`unitracker.optimiser` rewrites any canonical document (format 2.1) into a smaller one that
plays the same register grid on unitracker-player. It knows no tracker: it reads and writes the
document only through unitracker-grammar's model (`unitracker_grammar.document`), whose `printed` is the one canonical
text.

```sh
python -m unitracker.optimiser IN.yaml [-o OUT.yaml] [--ticks N] [--rounds R] [--inline MODE]
```

prints the kept edits, the refused count and the size before and after (stderr), and writes the
document to `OUT.yaml` or stdout.

```python
from unitracker.optimiser import optimise
result = optimise(text, ticks=None, inline="named")   # Result: doc, text, before, after, kept, refused
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
| `merge` | each definition equal to an earlier one of its kind becoming it, its record dropped and its references renamed: instruments, `ctrl`, `pw`, `filter` and `restart` programs, phrases, and templates of modulators and programs, equal up to their parameters' names |
| `template` | per program kind (`ctrl`, `restart`, `pw`, `filter`), each group of two or more programs, records or instruments' inline ones, alike but for integers (a row's `wait`, `hold`, `ctrl`, `mask`, `gate`, `ad`, `sr`, `res`, `route`, `route_on`, `route_off`, `mode`, `vol`, a relative `note`, a constant cell value; `wait: 0` and `hold: 1` written out to compare) becoming one program template `{kind}_k` with a parameter per position whose values differ (positions of equal values across the group sharing one, named by the key's stem), each member a call of it: `ctrl: ctrl_1(7,4)`, `restart: restart_1(2,9,240)`, `!run:ctrl_1(7,3)` |
| `hoist` | a binding every instrument carries identically for a slot without a default becoming that slot's default in the `slots` line, reset on `note` where it reads phi, its fields dropped |

A `tails` pass (a program ending in another's rows running that one) is not exact: a `run:` row
starts the program at its own tick, so the shorter program's first row would apply a `hold` early,
and a row's `run` of its own slot's kind applies within the phase that runs it; it is not proposed.

Each pass's edits are what the semantics make equal in general; whether the song plays the same
is the guarantee's to decide (a default binding is bound from tick 0, an instrument's from its
note; merged templates keep the phi of one call where two names restarted it).

## Inline

`--inline MODE` (`optimise(..., inline=MODE)`, `unitracker-lift --optimise --inline MODE`) runs
after the passes and writes named definitions at their uses, for documents a reader (or a
language model) learns the format from without resolving names
(`unitracker.optimiser.inline`):

| mode | writes inline |
|---|---|
| `named` | nothing (the default) |
| `single` | every definition referenced exactly once: an instrument, a phrase, a `restart`, `ctrl`, `pw` or `filter` record; shared ones stay named |

Templates (`mod:` records and program templates) are the format's shared vocabulary and stay
named, a call is already an inline use and stays a call. The inline forms
([semantics](https://github.com/anarkiwi/unitracker-player/blob/main/docs/semantics.md),
Definitions):

| reference | inline |
|---|---|
| an event's instrument, `C4.i1` | `C4.{ad: 9, ...}` |
| a play entry's phrase, `p1+2` | `[C4, -*3]+2` |
| an instrument's `ctrl`, `restart`, `pw` or `filter` program | the program, `ctrl: [{ctrl: 65}, end]` |
| a `run:` or `!run:` of a program | the program after its kind, `run: pw[{pw: 800}, end]` |
| a `pw` or `filter` record that is a binding `b` | the program of its one row, `[{pw: b}, end]` |
| a record that is a call of a program template | the call |

A binding record becomes the program of its one row, never the instrument's own binding: the
player starts an instrument's own binding's φ at 0 on each note, while a named binding, a
program, keeps its φ where the same note binds it again. A definition that reaches itself (a
program whose rows run it) has no inline form and stays named. The definitions are written
inline together where the guarantee holds over the document the passes left (so after any
lossy step), else in halves as the driver proves edits; a refused one stays named. A
definition's records leave its section, and a section left empty is dropped.

## Tests

`tests/test_optimiser.py`: each pass on the hand-written documents of `tests/optimiser_docs.py`,
kept and refused; the guarantee's horizons; `tests/test_inline.py` each inline mode on a
hand-written document and on lifted documents of the four direct lifters; the CLI; and, for each `.tunecache/direct/*.yaml`
(or `$UNITRACKER_DIRECT_DOCS`), a lifted document optimised over 3000 ticks. Those are written by
the GoatTracker 2 transcriber (`python -m unitracker.direct.goattracker2 TUNE.sid > DOC.yaml`, on
the `gt2-direct` branch).
