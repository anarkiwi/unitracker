# Modulation analysis (`unitracker.modfit`)

`unitracker.modfit` derives each modulator occurrence of a song as phase and value
expressions in the format's grammar (see [expressions.md](expressions.md)), proves
them against the playroutine's own action lists with z3, and rewrites the song with them
(`convert`). The `unitracker-modfit` command reports these derivations and the
playroutine-level analyses (accumulator fits, keyword composition and signatures).

## Inputs

| input | read by |
|---|---|
| the playroutine's `modulators` declaration (see [Modulator declarations](../playroutines/schema.md#modulator-declarations)); each player's units are listed in the *Modulators* section of [playroutines/goattracker2.md](../playroutines/goattracker2.md), [playroutines/sidwizard.md](../playroutines/sidwizard.md) and [playroutines/defmon.md](../playroutines/defmon.md) | derivation, conversion, `exprs` |
| the playroutine library (`src/unitracker/playroutines/*.yaml`): cells, procs, record types, commands | every analysis |
| `src/unitracker/modfit/specs.yaml` | `fit`, `command`, `sweeps`, `orbit` (see [Specs](#specs)) |

How a declaration feeds the analysis:

- `fire` is the action list of one firing; `pre`/`post` bound a firing in the compiled player
  (`unitracker.player.modulation`), and `hooks` mark a start, which opens an occurrence at the
  unit's next firing and runs to its next start. `scope: global` makes one occurrence per unit.
- `cell` is the modulated value `v`; a pair `hi:lo[:k]` is fused into one value. With `chip`,
  `v` is the chip value `value` (`width` bits) and `store` writes it back; on a pair cell the
  chip value must be the pair plus an offset free of its parts.
- `notes`, `transpose` and `note_offset` give `N`; `relative` cells start as `N` plus their
  offset; `freq` gives `F(k)`; `tables` are the named tables `T[k]` and `functions` the
  playroutine functions rewritten over them.
- `label` groups occurrences; `site`, `commands`, `legato` and `retire` say where
  and how the conversion writes texts (see [Conversion](#conversion)).

## Phase and value expressions

A start is derived from its *start key*: the unit, its start control state and its start
value term (`exprs.Analysis.key`).

1. **Slice.** `fire` (record `run`s dispatched as `if` chains) is sliced to the cell. Cells it
   reads before writing and writes again are the *control*; other cells it reads are parameters.
   Note cells become `N`; the start hook is re-run with the note as `N`, and a cell it writes is
   kept as a term of `N` where that term gives the observed start value. The start value `v0`
   is the hook's write of the cell, when it gives the observed value.
2. **Graph.** From the start's concrete control, each firing runs through `form.Form` with `v`
   symbolic. The reachable control states and their guarded cases form a graph, minimised by
   bisimulation (equal value update, equal guarded successor classes).
3. **Phase.** If every class has one successor, `φ` counts firings (`time`: a prefix and a
   cycle). If a class stays until a test of `v` moves it to one other class, `φ` counts turns
   (`turn`: `a` is that test). Anything else fails (*more than one phase*).
4. **Value update.** Each class's cases become one expression (a decision tree on its tests,
   sides chosen by a test of their difference joined as `min`/`max`, comparisons of one linear
   form met as intervals). `f(v, φ)` is the shortest (`grammar.rank`) of the position-indexed
   form and, when every class adds or sets, the add form; cycles are rotated to start at a
   change. Frequency-table reads fold to `F`; named tables replace the functions they rewrite.
5. **Closed form.** When every class adds or sets and the start value is known (or set before
   any add), candidate `g(φ)` (the value after the firing at `φ`) are window ramps over the
   cycle plus its drift, gated `[φ >= d]` after a constant prefix, also rewritten through `abs`;
   wrapped only when the range leaves the cell. The shortest proved candidate is kept.

### What is proved

z3 over 48-bit bit-vectors; `φ` ranges over `[0, 2^20)`; `v` over the cell's (or chip value's)
domain; `N` over 7 bits; table reads as uninterpreted functions bounded by the table's
least and greatest entry.

| obligation | holds for |
|---|---|
| `f` (and for `turn`, `a`) at the class's phase equals the player's value (and advance) | every reachable control state, every guarded path |
| every firing leaves the chip value in its domain (`lo..hi`, `r mod m`) | chip units |
| `f(v, φ + P) = f(v, φ)`, same for `a` | `φ >=` prefix, when the period `P > 1` or the phase is `turn` |
| `g(0) = f(v0, 0)` and `g(φ + 1) = f(g(φ), φ + 1)` | the closed form, when there is one |

A start is *exact* when every obligation holds. For a chip unit, `chipinv` also reports the
invariant its recurrence holds under: `G`, guard comparisons over the hidden cells that every
action list of the player keeps (`invariant.inductive`), and `I`, the strongest conjunction
(Houdini) of guard comparisons and domain bounds true after the first firing and kept by
every firing. This result is reported, not required by the conversion.

## Conversion

`convert.convert(ir)` returns the song with every declared unit's native modulation
written as expressions; `convert.analyse(ir)` returns the whole `Conversion`.

1. **Window.** Repeats are written out; the song is replayed natively to the recurrence of its
   state and one period more (`reach.table_reads`). Each occurrence records its start cells
   and the event instance `(voice, play count, step)` that started it.
2. **Derive.** Start keys are computed in chunks; each distinct key is derived and proved
   once (`exprs.Pool`, `--workers` processes, two BLAS threads each).
3. **Group.** Occurrences group by unit and `label`. Over a group's starts that modulate: a
   shared closed form when every start has the same one, else a shared recurrence when every
   start has the same one, else each start's own (closed form if any, else recurrence). A start
   whose first value differs from the player's by a carried constant `c` is written `f - c·[φ < 1]`
   (a shared closed form gives way to the recurrence first). A start that does not modulate
   writes nothing (`v` at a row site or where a native command is replaced).
4. **Write.** At the unit's `site` when it exists and its `when` holds over the start cells:
   - a program site: the program is copied with `clear` applied to every row, `first` set on
     its first row and the text in its first row's `field`; trailing rows that set nothing are
     dropped; references to the program are redirected to the copy;
   - a `row` site: the text at `φ = 0` is written in the row itself;
   - several texts for one unit: the most frequent takes the site, each other gets a copy and
     each starting event is pointed at its copy;
   - integer texts in a program whose rows have other data the format writes stay native rows.

   Otherwise the event's `commands` codes are replaced by the `!freq` command naming the text,
   preceded by `!legato:0` when the start's `legato` cell is set on a note; with an `x` that
   reads the site's field, commandless steps the occurrence spans get it too. Changed events
   go into phrase variants (`p3~1`); a track's loop is unrolled where its passes differ.
   `retire` fields are cleared in every row of their record.
5. **Verify.** The rewritten song's register grid must equal the native song's over the whole
   window, each voice from its first note (`vm.first_mismatch`).

Observations, derivations and conversions are cached in `out/modfit/cache/` under the sha256
of the song and the sources they depend on (`convert.SOURCES`).

### Failures

`ConversionError` names the unit, program and start (`convert.diagnosis`) where it can.

| stage | reason |
|---|---|
| start key | a cell the slice reads has no start value |
| derivation | control reads the value; a pair's value reads its parts beyond their sum; a chip value is not its pair plus an offset; a term the grammar has no form for (`reads ...`); more than one phase |
| proof | an obligation of [What is proved](#what-is-proved) is refuted (`not proved`, with a witness) |
| text | the phase is not one per firing (a `turn` phase written as a recurrence); the text is outside the fixed grammar (`xexpr.source`) |
| site | two units at one site each need several texts; a `row` site needs several; a record type runs out of rows; no event selects (or names) the site |
| event | an expression index above a byte; three commands on one event; a track's later loop passes differ |
| program | a referenced program of a site type keeps no shared form and no start proves its expression |
| verify | the rewritten song plays a different register at some tick |

## General forms

Within a group whose texts differ only in values carried from earlier playback, `inherit`
proves one form for every value: the differing start cells are made symbolic and derived
again; a cell the firing never counts becomes an input, proved for every value of its width
and equal to each start's expression. Otherwise one carried counter that each firing lowers by
one, keeping `v`, is a delay gate `[φ >= d]` before the run of the start whose counter ran out.
Otherwise the reason is reported. This is reported by `exprs`; the conversion writes each
start's constants.

## Signatures

`keywords.Signer` gives each keyword and record field a set of `(role, kind, argument use)`:

- role: the SID bits the effect reaches through the bit dataflow (`flow`): `FREQ`, `PW`, `CUT`,
  `CTRL`, `AD`, `SR`, `RESFILT`, `MODEVOL`;
- kind: `LOAD`, `MASK`, `RESET`, `ARM`, `MODE`, `PTR`, `REF`, `TEST`, and per tick `ACC` with
  the step's provable sign (`+`, `-`, `±`, `0`), `TOWARD`, `LOAD`, `REFLECT`, or `MOD` when no
  single accumulator fits;
- argument use: its bit mapping into the role, `ref <type>`, `note-relative` or `table`.

Matches against the other playroutines are *same effect* (identical), *same effect, different
argument decoding* (same roles and kinds), *partial* (a kind over an intersecting quantity) or
*different*. An identical signature in the same playroutine is a duplicate, *partial* when the
two differ on a row with a note.

## `unitracker-modfit`

`--out FILE` writes the report to a file instead of standard output (`exprs` defaults to
`out/modfit/exprs.txt`). A `SONG` is a unitracker YAML or a tune a backend lifts; a pair cell
is `hi:lo` or `hi:lo:k`.

| subcommand | arguments | output |
|---|---|---|
| `fit` | `[PLAYROUTINE:PATH:CELL ...]` (default: `fits` and `checks` of the specs) | per dispatch guard, the proved `set`/`accumulate` rewrite, or the nearest candidate with a witness and the fit re-proved under the cell's inductive invariants; each given check proved or refuted with a witness |
| `poly` | `PLAYROUTINE:PATH ...` | each written cell's arithmetic form `x + Σ [G_i]·(u_i − x)` (`arith`) |
| `command` | `[PLAYROUTINE[:NAME] ...]` (default: the specs' `commands`) | each keyword's setup and per-tick effect on a row with and without a note, freed cells fitted as accumulators (`compose`) |
| `keywords` | `[PLAYROUTINE ...]` | one row per keyword or field: playroutine, shared key, signature, matches, verdict, reason ([Signatures](#signatures)) |
| `generic` | `[PLAYROUTINE ...] [--ticks N]` | each keyword and record row kind factorised as one generic accumulator (step, basis, hit, respond, gate, proof status), the rows grouped, timers, vibrato and bounce orbits |
| `sweeps` | `[SONG ...]` | the specs' `bounces`, the `fit` lines of the specs' pair-cell fits and checks, and each song's table programs matched to one accumulator |
| `exprs` | `[SONG ...] [--before DIR] [--after DIR] [--workers N]` (default: `cli.SONGS`) | per song: occurrences over the window, the grid check, its text read back against the native player, unproved named-table functions; per unit and label: occurrences, distinct starts, each start's phase, value update, closed form, proof and invariant, each written text and where, the general form; token counts against the `--before` and `--after` documents |
| `orbit` | `[PLAYROUTINE ...] [--ticks N]` (default: the specs' `orbits`) | per period, the reflect accumulator whose per-tick deltas equal the original's over the run, then the periods with none |

### Specs

`specs.yaml` keys: `fits` (playroutine, dotted path, cell), `checks` (a given candidate and an
optional guard), `orbits` (period cell or `record.index.field`, inclusive `periods`, initial
`state`, record `data`), `commands` (setup template with `CODE` and `arg`, tick lists, routing
cells left out of reports, `plain` and `note` row templates), `bounces` and `sweeps` (record
type, stepping list, swept cell, program pointer, control state at a start, instrument cell).
