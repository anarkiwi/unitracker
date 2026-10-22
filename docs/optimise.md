# The optimiser

`unitracker.optimise` rewrites the lifter's song so that its [generic
document](format/document.md) is smaller and plays the same register grid. Each candidate is
written with `document.write`; size is the generic document's token count under `cl100k_base`
(`tiktoken`), and an edit is proved when the lifter's VM replays the candidate song as the input
(see [The guarantee](#the-guarantee)) and its generic document plays on unitracker-player as the
input's does. A song with no generic document (`document.write` raises) cannot be optimised. Its canonical passes are the
`canonical` stage of `Lift` ([Lifting](playroutines/lifting.md)); `unitracker-lift TUNE.sid
--optimise` runs every pass, in the order of the table below, before the verify stage.

## Passes

Each pass lists candidate edits of the current song in a deterministic order; each edit is a pure
function of a song and its arguments, `None` where it does not apply.

| pass | edit | rewrites |
|---|---|---|
| `staircases` | `staircase` | a run of steps each carrying a value command for one key (`!cut`, `!ad`, `!sr`, `!vol`), whose values are one function of the step, becomes one expression command at its first step |
| `slides` | `stitch` | a run of `!freq` steps of constant offsets `v + c`, `c` a function of the firing, becomes one expression in `φ` from its first step |
| `holds` | `hold` | a `!freq` of an expression equal to `0`, or to `v` on an instrument without `freq`, on an event that does not start its note, becomes `!freq:none` |
| `anchors` | `stitch` | a `!freq` whose expression is another of the song's, `a`, translated, `a(φ + d)`, with the groups `d` firings before it playing `a`'s first values, becomes `a` from those groups |
| `restitch` | `stitch` | a run of `!freq` groups that continue the first group's expression with its phase becomes that expression from the first |
| `hoist` | `hoist` | a command on notes of an instrument becomes a field of a copy of that instrument, which those notes name; refused where the command is on a non-note or the instrument is unknown |
| `instruments` | `merge` | every reference to one instrument names another |
| | `unhoist` | an instrument one field away from another plays as the other, with the command that sets that field in a free slot of each of its notes (referenced programs compared by their rows) |
| `programs` | `merge` | every reference to one program names another |
| | `truncate` | a program cut to its first `n` rows and ending there (the type's `end` fields set; its `stop` must hold) |
| | `reloop` | a looping program rewritten with its shortest cycle and prefix |
| `expressions` | `merge` | every reference to one expression names another |
| `phrases` | `alias` | every play of one phrase plays another of the same steps, transposed by their first notes |
| | `factor` | a phrase that is its first `n` events repeated is played as plays of that part |
| | `share` | every phrase holding a maximal run of two or more events that recurs apart (alike up to a transpose) is played as the parts around and of the run |
| `commands` | `drop_command` | an event loses one command |
| | `drop_keep` | an event loses its `!pw`/`!filter` |
| | `drop_instrument` | an event stops naming its instrument |
| `fields` | `clear` | one record field (other than a program's chain) goes to its default |

Pinned records are never dropped by `merge` nor rewritten by `truncate` or `reloop`.

### Canonical passes

The first five passes (`optimise.CANONICAL`) write modulation the lift wrote shattered in its
canonical form. They read only the lifter's song: phrases of events and their shared commands,
and expressions in `v` and `φ`.

A run is a phrase's consecutive events each carrying the pass's command, with no note after its
first. A `!freq` run is cut into groups, consecutive events that name one expression, each one
occurrence of the unit that plays `!freq`; a group lasts the firings the song's own playback gives
it, alike wherever the phrase plays (a group whose occurrence differs between plays, or that
another occurrence interrupts, ends the run). One expression `E` plays a run's groups when
`E(φ + S_k) = e_k(φ)` over each group's firings, `S_k` the firings before group `k`; the equality is
decided by z3 for every `v`, `N`, frequency word and table. `stitch` names `E` on each step of the
run where `!freq` lasts a step (`lasts: step`), else on its first step alone.

`slides` and `staircases` fit the run's values as a function of the firing (of the step for
`staircases`) with the grammar's exact step and ramp forms (`modfit.phase`), the shortest by the
grammar's rank. No playroutine has an expression form of `!cut`, `!ad`, `!sr` or `!vol` yet, so
`staircase` rewrites nothing until one does.

A canonical pass is not screened candidate by candidate: each candidate that is no larger is
applied together with the others, the composite is proved once, and a
refused composite is split in halves, each kept where it proves, so `k` refused candidates of `n`
cost `O(k log n)` proofs; a refused part of at most `workers` candidates is screened in one
parallel batch, and the composite of those that prove alone is proved in turn. A pass that keeps
its candidates is not run again on its own result. `Result.tally` counts each pass's candidates
`found`, `refused` and `proved`.

## The guarantee

`Result.text` is the generic document of `Result.ir`, the edited song itself; records an edit
leaves unreached stay in it, and the document drops them. `Result.findings` holds a `Finding`
per proved `(pass, edit)`. The result plays the input's register grid in the song's own state: every voice's registers from
the tick its first note starts, the filter and volume registers from the earliest start
(`gridcmp.first_mismatch` under the playroutine's `tolerance`), and the voices' start ticks equal
the input's.

The check (`proof.mismatch`) is exact, not sampled:

1. `Trace` plays a song until its complete playback state recurs (`reach.recurrence`, Brent's
   algorithm). From `mu` on the grid repeats with `period`, so the trace gives every tick.
2. A candidate is traced the same way, stopping at the first tick that departs from the input.
3. Both grids repeat from `max(mu)` with period `lcm(period)`; agreement over
   `max(mu) + lcm(period)` ticks is agreement forever.
4. A candidate that fails to play, whose state does not recur, or whose bound exceeds
   `reach.MAX_TICKS` is unproved and never kept.
5. The candidate's generic document, played by unitracker-player over the input's ticks, equals
   the input's document's grid; one the writer refuses or the player cannot play is never kept.

### Resuming from the input

A candidate plays exactly as the input until it first reads a datum the two hold differently, so
its trace need not start at tick 0. The input, when it plays its own grid, is played once as a `Baseline` (`unitracker.player.checkpoint`), kept on the input's trace:

| it notes | |
|---|---|
| first reads | the tick each datum is first read: a record field, a table entry, a record row's action list (by its generated source), a play of a track (phrase length, transpose, repeat), a phrase step, a track's play count and loop, an expression |
| snapshots | every 64 ticks within the kept-state window of `reach.recurrence`, the complete player state: cells, cursors, registers, starts, write counts and open modulator occurrences |

A candidate whose generated code, but for its record rows' functions, and playroutine are the
input's compares its data with the noted reads in tick order, and its trace resumes from the last
snapshot before the first that differs, with the input's rows and kept states before it. Anything
else plays from tick 0. The verdict is the full replay's.

## The driver

| step | what happens |
|---|---|
| screen | each candidate is applied, counted and checked in a process pool; a candidate that leaves the song unchanged is skipped; `factor` and `share` are checked only when they shrink the song; a pass is not screened again on a song it kept nothing from |
| keep | the proved candidates are applied in candidate order, each kept in the run only if it shrinks the running song; the longest prefix of that run that proves is kept, found by bisection |
| settle | a canonical pass instead keeps its candidates together, bisecting a refused composite (see [Canonical passes](#canonical-passes)) |
| repeat | a pass repeats while it keeps an edit; the passes repeat until none does |

A pass's screen depends only on the current song and its document. One pool of `workers` spawned processes (by
default `$UNITRACKER_WORKERS`, else one per core the process may run on; `1` screens in-process),
each holding the input's trace, screens every pass and is stopped when the optimiser finishes.
Screening results are ordered by candidate index, so the result does not depend on `workers`.
`optimise(song, passes, workers)` takes the pass names to run in order, by default every pass.
