# Optimising lifted tunes

A lifted tune transcribes the native module's data; it is not the smallest document that plays
the same. `unitracker-optimise` rewrites a song into a smaller document with the same output, where
"the same" is the project's criterion: every voice's registers identical to the input's from its
first note (`SongPlayer.mismatch`, `first_mismatch`), and the filter and volume registers from the
earliest onset. Size is measured in tokens, since the document is read by people and language
models: `cl100k_base` (GPT-4's BPE) is the objective, and every count below is also given for
`o200k_base` (GPT-4o) and Llama 2's SentencePiece tokenizer.

```sh
unitracker-optimise song.yaml -o smaller.yaml [--passes hoist,commands] [--workers N]
unitracker-lift TUNE.sid -o song.yaml --optimise
```

The code is the package `unitracker.optimise` (`proof`, `model`, `edits`, `passes`, `driver`,
`describe`).

## The proof

`Trace` plays a song to the recurrence of its complete state (cells, cursors, registers and
modulator occurrences), with the Brent loop that `reach.table_reads` uses (`reach.recurrence`).
Playback is deterministic, so from `mu = tick - period` on, row `t` of the register grid equals row
`mu + (t - mu) % period`: the trace gives the grid of every tick.

A candidate is traced the same way, compared tick by tick with the input's grid and stopped at the
first register that differs in the song's own state. Both grids repeat from `M = max(mu)` with
period `L = lcm(period)`, so when they agree over `M + L` ticks they agree forever. Agreement is
`first_mismatch` over that window with the input's starts, and the candidate's starts must be the
input's, so `unitracker-play --check` compares the same window against the native player. A bound
past `reach.MAX_TICKS` leaves the candidate unproved.

What is proved is the candidate's written text read back (`songio.loads(songio.dumps(candidate))`),
so the canonical writer's own rewriting (merged phrases, folded restarts, envelope bytes) is inside
the proof, and the output document is exactly what was proved.

## The driver

Each pass lists its candidate edits of the current song in a deterministic order. They are
screened in a process pool (`--workers`, default the host's cores): an edit is applied, written,
counted, read back and proved. The proved edits that shrink the text are then applied in order, and
the longest run of them whose written text proves is kept, found by bisection (the first is proved
by its screening), so a round costs a logarithmic number of extra proofs. A pass repeats while it
keeps an edit; the passes repeat until none does. Tokens strictly decrease, so the driver reaches a
fixpoint. The result is written from its own reading and proved once more, so the output is
canonical (`dumps(loads(text)) == text`). Screening results are sorted by candidate index, so the
output and the findings do not depend on the worker count (tested).

Every edit the grid proves is recorded as a finding, kept or not, with its place and the two
written forms. `factor` and `share`, which
rewrite structure rather than vocabulary, are proved only when they shrink the text.

## Passes

Each edit is a pure function of a song and its arguments (`unitracker.optimise.edits`), None
where it does not apply; each pass lists edits (`unitracker.optimise.passes`).

| pass | edit | what it does |
|---|---|---|
| `hoist` | `hoist` | a command on the notes of an instrument becomes a field of a copy of the instrument, which those notes name: `!run:@c1` → the instrument's `ctrl: c1`, `!ad:n`/`!sr:n` → `ad`/`sr` (a SID-Wizard envelope nibble as the byte it writes), `!freq:@x` → the instrument's `freq` where it has one; notes keep their instrument named where the voice changes it |
| `instruments` | `merge` | every use of one instrument names another; the writer then drops the unused one |
| | `unhoist` | an instrument one field away from another is played as the other with the command that sets the field on each of its notes, the inverse of `hoist` (referenced programs compared by their rows) |
| `programs` | `merge` | every reference to one program names another |
| | `truncate` | a program cut to its first `n` rows and ending there (the type's `end` fields set, a stop row required where the type has one) |
| | `reloop` | a looping program written with its shortest cycle and prefix: an unrolled loop folds back |
| `expressions` | `merge` | every reference to one expression names another |
| `phrases` | `alias` | every play of one phrase plays another of the same length, transposed by their first notes |
| | `factor` | a phrase that is one part repeated is played as plays of the part |
| | `share` | every phrase holding a recurring run of events (alike up to a transpose, maximal) is played as the parts around and of it, so one phrase plays the run everywhere |
| `commands` | `drop_command` | an event loses one command |
| | `drop_instrument` | a note stops naming its instrument |
| | `drop_keep` | a note loses `!pulse:0`/`!filter:0` |
| `fields` | `clear` | one field of a record (instrument, program row, initial state) goes to its default |

The passes cover the issue's list as follows. Redundant instruments: `merge` and `unhoist`.
Redundant programs: `merge` (equal up to an unreached tail or in context), `truncate` (rows no
start reaches) and `reloop` (loop unrolling). Command folding: `drop_command`, which also drops a
`!freq` equal to the instrument's `freq`. Phrase factoring: `alias`, `factor`, `share`, and the
canonical writer's merge of phrases equal up to a transpose. Hint and name hygiene: the writer
names records in first-use order and the driver's last step makes the output canonical.

Not done, because the format cannot write the result:

- **A program sharing another's prefix** with its `loop` or `end` moved: the format writes every
  program whole, so two programs cannot share rows.
- **Expression generalisation**: the families of expressions that differ only in per-use
  constants (Csillag's legato glides `([v >= 32768]*((v + K) % 65536) + ...)*[v < 65538 - K]`,
  Metal Warrior's two vibrato depths) are one general form only with inputs, which the grammar of
  `v`, `φ` and `N` does not have; the modfit report proves those general forms with the constants
  as inputs (`docs/modfit.md`, *Conversion*). Within the grammar the pass merges expressions that
  play alike in context, which the grid finds even where the texts differ.
- **Relative versus absolute row forms** (`note: +n` against `=n`): the absolute form depends on
  the note each use sounds; the optimiser does not guess values for the grid to test.
- **An instrument override in the play entry**: a format change; no two phrases of the three
  tunes differ only by their instruments.

## Results

The three reference tunes, lifted and optimised by every pass to the fixpoint (12 workers for
Csillag, 6 for the others, on a 24-core host):

| tune | tokenizer | before | after | delta | header | definitions | phrases | plays | structure |
|---|---|---|---|---|---|---|---|---|---|
| Metal Warrior | cl100k_base | 3601 | 3227 | -374 (-10.4%) | 79 → 79 | 929 → 894 | 2503 → 2164 | 65 → 65 | 26 → 26 |
| | o200k_base | 3601 | 3227 | -374 | 80 → 80 | 929 → 894 | 2502 → 2163 | 65 → 65 | 26 → 26 |
| | llama2 | 4040 | 3653 | -387 | 99 → 99 | 1082 → 1057 | 2752 → 2390 | 76 → 76 | 32 → 32 |
| Csillag | cl100k_base | 9011 | 7725 | -1286 (-14.3%) | 77 → 77 | 2667 → 1844 | 5677 → 5214 | 477 → 477 | 114 → 114 |
| | o200k_base | 9008 | 7722 | -1286 | 74 → 74 | 2667 → 1844 | 5677 → 5214 | 477 → 477 | 114 → 114 |
| | llama2 | 10619 | 8788 | -1831 | 88 → 88 | 3466 → 2283 | 6342 → 5694 | 584 → 584 | 142 → 142 |
| Crosseyed | cl100k_base | 1693 | 1412 | -281 (-16.6%) | 64 → 64 | 860 → 600 | 564 → 543 | 176 → 176 | 30 → 30 |
| | o200k_base | 1692 | 1411 | -281 | 63 → 63 | 860 → 600 | 564 → 543 | 176 → 176 | 30 → 30 |
| | llama2 | 2119 | 1750 | -369 | 78 → 78 | 1136 → 792 | 679 → 654 | 190 → 190 | 37 → 37 |

| tune | kept edits (tokens, cl100k_base) | time |
|---|---|---|
| Metal Warrior | 3 `hoist` (-191), 18 `drop_command` and 10 `drop_instrument` (-124), 11 `clear` (-59) | 138 s |
| Csillag | 1 `hoist` (-26), 1 instrument `merge` (-97), 14 expression `merge` (-746), 59 `drop_command` and 49 `drop_instrument` (-403), 3 `clear` (-14) | 838 s |
| Crosseyed | 1 instrument `merge` (-140), 1 `truncate` and 1 `reloop` (-22), 3 `drop_command` (-42), 14 `clear` (-77) | 60 s |

Each optimised document plays its tune's native player (`tools/grid_diff.py song.yaml TUNE.sid
--ticks 3007 --to 0`: `first mismatch: None`), and the tests check the same for each tune under a
subset of the passes (`tests/conftest.py`, `OPTIMISED`), which keeps the suite's time bounded.

What the edits found, in brief:

- **Metal Warrior.** `!run:@c1`, `@c2` and `@c3` on i3's notes are three instruments, which
  removes every `!run`. The toneporta `!freq:@x2` on legato notes and on the steps after them
  plays as `!legato:0` alone, so it drops from 18 events. The first two wavetable rows
  `{ctrl: 9}` of most instruments repeat the waveform the hard restart's first-tick row already
  writes, and a `{ctrl: 128}` row repeats the row before it. A phrase's first `.i1` names the
  instrument the voice already plays, on every pass of the loop.
- **Csillag.** Fourteen of the legato glides (`([v >= 32768]*((v + K) % 65536) + ...)*[v <
  65538 - K]`, one per speed `K`) play as the constant expression `0` wherever they are used:
  each starts from a pitch offset of 0, which no branch of the expression moves. They merge into
  the song's constant expression `0`, and the `!freq` naming it then drops from the legato notes, which is most of the 746 and
  403 tokens. `!sr:72` on every note of i2 is i2's own `sr`; two instruments play as one; a
  pulse program `pw: 0` and a `freq: 0` on instruments are unheard; an `on!ad:208` repeats the
  byte its note's instrument already plays; and 49 instrument names repeat the voice's
  instrument. 109 more expression merges and 15 more hoists are proved but write more tokens.
- **Crosseyed.** `t7` plays as `t5`: their ACID expressions differ only in accumulator bits the
  replay never writes to `$D416`. `t3`'s last row is unreached and `t8`'s loop was unrolled once. The
  initial voice frequency and `tbase` in `vinit` are overwritten before any voice starts; a
  `!run2:@t6` on the step after a note that already armed `t6` plays nothing, and neither does the
  restart `r2`, whose lookahead row only sets the control mask 0.

