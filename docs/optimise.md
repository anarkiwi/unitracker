# unitracker-optimise

`unitracker-optimise` rewrites a song as a document with fewer tokens that plays the same register
grid. Size is the token count of the written text under `cl100k_base` (`tiktoken`). The code is
`unitracker.optimise`.

```sh
unitracker-optimise song.yaml -o smaller.yaml [--passes hoist,commands] [--workers N] [--report report.md]
unitracker-lift TUNE.sid -o song.yaml --optimise    # every pass, default workers
```

## Options

| option | meaning |
|---|---|
| `song` | the input document |
| `-o`, `--output` | where to write the result; standard output when absent |
| `--passes` | comma-separated pass names, run in the order given; default every pass in the order of the table below; an unknown name is an error |
| `--workers` | processes screening candidates; default the host's cores; `1` screens in-process |
| `--report MD` | write the report (see Report) to `MD` |

The token counts before and after are printed to standard error.

## Passes

Each pass lists candidate edits of the current song in a deterministic order; each edit is a pure
function of a song and its arguments, `None` where it does not apply.

| pass | edit | rewrites |
|---|---|---|
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
| | `drop_keep` | an event loses its `!pulse:0`/`!filter:0` |
| | `drop_instrument` | an event stops naming its instrument |
| `fields` | `clear` | one record field (other than a program's chain) goes to its default |

Pinned records are never dropped by `merge` nor rewritten by `truncate` or `reloop`.

## The guarantee

The output plays the input's register grid in the song's own state: every voice's registers from
the tick its first note starts, the filter and volume registers from the earliest start
(`first_mismatch`), and the voices' start ticks equal the input's.

The check (`proof.mismatch`) is exact, not sampled:

1. `Trace` plays a song until its complete playback state recurs (`reach.recurrence`, Brent's
   algorithm). From `mu` on the grid repeats with `period`, so the trace gives every tick.
2. A candidate is traced the same way, stopping at the first tick that departs from the input.
3. Both grids repeat from `max(mu)` with period `lcm(period)`; agreement over
   `max(mu) + lcm(period)` ticks is agreement forever.
4. A candidate that fails to play, whose state does not recur, or whose bound exceeds
   `reach.MAX_TICKS` is unproved and never kept.

What is checked is the candidate's written text read back (`songio.loads(songio.dumps(...))`), so
the canonical writer's own rewriting ([format.md](format.md), *Canonical form*) is inside the
check. A song that cannot be written with its playroutine by `ref` is written embedded
([format.md](format.md), *Embedding*).

## The driver

| step | what happens |
|---|---|
| screen | each candidate is applied, written, counted, read back and checked in a process pool; a candidate whose text is unchanged is skipped; `factor` and `share` are checked only when they shrink the text |
| keep | the proved candidates are applied in candidate order, each kept in the run only if it shrinks the running text; the longest prefix of that run whose text proves is kept, found by bisection |
| repeat | a pass repeats while it keeps an edit; the passes repeat until none does |
| canonical | after each round the text is read and rewritten; a rewrite that differs is checked again (failure is an error) and starts another round |

Screening results are ordered by candidate index, so the output does not depend on `--workers`.
The output is canonical: `dumps(loads(text)) == text`.

## Report

`--report` writes `analysis.orthogonality.report` as Markdown: one section per kind, one line per
finding (`player · place: the two forms; resolution`), alike findings joined.

| section | contents |
|---|---|
| Vocabulary redundancy | proved `hoist` edits |
| Non-orthogonal key | proved `drop_command`, `drop_keep`, `drop_instrument` and `clear` edits |
| VM dead state | the input playroutine's cells, procs, fields and constants that nothing reads or names, and tests that every value their cells take decides alike, found statically |
| Unreachable vocabulary | shared row keys no player's codec writes, shared commands no player names, and the input's codec shapes and commands no lifted row or event uses |
| Song redundancy | proved `merge`, `truncate`, `reloop`, `alias` and `factor` edits |

Every proved edit is listed, kept or not; one proved but not kept is marked `(not kept)`. Places
and names are those of the document the edit was proved on.
