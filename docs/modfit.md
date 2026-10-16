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
   counterexample. For a fused pair, a condition on either part (a carry) is dynamic, not a
   dispatch guard. Two plain accumulates whose guards differ in one complementary test join
   into one whose step selects on that test, when that is proved.
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

   The relations are composed to a fixpoint into the ghost registers, giving the byte
   pairs below and the `TIMING` role: a cell that can steer a test guarding a `fetch`,
   through data or the branches the test guards.

   A cell's or field's *role* (the SID quantity bits its value reaches) comes from the
   composed `init` and `tick` (`flow.Composed`): calls are inlined with their `with`
   bindings and every cell is resolved along its path, so a parameter or scratch cell
   carries only what each call site gives it. Tests steer nothing (a phase, carry or mode
   cell only gates), and a pointer into a chained record selects control, not a value.
   Quantities are `FREQ`, `PW` 12-bit, `CUT` 11-bit (`$D415` bits 0–2, `$D416` bits 3–10),
   `CTRL` bits, `AD`/`SR` nibbles, `RESFILT` (`$D417`) and `MODEVOL` (`$D418`). Bits that
   reach a register only through `&` with another cell, or `|`/`^` over bits both can set
   (cell bit ranges from inductive invariants), are a `MASK`.
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
   - `LOAD` (the arg's bits reaching the role), `MASK` (see step 5), `RESET` (a constant);
   - `ARM`: a constant the ticks read, armed for per-tick behaviour;
   - `MODE`: a cell the row procs test, a mode for later notes;
   - `PTR`: a pointer into a chained record type, a program start;
   - `REF`: a declared reference to an unchained record type;
   - per tick: `ACC+`/`ACC-`/`ACC±` (the step's provable sign), `TOWARD` and `LOAD` from
     the fits, `MOD` when no single accumulator holds, and `REFLECT` when the orbit spec's
     proc runs and the fitted step turns (several steps, or one reading a cell the tick
     moves);
   - `TEST`: a field read by a test or an `ite` test (not matched).

   A field's entries are the cells the composed lists give it, merged by kind.

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

9. **Generic accumulator** (`factor.Factorer`). Every effect on `x` is
   `x' = respond(v0, v)` when `hit(v)`, else `v = v0 + step`, applied under `gate`. A
   cell's guarded cases (cut to what can affect it, pairs fused, tests simplified on their
   own and split into conjuncts) are split at the brackets reading `x` or a moved cell.
   Conditions free of `x` are the gate, brackets reading it the hit; the change most cases
   share is the step, the values under the hit the respond: `hold`, `clamp` (a bound the
   hit compares with), `reflect` (`v0 − s`), `reload`, `bounce` (a constant and a flipped
   mode cell) or `reads v`. Gates differing only in a test of bound cells join with a
   selecting step. The lambdas are source over `v0`, `v` and `s`, re-read and proved equal
   to the original over all path pairs (`Fitter.check_paths`). Otherwise the reason is
   `step reads x`, `hit reads non-bound state` or `respond outside (v0, v, bounds)` (a cell
   that changes on steady ticks), or a witness. The step's *basis* is `constant`,
   `note-relative` tables (an index reading a note cell), `decoded` tables, fields and
   the argument, or the cells it reads.
10. **Timers** (`timer.Timers`). Every list writing a cell (except `init`) is analysed
   on its own; calls, runs and fetches leave fresh values for what they write. A timer's
   cases are proved `t ± c` (one `c`), a reload or reset free of `t`, or a flip `k − t`.
   A reload reading a cell the same path flips is an alternating reload. An orbit steps the
   timer through its counting list's cases with z3. A non-constant bound of its tests is
   the parameter, starting from the reset; otherwise the start value is. A model of a
   counting case fixes the other atoms, and a run stops at a firing case that reads any of
   them. Durations to the first fire and each test's sequence are classified: `constant`,
   `delay d`, `duration n`, `period p` (two equal runs per cycle: a sign flip), or `drift`
   (the run lengths). Their numbers are fitted exactly as `(a·p + b)//k` over maximal
   runs of the domain (least `k`, at most the point count), with more than three points;
   otherwise a table. Orbit specs' per-tick steps give the vibrato signatures the same way.
11. **Bounce orbits** (`bounce.Bounce`). A mode cell flipped `q ^ k` in a case leaves the
   parameter `m = q & ~k`. For each flip bit and each `m`, z3 finds the non-hit step (one some
   value takes, of one sign for every input) or proves the cell constant. Over all `m` of a
   step at once it then proves, for the first threshold `B` (a hit test's constant at each
   bracket value, or a width edge) and reload that hold: `v0 + s` within `B` steps the cell,
   past `B` it reloads and flips the mode, and beyond `B` the cell wraps with no hit. A phase
   from `x` lasts `|B − x|//|s| + 1` ticks and turns at `B ∓ |B − x| % |s|`, so two opposite
   phases give the cycle's period and bounds. The cycle is also stepped through the guarded
   cases for every `m` and input value and must recur first after the period, within the
   bounds.
12. **Table programs** (`sweeps`). A spec names a record type, the list that steps it, the
   swept cell, the program pointer and the control state at a start. The list is cut to what
   can affect the swept cell, pointer and control. A tick runs it on the concrete control state
   over the song's records (missing rows and fields at their defaults, templates run with
   `row` bound) with the swept cell symbolic: every path must give one op, `set c` or
   `add d` (wrapped, pairs fused), and one concrete control state. The control state recurs.
   From the cycle's set row (else the prefix's last one) the ticks that change the value are
   matched to one accumulator: a reload closing a sawtooth, a ping-pong (`clamp: reflect`), an
   endless sweep (wrap) or a sweep that stops (clamp at its last value). The candidate runs
   through `vm._acc` and must give every changing tick's value, and a bounded candidate must
   recur with the cycle. Otherwise the reason names the step sizes, the turns inside the
   bounds, the set rows in the cycle, or a value the program never sets. Starts are reference
   fields (with `when`) and command arguments.
13. **Report** (`generic`). One row per keyword (its composed per-tick effect's gate
   groups, or its setup) and per modulation gate group (a record row kind). Each row gives
   role, step, basis, hit, respond, gate and status. The gate is the timers it reads with
   their duration's closed form, else the lifetime: `until the next note` when a note row
   resets an armed cell, `until replaced`, or `row duration`. Rows are grouped by
   (role, respond kind, temporal kind).

14. **Phase and value expressions** (`phase`, `grammar`, `replay`, `exprs`). A modulator is
   a pair of integer expressions applied per firing on the state before it:
   `φ' = φ + a(v, φ)` and `v' = f(v, φ)`, with `φ = 0` and `v` the cell's incoming value at
   its start. The grammar is integers, `+ - * // % & | ^`, `abs`, `min`, `max`, Iverson
   brackets, `v`, `φ`, `N` (the voice's sounding note), `F(k)` (the player's frequency of
   format note `k`), player tables `name[k]` and other modulators by name; shifts and
   all-ones masks fold into `*`, `//` and `%`.
   - A unit (`specs.yaml` `exprs:`) names the modulated cell and the action list of one
     firing. The list is sliced to the cell; the cells it reads before writing them and the
     cells it writes that are needed again are the control.
   - From a start's concrete control (note cells as `N`) every firing runs through `Form` with
     the value symbolic; the reachable control states and their guarded cases are a graph,
     minimised by bisimulation (equal value updates, equal guarded successor classes). One
     successor per class makes `φ` the elapsed firings (`a = 1`, a prefix and a cycle); a
     class that stays until a test of `v` advances it makes `φ` count turns (`a` is that test).
     Anything else needs more than one phase and is reported.
   - Each class's cases become one expression: a decision tree on its tests, sides a test of
     their difference chooses joined as `min`/`max`, regions with the comparisons of one linear
     form met as intervals. The value update is the shortest (tokens, then characters, then
     text) of the position-indexed form and, when every class adds or sets, the add form;
     cycles are rotated to start at a change. The player's frequency words fold to `F`, and
     SID-Wizard's speed functions read the table `W` (`exptab` below 107, then frequencies),
     checked equal to `fmlo`/`fmhi` on every argument byte.
   - A closed form `g(φ)` (the value after the firing at `φ`) is built when the start value
     is known (a set row, or a value the start hook writes) and every class adds or sets: a
     window ramp `Σ d·min(max(x − s, 0), L)` over the cycle from a turning value, with its
     drift, gated `[φ >= d]` after a constant delay, also rewritten through `abs`; it is
     wrapped only when its range leaves the cell.
   - Display is deterministic from the canonical (expanded) form: brackets are split in sort
     order (`b·X + Y`, `(1 − b)·(−X) + (X + Y)`, `b·(X + Y) + (1 − b)·Y`), implied
     brackets dropped under the split's context and the cell's range, nested brackets split.
   - z3 proves, over 48-bit integers: for every reachable control state and every one of its
     paths, `f` and `a` at that state's phase equal the player's value and advance;
     `f(v, φ + P) = f(v, φ)` and the same for `a` from the prefix on; `g(0) = f(v0, 0)` and
     `g(φ + 1) = f(g(φ), φ + 1)`.
   - The song is replayed with the unit's firing functions and start hooks wrapped in the
     compiled player. A hook restarts the unit when it writes `written` cells, changes `key`,
     or writes a `pointer` to a program that sets one of the unit's fields; the hook is run
     again symbolically with the note as `N` for the start's terms (amplitudes, start values).
     Each firing applies the expressions and compares them with the native cell; a write of
     the cell between firings is reported. A second replay drives every modulator's cells with
     its expressions and its register grid is compared with the native player's.
   - Occurrences group by their document form. A form whose starts give more than one
     expression needs state that is no phase; the start cells that decide the expression are
     reported. Equal canonical texts across forms and players, and texts equal up to their
     constants, are listed. The token count rewrites each proved form as an `x` record
     `{a, v}`, deduplicated by text, named by its commands, programs and instrument fields.
   - Stages run in one process pool (`--workers`, default the host's cores, two BLAS threads
     each): per song the native grid and the start-collecting replay, then start keys in
     chunks, then each distinct start's derivation and proofs, then one driven replay per song.
     Lifted texts, native grids and collected starts are cached in `out/modfit/cache/` under
     the sha256 of the tune, the playroutine library and the code they depend on.

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
unitracker-modfit generic [PLAYROUTINE ...] [--ticks N]  # factorised events, grouping, timers, vibrato
unitracker-modfit --out out/modfit/generic.txt generic
unitracker-modfit sweeps [SONG ...]                  # bounce orbits, fused pair fits, table programs
unitracker-modfit exprs [SONG ...] [--ticks N]       # phase and value expressions, proved and replayed
                                                     # (writes out/modfit/exprs.txt)
unitracker-modfit --out out/modfit/sweeps.txt sweeps \
  .tunecache/MUSICIANS/C/Cadaver/Metal_Warrior_Title_Remix.sid \
  .tunecache/MUSICIANS/H/Hermit/Csillag_szazketto_kettoskereszt.sid
```

A song is a unitracker YAML or a tune its backend lifts.

A pair cell is named `hi:lo` (`k = 8`) or `hi:lo:k`, e.g. `sidwizard:procs.tick_filt:vfhi:vflo:3`.

`src/unitracker/modfit/specs.yaml` lists the modulations to fit, the given candidates to
check (proved, or refuted with a witness, under an optional guard), the orbit specs, the
bounce units and the sweep specs. An orbit spec gives the period cell or
`record.index.field`, the period range, the initial state and the record rows.

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
| SID-Wizard `procs.tick_pw` → `pwhi:pwlo` | one fused 16-bit `accumulate` over both carries, step `b + 65280·[b ≥ 128]`; `s8(b)` proved under the sweep guard |
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

- defMON's pulse bounds depend on the high byte (`(pw >> 8) == 0x0F`); its bounce orbit is
  below.
- defMON's ACID reload `(floor << 8) | (acc & 0xFF)` reads the stepped cell; the candidate
  reloads are cell-free, so the fit is refuted even under `acc < 32768`.

| Pair | Source |
| --- | --- |
| `vfhi:vflo:3`, `fcuthi:fcutlo:3` | SID-Wizard: `(vfhi << 3) \| (vflo & 7)`; `fcutlo` reaches only `$D415`, written `t3 & 7` |
| all 8:8 pairs | unchanged |

| Keyword composition | Effect |
| --- | --- |
| SID-Wizard big FX $01 / $02 (`!mod`) | `svib := $81`/`$82`; per tick `voff ± fm(arg, note+oct)` when `ci ≠ 0`, 0 when `arg = 0` |
| SID-Wizard `!attack` | `aov := arg`, `dov := −1`, `sad := (arg << 4) \| (ins[ci].ad & 15)` when `ci ≠ 0` |
| SID-Wizard `!iattack` … `!irelease` | setup and ticks equal the plain small FX on a row without a note |
| SID-Wizard `!iattack` on a note row | `note_setup` test `… and pifx != 0` sets `carry` |
| GoatTracker `!ad` | `ad := arg` |
| GoatTracker command 3 (`!mod`) | per tick `toward ftab[note]` at `spd(arg)`; `set ftab[note]` when `arg = 0` |
| defMON `!run2` | `rowB := arg`, `cntB := 0`; per tick `layer_b` runs the sidTAB row |

The keyword and field table is written by `unitracker-modfit keywords` (one row per keyword
or field: signature, matches, verdict, reason).

### Factorisation

| Effect | Step, basis | Hit, respond | Status |
| --- | --- | --- | --- |
| GT slide (`tickn.1`, command 1) | `spd(arg)`: note-relative `ftab`, decoded `speedtab` | none | proved |
| GT porta (`toneporta`, arg ≠ 0) | `spd(arg)` | toward `ftab[note]`: hold, clamp, reflect | proved |
| GT porta, arg = 0 | 0 | always: reload `ftab[note]` | proved |
| GT pulse, filter table step | `s8(row.r)`, `row.r` | none | proved |
| SW big FX $01 / $02 | `±fm(arg, note+oct)`: note-relative `exptab`, `freqlo`, `freqhi` | none | proved |
| SW big FX $03, `tick_porta` | `F + 1` | overshoot to +1 from below, 0 from above: hold, clamp, reflect | proved |
| SW pulse sweep `pwhi:pwlo` | `s8(pwtab.b)` (set rows: reload) | none | proved |
| SW filter sweep `vfhi:vflo:3` | `s8(flttab.b)` (set rows: reload `8·b`) | none | proved |
| defMON slide (`pitch` `sacc`) | `±rate(mode)`: decoded `rate_lo`, `rate_hi` | none | proved |
| defMON pulse `ps` | `ps & 0x7F`, `−ps − (1 − fc)` | `v` past `0x0FFF`/`0xFFFF` by `pw >> 8`: bounce | proved |
| defMON ACID | `astep` or `−astep − 1` | `v > 0x7FFF`: reads v, `512 + (v & 255)` | proved |

### Timers

A timer *counts n ticks* from its start (or reset, with a bound `p`) until a tick that does
not count it.

| Timer | Update | Counts n ticks |
| --- | --- | --- |
| GT `ptime`, `ftime` | −1, reload `l` | `p + 1` from start `p` |
| GT `tick` | −1, reload `tempo`, or `funk1`/`funk0` alternating on `tempo` | `p + 1` for `p` ≤ 128 |
| GT `vibtime` | +2, flip `1 − t` | `(p + 4)//2` for bound `l = p` in 0..125 |
| GT `vibdelay` | −1 while > 1 | `(254·p + 255)//255` (`max(p, 1)`) |
| GT `wtime` | +1, reset 0 | `p + 1` for `w = p` in 0..15 |
| defMON `cnt` | −1, reload `dl` | `p + 1` for `p` in 0..127, 1 from `0x80` (frozen) |
| SW `pwcnt`, `fsweep` | +1, reset 0 | `p + 1` for `a = p` in 0..127 |
| SW `sc` | +1, reset 0 | `p` for tempo `p` ≥ 2 |
| SW `arpcnt` | −1, reload `rw`, reset 255 | `p + 1` for `p` ≤ 128 |
| SW `vdc` | −1 while ≥ 0 | `p + 2`; the vibrato's `vdc >= 0` lasts `p + 1` |
| SW `vfc` | −1, reload `vper − 1` | `p + 1` |
| SW `hr` | −1, reset 1 | `p + 1` |

| Vibrato orbit | Signature |
| --- | --- |
| GT, `p` in 0..125 | period, bounds `[(−p − 2)//2, (p + 2)//2]` |
| SW, even `p` | period, bounds `[(−p)//2, 0]` |
| SW, odd `p` ≥ 3 | drift, runs `(p − 1)//2` down, `(p + 1)//2` up |

defMON's pulse sweep, `m = ps & 127`, `d = 1 − fc + m`, proved for every `m` and stepped for
every `m` and `fc`:

| Mode | Phase |
| --- | --- |
| `ps ≥ 128`, `m` in 1..127 | `+m` while `v ≤ 0x0FFF`, else `pw := 0x0FF8`, down; from `x ≤ 0x0FFF`: `(4095 − x)//m + 1` ticks; above, wraps at `0x10000` first |
| `ps < 128`, `m` in 1..127 | `−d` while `v ≥ 0`, else `pw := 1`, up; from `x`: `x//d + 1` ticks |
| `ps` 0 or 128 | constant |
| cycle | period `4094//m + 4088//d + 2`, bounds `[min(1, 4088 % d), max(4088, 4095 − 4094 % m)]` |

`fc` (the pitch carry) is read per tick: with it held the forms are exact, otherwise a phase
lasts between its forms at `fc = 0` and `fc = 1`.

Phase and value expressions (`exprs.txt`, the three conformance songs over their conformance
tick counts): every modulator's cells driven by its expressions give each native register
grid exactly. Every form has one proved expression except four, whose expression depends on
state that is neither a phase nor a modulator's value: defMON `t6` acid (the `sub` direction
and `astep` left by the previous program), SID-Wizard `!legato:0` glides (the low speed byte
`fml` left by the previous effect), `!notevib:8` (the vibrato delay `vdc` left by the note's
instrument vibrato) and the glide after `!noteglide:0` (a speed from the previous note).
Examples:

| Form | `a; f` (closed form where the start value is known) |
| --- | --- |
| GT `m1` slide, from a note | `1; (32*φ + F(N) + 32) % 65536` |
| GT `!vib:@s1` | `1; (v + 64*[(φ - 3) % 12 >= 6] - 32) % 65536` |
| GT Metal Warrior pulse `i1` | `1; 16*abs((φ - 32) % 128 - 64) + 512` |
| defMON `t2` slide | `0; (v - 139) % 65536` |
| SW Csillag pulse `i1` | `1; 2*min(64, φ) - 8*min(64, max(0, φ - 65)) + 2048` |
| SW `i1` vibrato | `1; ((-abs(φ % 6 - 3) + 1)*W[N + 13]*[φ >= 32]) % 65536` |
| SW `m1` slide | `0; (v + W[N + 25]) % 65536` |

Written as expression records the three songs take more tokens than as v1.4 records.

Reference-song table programs (`sweeps.txt`):

| Program | Accumulator |
| --- | --- |
| GT Metal Warrior pulse, `i1`, `i3` | set, then a reflect ping-pong on every tick |
| GT Metal Warrior pulse `i2`, filters `i1`, `i2` | set, then holds |
| SW Csillag pulse `i6`, `i8` | set, then one sweep clamped at its end |
| SW Csillag pulse `i3`, `i7` | set, then holds |
| SW Csillag pulse `i1`, `i2`, filters `i4`, `i5` | none: several step sizes |

Instruments are numbered as in the song file read back (tunes are lifted and written
canonically first).

The SID-Wizard header walk (before the first note `wf_row` reads instrument 1's header as
WF rows) gives `ins.ad` a `CTRL` role and `ins.hrad`, `ins.hrsr`, `ins.sr` a `FREQ` role.

