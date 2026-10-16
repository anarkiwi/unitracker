# Modulation fitting (`unitracker.modfit`)

`unitracker-modfit` reads modulation procs straight from the playroutine library
(`src/unitracker/playroutines/*.yaml`) and tries to rewrite each one as a compact
bounded accumulator (see *The bounded accumulator* in [format.md](format.md)). It proves
every rewrite it reports, or shows where none holds.

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

   Byte pairs are found from the structure of the playroutine: either a joined
   `(hi<<8)|lo`, or dataflow into the lo/hi halves of one SID ghost register pair. Each
   pair is fused into one 16-bit update.
2. **Arithmetic form** (`arith.Arith`). Each written cell becomes
   `x + Σ [G_i]·(u_i − x)`, a multilinear polynomial in the brackets that does not depend
   on the order of the tests. Brackets are idempotent, infeasible products are dropped
   and implied brackets are absorbed. Equivalent or complementary conditions share one
   atom, and each atom's polarity is chosen to give fewer monomials.
3. **One-tick fits** (`fit.Fitter`). For each modulated cell and dispatch guard, candidate
   `set`/`accumulate` actions are built from the cell's own cases: steps, thresholds and
   reload values. The forms are plain, `min`/`max` clamp, `on_bound` reload and `toward`.
   A candidate is accepted only when z3 shows that every pair of original and candidate
   paths gives the cell identical values under the guard. Otherwise `witness` returns a
   counterexample.
4. **Orbits** (`orbit`). For multi-tick rewrites such as vibrato, the proc is run
   concretely, once per period value, with record data and constant tables folded in.
   Each per-tick delta is wrapped to the cell's width as a signed value. The cumulative
   offsets are then compared with an idealised ping-pong: one step size, reversing only
   at the bounds, and turning at both bounds.

## Assumptions

- A record field is a byte (0..255) unless its default is negative.
- A cell with no `width` is scratch. Scratch cells are resolved within their action list
  and are never reported as updates.
- z3 works on 48-bit two's-complement bit-vectors. Playroutine values stay far inside
  this range, so bit-vector arithmetic, masks, shifts and signed comparisons agree with
  the unbounded integers.

## Run

```sh
unitracker-modfit fit [PLAYROUTINE:PATH:CELL ...]    # default: every fit in modfit/specs.yaml
unitracker-modfit poly PLAYROUTINE:PATH ...          # arithmetic form by bracket monomial
unitracker-modfit orbit [PLAYROUTINE ...] [--ticks N]
unitracker-modfit --out report.txt fit               # write the report to a file
```

`src/unitracker/modfit/specs.yaml` lists the modulations to fit and the orbit specs. An
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
| SID-Wizard `procs.tick_porta` → `voff` | no single accumulator |
| SID-Wizard `procs.tick_vibrato` → `vfc` | no single accumulator |

| Vibrato orbit | Ping-pong |
| --- | --- |
| GoatTracker, period `p` in 0..125 | bounds `[−((p+1)//2+1), p//2+1]`, first step +1 |
| GoatTracker, periods 126, 127 | none: a constant slide |
| SID-Wizard, even period `p` | bounds `[−p/2, 0]`, first step −1 |
| SID-Wizard, odd period ≥ 3 | none: drifts (runs −1, +1, +1 for period 3) |
| SID-Wizard, period 1 | none: a constant slide |

Not expressible yet:

- The accumulator has no reflect bound mode, so a vibrato ping-pong has no
  single-action form.
- SID-Wizard's porta compares a signed 16-bit view (`s16(voff)`), and the accumulator
  has no signed compare.
- defMON's pulse bounds depend on the high byte (`(pw >> 8) == 0x0F`).
- SID-Wizard's `vfc` counter rewrite holds only on reachable states (`vfc ≤ vper`). The
  witnesses fall outside them.
