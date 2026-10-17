# Optimising lifted tunes

A lifted tune transcribes the native module's data; it is not the smallest document that plays
the same. `unitracker-optimise` rewrites a song into a smaller document with the same output, where
"the same" is the project's criterion: every voice's registers identical to the input's from its
first note (`SongPlayer.mismatch`, `first_mismatch`), and the filter and volume registers from the
earliest onset. Size is measured in tokens, since the document is read by people and language
models: `cl100k_base` (GPT-4's BPE) is the objective, and every count below is also given for
`o200k_base` (GPT-4o) and Llama 2's SentencePiece tokenizer, which the studies use only when
its `tokenizer.json` is already in the local Hugging Face cache (nothing is fetched; the tests
that need it skip without it). The documents are v1.15.

```sh
unitracker-optimise song.yaml -o smaller.yaml [--passes hoist,commands] [--workers N] [--report orthogonality.md]
unitracker-lift TUNE.sid -o song.yaml --optimise
python -m unitracker.tokenstudy song.yaml ...     # the context-efficiency mock-ups below, as Markdown tables
python -m unitracker.spelling song.yaml ...       # the note spellings below
```

The code is the package `unitracker.optimise` (`proof`, `model`, `edits`, `passes`, `driver`,
`describe`); the orthogonality report is `unitracker.orthogonality` ([orthogonality.md](orthogonality.md)
for the three reference tunes) and the studies are `unitracker.tokenstudy` and `unitracker.spelling`.

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
written forms; `--report` turns them into the orthogonality report. `factor` and `share`, which
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
  program whole, so two programs cannot share rows (proposal P5).
- **Expression generalisation**: the families of expressions that differ only in per-use
  constants (Csillag's legato glides `([v >= 32768]*((v + K) % 65536) + ...)*[v < 65538 - K]`,
  Metal Warrior's two vibrato depths) are one general form only with inputs, which the grammar of
  `v`, `φ` and `N` does not have; the modfit report proves those general forms with the constants
  as inputs (`docs/modfit.md`, *Conversion*). Within the grammar the pass merges expressions that
  play alike in context, which the grid finds even where the texts differ (proposal P1).
- **Relative versus absolute row forms** (`note: +n` against `=n`): the absolute form depends on
  the note each use sounds; the optimiser does not guess values for the grid to test.
- **An instrument override in the play entry**: measured below (*Phrase text*); no two phrases of
  the three tunes differ only by their instruments, so it is not proposed.

## Results

The three reference tunes, lifted and optimised by every pass to the fixpoint (16 workers for
Csillag, 20 for the others, on a 24-core host):

| tune | tokenizer | before | after | delta | header | definitions | phrases | plays | structure |
|---|---|---|---|---|---|---|---|---|---|
| Metal Warrior | cl100k_base | 3601 | 3227 | -374 (-10.4%) | 79 → 79 | 929 → 894 | 2503 → 2164 | 65 → 65 | 26 → 26 |
|  | o200k_base | 3601 | 3227 | -374 | 80 → 80 | 929 → 894 | 2502 → 2163 | 65 → 65 | 26 → 26 |
|  | llama2 | 4040 | 3653 | -387 | 99 → 99 | 1082 → 1057 | 2752 → 2390 | 76 → 76 | 32 → 32 |
| Csillag | cl100k_base | 9011 | 7725 | -1286 (-14.3%) | 77 → 77 | 2667 → 1844 | 5677 → 5214 | 477 → 477 | 114 → 114 |
|  | o200k_base | 9008 | 7722 | -1286 | 74 → 74 | 2667 → 1844 | 5677 → 5214 | 477 → 477 | 114 → 114 |
|  | llama2 | 10619 | 8788 | -1831 | 88 → 88 | 3466 → 2283 | 6342 → 5694 | 584 → 584 | 142 → 142 |
| Crosseyed | cl100k_base | 1693 | 1412 | -281 (-16.6%) | 64 → 64 | 860 → 600 | 564 → 543 | 176 → 176 | 30 → 30 |
|  | o200k_base | 1692 | 1411 | -281 | 63 → 63 | 860 → 600 | 564 → 543 | 176 → 176 | 30 → 30 |
|  | llama2 | 2119 | 1750 | -369 | 78 → 78 | 1136 → 792 | 679 → 654 | 190 → 190 | 37 → 37 |

| tune | kept edits (tokens, cl100k_base) | proved, not kept | time |
|---|---|---|---|
| Metal Warrior | 3 hoist `hoist` (-191), 10 commands `drop_instrument` (-20), 18 commands `drop_command` (-104), 11 fields `clear` (-59) | none | 160 s |
| Csillag | 1 hoist `hoist` (-26), 1 instruments `merge` (-97), 14 expressions `merge` (-746), 49 commands `drop_instrument` (-98), 59 commands `drop_command` (-305), 3 fields `clear` (-14) | 15 hoist `hoist`, 109 expressions `merge`, 1 commands `drop_command` | 747 s |
| Crosseyed | 1 instruments `merge` (-140), 1 programs `truncate` (-6), 1 programs `reloop` (-16), 3 commands `drop_command` (-42), 14 fields `clear` (-77) | none | 61 s |

Each optimised document plays its tune's native player (`tools/grid_diff.py song.yaml TUNE.sid
--ticks 3007 --to 0`: `first mismatch: None`), and the tests check the same for each tune under a
subset of the passes (`tests/conftest.py`, `OPTIMISED`), which keeps the suite's time bounded.

What the edits found, in brief (the full list is [orthogonality.md](orthogonality.md)):

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
- **Crosseyed.** The layer-B program `c2` plays as `i7`'s `filter`: their ACID expressions
  differ only in accumulator bits the replay never writes to `$D416`. The last row of `i3`'s `ctrl`
  program is unreached and `i9`'s loop was unrolled once. The initial `note` and `freq` in `vinit`
  are overwritten before any voice starts; an empty step that re-arms the layer-B program its note's
  instrument `i6` already armed plays nothing, and neither does the restart `r2`, whose lookahead
  row only sets the control mask 0.

## Context-efficiency suggestions

Measured by `python -m unitracker.tokenstudy` on the three optimised documents. Each suggestion
is a mock-up: the study rewrites the written structure (`songio.dumps(ir, respell=...)`), which the
reader does not accept, so each is evidence for a format decision, not a format. Deltas are tokens
against the optimised document; totals under each tokenizer, sections under `cl100k_base`.

| suggestion | tune | cl100k_base | o200k_base | llama2 | header | definitions | phrases | plays | structure |
|---|---|---|---|---|---|---|---|---|---|
| parameterised forms | Metal Warrior | +16 | +16 | +22 | +68 | -52 | +0 | +0 | +0 |
|  | Csillag | -219 | -219 | -309 | +187 | -406 | +0 | +0 | +0 |
|  | Crosseyed | +61 | +61 | +69 | +24 | +37 | +0 | +0 | +0 |
| `tri`, `clamp` primitives | Metal Warrior | -16 | -16 | -20 | +0 | -16 | +0 | +0 | +0 |
|  | Csillag | -12 | -8 | -12 | +0 | -12 | +0 | +0 | +0 |
|  | Crosseyed | +0 | +0 | +0 | +0 | +0 | +0 | +0 | +0 |
| sub-phrase references | Metal Warrior | -463 | -463 | -504 | +0 | +614 | -1077 | +0 | +0 |
|  | Csillag | -599 | -599 | -616 | +0 | +920 | -1519 | +0 | +0 |
|  | Crosseyed | -85 | -85 | -108 | +0 | +141 | -226 | +0 | +0 |
| instruments in the play entry | Metal Warrior | +0 | +0 | +0 | +0 | +0 | +0 | +0 | +0 |
|  | Csillag | +0 | +0 | +0 | +0 | +0 | +0 | +0 | +0 |
|  | Crosseyed | +0 | +0 | +0 | +0 | +0 | +0 | +0 | +0 |
| grouped definitions | Metal Warrior | -33 | -33 | -44 | +0 | -9 | -12 | +4 | -16 |
|  | Csillag | -196 | -196 | -260 | +0 | -40 | -78 | +26 | -104 |
|  | Crosseyed | -34 | -34 | -45 | +0 | -4 | -15 | +5 | -20 |
| instrument summaries | Metal Warrior | +110 | +108 | +124 | +110 | +0 | +0 | +0 | +0 |
|  | Csillag | +153 | +150 | +175 | +153 | +0 | +0 | +0 | +0 |
|  | Crosseyed | +61 | +61 | +62 | +61 | +0 | +0 | +0 | +0 |
| mnemonic names | Metal Warrior | +181 | +181 | +277 | +0 | +9 | +172 | +0 | +0 |
|  | Csillag | +312 | +312 | +514 | +0 | +13 | +299 | +0 | +0 |
|  | Crosseyed | +83 | +83 | +141 | +0 | +10 | +73 | +0 | +0 |
| tempo hint | Metal Warrior | +5 | +5 | +7 | +5 | +0 | +0 | +0 | +0 |
|  | Csillag | +5 | +5 | +8 | +5 | +0 | +0 | +0 | +0 |
|  | Crosseyed | +5 | +5 | +8 | +5 | +0 | +0 | +0 | +0 |
| contents per voice | Metal Warrior | +50 | +50 | +58 | +50 | +0 | +0 | +0 | +0 |
|  | Csillag | +334 | +334 | +412 | +334 | +0 | +0 | +0 | +0 |
|  | Crosseyed | +123 | +123 | +127 | +123 | +0 | +0 | +0 | +0 |
| plays first | Metal Warrior | -34 | -34 | -45 | +0 | -8 | -13 | +5 | -18 |
|  | Csillag | -197 | -197 | -266 | +0 | -38 | -80 | +27 | -106 |
|  | Crosseyed | -37 | -37 | -49 | +0 | -6 | -15 | +6 | -22 |

**Expression size.** The *forms* mock-up writes every family of expressions that differ only in
integers as one parameterised form in the header (`f1: '(a, b) (v + a*[(φ - 3) % 12 >= 6] - b) %
65536'`) and each use as `f1(64, 32)`. It saves 219 tokens on Csillag, whose glides and vibratos
come in families, and costs 16 and 61 on the others, where a family has two members and the
definition costs more than the uses save. On the lifted Csillag, before the optimiser proves
fourteen glides constant, it saved 689: most of the saving the format change would buy, the
optimiser already buys within the format. The forms keep the semantics visible (the body is the
proved expression), and the modfit report already proves the inputs (`docs/modfit.md`). Of the
grammar primitives, `abs((φ - a) % p - p/2)` occurs 7 times (Metal Warrior's and Csillag's pulse
ping-pongs) and `min(n, max(0, φ - d))` 5 times (Csillag's ramps), none in defMON's tune; writing
them `tri(φ - a, p)` and `clamp(φ - d, 0, n)` saves 16, 12 and 0 tokens, under 0.5%.
*Recommendation:* parameterised forms need a format change and pay only on a tune with large
families: sub-issue P1, low priority. Reject the primitives: the saving does not pay for two more
grammar functions every reader must learn.

**Phrase text.** Repetition the transposed-phrase merge leaves is large: the *sub-phrases* mock-up
writes every run of four or more atoms that recurs apart once, in a `sub` definition, and as
`&s1` where it occurs; it saves 463, 599 and 85 tokens (14%, 8%, 6%), the largest measured. Within
the format the `share` pass plays such runs as phrases of their own, but every split adds a
timeline row naming all three voices, so on the three tunes no split pays and none is kept. An
instrument override in the play entry (`p1(i1=i4)`) would merge phrases that differ only in their
instruments: the three tunes have none, so it saves nothing and is not proposed. Run lengths are
already cheap: writing every step (`C4, -, -` for `C4*3`) costs 36 to 863 tokens with
`cl100k_base` and 359 to 2310 with Llama 2, and rests and gate-offs are 33%, 9% and 18% of the
phrase atoms' tokens even run-length coded. *Recommendation:* sub-phrase references need a format
change: sub-issue P2. Keep `*n`; reject the play-entry instrument override.

**Definition placement.** Grouping every definition in the first row saves 33, 196 and 34 tokens
(1-2.5%), mostly the `- def:` keys of later rows and the indentation of phrases; it loses the
property that a definition sits in the row that first plays it, which the document's prefix
playability and a reader's locality rely on. A one-line summary per instrument derived from its
keys (`i1: 'pul wave; ad, ctrl, pw, restart'`) costs 61 to 153 tokens and says nothing the
definition's keys do not. *Recommendation:* reject both.

**Naming.** A waveform mnemonic on every instrument name (`i1_pul`) costs 83 to 312 tokens
(+5.6%, +4.0%, +5.9%; 6-8% under Llama 2, which splits the suffix finer), on every note that names an
instrument, for a word the definition's first `ctrl` already gives. *Recommendation:* reject.

**Header and hints.** The headers are 64 to 79 tokens; `hints` already carry `ticks_per_step` and
`phrase_steps`, and `loop` is its own key. A derived tempo (`bpm`, four steps to the beat) costs 5
tokens but assumes a beat the format does not know. *Recommendation:* reject until the hints carry
the song's beat structure (#39).

**Document ordering.** Writing the play rows first and every definition after saves 34 to 197
tokens, almost all from the same `- def:` keys as grouping, and puts a definition far from the row
that first needs it. A table of contents of each voice's phrases in the header costs 50, 334 and
123 tokens and repeats the timeline, whose rows already answer "what plays at row 12". *Recommendation:*
reject both.

## Tokenization of notes and phrases

Measured by `python -m unitracker.spelling` on the three optimised documents. Every spelling has
an exact inverse (`spelling.absolute`), tested on every phrase: pitch is pitch, only the spelling
changes. How the tokenizers split atoms:

| atom | cl100k_base | o200k_base | llama2 |
|---|---|---|---|
| `C#4` | `C` `#` `4` | `C` `#` `4` | `▁C` `#` `4` |
| `A#1` | `A` `#` `1` | `A` `#` `1` | `▁A` `#` `1` |
| `C10` | `C` `10` | `C` `10` | `▁C` `1` `0` |
| `G#4.i7*3` | `G` `#` `4` `.i` `7` `*` `3` | `G` `#` `4` `.i` `7` `*` `3` | `▁G` `#` `4` `.` `i` `7` `*` `3` |
| `-!freq:@x12*2` | `-` `!` `freq` `:@` `x` `12` `*` `2` | `-` `!` `freq` `:@` `x` `12` `*` `2` | `▁-` `!` `f` `req` `:@` `x` `1` `2` `*` `2` |
| `+12` | `+` `12` | `+` `12` | `▁+` `1` `2` |
| `-3` | `-` `3` | `-` `3` | `▁-` `3` |
| `3#o4` | `3` `#` `o` `4` | `3` `#` `o` `4` | `▁` `3` `#` `o` `4` |
| `C4/4` | `C` `4` `/` `4` | `C` `4` `/` `4` | `▁C` `4` `/` `4` |
| `&s12` | `&s` `12` | `&s` `12` | `▁&` `s` `1` `2` |

| suggestion | tune | cl100k_base | o200k_base | llama2 | header | definitions | phrases | plays | structure |
|---|---|---|---|---|---|---|---|---|---|
| relative to the phrase's root | Metal Warrior | -25 | -11 | +89 | +0 | +0 | -25 | +0 | +0 |
|  | Csillag | -109 | -72 | +103 | +0 | +0 | -109 | +0 | +0 |
|  | Crosseyed | +12 | +20 | +41 | +0 | +0 | +12 | +0 | +0 |
| scale degrees | Metal Warrior | +452 | +452 | +433 | +0 | +0 | +447 | +0 | +5 |
|  | Csillag | +970 | +970 | +924 | +0 | +0 | +965 | +0 | +5 |
|  | Crosseyed | +69 | +69 | +67 | +0 | +0 | +64 | +0 | +5 |
| delta from the previous note | Metal Warrior | -67 | -67 | +24 | +0 | +0 | -67 | +0 | +0 |
|  | Csillag | -220 | -220 | -57 | +0 | +0 | -220 | +0 | +0 |
|  | Crosseyed | -13 | -13 | -2 | +0 | +0 | -13 | +0 | +0 |
| every step written | Metal Warrior | +36 | +36 | +359 | +0 | +0 | +36 | +0 | +0 |
|  | Csillag | +658 | +658 | +2310 | +0 | +0 | +658 | +0 | +0 |
|  | Crosseyed | +863 | +863 | +1713 | +0 | +0 | +863 | +0 | +0 |
| duration as `/n` | Metal Warrior | +0 | +0 | +0 | +0 | +0 | +0 | +0 | +0 |
|  | Csillag | +0 | +0 | +0 | +0 | +0 | +0 | +0 | +0 |
|  | Crosseyed | +0 | +0 | +0 | +0 | +0 | +0 | +0 | +0 |

No spelling makes any phrase definition identical to another in any of the three tunes.

**Absolute note names.** All three tokenizers split a name into letter, accidental and octave
(`C` `#` `4`), so a sharp costs one token and a name two or three; Llama 2 also splits every
multi-digit number into digits (`C10`, `x12`, `*12`). The same melodic shape in two keys is two
token sequences: of the runs of four notes, 39 of 219 (Metal Warrior), 197 of 512 (Csillag) and 0
of 15 (Crosseyed) have an interval shape that also starts from another note. A model answering
"what is the melody" reads it directly; one asked about transposition or contour must subtract.
*Recommendation:* keep.

**Phrase-relative pitch.** Each note as an interval from the phrase's first note, the root stated
once (`=C4`, `+0.i1*2`, `+3`, `+7`). It makes no further phrase identical, because the canonical
writer already merges phrases equal up to a transpose and carries the key in the play entry. It
saves 25 and 109 tokens and costs 12 under the BPE tokenizers, and costs 41 to 103 under Llama 2,
which spends a token on every sign and every digit. *Recommendation:* reject: the key independence
it offers at phrase level the format already has, and within a phrase it hides the pitch.

**Scale degrees.** The key that holds most notes is C minor for all three tunes; a note is its degree, a sharp when outside the scale, and its
octave (`3o4`, `4#o2`). It costs 452, 970 and 69 tokens (+14%, +13%, +5%) under every tokenizer:
each note carries one piece more than its name, the octave mark. No phrase becomes identical. *Recommendation:* reject.

**Delta from the previous note.** The first note by name, every later one as the interval from the
one before (`+3`, `-12`). It saves 67, 220 and 13 tokens with the BPE tokenizers (2-4% of the
phrases) and costs or nearly breaks even under Llama 2 (+24, -57, -2). It makes a shape the same
tokens in every key, which helps a model judge contour and repetition, but a note's pitch then needs
the sum of the phrase before it, so "which note plays at step 20" loses random access, and an edit
to one note moves every note after it. *Recommendation:* reject as the canonical spelling; a
reading aid that derives it belongs in a tool, not the format.

**Durations and rests.** `*n` binds the duration to its event and costs two tokens (`*` and the
number) under every tokenizer; `/n` costs exactly the same; writing every step costs 36 to 863
tokens with `cl100k_base` and 359 to 2310 with Llama 2, which tokenizes `, -` per step.
*Recommendation:* keep `*n`, bound to the event.

## Proposals

Each can be filed as a sub-issue of #23; neither is implemented here.

- **P1: parameterised expression forms.** Let the `x` definitions take integer parameters, `x:
  {f1: '(a, b) (v + a*[(φ - 3) % 12 >= 6] - b) % 65536'}`, and a use name a form with arguments
  (`!freq:@f1(64, 32)`, `freq: f1(32, 16)`); the reader expands each use to the expression it
  proves today, so the grid is unchanged and the canonical form writes a form only for a family of
  two or more texts that saves tokens. Measured: -219 tokens on Csillag, +16 and +61 on the others
  with every family written, so the writer must choose per family. Low priority.
- **P2: sub-phrase references.** Let a phrase name a recurring run of atoms defined once (`sub:
  {s1: [off*3, G4*3]}`, `p2: [&s1, &s1, ...]`), expanded by the reader into the events it stands
  for, with the canonical form choosing runs by a fixed rule (maximal runs that recur apart, longest
  first) so that it stays unique. Measured: -463, -599 and -85 tokens (14%, 8%, 6%) on the three
  optimised tunes, the largest of every change measured; it also gives a model the repetition
  structure directly.
- Not measured, so not proposed: a program written as another program's prefix with its `end` or
  `loop` moved (the passes cannot produce it to measure, since the format has no form for it).
