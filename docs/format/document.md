# Document

Top-level keys, in this order:

| key | content |
|---|---|
| `unitracker` | `1.20`; readers reject any other version |
| `playroutine` | `{ref: name, constants: {...}, tables: {...}}`, or the full definition (see [Embedding](#embedding)) |
| `meta` | `title`, `author`, `released` |
| `clock`, `voices` | only where they differ from the playroutine's `defaults` |
| `tempo` | the initial tempo, only where it differs from the playroutine's `defaults`: `n` ticks per step, or `[a, b]`, a swing whose steps alternate `a` and `b` ticks (two different tempos); a playroutine without a tempo refuses it |
| `song` | the timeline: rows, then `end` or `{loop: k}`, as a program ends |

Each value has one spelling: an integer is decimal without leading zeros (`129`, not `0x81` or
`0201`), a relative note carries its sign (`note: +0`), a note name its octave without leading
zeros (`C2`, not `C02`), and the only booleans are `true` and `false` (`off` and `on` are atoms);
the reader refuses YAML's other spellings (`no`, `False`, `0x81`).

`playroutine.constants` lists only the constants that differ from the library's.
`playroutine.tables` patches the library's tables: `{index: value}` for the entries that
differ, or a full list when the length differs.

The patch holds only entries the song can read. The writer plays the song, recording every
table index read, until its complete state (all cells, cursors and registers) recurs. Playback
is deterministic, so no later tick reads anything new, and an entry never read cannot change the
output; the library default stands in for it. If the state does not recur within a bound, the
patch is written whole.

## Embedding

`unitracker-lift --embed` writes the playroutine in full instead of `ref`, with its `tables`
complete, so the file plays without the library.

## Timeline

`song` is a list of rows followed by `end`, where the song stops, or `{loop: k}`, where it
continues at row `k`. Each row is `{def: {...}, play: [entry per voice]}`; `def` is present only
when something is first used in that row. An entry is:

| entry | meaning |
|---|---|
| `p3` | the voice starts phrase `p3` |
| `p3+5`, `p3-2` | the voice starts phrase `p3` transposed by that many semitones |
| `end` | the voice stops |
| `~` | the voice is still playing its phrase |

A row starts at the earliest end of the voices' current phrases. Every voice whose phrase ends
there must have an entry, and every other voice must be `~`; readers reject anything else. `end`
stops every voice still playing, so a song that ends has no last row of `end` and `~` entries
only, and readers reject one.
Repeats are written out as rows. When the voices' loops have different lengths, the rows run
until all of them are back in the same state together, and `{loop: k}` names the row where that
state first occurred. The loop is the song's shortest, from the earliest row the song repeats
from; readers reject a loop that is a whole number of the song's loops long, or that starts later
than it repeats.

## Definitions

`def` maps a record type, `x`, or `phrase` to `{name: value}`. Names are the record type's
prefix and a number, given in first-use order (`i1`, `i2`, ..., `c1`, ...; expressions `x1`,
...; phrases `p1`, ...). The prefixes are distinct, so a name says what it is. The reader
allocates each type's indices in definition order from the type's `none + 1` (0 for a type with
no `none`), so a name
stands for whatever index the playroutine needs.

Every playroutine writes its records under these record types (its `record_types` entry names
the type each native table is written as, `writes_as`):

| record type | prefix | holds |
|---|---|---|
| `ins` | `i` | an instrument |
| `ctrl` | `c` | a control program: waveform, notes, and any other key a row of the player can set |
| `pw` | `pw` | a pulse program |
| `filter` | `f` | a filter program |
| `restart` | `r` | a hard restart (see [Hard restart](vocabulary.md#hard-restart)) |
| `x` | `x` | a modulator expression (see [Modulators](../modulation/expressions.md)) |

- **Rows** use the shared row keys (see [Shared vocabulary](vocabulary.md)) wherever the
  playroutine's codec can express them: `{ctrl: 129, note: G#5}`. A row no codec shape covers is
  written in the type's `form`: a `list` row holds the fields in declaration order, with trailing
  defaults left out (`[4, 32]`); a `map` row holds the fields that differ from their defaults.
- **Programs** are the rows of a chained type, in the order they run, followed by `end` or
  `{loop: k}` (k is a row of the same program). A program is cut from the player's chained
  tables at every entry point. A tail two programs share is written in both, which is exact
  because rows are deterministic. A program that is one expression is written as that
  expression: `pw: 16*abs((φ - 32) % 128 - 64) + 512`. A row of a type whose rows that only time
  the program add up (`adds`, see [Record types](../playroutines/schema.md#record-types)) is
  written once for each run of them, as long as one row lasts, and none ends a program: a
  GoatTracker 2 filter program writes `{hold: 5}`, not `{hold: 2}, {hold: 3}`, and no `{hold: 5}`
  before its `end`.
- **References.** A field that references another record holds a name. A program that only one
  instrument uses is written inline in that instrument instead
  (`ctrl: [{ctrl: 9, note: +0}, {ctrl: 65, note: +0}, end]`).
- **Expressions** (`x`) are the modulator expressions an event command names (`!freq:x1`):
  `x: {x1: v + 32}`. An expression no command names is written where it is used.
- **Fixed** record types (`fixed`, such as an initial voice state) keep their indices and are
  defined in the first row: `vinit: {0: {note: C3, pw: 256, freq: 2227}, ...}`. They use the
  shared keys with their row meaning wherever one fits, and an omitted field is 0.

## Events

A phrase is a list of event atoms:

```
note[.instrument][!command[:arg]...][*steps]
```

- `note` is a note name such as `C2`, `A#3`, `C10` or `B-1`, `-` for no note, `off` (gate off) or
  `on` (gate on). Names are real pitch: `C2` is 65.4 Hz in every song, whatever index the
  player's frequency table gives it (`note_offset`).
- `.i3` names the instrument. A note that names the instrument its phrase last named plays it
  without the name, and is written without it: but where the instrument keeps a program by a
  lifetime flag (naming it restarts the program, see [Lifetime](vocabulary.md#lifetime)), where a
  command of the note is read into another column when it names one, and in a player whose events
  arm programs through their instrument (see [Instrument columns](vocabulary.md#instrument-columns)).
  The reader refuses the name where it changes nothing.
- `!run:c1` is a command with an argument: a number, the name of the record or expression a
  command references (names are a prefix and digits, so they read as names), `none`, or values
  joined by `/` for a command of several values (`!tempo:6/7`). A command that takes no argument is
  written without one (`!legato`). A command's argument is a byte: `!freq:x7` names one of the
  first 255 expressions. An event carries at most two commands, its two slots, then `!pw`
  and `!filter` (see [Lifetime](vocabulary.md#lifetime)), which take no slot. The first slot is
  the instrument column and the second the FX column; an event that names an instrument fills the
  instrument column with it, so its commands are the FX column's. A playroutine declares the
  column each command is read into (`column`); a column holds one command, and an event with two
  in one is refused, naming both. The commands are written in the order of the command table of
  [Shared vocabulary](vocabulary.md), but an envelope command keeps its slot, which decides its
  column (see [Envelope commands](vocabulary.md#envelope-commands)); `!pw` and `!filter` come last,
  in that order. The reader refuses any other order.
- `*3` makes the event last 3 steps; the steps after the first are empty. One step is written
  without `*`: `*1` is refused.

## Canonical form

The writer produces one text per song:

- names are given in first-use order, each a record type's prefix and a number;
- records and rows the song never reaches are dropped;
- identical phrases, and phrases that are equal up to a transpose, are merged, and the transpose
  moves into the play entry;
- equal expression texts share one definition, and a constant is written in its plain form
  (`pw: 2048`; see [Where expressions go](../modulation/expressions.md#where-expressions-go));
- runs of empty steps become `*steps`;
- a restart its notes arm on the steps before them is written as their instrument's `restart`
  (see [Hard restart](vocabulary.md#hard-restart));
- a phrase whose envelope bytes differ between the places it plays is split into one phrase per
  place (see [Envelope commands](vocabulary.md#envelope-commands)), the inverse of the merge.

Writing what the reader returns gives the same bytes.
