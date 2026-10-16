# Modulation fitting (`unitracker.modfit`)

`unitracker-modfit` reads modulation procs straight from the playroutine library
(`src/unitracker/playroutines/*.yaml`) and tries to rewrite each one as a compact
bounded accumulator (see *The bounded accumulator* in [format.md](format.md)). It proves
every rewrite it reports, or shows where none holds. It also composes each keyword's
setup and per-tick effect, and matches keywords and record fields across playroutines
by what they do to the SID.

## Method

1. **Guarded cases** (`form.Form`). The action language is evaluated symbolically.
   Sequential sets wrap to the cell width, expressions are unbounded integers, and
   `accumulate` expands exactly as `vm._acc`. `Form.paths` returns the guarded cases: a
   path condition plus each cell's final term. z3 prunes infeasible paths. Terms are
   normalised as linear forms, Iverson brackets and interval ranges:
   - masks are dropped when the range is already covered;
   - wraps are reduced modulo the width, and carries are removed when the guard pins them;
   - `256·((E>>8)&255) + (E&255)` folds to `E`;
   - comparisons are canonicalised to `lt(E,0)` / `eq(E,0)`.

   Cell pairs `(lo, hi, k)`, meaning `P = hi·2^k + (lo & (2^k−1))`, come from the
   structure of the playroutine: a joined `(hi<<k)|lo` or `(hi<<k)|(lo&mask)` for any
   constant `k`, or dataflow into the low and high register of one SID register pair
   (`$00/$01`, `$02/$03`, `$15/$16`). For the dataflow case, `k` is the number of bits any
   write ever gives `lo`, read from the masks and shifts of the bit dataflow (step 5). A
   pair is kept only when `lo` never holds more than `k` bits. A pair's joint update is
   fused into one `(k + width(hi))`-bit update `P + D` only when `D` is proved free of the
   pair's parts; a fit on a pair cell runs on the fused paths.
2. **Arithmetic form** (`arith.Arith`). Each written cell becomes
   `x + Σ [G_i]·(u_i − x)`, a multilinear polynomial in the brackets that does not depend
   on the order of the tests. Brackets are idempotent, infeasible products are dropped
   and implied brackets are absorbed. Equivalent or complementary conditions share one
   atom, and each atom's polarity is chosen to give fewer monomials.
3. **One-tick fits** (`fit.Fitter`). For each modulated cell and dispatch guard, candidate
   `set`/`accumulate` actions are built from the cell's own cases: steps, thresholds and
   reload values. The forms are plain, `min`/`max` clamp, `on_bound` reload and `toward`,
   each also `signed` when the cell has a width.
   A candidate is accepted only when z3 shows that every pair of original and candidate
   paths gives the cell identical values under the guard. Otherwise `witness` returns a
   counterexample.
4. **Orbits** (`orbit`). For multi-tick rewrites such as vibrato, the proc is run
   concretely, once per period value, with record data and constant tables folded in.
   Each per-tick delta is wrapped to the cell's width as a signed value. A ping-pong of
   the cumulative offsets (one step size, reversing only at both bounds) gives the bounds
   and first step of a candidate `clamp: reflect` accumulator, its direction a fresh 1-bit
   cell flipped in `on_bound` (and `signed` when the bounds leave the unsigned range). The
   candidate is run concretely and accepted only when its per-tick deltas equal the
   original's over the whole run.

5. **Bit dataflow and register roles** (`flow.Flow`). Every assignment is read as a bit
   relation from each source name to the result bits each source bit can reach.
   - Shifts and masks move bits; `x | c` cannot reach the bits `c` sets.
   - A carry spreads upwards, except in a sum of operands with disjoint bits.
   - A table, record index or test steers every bit of the value it selects.
   - Temporaries come from def-before-use summaries: a widthless cell never read
     before its own list assigns it.

   The relations are composed to a fixpoint into the ghost registers, giving each
   cell, field and event cell its *role*: the SID quantity bits it can reach. Quantities
   are `FREQ`, `PW` 12-bit, `CUT` 11-bit (`$D415` bits 0–2, `$D416` bits 3–10), `CTRL`
   bits, `AD`/`SR` nibbles, `RESFILT` (`$D417`) and `MODEVOL` (`$D418`). A cell that can
   steer a test guarding a `fetch`, through data or the branches the test guards, also
   has the role `TIMING`.
6. **Inductive invariants** (`invariant`). When a fit is refuted by a witness, the
   candidates are comparisons `cell op T`. `T` is a bound or reload term of the cell's
   cases, a term compared with a wrapped expression of the cell, 0 or the width maximum.
   A candidate is accepted when z3 proves two things. First, it holds at the cells'
   initial values. Second, it holds after every action list that assigns one of its
   cells (procs, templates, record lists, `init`), starting from any state that satisfies
   it. Each such list is sliced to the actions that can affect the candidate's cells. A
   nested `for_voices`, `run` or `fetch` havocs what it can write: the invariant is proved
   before it and assumed after it. The refuted fit is then re-proved under each accepted
   invariant (candidates from every case of the cell), and then under all of them
   together.
7. **Keyword composition** (`compose.Composer`). `specs.yaml` `commands:` gives, per
   playroutine:
   - the setup template (`CODE`, `arg`);
   - the tick lists;
   - the routing cells, which are left out of the report;
   - a row template without a note, and one with a note.

   The keywords are the playroutine's own `commands:` map, and a `when` is assumed
   true. The setup is evaluated with branches joined: each cell becomes one `ite` term,
   and a `return` clears a live condition that later assignments respect. Each `ite`
   side is simplified under its condition. A tick list joins when it reads a cell the
   setup changes. Cells the ticks change are freed (later ticks), the rest keep their
   setup values. Each freed cell is fitted with `fit.Fitter`, on its tick term split at
   the branch joins. On a row with a note, cells appear only where they can differ from
   the same row with no command. *Routed tests* are the tests outside the setup's procs
   that read a routing cell and whose value the keyword changes.
8. **Signatures** (`keywords.Signer`). A keyword's or field's signature is a set of
   `(role, kind, argument use)`:
   - `LOAD` (the arg's bits reaching the role), `RESET` (a constant);
   - `ARM`: a constant the ticks read, armed for per-tick behaviour;
   - `MODE`: a cell the row procs test, a mode for later notes;
   - `PTR`: a pointer into a chained record type, a program start;
   - `REF`: a declared reference to an unchained record type;
   - per tick: `ACC+`/`ACC-`/`ACC±` (the step's provable sign), `TOWARD` and `LOAD` from
     the fits, `MOD` when no single accumulator holds, and `REFLECT` when the orbit spec's
     proc runs and the fitted step turns (several steps, or one reading a cell the tick
     moves);
   - `TEST`: a field read by a test (not matched).

   Argument use is the arg's bit mapping into the role, `ref <type>`, `note-relative` (a
   table index that also reads the event note) or `table`. Per-tick effects steered by a
   started program's pointer are the program's, and are left out. Matches come from the
   other playroutines, by class:
   - *same effect*: identical signature;
   - *same effect, different argument decoding*: same roles and kinds;
   - *partial*: entries of the same kind over an intersecting quantity;
   - *different*: nothing shared, or only tests.

   A keyword with an identical signature in its own playroutine is a duplicate. It is
   *partial* when the two differ on a row with a note (setup cells or routed tests).
   Reasons name the differing entries, a *sticky override* and a *mode for later notes*. A
   sticky override is an arg load folded by a setup proc into another loaded cell, where
   that proc also runs outside the setup.

## Assumptions

- A record field is a byte (0..255) unless its default is negative.
- A cell with no `width` is scratch to `Form`: it is resolved within its action list and
  never reported as an update by `fit`. Composition reports every cell whose value can
  carry to the next tick or reach the ghost.
- Composition runs `for_voices` once for the current voice, takes `fetch` cells from the
  row template, and assumes a template's record row exists.
- z3 works on 48-bit two's-complement bit-vectors. Playroutine values stay far inside
  this range, so bit-vector arithmetic, masks, shifts and signed comparisons agree with
  the unbounded integers.

## Run

```sh
unitracker-modfit fit [PLAYROUTINE:PATH:CELL ...]    # default: every fit and check in modfit/specs.yaml
unitracker-modfit poly PLAYROUTINE:PATH ...          # arithmetic form by bracket monomial
unitracker-modfit orbit [PLAYROUTINE ...] [--ticks N]
unitracker-modfit command [PLAYROUTINE[:NAME] ...]   # keyword setup and per-tick effects
unitracker-modfit keywords [PLAYROUTINE ...]         # signature table with matches and verdicts
unitracker-modfit --out out/modfit/keywords.txt keywords
```

A pair cell is named `hi:lo` (`k = 8`) or `hi:lo:k`, e.g. `sidwizard:procs.tick_filt:vfhi:vflo:3`.

`src/unitracker/modfit/specs.yaml` lists the modulations to fit, the given candidates to
check (proved, or refuted with a witness) and the orbit specs. An
orbit spec gives the period cell or `record.index.field`, the period range, the initial
state and the record rows.

## Results

| Modulation | Rewrite |
| --- | --- |
| GT `record_types.pulsetab.each` → `pulse` | `accumulate`, step `s8(row.r)` |
| GT `records.tickn.1.do` → `freq` | `accumulate`, step the slide speed |
| GT `procs.toneporta` → `freq` | `toward ftab[note]` when `arg≠0`; `set ftab[note]` when `arg=0` |
| defMON `procs.pitch` → `sacc` | `accumulate`, signed `rate(mode)` |
| defMON `procs.pulse` → `pw` | no single accumulator |
| SID-Wizard `procs.tick_porta` → `voff` | no single accumulator; see below |
| SID-Wizard `procs.tick_vibrato` → `vfc` | `accumulate`, step −1 (`255`), `max: (vper−1)&255`, proved under the invariant `vfc <= (vper−1)&255` |
| GT `record_types.filttab.each` → `filtercutoff` | `accumulate`, step `row.r` |
| SID-Wizard `procs.tick_pw` → `pwhi:pwlo` | `accumulate`, step `s8(b)` as `b` or `b − 256` |
| SID-Wizard `procs.tick_filt` → `vfhi:vflo:3` | 11-bit `accumulate`, step `s8(flttab[fpos].b)`; set rows `8·b` |
| defMON `procs.acid` → `acc` | refuted under the invariant `acc < 32768` (the reload reads the stepped cell) |
| defMON `procs.acid` → `acc`, given | `accumulate`, `max: 0x7FFF`, `clamp: false`, `on_bound: acc := 512 + (acc & 255)`: proved |

Each vibrato below is a single reflect accumulator, verified against the original over
the full orbit (`D` is the spec's unit depth, `vd` a fresh 1-bit direction cell):

```yaml
accumulate: CELL
step: "D if vd == 0 else 0 - D"
min: start + lo
max: start + hi
clamp: reflect
on_bound: [{set: {vd: "vd ^ 1"}}]
```

| Vibrato orbit | Reflect accumulator |
| --- | --- |
| GoatTracker, period `p` in 0..125 | bounds `[−((p+1)//2+1), p//2+1]`, first step +1 |
| GoatTracker, periods 126, 127 | none: a constant slide |
| SID-Wizard, even period `p` | bounds `[−p/2, 0]`, first step −1, `signed` |
| SID-Wizard, odd period ≥ 3 | none: drifts (runs −1, +1, +1 for period 3) |
| SID-Wizard, period 1 | none: a constant slide |

SID-Wizard's porta against `accumulate voff, step ((fmh << 8) | fml) + 1, toward 0,
signed: true` is refuted. With a depth `F = (fmh << 8) | fml` below `0x8000` it is proved
everywhere except `s16(voff) + F == 0`: approaching 0 from below, the original
overshoots to `+1` where the accumulator clamps to 0. With `F ≥ 0x8000` the step wraps
past the signed range and the two also differ.

Not expressible yet:

- defMON's pulse bounds depend on the high byte (`(pw >> 8) == 0x0F`).
- defMON's ACID reload `(floor << 8) | (acc & 0xFF)` reads the stepped cell; the candidate
  reloads are cell-free, so the fit is refuted even under `acc < 32768`.

| Pair | Source |
| --- | --- |
| `vfhi:vflo:3`, `fcuthi:fcutlo:3` | SID-Wizard: `(vfhi << 3) \| (vflo & 7)`; `fcutlo` reaches only `$D415`, written `t3 & 7` |
| all 8:8 pairs | unchanged |

| Keyword composition | Effect |
| --- | --- |
| SID-Wizard `!pitchup` / `!pitchdown` | `svib := $81`/`$82`; per tick `voff ± fm(arg, note+oct)` when `ci ≠ 0`, 0 when `arg = 0` |
| SID-Wizard `!attack` | `aov := arg`, `dov := −1`, `sad := (arg << 4) \| (ins[ci].ad & 15)` when `ci ≠ 0` |
| SID-Wizard `!iattack` … `!irelease` | setup and ticks equal the plain small FX on a row without a note |
| SID-Wizard `!iattack` on a note row | `note_setup` test `… and pifx != 0` sets `carry` |
| GoatTracker `!ad` | `ad := arg` |
| GoatTracker `!porta` | per tick `toward ftab[note]` at `spd(arg)`; `set ftab[note]` when `arg = 0` |
| defMON `!run2` | `rowB := arg`, `cntB := 0`; per tick `layer_b` runs the sidTAB row |

The keyword and field table is written by `unitracker-modfit keywords` (one row per keyword
or field: signature, matches, verdict, reason).
