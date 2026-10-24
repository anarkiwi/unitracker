# The generic optimiser

`unitracker.optimiser` rewrites any canonical document (format 2.3) into a smaller one that
plays the same register grid on unitracker-player. It knows no tracker: it reads and writes the
document only through unitracker-grammar's model (`unitracker_grammar.document`), whose `printed` is the one canonical
text.

```sh
python -m unitracker.optimiser IN.yaml [-o OUT.yaml] [--ticks N] [--rounds R] [--inline MODE]
    [--lossy [--freq-tol P95,MAX] [--pw-tol P95,MAX] [--cut-tol P95,MAX]]
```

prints the kept edits, the refused count and the size before and after (stderr), and writes the
document to `OUT.yaml` or stdout. `--lossy` runs the [lossy mode](#lossy-mode) first
(`unitracker-lift ... --optimise --lossy` takes the same options).

```python
from unitracker.optimiser import optimise
result = optimise(text, ticks=None, inline="named")   # Result: doc, text, before, after, kept, refused, lossy
result = optimise(text, ticks, lossy={"freq": Tolerance(10, 25)})   # optimiser.fit.Tolerance
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
| `defaults` | dropping what is spelled at the value it has anyway ([semantics](https://github.com/anarkiwi/unitracker-player/blob/main/docs/semantics.md)): `tempo:6`, `voices:3`, a `clock` at PAL's `clock_hz`, `tempo:[a,a]` as `a`; a row's `wait:0` and `hold:1`; `tempo:a/a` and `vtempo:a/a` as `a`, `*1`, and a non-note's `legato`, `pw`, `filter`; a `none` (row key, instrument field or command) of a slot nothing binds, and for `freq` nothing holds; an instrument's binding equal to its slot's default; a first row's `note:+0` of a control program no `run` starts (a note clears the rows' notes as it starts it) |
| `merge` | each definition equal to an earlier one of its kind becoming it, its record dropped and its references renamed: instruments, `ctrl`, `pw`, `filter` and `restart` programs, phrases, and templates of modulators and programs, equal up to their parameters' names |
| `template` | per program kind (`ctrl`, `restart`, `pw`, `filter`), each group of two or more programs, records or instruments' inline ones, alike but for integers (a row's `wait`, `hold`, `ctrl`, `mask`, `gate`, `ad`, `sr`, `res`, `route`, `route_on`, `route_off`, `mode`, `vol`, a relative `note`, a constant cell value; `wait:0` and `hold:1` written out to compare) becoming one program template `{kind}_k` with a parameter per position whose values differ (positions of equal values across the group sharing one, named by the key's stem), each member a call of it: `ctrl:ctrl_1(7,4)`, `restart:restart_1(2,9,240)`, `run:ctrl_1(7,3)` |
| `hoist` | a binding every instrument carries identically for a slot without a default becoming that slot's default in the `slots` line, reset on `note` where it reads phi, its fields dropped |

A `tails` pass (a program ending in another's rows running that one) is not exact: a `run:` row
starts the program at its own tick, so the shorter program's first row would apply a `hold` early,
and a row's `run` of its own slot's kind applies within the phase that runs it; it is not proposed.

Each pass's edits are what the semantics make equal in general; whether the song plays the same
is the guarantee's to decide (a default binding is bound from tick 0, an instrument's from its
note; merged templates keep the phi of one call where two names restarted it).

## Lossy mode

`lossy.simplify(text, ticks, tolerances)`, off by default, trades a bounded loss of fidelity
for simplicity: each call of a code template (`vib(0,4,32)`) becomes a call of an expression
template fitted to what the call did, and nothing else in the document changes. The exact passes
then run on its result, their guarantee taken against it.

1. **Record** (`trace.py`): the document plays over `ticks` (to its end where it ends; a looping
   song needs `ticks`), and each firing of each call's routine is recorded by call and cell:
   tick, voice, phi, input `v`, output, `N`, `n`.
2. **Fit** (`fit.py`): per call, residuals of the output against each base (`v`, `0`, `F(N)`,
   `F(n)`; the notes for the frequency only) are summarised per phi, and each shape's integer
   parameters fitted:

   | shape | form (added to the base) | fit |
   |---|---|---|
   | `keep` | `0` | none |
   | `off` | `q` | weighted median |
   | `lin` | `q + s*phi` | weighted least squares, slope rounded |
   | `up`, `down` | `min(q + s*phi,m)`, `max(...)` | the line short of the extreme, held at it |
   | `sq` | `q + w*((phi + o) // h % 2)` | every half period `h` to 64 and offset `o` |
   | `tri` | `q + s*abs((phi + o) % (2*h) - h)` | as `sq`, `q` and `s` by least squares |
   | `tab` | `T[k + min(phi,l)] - z` | the residual per phi to its last change, held after |
   | `loop` | `T[k + min(phi,l) + max(phi - l,0) % p] - z` | the shortest preperiod `l` and period `p` over two periods or more |
   | `first` | `v + (phi < l)*(B + q - v)` | the base `B` until phi `l`, the input after |

   A shape on a base whose residual is 0 before some phi `d` is also fitted from `d` on, as
   `(phi >= d)*(...)`. A form is kept when its error over every firing keeps to the cell's
   tolerance; along a chain (a firing whose input is what the voice's firing before left) the
   form's own output feeds the next firing, so a form that drifts is refused.
3. **Template**: per code template, the cheaper (in tokens) of one form key fitting all its
   calls and each call's cheapest form, grouped by key. Each group is one expression template
   named by its shape with a parameter per value its calls differ in, a parameter that is an
   integer multiple of another written as that multiple, and the rest literal
   (`vib(0,4,32)`, `vib(0,4,64)` → `sq(32)`, `sq(64)` of `sq(q):v - 2*(phi // 2 % 2)*q + q`). A table
   form's tables are one new table, each call's offset `k`.
4. **Prove**: each code template's replacement is screened alone for size, then the
   replacements are proved together and, refused, in halves, as the driver's edits: kept where
   the document (its replaced code templates no call names any more dropped, with the state
   variables and tables nothing reads) shrinks and the check holds.

The check plays the candidate over the same horizon: every register outside the targeted cells
(the frequency, pulse width or cutoff of each voice a replaced call fired on) must equal the
input's, and each targeted cell's error over the ticks a replaced call fired on, and every tick
it differs, must keep to its tolerance at its 95th percentile and maximum:

| cell | error | default `P95,MAX` |
|---|---|---|
| `freq` | cents, infinite where exactly one of the two is 0 | `10,25` |
| `pw` | register units (12 bits), or percent of the input's value with a `%` suffix | `32,128` |
| `cut` | register units (11 bits), or percent as for `pw` | `16,64` |

The report (stderr) gives, per replaced code template, its calls, the templates they became, the
tokens it saves alone and its fitted error (p50, p95, max), each refused one, each targeted
cell's error and the size before and after.

A call fitted from its own firings cannot follow state another code template writes: a template
that only feeds another's state fits `keep` and is refused by the check, and a call bound on two
cells is left as it is. The guarantee covers the horizon only: a form fitted to the phi a song
reaches may differ past it.

`python tools/lossy_eval.py DOC.yaml ... [--ticks N] [--wav DIR [--seconds S]]` tabulates the
tokens of the exact passes against the lossy mode with them, what each code template became and
the cells' errors, and renders each document before and after to WAV (pysidtracker on a 6581).

## Inline

`--inline MODE` (`optimise(..., inline=MODE)`, `unitracker-lift --optimise --inline MODE`) runs
after the passes and writes named definitions at their uses, for documents a reader (or a
language model) learns the format from without resolving names
(`unitracker.optimiser.inline`):

| mode | writes inline |
|---|---|
| `named` | nothing (the default) |
| `single` | every definition referenced exactly once, anonymously: an instrument, a phrase, a `restart`, `ctrl`, `pw` or `filter` record; shared ones and templates stay named |
| `all` | every definition the song reaches, templates included, at its first use in reading order (`NAME=` before its value, a template's `SIGNATURE=` before its body and its call's arguments: `C4.i3={ad:9}`, `freq:vib(d)='v + d*(phi % 4)'(2)`), referenced by name after (`unitracker_grammar.document.first_use`); a definition the song never reaches is dropped, so no section is left |

In `single`, templates (`mod:` records and program templates) are the format's shared
vocabulary and stay named, and a call is already an inline use and stays a call. Its anonymous
forms
([semantics](https://github.com/anarkiwi/unitracker-player/blob/main/docs/semantics.md),
Definitions):

| reference | inline |
|---|---|
| an event's instrument, `C4.i1` | `C4.{ad:9,...}` |
| a play entry's phrase, `p1+2` | `[C4,-*3]+2` |
| an instrument's `ctrl`, `restart`, `pw` or `filter` program | the program, `ctrl:[{ctrl:65},end]` |
| a `run:` row key or command of a program | the program after its kind, `run:pw[{pw:800},end]` |
| a `pw` or `filter` record that is a binding `b` | the program of its one row, `[{pw:b},end]` |
| a record that is a call of a program template | the call |

A binding record becomes the program of its one row, never the instrument's own binding: the
player starts an instrument's own binding's phi at 0 on each note, while a named binding, a
program, keeps its phi where the same note binds it again. A definition that reaches itself (a
program whose rows run it) has no inline form and stays named. The definitions are written
inline together where the guarantee holds over the document the passes left (so after any
lossy step), else in halves as the driver proves edits; a refused one stays named. A
definition's records leave its section, and a section left empty is dropped. `all` is proved
as one edit: refused, the document stays as the passes left it.

## Tests

`tests/test_lossy.py`: code that is exactly an expression fitted exactly, a looser tolerance
picking a simpler form, noise refused at a tight tolerance and kept at a loose one, a replacement
that breaks code reading its state refused, each shape fitted, and the CLIs.
`tests/test_optimiser.py`: each pass on the hand-written documents of `tests/optimiser_docs.py`,
kept and refused; the guarantee's horizons; `tests/test_inline.py` each inline mode on a
hand-written document and on lifted documents of the four direct lifters; the CLI; and, for
each `.tunecache/direct/*.yaml`
(or `$UNITRACKER_DIRECT_DOCS`), a lifted document optimised over 3000 ticks. Those are written by
the GoatTracker 2 transcriber (`python -m unitracker.direct.goattracker2 TUNE.sid > DOC.yaml`, on
the `gt2-direct` branch).
