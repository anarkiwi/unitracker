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
| `defaults` | dropping what is spelled at the value it has anyway ([semantics](https://github.com/anarkiwi/unitracker-player/blob/main/docs/semantics.md)): `tempo:6`, `voices:3`, a `clock` at PAL's `clock_hz`, `tempo:[a,a]` as `a`; a row's `wait:0` and `hold:1`; ` tempo:a/a` and ` vtempo:a/a` as `a`, `*1`, and a non-note's ` legato`, ` pw`, ` filter`; a `none` (row key, instrument field or command) of a slot nothing binds, and for `freq` nothing holds; an instrument's binding equal to its slot's default; a first row's `note:+0` of a control program no `run` starts (a note clears the rows' notes as it starts it); an instrument's `none` field, one per instrument (it ends a binding only where one lasts to that instrument's note, so the guarantee decides); a program row of only `wait` and `hold` folded into the hold of the row before (`{hold:2,ctrl:65},{}` is `{hold:3,ctrl:65}`), not where a loop starts at it |
| `merge` | each definition equal to an earlier one of its kind becoming it, its record dropped and its references renamed: instruments, `ctrl`, `pw`, `filter` and `restart` programs, phrases, and templates of modulators and programs, equal up to their parameters' names |
| `transpose` | each phrase whose events are an earlier phrase's but for one shift `k` of every note (a note atom's; a program row's notes are the instrument's and stay) becoming the first such phrase in document order, its record dropped and each play entry of it naming that phrase with `k` added to its transpose (`p2` = `p1` up 2: `p2-1` is `p1+1`) |
| `snap` | each phrase event's frequency binding under which the voice's frequency was `F(N)` at every tick the binding lasted over the guarantee's horizon (see [Snap](#snap)) becoming the cell's own `F(N)`: the binding dropped where no frequency slot was bound as it applied, else a `none` of each slot that was |
| `template` | per program kind (`ctrl`, `restart`, `pw`, `filter`), each group of two or more programs, records or instruments' inline ones, alike but for integers (a row's `wait`, `hold`, `ctrl`, `mask`, `gate`, `ad`, `sr`, `res`, `route`, `route_on`, `route_off`, `mode`, `vol`, a relative `note`, a constant cell value; `wait:0` and `hold:1` written out to compare) becoming one program template `{kind}_k` with a parameter per position whose values differ (positions of equal values across the group sharing one, named by the key's stem), each member a call of it: `ctrl:ctrl_1(7,4)`, `restart:restart_1(2,9,240)`, ` run:ctrl_1(7,3)` |
| `hoist` | a binding every instrument carries identically for a slot without a default becoming that slot's default in the `slots` line, reset on `note` where it reads phi, its fields dropped |
| `repeats` | each phrase's events (a record, an inline phrase; groups played out first) in their cheapest encoding with groups, `[D,E]x4`, `[D,E\|F\|G]`, nested; the song's rows in sections and groups, the loop item the first of the looped rows (see [Repeats](#repeats)) |

### Repeats

A phrase's encoding is an exact interval DP over its events: the cheapest encoding of each span
is a sequence of events and groups, a group a tandem repeat `[B]xk` of a primitive body or a
volta `[B|E1|E2...]` (endings of one length, the body their longest common prefix), its parts
encoded alike. Costs are cl100k tokens: an event's text alone (its octave written), a group's
brackets, bars and count; the driver measures the true size. The song's rows are split at the
loop row, Re-Pair (`unitracker_player.streams.repair`) compresses both parts over shared rules,
a rule whose definition costs at least what its uses save (by the same DP) is inlined, one at a
time, deepest first, to a fixpoint, and each kept rule is a section `sK` (numbered in reading
order); the song and each section body are then encoded by the DP, a repeated section a part
`s3x2`. Both edits are the input once their sugar is expanded, and a second round proposes none.

After the last round the `names` rename (`optimise(..., rename=True)`, the default) gives each
program template the `template` pass named `{kind}_k` a name by its shape, in document order:

| shape | name |
|---|---|
| a looping control program whose rows read a parameter as a relative note (`note:+t`) | `arp` |
| a restart with a row gating off (`gate:0`; its note gates on) | `hr` |
| any other control, restart, pulse or filter program | `wave`, `rs`, `pwm`, `flt` |

A name taken (by a template, `abs`, `min`, `max`, `none`, `hold`, `reset`, `end`, `loop`,
`on`, `off`, or an earlier rename) is numbered from 2 (`arp2`); no stem is a record's
(`c2`, `f2`). The modulator templates keep the lifters' names. The rename is cosmetic: it is
applied whatever its size, as one edit the guarantee proves (refused, nothing is renamed), and
`Result.saved["names"]` is negative where the names cost tokens.

A `tails` pass (a program ending in another's rows running that one) is not exact: a `run:` row
starts the program at its own tick, so the shorter program's first row would apply a `hold` early,
and a row's `run` of its own slot's kind applies within the phase that runs it; it is not proposed.

Each pass's edits are what the semantics make equal in general; whether the song plays the same
is the guarantee's to decide (a default binding is bound from tick 0, an instrument's from its
note; merged templates keep the phi of one call where two names restarted it).

## Snap

A lifted "change pitch without retrigger" is a ` legato` note (which sets only the event note)
bound to a modulator that writes `F(n)` (GoatTracker's `snap()`, a `tporta` at speed 0). Where
no other binding moves the frequency while it lasts, the frequency is what an unbound cell
plays anyway: `F(N)`. `snap.sites` plays the document over the guarantee's horizon (a traced
pass: the driver passes it `len` of the reference grid) with a player that keeps, for each
phrase event's frequency binding command (by phrase, event and slot), the slots bound as it
applied and whether the voice's frequency equalled `F(N)` at every tick the binding was in its
slot, its hold ticks included, and after it while another slot its `none` would end stays
bound as it was (until something binds or stops that slot). Each such binding becomes `none` of the slots bound before it
(the binding's own included, and it where the frequency held), so the cell is unbound and plays
`F(N)`; with none bound it is dropped. State the modulator writes for a later binding to read
(a vibrato's note) is not in the evidence; the guarantee refuses those.

## Lossy mode

`lossy.simplify(text, ticks, tolerances)`, off by default, trades a bounded loss of fidelity
for simplicity: each call of a code template (`vib(0,4,32)`) becomes a call of an expression
template fitted to what the call did, or, where code templates share state, to what they did
together, and nothing else in the document changes. The exact passes then run on its result,
their guarantee taken against it.

1. **Record** (`trace.py`): the document plays over `ticks` (to its end where it ends; a looping
   song needs `ticks`), and every slot's firing is logged in firing order: tick, cell, the voice
   whose cell it is and the one that bound it, the slot, its code call (if any), phi, input `v`,
   output, `N`, `n`. Each code call's firings are those of its call and cell.
2. **Fit** (`fit.py`): per call, residuals of the output against each base (`v`, `0`, `F(N)`,
   `F(n)`; the notes for the frequency only) are summarised per phi, and each shape's integer
   parameters fitted. Each shape has a name by what it does on each base, and each parameter a
   name by its role:

   | shape | form (added to the base) | fit | on `v` / `0` / `F(N)` | parameters |
   |---|---|---|---|---|
   | `keep` | `0` | none | `none` / - / `pitch` | |
   | `off` | `q` | weighted median | `ramp` / `level` / `detune` | `step` / `value` / `offset` |
   | `lin` | `q + s*phi` | weighted least squares, slope rounded | `accel` / `line` / `glide` | `q`, `s` |
   | `up`, `down` | `min(q + s*phi,m)`, `max(...)` | the line short of the extreme, held at it | `accelup` / `rise` / `glideup`, ... | `q`, `s`, `limit` |
   | `sq` | `q + w*((phi + o) // h % 2)` | every half period `h` to 64 and offset `o` | `stepsq` / `square` / `vibsq` | `q`, `depth`, `phase`, `half` |
   | `tri` | `q + s*abs((phi + o) % (2*h) - h)` | as `sq`, `q` and `s` by least squares | `steptri` / `tri` / `vib` | `lo`, `step`, `phase`, `half` |
   | `tab` | `T[k + min(phi,l)] - z` | the residual per phi to its last change, held after | `steps` / `seq` / `pitchseq` | `at`, `last` |
   | `loop` | `T[k + min(phi,l) + max(phi - l,0) % p] - z` | the shortest preperiod `l` and period `p` over two periods or more | `stepcycle` / `cycle` / `pitchcycle` | `at`, `last`, `period` |
   | `first` | `v + (phi < l)*(B + q - v)` | the base `B` until phi `l`, the input after | - / `start` / `note` | `offset`, `len` |
   | `toward` | `v + max(-s,min(s,B - v))` | the input moved toward the note `B` by the median move | - / - / `glideto` | `step` |

   `q` is `step` on `v`, `lo` on `0` and `offset` on a note; `s` is `accel` on `v`, else
   `step`. A form on `F(n)` is named as on `F(N)` after `ev`. A shape on a base whose residual
   is 0 before some phi `d` is also fitted from `d` on, as `(phi >= d)*(...)` (named after
   `late`); one whose output is its input before some phi `d` (a slot the player holds on a
   note's first tick) as `v + (phi >= d)*(B + ... - v)` (after `held`), and that with a later
   `late` start `d` after `e` (after `heldlate`). A form is kept when its error over every
   firing keeps to the cell's tolerance; along a chain (a firing whose input is what the voice's
   firing before left) the form's own output feeds the next firing, so a form that drifts is
   refused.
3. **Couple** (`coupled.py`): code templates of a cell that name the same declared `state:`
   variables are joined into groups (connected components; `snd+stp+tone`, `note+porta+vib`). A
   group's calls are replaced together by a plan, per slot `h` its calls fire in (the host):
   - at each tick a group's calls fire on a voice's cell, the group's input is the value its first
     firing took and its output the value its last left;
   - each call bound in `h` is fitted (step 2) to the group's output at its own firings, with
     its own phi, the group's input as `v` (or, where the group's calls in the slots before `h`
     keep their own output, `h`'s own input, and those calls are fitted to their own output);
   - every other call of the group, a call no firing reached among them, becomes `none`, and a
     slot left bound by nothing loses its `none` bindings.
   A plan is refused where a call fits no form (the report names the nearest form and its
   error), where a call fires both before and after `h`, or where on the ticks `h` does not fire
   the group moves the cell beyond the tolerance (the cell then takes the group's input, or the
   note where nothing else fires). A group is unfit where every plan is refused, another
   modulator fires between its slots or one of its templates fires on two cells. Templates in no
   group are fitted per call as before.
4. **Template**: per code template (or per host slot's calls), the cheaper (in tokens) of one
   form key fitting all its calls and each call's cheapest form, grouped by key. Each group is
   one expression template named by its shape and base (`ramp`, `vib`, `heldpitchseq`), the
   least count after the second, clear of the document's records, tables and state variables.
   Its parameters are its shape's natural ones (every one but a table's `z`, and those at
   their neutral value, 0 for `q`, `o` and `k` and 1 for a `first`'s `l`, in every call),
   whether or not its calls differ in them, plus any value its calls differ in, named by role
   and, where a role's name is taken, by an alternate (`step`, `inc`, `delta`, then `step2`);
   a parameter that is an integer multiple of another in every call is written as that
   multiple, and the rest are literal (`vib(0,4,32)`, `vib(0,4,64)` → `stepsq(32,2)`,
   `stepsq(64,2)` of `stepsq(step,half):v - 2*(phi // half % 2)*step + step`). A form that
   keeps its input (`keep` on `v`) ends its call's binding (`none`). A table form's tables are
   one new table, each call's offset `k`.
5. **Prove**: each replacement (a code template's, or a group's best plan) is screened alone for
   size, then the replacements are proved together and, refused, in halves, as the driver's
   edits: kept where the document (its replaced code templates no call names any more dropped,
   with the state variables and tables nothing reads) shrinks and the check holds. A refused
   group tries its other plans, most tokens saved first, then its templates' own replacements;
   an unfit group's templates offer theirs at once. The templates kept are then named in order
   from their stems.

Parameters stay parameters even where one call uses them (`ramp(32)`, not `ramp():v + 32`):
the output is material for a reader, or a language model, learning the format's vocabulary,
and a shape called with its arguments says what it does and is reused as it stands; the cost is
a few tokens per template.

The check plays the candidate over the same horizon: every register outside the targeted cells
(the frequency, pulse width or cutoff of each voice a replaced call fired on) must equal the
input's, and each targeted cell's error over the ticks a replaced call fired on, and every tick
it differs, must keep to its tolerance at its 95th percentile and maximum:

| cell | error | default `P95,MAX` |
|---|---|---|
| `freq` | cents, infinite where exactly one of the two is 0 | `10,25` |
| `pw` | register units (12 bits), or percent of the input's value with a `%` suffix | `32,128` |
| `cut` | register units (11 bits), or percent as for `pw` | `16,64` |

The report (stderr) gives, per replaced code template or group, its calls, the templates they
became (`none` for an ended binding), the tokens it saves alone and its fitted error (p50, p95,
max), each refused one, each unfit group with why each of its plans was refused, each targeted
cell's error and the size before and after.

A group whose output depends on more than its host's calls and phi (a vibrato whose phase a note
resets while its command's binding runs on, a slot the player holds for ticks before each note
under a vibrato) fits no plan within a tight maximum, and its templates keep their per-call
replacements where those hold. The guarantee covers the horizon only: a form fitted to the phi
a song reaches may differ past it.

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
| `single` | every definition referenced exactly once, anonymously: an instrument, a phrase, a `restart`, `ctrl`, `pw` or `filter` record, a section (its items in place in the song or the section that plays it, the loop item kept); shared ones and templates stay named |
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
| a song item `s1`, `s1x2` | its items, `[...]x2` |

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
picking a simpler form, noise refused at a tight tolerance and kept at a loose one, two routines
sharing state fitted jointly with their state dropped, an unfit group and its templates' own
replacements refused, another slot firing inside a group, a refused group's other plans and
fallback, shape and parameter names, the parameter policy, each shape fitted, and the CLIs.
`tests/test_optimiser_idioms.py`: the `transpose` and `snap` passes, the `names` rename and the
`defaults` pass's instrument `none`s and empty rows on hand-written documents (folded and kept,
refused by size or by the guarantee) and every pass on lifted documents of the four direct
lifters; `python tools/passes_eval.py DOC.yaml ...` tabulates the tokens each pass saves over
each song's length and lists the kept edits.
`tests/test_optimiser.py`: each pass on the hand-written documents of `tests/optimiser_docs.py`,
kept and refused; the guarantee's horizons; `tests/test_inline.py` each inline mode on a
hand-written document and on lifted documents of the four direct lifters; the CLI; and, for
each `.tunecache/direct/*.yaml`
(or `$UNITRACKER_DIRECT_DOCS`), a lifted document optimised over 3000 ticks. Those are written by
the GoatTracker 2 transcriber (`python -m unitracker.direct.goattracker2 TUNE.sid > DOC.yaml`, on
the `gt2-direct` branch).
